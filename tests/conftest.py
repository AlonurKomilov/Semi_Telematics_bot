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

import os
import sys
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
        yield override
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


@pytest_asyncio.fixture
async def pg_db(_pg_container_url, monkeypatch) -> AsyncIterator[Database]:
    """Per-test Postgres-backed ``Database``.

    The container is shared across the session, but each test gets a
    pristine schema: DROP and recreate ``public`` before initialise(),
    then re-run the full migration pipeline.  Slower per-test (~7 s)
    than a TRUNCATE-only strategy, but migrations that RENAME or DROP
    columns (024_rename_work_schedules_to_work_hours, etc.) aren't
    idempotent against a half-cleared schema — they assume the
    pre-rename table still exists.  DROP+CREATE is the only correct
    reset short of teaching migrations to no-op when the target
    schema is already current, which is a larger project.
    """
    import asyncpg
    conn = await asyncpg.connect(_pg_container_url)
    try:
        await conn.execute("DROP SCHEMA IF EXISTS public CASCADE")
        # The telemetry warehouse lives in its own schema (migration
        # 183) — reset it too, or the previous test's warehouse tables
        # survive into this test's fresh migration run.
        await conn.execute("DROP SCHEMA IF EXISTS warehouse CASCADE")
        await conn.execute("CREATE SCHEMA public")
    finally:
        await conn.close()

    # The Database class captures DATABASE_URL at the module level —
    # patch it so initialise() picks the PG branch.  monkeypatch
    # restores the previous value after the test returns.
    import adapters.storage.core as _core
    monkeypatch.setattr(_core, "_DATABASE_URL", _pg_container_url)

    database = Database("ignored_path_pg_branch_used", pool_size=2)
    await database.initialize()
    try:
        yield database
    finally:
        await database.close()


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
