"""Shared fixtures for the Semi Telematics Bot test suite.

Test backend is Postgres exclusively.  ``db`` is an alias for ``pg_db``
backed by a session-scoped ``testcontainers`` Postgres instance — every
test gets a pristine schema (DROP + recreate ``public``) before
``Database.initialize()`` runs.  The SQLite fallback that used to live
here was retired with the rest of the SQLite production path; if you
need to write a test that exercises a specific PG behaviour (upsert
semantics, transaction isolation, asyncpg cursor edge cases), use
``pg_db`` directly — the alias makes that the default for legacy tests
that were originally written against SQLite.
"""

from __future__ import annotations

import itertools
import os
import sys
import tempfile
from typing import AsyncIterator

# Ensure encryption is not active during tests (unless explicitly tested)
os.environ.setdefault("ENCRYPTION_KEY", "")

# JWT_SECRET is required at API startup (the fallback to TELEGRAM_TOKEN
# was removed because rotating the bot token would silently invalidate
# every issued JWT).  Tests don't authenticate real users so the value
# is deterministic and only used to import auth-touching modules.
os.environ.setdefault("JWT_SECRET", "test-jwt-secret-not-for-production-use-only")
# Notification email links are signed with their OWN secret (decoupled
# from JWT_SECRET so JWT rotation can't break in-inbox unsubscribe links).
os.environ.setdefault(
    "NOTIFICATION_SIGNING_SECRET", "test-notification-signing-secret-32b+")

# Object writes go to a THROWAWAY root, never the real tenant tree.
#
# accounts.id starts at 10000001 by schema design, so the first account
# a fresh test DB creates carries the same id as the first real one —
# and with the default root that put test fixtures inside a live
# customer's folders (64 "Roe, Jane" application paths were found
# sitting in production's tree).  Assigned, not setdefault: an exported
# OBJECT_STORE_ROOT pointing at production must not be able to win here.
os.environ["OBJECT_STORE_ROOT"] = os.path.join(
    tempfile.gettempdir(), "4truck-test-userdata",
)

import pytest  # noqa: E402
import pytest_asyncio  # noqa: E402

# Ensure project root is on sys.path
sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

# E402: the os.environ.setdefault calls above must happen BEFORE these
# imports so adapters.storage / interfaces.api.auth see the test env at
# module load.  Refactoring to a pytest_configure hook won't help —
# that fires AFTER conftest module imports.
from adapters.storage import Database, Role  # noqa: E402


@pytest.fixture(autouse=True)
def _reset_rate_limiter():
    """Reset the shared slowapi limiter before each test.

    Per-endpoint hour limits (e.g. ``/api/applications/apply`` at 30/hour)
    otherwise accumulate across a whole test file — once exhausted, later
    tests receive 429 instead of their expected status (the applications
    suite hit exactly this: 422-expecting tests got 429).  Resetting the
    in-memory counters per test isolates them.
    """
    try:
        from interfaces.api.rate_limit import limiter
        limiter.reset()
    except Exception:
        pass
    yield


@pytest_asyncio.fixture
async def db(pg_db):
    """Postgres-backed ``Database`` (legacy ``db`` fixture alias).

    Forwards to :func:`pg_db` so the ~800 tests that were originally
    written against the SQLite ``db`` fixture continue to work after
    the SQLite path was retired.  Schema is reset between tests and
    the container is reused across the session.

    New tests should request ``pg_db`` directly — the alias is a
    transitional convenience for the legacy fixture name.
    """
    yield pg_db


# ── Postgres test infrastructure ─────────────────────────────────


def _has_docker() -> bool:
    """Cheap probe used by the skip guard.  We don't want to spend
    10 s spinning up a container just to fail on a CI box that has
    no Docker daemon — better to skip with a clear message."""
    try:
        import docker  # type: ignore[import]
        docker.from_env().ping()
        return True
    except Exception:
        return False


def _per_worker_database(url: str) -> str:
    """Give each xdist worker its OWN database on a shared server.

    The container path already isolates workers — each is a separate
    process running its own session fixture, so each gets its own
    container.  The ``POSTGRES_TEST_URL`` path handed every worker the
    SAME url, so under ``-n auto`` eight of them raced on the per-test
    ``DROP SCHEMA public`` / ``CREATE SCHEMA public`` reset and died with
    ``DuplicateSchemaError``.  The documented CI path was therefore
    incompatible with the configured parallelism, which is part of why
    it was never actually wired up.

    Appending the worker id keeps the promise the docstring below makes,
    at the cost of one CREATE DATABASE per worker per session.  Serial
    runs (no ``PYTEST_XDIST_WORKER``) are untouched.
    """
    worker = os.getenv("PYTEST_XDIST_WORKER", "").strip()
    if not worker:
        return url
    head, _, tail = url.rpartition("/")
    dbname, sep, query = tail.partition("?")
    target = f"{dbname or 'test'}_{worker}"

    async def _ensure() -> None:
        import asyncpg
        conn = await asyncpg.connect(url)
        try:
            await conn.execute(f'CREATE DATABASE "{target}"')
        except Exception:
            # Already there from a previous run — the per-test schema
            # reset below scrubs it, so existing state is not a problem.
            pass
        finally:
            await conn.close()

    import asyncio
    import concurrent.futures
    # Same thread trick as the container start: pytest-asyncio may
    # already own the running loop in this process.
    with concurrent.futures.ThreadPoolExecutor(max_workers=1) as pool:
        pool.submit(lambda: asyncio.run(_ensure())).result()
    return f"{head}/{target}{sep}{query}"


# ── Per-test databases by TEMPLATE, not by re-migration ───────────
#
# Every DB-backed test used to DROP the schema and replay all 194
# migrations: ~34 s per test measured, against 2.5 s for a test that
# needs no database.  With ~3,400 tests that is the 4h22m suite nobody
# could run — and a suite nobody runs is why CI became the only thing
# running it, and why CI silently skipping the DB tests went unnoticed
# for weeks.
#
# The cost is avoidable because migrations are VERSION-TRACKED
# (adapters/storage/migrations.py: _is_applied / _mark_applied).  Build
# the schema ONCE per worker into a template database, then give each
# test a copy: `CREATE DATABASE x TEMPLATE t` is a file copy inside
# Postgres, and the copy carries the version table, so run_all() skips
# all 194 instead of executing them.
#
# The isolation property is unchanged — every test still gets its own
# pristine database, never a half-cleared one.  Only the way that
# database is PRODUCED changes: copied, not rebuilt.

_TEST_DB_SEQ = itertools.count(1)


def _split_url(url: str) -> tuple[str, str, str, str]:
    """(head, dbname, sep, query) — the pieces needed to swap databases."""
    head, _, tail = url.rpartition("/")
    dbname, sep, query = tail.partition("?")
    return head, (dbname or "test"), sep, query


def _with_db(url: str, name: str) -> str:
    head, _dbname, sep, query = _split_url(url)
    return f"{head}/{name}{sep}{query}"


def _admin_url(url: str) -> str:
    """A connection on the same server but a database we never copy.

    CREATE/DROP DATABASE cannot run from inside the database being
    acted on, and `postgres` is guaranteed to exist.
    """
    return _with_db(url, "postgres")


def _run_sync(coro_fn) -> None:
    """Run an async helper from a SYNC fixture.

    Same reason the container start uses a thread: pytest-asyncio may
    already own a running loop in this process, and asyncio.run() would
    refuse.
    """
    import asyncio
    import concurrent.futures
    with concurrent.futures.ThreadPoolExecutor(max_workers=1) as pool:
        pool.submit(lambda: asyncio.run(coro_fn())).result()


@pytest.fixture(scope="session")
def _pg_container_url() -> str:
    """Start one Postgres container for the whole test session.

    Cost: ~5–10 s on first use (Docker pull + boot); negligible
    thereafter (every test reuses the same container).  Per-worker
    when running under ``pytest-xdist`` — each worker is a separate
    process and gets its own container so test isolation is preserved
    even with parallel execution.

    ``POSTGRES_TEST_URL`` override skips the container entirely so CI
    pipelines that already provide a clean PG (GitHub Actions
    ``services: postgres``) don't pay the container startup cost.

    The ``start()`` call runs in a worker thread because
    ``testcontainers``' ``wait_until_ready`` internally calls
    ``asyncio.run()`` — when pytest-asyncio's loop is already active
    that raises ``Runner.run() cannot be called from a running event
    loop``.  Offloading to a thread sidesteps the conflict without
    forking testcontainers.
    """
    override = os.getenv("POSTGRES_TEST_URL", "").strip()
    if override:
        yield _per_worker_database(override)
        return

    if not _has_docker():
        pytest.skip(
            "Docker not available and POSTGRES_TEST_URL not set — "
            "skipping pg_db-backed tests."
        )

    import concurrent.futures
    from testcontainers.postgres import PostgresContainer

    container = PostgresContainer("postgres:16-alpine")
    with concurrent.futures.ThreadPoolExecutor(max_workers=1) as pool:
        pool.submit(container.start).result()

    try:
        url = container.get_connection_url()
        url = url.replace("postgresql+psycopg2://", "postgresql://")
        url = url.replace("postgresql+psycopg://", "postgresql://")
        yield url
    finally:
        with concurrent.futures.ThreadPoolExecutor(max_workers=1) as pool:
            pool.submit(container.stop).result()


@pytest.fixture(scope="session")
def _pg_template(_pg_container_url) -> str:
    """Build the migrated schema ONCE per worker.  Tests copy it.

    This is the whole optimisation: the 194-migration replay happens
    here, one time, instead of inside every test.  Because migrations
    are version-tracked, the copy carries the version table and
    ``run_all()`` finds everything already applied.
    """
    base = _pg_container_url
    template = f"{_split_url(base)[1]}_tmpl"

    async def _create() -> None:
        import asyncpg
        conn = await asyncpg.connect(_admin_url(base))
        try:
            await conn.execute(f'DROP DATABASE IF EXISTS "{template}" WITH (FORCE)')
            await conn.execute(f'CREATE DATABASE "{template}"')
        finally:
            await conn.close()

    async def _migrate() -> None:
        import adapters.storage.core as _core
        saved = _core._DATABASE_URL
        _core._DATABASE_URL = _with_db(base, template)
        try:
            d = Database("ignored_path_pg_branch_used", pool_size=2)
            await d.initialize()
            await d.close()
        finally:
            _core._DATABASE_URL = saved

    _run_sync(_create)
    _run_sync(_migrate)
    return template


@pytest_asyncio.fixture
async def pg_db(_pg_container_url, _pg_template, monkeypatch) -> AsyncIterator[Database]:
    """Per-test Postgres-backed ``Database`` — a COPY of the template.

    Each test still gets its own pristine database; it is produced by
    copying the session template rather than by dropping the schema and
    replaying every migration.  The original reason for DROP+CREATE
    still holds and is still honoured — migrations that RENAME or DROP
    columns are not idempotent against a half-cleared schema, so no test
    ever sees one: a fresh copy is as clean as a fresh build.
    """
    import asyncpg
    base = _pg_container_url
    name = f"{_split_url(base)[1]}_t{next(_TEST_DB_SEQ)}"
    admin = _admin_url(base)

    conn = await asyncpg.connect(admin)
    try:
        await conn.execute(f'DROP DATABASE IF EXISTS "{name}" WITH (FORCE)')
        await conn.execute(f'CREATE DATABASE "{name}" TEMPLATE "{_pg_template}"')
    finally:
        await conn.close()

    # The Database class captures DATABASE_URL at the module level —
    # patch it so initialise() picks the PG branch.  monkeypatch
    # restores the previous value after the test returns.
    import adapters.storage.core as _core
    monkeypatch.setattr(_core, "_DATABASE_URL", _with_db(base, name))

    # initialise() re-runs four schema steps after opening the pool.  On
    # a COPY every one of them can only no-op — the copy already has the
    # tables and the applied-migration rows, because the template was
    # built by running these same four for real.  Measured, they cost
    # 19.0 s per test (CREATE TABLE IF NOT EXISTS × ~200, then 194
    # applied-checks) against 0.85 s for the copy itself: this, not the
    # migrations, was where the suite's time actually went.
    #
    # This can never hide a schema change.  The template is built
    # through the REAL path once per session, so a missing or broken
    # migration fails there — loudly, and before any test runs.
    async def _already_in_the_copy(*_a, **_kw):
        return None

    from adapters.storage import migrations as _migrations
    from adapters.storage import platform_migrations as _pmigrations
    from adapters.storage import platform_schema as _pschema
    from adapters.storage import schema as _schema
    monkeypatch.setattr(_schema, "create_tables", _already_in_the_copy)
    monkeypatch.setattr(_migrations, "run_all", _already_in_the_copy)
    monkeypatch.setattr(_pschema, "create_tables", _already_in_the_copy)
    monkeypatch.setattr(_pmigrations, "run_all", _already_in_the_copy)

    database = Database("ignored_path_pg_branch_used", pool_size=2)
    await database.initialize()
    try:
        yield database
    finally:
        await database.close()
        conn = await asyncpg.connect(admin)
        try:
            await conn.execute(f'DROP DATABASE IF EXISTS "{name}" WITH (FORCE)')
        finally:
            await conn.close()


@pytest_asyncio.fixture
async def seeded_db(db: Database):
    """Database pre-loaded with one account, one company, and one owner user."""
    account = await db.create_account("Test Fleet Co")
    company = await db.add_company(
        account_id=account.id,
        code="TFC",
        samsara_api_key="samsara_api_test_key_123",
        display_name="Test Fleet",
    )
    owner = await db.create_user(
        telegram_id=100001,
        account_id=account.id,
        role=Role.OWNER,
    )
    return {
        "db": db,
        "account": account,
        "company": company,
        "owner": owner,
    }


# ── Core infrastructure fixtures ─────────────────────────────────

@pytest_asyncio.fixture
async def core_platform(pg_db):
    """Wire infra.platform with the test PG database.

    Yields the same Database instance pg_db produced.  We swap it into
    the platform module's singleton (``infra.platform._db``) so any
    code that resolves DB access through ``get_db()`` /
    ``get_platform_db()`` sees the test instance; the original
    singleton is restored on teardown.
    """
    import infra.platform as _cp

    _old_db = _cp._db
    _cp._db = pg_db

    yield pg_db

    _cp._db = _old_db


@pytest_asyncio.fixture
async def tenant_registry(core_platform):
    """Provide a TenantRegistry backed by the test core_platform."""
    from infra.registry import TenantRegistry

    registry = TenantRegistry()
    yield registry
    await registry.close_all()
