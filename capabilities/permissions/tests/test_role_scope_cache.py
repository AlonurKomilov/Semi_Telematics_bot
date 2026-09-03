"""The role-width cache: hits skip the DB, writes invalidate, failures
are not remembered, and the TTL is the permissions cache's own."""

from __future__ import annotations

import os

os.environ.setdefault("ENCRYPTION_KEY", "")

import pytest

from capabilities.permissions import scope
from capabilities.permissions.roles import Role


class _DB:
    def __init__(self, value="assigned", fail=False):
        self.value, self.fail, self.calls = value, fail, 0

    async def get_role_vehicle_scope(self, account_id, role):
        self.calls += 1
        if self.fail:
            raise RuntimeError("db down")
        return self.value


@pytest.fixture(autouse=True)
def _fresh_cache():
    scope.invalidate_role_scope_cache()
    yield
    scope.invalidate_role_scope_cache()


@pytest.mark.asyncio
async def test_second_ask_is_a_hit():
    db = _DB()
    assert await scope.role_scope_layer(1, Role.FLEET, db) == "assigned"
    assert await scope.role_scope_layer(1, Role.FLEET, db) == "assigned"
    assert db.calls == 1


@pytest.mark.asyncio
async def test_absence_is_cached_too():
    # None ("built-in default") is an answer, not a miss.
    db = _DB(value=None)
    assert await scope.role_scope_layer(1, "fleet", db) is None
    assert await scope.role_scope_layer(1, "fleet", db) is None
    assert db.calls == 1


@pytest.mark.asyncio
async def test_invalidation_forces_a_fresh_read_for_that_account_only():
    a, b = _DB("assigned"), _DB("assigned")
    await scope.role_scope_layer(1, "fleet", a)
    await scope.role_scope_layer(2, "fleet", b)
    scope.invalidate_role_scope_cache(1)
    await scope.role_scope_layer(1, "fleet", a)
    await scope.role_scope_layer(2, "fleet", b)
    assert (a.calls, b.calls) == (2, 1)


@pytest.mark.asyncio
async def test_a_failed_read_is_not_remembered():
    db = _DB(fail=True)
    assert await scope.role_scope_layer(1, "fleet", db) is None
    db.fail = False
    assert await scope.role_scope_layer(1, "fleet", db) == "assigned"
    assert db.calls == 2


@pytest.mark.asyncio
async def test_ttl_expiry_rereads(monkeypatch):
    import time
    db = _DB()
    t = [1000.0]
    monkeypatch.setattr(time, "monotonic", lambda: t[0])
    await scope.role_scope_layer(1, "fleet", db)
    from capabilities.permissions.roles import _PERMS_CACHE_TTL_S
    t[0] += _PERMS_CACHE_TTL_S + 1
    await scope.role_scope_layer(1, "fleet", db)
    assert db.calls == 2


class TestWidthIgnoresThePair:
    """The E1c regression, pinned: a member whose role was granted View
    but not Manage on a manage-pair keeps their Team Management width.
    The pair's halves are verbs now, not width."""

    @pytest.mark.asyncio
    async def test_fleet_view_only_maintenance_stays_wide(self, monkeypatch):
        from adapters.storage.models import User
        from capabilities.permissions import roles
        fleet = User(id=1, telegram_id=1, account_id=1, role=Role.FLEET,
                     truck_num=None, alerts_on=False, is_active=True, created_at="")
        # if the core still consulted the grant, this would fire
        async def _boom(*a, **k): raise AssertionError("width read the permission pair")
        monkeypatch.setattr(roles, "can_for_account", _boom)
        async def _no_role_layer(*a, **k): return None
        monkeypatch.setattr(scope, "role_scope_layer", _no_role_layer)
        assert await scope.unit_width(1, Role.FLEET, fleet, "maintenance") == "all"

    @pytest.mark.asyncio
    async def test_driver_is_still_assigned_from_the_builtin_layer(self, monkeypatch):
        from adapters.storage.models import User
        drv = User(id=2, telegram_id=2, account_id=1, role=Role.DRIVER,
                   truck_num="T1", alerts_on=False, is_active=True, created_at="")
        async def _no_role_layer(*a, **k): return None
        monkeypatch.setattr(scope, "role_scope_layer", _no_role_layer)
        assert await scope.unit_width(1, "driver", drv, "maintenance") == "assigned"

    @pytest.mark.asyncio
    async def test_unknown_feature_is_a_loud_error(self):
        with pytest.raises(KeyError):
            await scope.unit_width(1, Role.FLEET, None, "not_a_feature")


class TestNoMemberRowFallsBackToBuiltin:
    @pytest.mark.asyncio
    async def test_driver_without_a_row_is_assigned(self):
        assert await scope.unit_width(1, Role.DRIVER, None, "events") == "assigned"

    @pytest.mark.asyncio
    async def test_wide_role_without_a_row_stays_wide(self):
        for r in (Role.OWNER, Role.FLEET, Role.SAFETY):
            assert await scope.unit_width(1, r, None, "events") == "all", r
