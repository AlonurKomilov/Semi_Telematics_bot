"""Tests for core/ infrastructure — TenantContext, TenantRegistry, platform, isolation, context vars.

Covers:
- TenantContext isolation (caches, redis namespacing, rate limits)
- TenantRegistry lifecycle (lazy creation, invalidation, close_all)
- Platform init/shutdown via infra.startup
- Fault isolation via run_account_job
- ContextVar-based company_display / org_ids scoping
"""

from __future__ import annotations

import asyncio
import os
import sys

os.environ.setdefault("ENCRYPTION_KEY", "")

import pytest
import pytest_asyncio

from adapters.storage import Database


# ═══════════════════════════════════════════════════════════════════
#  Fixtures
# ═══════════════════════════════════════════════════════════════════

@pytest_asyncio.fixture
async def platform_db(pg_db):
    """Provide an initialised platform Database and wire infra.platform.

    Uses the shared ``pg_db`` fixture (testcontainers PG) — the
    SQLite tmp-file path was retired with the rest of the SQLite
    branch.
    """
    import infra.platform as _cp

    _old_db = _cp._db
    _cp._db = pg_db

    yield pg_db

    _cp._db = _old_db


@pytest_asyncio.fixture
async def seeded_platform(platform_db):
    """Platform DB with two accounts and companies."""
    acct_a = await platform_db.create_account("Fleet A")
    acct_b = await platform_db.create_account("Fleet B")
    await platform_db.add_company(acct_a.id, "CODEA", "key_a", "Company A")
    await platform_db.add_company(acct_b.id, "CODEB", "key_b", "Company B")
    return {
        "db": platform_db,
        "acct_a": acct_a,
        "acct_b": acct_b,
    }


# ═══════════════════════════════════════════════════════════════════
#  TenantContext tests
# ═══════════════════════════════════════════════════════════════════

class TestTenantContext:
    """Per-account isolated state — caches, redis keys, rate limits."""

    async def test_isolation_basic(self, platform_db):
        """Two TenantContexts have independent caches."""
        from infra.tenant import TenantContext

        ctx1 = TenantContext(1, platform_db)
        ctx2 = TenantContext(2, platform_db)

        ctx1.known_faults["v1"] = {"P0001"}
        ctx2.known_faults["v2"] = {"P0002"}

        assert "v1" in ctx1.known_faults
        assert "v1" not in ctx2.known_faults
        assert "v2" in ctx2.known_faults
        assert "v2" not in ctx1.known_faults

    async def test_company_display_isolated(self, platform_db):
        """company_display dict is per-context, not shared."""
        from infra.tenant import TenantContext

        ctx1 = TenantContext(1, platform_db)
        ctx2 = TenantContext(2, platform_db)

        ctx1.company_display["CODE"] = "Fleet One"
        ctx2.company_display["CODE"] = "Fleet Two"

        assert ctx1.company_display["CODE"] == "Fleet One"
        assert ctx2.company_display["CODE"] == "Fleet Two"

    async def test_redis_key_namespacing(self, platform_db):
        """redis_key() produces account-scoped keys."""
        from infra.tenant import TenantContext

        ctx1 = TenantContext(1, platform_db)
        ctx2 = TenantContext(2, platform_db)

        assert ctx1.redis_key("faults:v1") == "t:1:faults:v1"
        assert ctx2.redis_key("faults:v1") == "t:2:faults:v1"
        assert ctx1.redis_key("lowfuel:X") == "t:1:lowfuel:X"

    async def test_rate_limit_independent(self, platform_db):
        """Rate limits are per-context, not global."""
        from infra.tenant import TenantContext

        ctx1 = TenantContext(1, platform_db)
        ctx2 = TenantContext(2, platform_db)

        # First call allowed on both
        assert ctx1.check_rate_limit(user_id=100, command="faults") is True
        assert ctx2.check_rate_limit(user_id=100, command="faults") is True

        # Second call throttled on ctx1 but allowed on ctx2 (different context)
        assert ctx1.check_rate_limit(user_id=100, command="faults") is False
        # ctx2 should also be throttled for the same user+command now
        assert ctx2.check_rate_limit(user_id=100, command="faults") is False

    async def test_rate_limit_different_users(self, platform_db):
        """Different users have independent rate limits within same context."""
        from infra.tenant import TenantContext

        ctx = TenantContext(1, platform_db)

        assert ctx.check_rate_limit(user_id=100, command="faults") is True
        assert ctx.check_rate_limit(user_id=200, command="faults") is True

        # User 100 throttled, user 200 independent
        assert ctx.check_rate_limit(user_id=100, command="faults") is False
        # Different command should be allowed
        assert ctx.check_rate_limit(user_id=100, command="truck") is True

    async def test_close_releases_resources(self, platform_db):
        """close() sets samsara client to None."""
        from infra.tenant import TenantContext

        ctx = TenantContext(1, platform_db)
        assert ctx.samsara is None

        # Close is safe even without samsara client
        await ctx.close()
        assert ctx.samsara is None

    async def test_active_messages_isolated(self, platform_db):
        """active_messages LRU cache is per-context."""
        from infra.tenant import TenantContext

        ctx1 = TenantContext(1, platform_db)
        ctx2 = TenantContext(2, platform_db)

        ctx1.active_messages[(100, 100)] = [1, 2, 3]
        assert (100, 100) not in ctx2.active_messages

    async def test_repr(self, platform_db):
        from infra.tenant import TenantContext
        ctx = TenantContext(42, platform_db)
        assert "42" in repr(ctx)

    async def test_redis_prefix(self, platform_db):
        from infra.tenant import TenantContext
        ctx = TenantContext(7, platform_db)
        assert ctx.redis_prefix == "t:7:"


# ═══════════════════════════════════════════════════════════════════
#  TenantRegistry tests
# ═══════════════════════════════════════════════════════════════════

class TestTenantRegistry:
    """TenantRegistry lifecycle — lazy creation, invalidation, close_all."""

    async def test_lazy_creation(self, platform_db):
        """get() creates TenantContext on first access."""
        from infra.registry import TenantRegistry

        registry = TenantRegistry()
        assert len(registry) == 0

        ctx = await registry.get(1)
        assert ctx.account_id == 1
        assert len(registry) == 1
        assert 1 in registry

    async def test_returns_same_instance(self, platform_db):
        """get() returns cached instance on subsequent calls."""
        from infra.registry import TenantRegistry

        registry = TenantRegistry()
        ctx1 = await registry.get(1)
        ctx2 = await registry.get(1)
        assert ctx1 is ctx2

    async def test_multiple_accounts(self, platform_db):
        """Multiple accounts get separate contexts."""
        from infra.registry import TenantRegistry

        registry = TenantRegistry()
        ctx1 = await registry.get(1)
        ctx2 = await registry.get(2)

        assert ctx1 is not ctx2
        assert ctx1.account_id == 1
        assert ctx2.account_id == 2
        assert len(registry) == 2
        assert sorted(registry.active_accounts) == [1, 2]

    async def test_invalidate(self, platform_db):
        """invalidate() removes and closes context."""
        from infra.registry import TenantRegistry

        registry = TenantRegistry()
        await registry.get(1)
        assert 1 in registry

        await registry.invalidate(1)
        assert 1 not in registry
        assert len(registry) == 0

    async def test_invalidate_nonexistent(self, platform_db):
        """invalidate() on unknown account is a no-op."""
        from infra.registry import TenantRegistry

        registry = TenantRegistry()
        await registry.invalidate(999)  # should not raise

    async def test_close_all(self, platform_db):
        """close_all() removes all contexts."""
        from infra.registry import TenantRegistry

        registry = TenantRegistry()
        await registry.get(1)
        await registry.get(2)
        await registry.get(3)
        assert len(registry) == 3

        await registry.close_all()
        assert len(registry) == 0
        assert registry.active_accounts == []

    async def test_close_all_handles_exceptions(self, platform_db):
        """close_all() continues even if one context.close() fails."""
        from unittest.mock import patch
        from infra.registry import TenantRegistry
        from infra.tenant import TenantContext

        registry = TenantRegistry()
        await registry.get(1)
        await registry.get(2)

        call_count = 0
        _orig_close = TenantContext.close

        async def _flaky_close(self):
            nonlocal call_count
            call_count += 1
            if self.account_id == 1:
                raise RuntimeError("boom")
            await _orig_close(self)

        # Patch at class level (slots prevents instance patching)
        with patch.object(TenantContext, "close", _flaky_close):
            await registry.close_all()

        assert len(registry) == 0
        assert call_count == 2  # both contexts attempted


# ═══════════════════════════════════════════════════════════════════
#  Platform init/shutdown tests
# ═══════════════════════════════════════════════════════════════════

class TestPlatformInit:
    """infra.platform initialize/close cycle."""

    async def test_initialize_and_access(self, _pg_container_url, monkeypatch):
        """initialize() makes get_db() and get_router() available."""
        import infra.platform as _cp

        # Save and clear
        _saved_db = _cp._db
        _cp._db = None

        try:
            # Point Database.initialize() at the test container.  Reset
            # the schema first so init's migration step starts from a
            # clean public schema.
            import asyncpg
            conn = await asyncpg.connect(_pg_container_url)
            try:
                await conn.execute("DROP SCHEMA IF EXISTS public CASCADE")
                # The telemetry warehouse lives in its own schema (migration
                # 183) — reset it too, or the previous run's warehouse tables
                # survive and migration 183 collides on vehicle_state_pkey.
                # conftest.py already did this; these three copies did not.
                await conn.execute("DROP SCHEMA IF EXISTS warehouse CASCADE")
                await conn.execute("CREATE SCHEMA public")
            finally:
                await conn.close()
            import adapters.storage.core as _core
            monkeypatch.setattr(_core, "_DATABASE_URL", _pg_container_url)

            await _cp.initialize()

            assert _cp.get_db() is not None
            assert _cp.get_router() is not None
            assert _cp.get_platform_db() is not None

            await _cp.close()
        finally:
            _cp._db = _saved_db

    async def test_get_db_before_init_raises(self):
        """get_db() raises AssertionError before initialize()."""
        import infra.platform as _cp

        _saved = _cp._db
        _cp._db = None
        try:
            with pytest.raises(AssertionError, match="not initialized"):
                _cp.get_db()
        finally:
            _cp._db = _saved

    async def test_get_router_before_init_raises(self):
        """get_router() raises AssertionError before initialize().

        The shim is stateless but accesses ``_db`` lazily, which
        triggers the not-initialized assertion when called before
        initialize().
        """
        import infra.platform as _cp

        _saved = _cp._db
        _cp._db = None
        try:
            with pytest.raises(AssertionError, match="not initialized"):
                # ``.platform`` reaches into get_db() which asserts.
                _cp.get_router().platform
        finally:
            _cp._db = _saved

    async def test_close_sets_none(self, _pg_container_url, monkeypatch):
        """close() sets _db to None."""
        import infra.platform as _cp

        _saved_db = _cp._db
        _cp._db = None

        try:
            import asyncpg
            conn = await asyncpg.connect(_pg_container_url)
            try:
                await conn.execute("DROP SCHEMA IF EXISTS public CASCADE")
                # The telemetry warehouse lives in its own schema (migration
                # 183) — reset it too, or the previous run's warehouse tables
                # survive and migration 183 collides on vehicle_state_pkey.
                # conftest.py already did this; these three copies did not.
                await conn.execute("DROP SCHEMA IF EXISTS warehouse CASCADE")
                await conn.execute("CREATE SCHEMA public")
            finally:
                await conn.close()
            import adapters.storage.core as _core
            monkeypatch.setattr(_core, "_DATABASE_URL", _pg_container_url)

            await _cp.initialize()
            await _cp.close()

            assert _cp._db is None
        finally:
            _cp._db = _saved_db


# ═══════════════════════════════════════════════════════════════════
#  Fault isolation tests
# ═══════════════════════════════════════════════════════════════════

class TestFaultIsolation:
    """run_account_job() — timeout + exception containment."""

    async def test_successful_job(self):
        """Normal job returns True."""
        from infra.isolation import run_account_job

        async def _good_job():
            pass

        result = await run_account_job(
            _good_job(), account_id=1, job_name="test_job",
        )
        assert result is True

    async def test_failing_job_returns_false(self):
        """Exception in job returns False (doesn't propagate)."""
        from infra.isolation import run_account_job

        async def _bad_job():
            raise ValueError("something broke")

        result = await run_account_job(
            _bad_job(), account_id=1, job_name="test_job",
        )
        assert result is False

    async def test_timeout_returns_false(self):
        """Job exceeding timeout returns False."""
        from infra import isolation

        # Temporarily lower timeout for fast test
        _orig = isolation.ACCOUNT_JOB_TIMEOUT
        isolation.ACCOUNT_JOB_TIMEOUT = 0.1

        async def _slow_job():
            await asyncio.sleep(10)

        try:
            result = await isolation.run_account_job(
                _slow_job(), account_id=1, job_name="test_job",
            )
            assert result is False
        finally:
            isolation.ACCOUNT_JOB_TIMEOUT = _orig

    async def test_one_account_failure_doesnt_block_another(self):
        """Simulate sequential per-account processing — failure isolated."""
        from infra.isolation import run_account_job

        results = {}

        async def _fail():
            raise RuntimeError("account 1 explosion")

        async def _succeed():
            results["account_2"] = "ok"

        r1 = await run_account_job(_fail(), account_id=1, job_name="check")
        r2 = await run_account_job(_succeed(), account_id=2, job_name="check")

        assert r1 is False
        assert r2 is True
        assert results["account_2"] == "ok"


# ═══════════════════════════════════════════════════════════════════
#  ContextVar tests (company_display / org_ids scoping)
# ═══════════════════════════════════════════════════════════════════

class TestContextVars:
    """set_tenant_display / get_company_display / get_org_ids scoping."""

    async def test_set_and_get(self):
        """set_tenant_display values are readable via getters."""
        from infra.context import set_tenant_display, get_company_display, get_org_ids

        display = {"ACME": "Acme Trucking"}
        org = {"ACME": "org_123"}
        set_tenant_display(display, org)

        assert get_company_display() is display
        assert get_org_ids() is org

    async def test_isolation_across_tasks(self):
        """Different asyncio tasks see different display dicts."""
        from infra.context import set_tenant_display, get_company_display

        results = {}

        async def _task(name: str, display: dict):
            set_tenant_display(display)
            await asyncio.sleep(0)  # yield to let other task run
            results[name] = get_company_display()

        d1 = {"CODE": "Fleet A"}
        d2 = {"CODE": "Fleet B"}

        await asyncio.gather(
            _task("t1", d1),
            _task("t2", d2),
        )

        assert results["t1"] is d1
        assert results["t2"] is d2
        assert results["t1"]["CODE"] == "Fleet A"
        assert results["t2"]["CODE"] == "Fleet B"

    async def test_fallback_to_global(self):
        """Without set_tenant_display, getter falls back to samsara_client global."""
        from infra.context import _company_display_var, get_company_display
        from adapters.samsara.client import COMPANY_DISPLAY

        # Reset the context var to trigger fallback
        token = _company_display_var.set(None)
        try:
            result = get_company_display()
            assert result is COMPANY_DISPLAY
        finally:
            _company_display_var.reset(token)


# ═══════════════════════════════════════════════════════════════════
#  Startup (infra.startup) integration tests
# ═══════════════════════════════════════════════════════════════════

class TestStartup:
    """infra.startup.initialize() / shutdown() full cycle."""

    async def test_full_cycle(self, _pg_container_url, monkeypatch):
        """initialize() → use → shutdown() works end-to-end."""
        import infra.startup
        import infra.platform as _cp

        # Save original state
        _saved_db = _cp._db
        _saved_reg = infra.startup.tenant_registry
        _cp._db = None
        infra.startup.tenant_registry = None

        # Reset PG schema + point Database at the test container.
        import asyncpg
        conn = await asyncpg.connect(_pg_container_url)
        try:
            await conn.execute("DROP SCHEMA IF EXISTS public CASCADE")
            # The telemetry warehouse lives in its own schema (migration
            # 183) — reset it too, or the previous run's warehouse tables
            # survive and migration 183 collides on vehicle_state_pkey.
            # conftest.py already did this; these three copies did not.
            await conn.execute("DROP SCHEMA IF EXISTS warehouse CASCADE")
            await conn.execute("CREATE SCHEMA public")
        finally:
            await conn.close()
        import adapters.storage.core as _core
        monkeypatch.setattr(_core, "_DATABASE_URL", _pg_container_url)

        try:
            registry = await infra.startup.initialize()

            # DB layer accessible
            assert _cp.get_db() is not None
            assert _cp.get_router() is not None

            # Registry returned and cached
            assert registry is not None
            assert infra.startup.tenant_registry is registry

            # Can use registry
            ctx = await registry.get(1)
            assert ctx.account_id == 1

            await infra.startup.shutdown()

            # Cleaned up
            assert _cp._db is None
            assert infra.startup.tenant_registry is None

        finally:
            _cp._db = _saved_db
            infra.startup.tenant_registry = _saved_reg

    async def test_shutdown_safe_without_init(self):
        """shutdown() is safe to call even before initialize()."""
        import infra.startup
        import infra.platform as _cp

        _saved_db = _cp._db
        _saved_reg = infra.startup.tenant_registry
        _cp._db = None
        infra.startup.tenant_registry = None

        try:
            # Should not raise
            await infra.startup.shutdown()
        finally:
            _cp._db = _saved_db
            infra.startup.tenant_registry = _saved_reg


# ═══════════════════════════════════════════════════════════════════
#  Tier 4 Phase 2 — pool refactor + RLS GUC isolation
# ═══════════════════════════════════════════════════════════════════

class TestPoolGucIsolation:
    """Two parallel ``with_account`` blocks see isolated ``app.account_id``.

    The whole point of the Tier 4 Phase 2 pool refactor: the old shared
    single-connection design had a race where one task's
    ``set_config('app.account_id', '1')`` could leak to another
    concurrent task between awaits.  In the pool world, each task tree
    pins its own connection via contextvars and the pool acquire-hook
    stamps the GUC from a contextvar.

    These tests deliberately interleave two tasks to confirm the leak
    no longer happens.
    """

    async def test_parallel_with_account_no_leak(self, db):
        """Two concurrent ``with_account`` blocks must not see each
        other's GUC.

        Without the refactor, task B's set_config could overwrite task
        A's GUC mid-block.  With contextvars, each task sees only its
        own.
        """
        async def read_account_id() -> str:
            cur = await db._db.execute(
                "SELECT current_setting('app.account_id', true) AS v"
            )
            row = await cur.fetchone()
            return row["v"] or "" if row else ""

        async def task(account_id: int, results: list) -> None:
            # Set our own scope, await a few times to interleave with
            # the other task, then verify our scope is still ours.
            async with db.with_account(account_id):
                await asyncio.sleep(0)  # yield to peer
                v1 = await read_account_id()
                await asyncio.sleep(0)
                v2 = await read_account_id()
                results.append((account_id, v1, v2))

        # Note: under the legacy shared-conn design these two tasks
        # would interleave their SET on the same connection and see
        # each other's value.  Under the pool refactor, each task tree
        # has its own contextvar and each ``execute()`` acquires its
        # own conn that's stamped from that contextvar.
        results: list[tuple[int, str, str]] = []
        await asyncio.gather(
            task(101, results),
            task(202, results),
            task(303, results),
        )
        # Each task should only ever see ITS OWN account_id.
        for acct, v1, v2 in results:
            assert v1 == str(acct), f"task {acct} saw v1={v1!r}"
            assert v2 == str(acct), f"task {acct} saw v2={v2!r}"

    async def test_transaction_pins_conn(self, db):
        """``Database.transaction()`` reuses one connection across calls.

        We can't observe asyncpg's connection identity directly through
        the proxy, but we can prove it indirectly via session-scoped
        state: a temp table created in the transaction must be visible
        to a subsequent ``execute()`` in the same block (would NOT be
        on a different connection — TEMP TABLES are session-local).

        Use SELECT not INSERT here — the proxy's INSERT path auto-appends
        ``RETURNING id`` which would error on a no-id table and abort the
        transaction.  Unrelated to pinning.
        """
        async with db.transaction():
            await db._db.execute(
                "CREATE TEMPORARY TABLE _tier4_pin_check (v INTEGER) ON COMMIT DROP"
            )
            # Empty SELECT — proves the table is reachable on the same conn.
            cur = await db._db.execute("SELECT COUNT(*) AS c FROM _tier4_pin_check")
            row = await cur.fetchone()
            assert row["c"] == 0, "TEMP TABLE not visible — pinning broken"

    async def test_transaction_rolls_back_on_exception(self, seeded_db):
        """``Database.transaction()`` rollback on exception leaves no
        partial state behind."""
        db = seeded_db["db"]
        acct_id = seeded_db["account"].id

        before_count_cur = await db._db.execute(
            "SELECT COUNT(*) AS c FROM maintenance_tasks WHERE account_id = ?",
            (acct_id,),
        )
        before = (await before_count_cur.fetchone())["c"]

        with pytest.raises(RuntimeError):
            async with db.transaction():
                await db._db.execute(
                    """INSERT INTO maintenance_tasks
                       (account_id, vehicle_name, task_type, status,
                        due_date, due_miles, created_by, created_at)
                       VALUES (?, ?, 'oil_change', 'pending',
                               '2099-01-01', 0, 1, '2026-01-01T00:00:00')""",
                    (acct_id, "TIER4-TEST-TRUCK"),
                )
                raise RuntimeError("forced rollback")

        after_count_cur = await db._db.execute(
            "SELECT COUNT(*) AS c FROM maintenance_tasks WHERE account_id = ?",
            (acct_id,),
        )
        after = (await after_count_cur.fetchone())["c"]
        assert after == before, (
            f"Rollback failed — count went from {before} to {after}"
        )
