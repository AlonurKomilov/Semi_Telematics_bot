"""Tests for core/ infrastructure — TenantContext, TenantRegistry, platform, isolation, context vars.

Covers:
- TenantContext isolation (caches, redis namespacing, rate limits)
- TenantRegistry lifecycle (lazy creation, invalidation, close_all)
- Platform init/shutdown via core.startup
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

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

from database import Database


# ═══════════════════════════════════════════════════════════════════
#  Fixtures
# ═══════════════════════════════════════════════════════════════════

@pytest_asyncio.fixture
async def platform_db(tmp_path):
    """Provide an initialised platform Database and wire core.platform."""
    import core.platform as _cp

    db_path = str(tmp_path / "platform_test.db")
    database = Database(db_path, pool_size=1)
    await database.initialize()

    # Wire core.platform so lookups resolve properly
    _old_db = _cp._db
    _old_router = _cp._router
    _cp._db = database

    from database.tenant_router import LegacyRouter
    _cp._router = LegacyRouter(database)

    yield database

    _cp._db = _old_db
    _cp._router = _old_router
    await database.close()


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
        from core.tenant import TenantContext

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
        from core.tenant import TenantContext

        ctx1 = TenantContext(1, platform_db)
        ctx2 = TenantContext(2, platform_db)

        ctx1.company_display["CODE"] = "Fleet One"
        ctx2.company_display["CODE"] = "Fleet Two"

        assert ctx1.company_display["CODE"] == "Fleet One"
        assert ctx2.company_display["CODE"] == "Fleet Two"

    async def test_redis_key_namespacing(self, platform_db):
        """redis_key() produces account-scoped keys."""
        from core.tenant import TenantContext

        ctx1 = TenantContext(1, platform_db)
        ctx2 = TenantContext(2, platform_db)

        assert ctx1.redis_key("faults:v1") == "t:1:faults:v1"
        assert ctx2.redis_key("faults:v1") == "t:2:faults:v1"
        assert ctx1.redis_key("lowfuel:X") == "t:1:lowfuel:X"

    async def test_rate_limit_independent(self, platform_db):
        """Rate limits are per-context, not global."""
        from core.tenant import TenantContext

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
        from core.tenant import TenantContext

        ctx = TenantContext(1, platform_db)

        assert ctx.check_rate_limit(user_id=100, command="faults") is True
        assert ctx.check_rate_limit(user_id=200, command="faults") is True

        # User 100 throttled, user 200 independent
        assert ctx.check_rate_limit(user_id=100, command="faults") is False
        # Different command should be allowed
        assert ctx.check_rate_limit(user_id=100, command="truck") is True

    async def test_close_releases_resources(self, platform_db):
        """close() sets samsara client to None."""
        from core.tenant import TenantContext

        ctx = TenantContext(1, platform_db)
        assert ctx.samsara is None

        # Close is safe even without samsara client
        await ctx.close()
        assert ctx.samsara is None

    async def test_active_messages_isolated(self, platform_db):
        """active_messages LRU cache is per-context."""
        from core.tenant import TenantContext

        ctx1 = TenantContext(1, platform_db)
        ctx2 = TenantContext(2, platform_db)

        ctx1.active_messages[(100, 100)] = [1, 2, 3]
        assert (100, 100) not in ctx2.active_messages

    async def test_repr(self, platform_db):
        from core.tenant import TenantContext
        ctx = TenantContext(42, platform_db)
        assert "42" in repr(ctx)

    async def test_redis_prefix(self, platform_db):
        from core.tenant import TenantContext
        ctx = TenantContext(7, platform_db)
        assert ctx.redis_prefix == "t:7:"


# ═══════════════════════════════════════════════════════════════════
#  TenantRegistry tests
# ═══════════════════════════════════════════════════════════════════

class TestTenantRegistry:
    """TenantRegistry lifecycle — lazy creation, invalidation, close_all."""

    async def test_lazy_creation(self, platform_db):
        """get() creates TenantContext on first access."""
        from core.registry import TenantRegistry

        registry = TenantRegistry()
        assert len(registry) == 0

        ctx = await registry.get(1)
        assert ctx.account_id == 1
        assert len(registry) == 1
        assert 1 in registry

    async def test_returns_same_instance(self, platform_db):
        """get() returns cached instance on subsequent calls."""
        from core.registry import TenantRegistry

        registry = TenantRegistry()
        ctx1 = await registry.get(1)
        ctx2 = await registry.get(1)
        assert ctx1 is ctx2

    async def test_multiple_accounts(self, platform_db):
        """Multiple accounts get separate contexts."""
        from core.registry import TenantRegistry

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
        from core.registry import TenantRegistry

        registry = TenantRegistry()
        await registry.get(1)
        assert 1 in registry

        await registry.invalidate(1)
        assert 1 not in registry
        assert len(registry) == 0

    async def test_invalidate_nonexistent(self, platform_db):
        """invalidate() on unknown account is a no-op."""
        from core.registry import TenantRegistry

        registry = TenantRegistry()
        await registry.invalidate(999)  # should not raise

    async def test_close_all(self, platform_db):
        """close_all() removes all contexts."""
        from core.registry import TenantRegistry

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
        from core.registry import TenantRegistry
        from core.tenant import TenantContext

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
    """core.platform initialize/close cycle."""

    async def test_initialize_and_access(self, tmp_path):
        """initialize() makes get_db() and get_router() available."""
        import core.platform as _cp

        # Save and clear
        _saved_db = _cp._db
        _saved_router = _cp._router
        _cp._db = None
        _cp._router = None

        try:
            # Set DATABASE_PATH to temp
            import core.config
            old_path = core.config.DATABASE_PATH
            core.config.DATABASE_PATH = str(tmp_path / "init_test.db")
            old_mt = core.config.MULTI_TENANT
            core.config.MULTI_TENANT = False

            await _cp.initialize()

            assert _cp.get_db() is not None
            assert _cp.get_router() is not None
            assert _cp.get_platform_db() is not None

            await _cp.close()

            core.config.DATABASE_PATH = old_path
            core.config.MULTI_TENANT = old_mt
        finally:
            _cp._db = _saved_db
            _cp._router = _saved_router

    async def test_get_db_before_init_raises(self):
        """get_db() raises AssertionError before initialize()."""
        import core.platform as _cp

        _saved = _cp._db
        _cp._db = None
        try:
            with pytest.raises(AssertionError, match="not initialized"):
                _cp.get_db()
        finally:
            _cp._db = _saved

    async def test_get_router_before_init_raises(self):
        """get_router() raises AssertionError before initialize()."""
        import core.platform as _cp

        _saved = _cp._router
        _cp._router = None
        try:
            with pytest.raises(AssertionError, match="not initialized"):
                _cp.get_router()
        finally:
            _cp._router = _saved

    async def test_close_sets_none(self, tmp_path):
        """close() sets _db and _router to None."""
        import core.platform as _cp

        _saved_db = _cp._db
        _saved_router = _cp._router
        _cp._db = None
        _cp._router = None

        try:
            import core.config
            old_path = core.config.DATABASE_PATH
            core.config.DATABASE_PATH = str(tmp_path / "close_test.db")
            old_mt = core.config.MULTI_TENANT
            core.config.MULTI_TENANT = False

            await _cp.initialize()
            await _cp.close()

            assert _cp._db is None
            assert _cp._router is None

            core.config.DATABASE_PATH = old_path
            core.config.MULTI_TENANT = old_mt
        finally:
            _cp._db = _saved_db
            _cp._router = _saved_router


# ═══════════════════════════════════════════════════════════════════
#  Fault isolation tests
# ═══════════════════════════════════════════════════════════════════

class TestFaultIsolation:
    """run_account_job() — timeout + exception containment."""

    async def test_successful_job(self):
        """Normal job returns True."""
        from core.isolation import run_account_job

        async def _good_job():
            pass

        result = await run_account_job(
            _good_job(), account_id=1, job_name="test_job",
        )
        assert result is True

    async def test_failing_job_returns_false(self):
        """Exception in job returns False (doesn't propagate)."""
        from core.isolation import run_account_job

        async def _bad_job():
            raise ValueError("something broke")

        result = await run_account_job(
            _bad_job(), account_id=1, job_name="test_job",
        )
        assert result is False

    async def test_timeout_returns_false(self):
        """Job exceeding timeout returns False."""
        from core import isolation

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
        from core.isolation import run_account_job

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
        from core.context import set_tenant_display, get_company_display, get_org_ids

        display = {"ACME": "Acme Trucking"}
        org = {"ACME": "org_123"}
        set_tenant_display(display, org)

        assert get_company_display() is display
        assert get_org_ids() is org

    async def test_isolation_across_tasks(self):
        """Different asyncio tasks see different display dicts."""
        from core.context import set_tenant_display, get_company_display

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
        from core.context import _company_display_var, get_company_display
        from samsara_client import COMPANY_DISPLAY

        # Reset the context var to trigger fallback
        token = _company_display_var.set(None)
        try:
            result = get_company_display()
            assert result is COMPANY_DISPLAY
        finally:
            _company_display_var.reset(token)


# ═══════════════════════════════════════════════════════════════════
#  Startup (core.startup) integration tests
# ═══════════════════════════════════════════════════════════════════

class TestStartup:
    """core.startup.initialize() / shutdown() full cycle."""

    async def test_full_cycle(self, tmp_path):
        """initialize() → use → shutdown() works end-to-end."""
        import core.startup
        import core.platform as _cp
        import core.config

        # Save original state
        _saved_db = _cp._db
        _saved_router = _cp._router
        _saved_reg = core.startup.tenant_registry
        _cp._db = None
        _cp._router = None
        core.startup.tenant_registry = None

        old_path = core.config.DATABASE_PATH
        core.config.DATABASE_PATH = str(tmp_path / "startup_test.db")
        old_mt = core.config.MULTI_TENANT
        core.config.MULTI_TENANT = False

        try:
            registry = await core.startup.initialize()

            # DB layer accessible
            assert _cp.get_db() is not None
            assert _cp.get_router() is not None

            # Registry returned and cached
            assert registry is not None
            assert core.startup.tenant_registry is registry

            # Can use registry
            ctx = await registry.get(1)
            assert ctx.account_id == 1

            await core.startup.shutdown()

            # Cleaned up
            assert _cp._db is None
            assert core.startup.tenant_registry is None

        finally:
            core.config.DATABASE_PATH = old_path
            core.config.MULTI_TENANT = old_mt
            _cp._db = _saved_db
            _cp._router = _saved_router
            core.startup.tenant_registry = _saved_reg

    async def test_shutdown_safe_without_init(self):
        """shutdown() is safe to call even before initialize()."""
        import core.startup
        import core.platform as _cp

        _saved_db = _cp._db
        _saved_router = _cp._router
        _saved_reg = core.startup.tenant_registry
        _cp._db = None
        _cp._router = None
        core.startup.tenant_registry = None

        try:
            # Should not raise
            await core.startup.shutdown()
        finally:
            _cp._db = _saved_db
            _cp._router = _saved_router
            core.startup.tenant_registry = _saved_reg
