"""AI Vehicle-Access isolation (Stage 3): Account / Company / Vehicle scope.

The AI tool gate isolates a user to exactly the vehicles their Team Management
**Vehicle Access** allows.  ``scoped_vehicle_nums`` in ``user_context`` is the
resolved effective set:

  * ``None`` → unrestricted (All / owner)
  * ``[...]`` → the only vehicles allowed
  * ``[]``   → none (fail-closed)

One rule covers drivers, vehicle-scoped, and company-scoped users.  The
resolver reads only the Vehicle Access SSOT (``get_user_vehicle_nums`` +
``get_user_company_codes``), so Team Management governs the AI directly.
"""

from __future__ import annotations

import os

os.environ.setdefault("ENCRYPTION_KEY", "")

import pytest

from capabilities.ai.intelligence import _check_tool_permission
from capabilities.ai.scope import resolve_vehicle_scope


# ── Gate: scoped_vehicle_nums isolation (pure, no DB) ────────────────────────
# admin/fleet both hold can_vehicle_all, so the role check passes and the
# Vehicle-Access scope is what blocks (or allows).


@pytest.mark.asyncio
class TestGateVehicleAccessScope:
    async def test_unrestricted_user_allowed_everywhere(self):
        ctx = {"role": "fleet", "scoped_vehicle_nums": None}
        assert await _check_tool_permission("get_account_stats", {}, "fleet", ctx) is None
        assert await _check_tool_permission("get_vehicle_detail", {}, "fleet", ctx) is None

    async def test_company_scoped_blocked_outside_its_vehicles(self):
        # Allowed company B → B-1, B-2.  A-3 belongs to company A.
        ctx = {"role": "fleet", "scoped_vehicle_nums": ["B-1", "B-2"]}
        blocked = await _check_tool_permission(
            "get_vehicle_detail", {"vehicle_name": "A-3"}, "fleet", ctx,
        )
        assert blocked is not None
        assert "Access denied" in blocked["error"]

    async def test_company_scoped_allowed_for_own_vehicle(self):
        ctx = {"role": "fleet", "scoped_vehicle_nums": ["B-1", "B-2"]}
        assert await _check_tool_permission(
            "get_vehicle_detail", {"vehicle_name": "B-1"}, "fleet", ctx,
        ) is None

    async def test_company_scoped_blocked_from_fleetwide_tools(self):
        ctx = {"role": "fleet", "scoped_vehicle_nums": ["B-1", "B-2"]}
        blocked = await _check_tool_permission("get_drivers_list", {}, "fleet", ctx)
        assert blocked is not None
        assert "account-wide" in blocked["error"]

    async def test_vehicle_scoped_restricted_to_picked_vehicle(self):
        ctx = {"role": "admin", "scoped_vehicle_nums": ["001"]}
        assert await _check_tool_permission(
            "get_vehicle_detail", {"vehicle_name": "002"}, "admin", ctx,
        ) is not None
        assert await _check_tool_permission(
            "get_vehicle_detail", {"vehicle_name": "001"}, "admin", ctx,
        ) is None

    async def test_scoped_optional_vehicle_tool_requires_named_vehicle(self):
        # get_recent_work_orders has an OPTIONAL vehicle_name → without one it
        # would run account-wide, so a scoped user must name an allowed vehicle.
        ctx = {"role": "fleet", "scoped_vehicle_nums": ["B-1"]}
        assert await _check_tool_permission(
            "get_recent_work_orders", {}, "fleet", ctx,
        ) is not None

    async def test_empty_scope_fails_closed(self):
        ctx = {"role": "fleet", "scoped_vehicle_nums": []}
        assert await _check_tool_permission(
            "get_vehicle_detail", {"vehicle_name": "B-1"}, "fleet", ctx,
        ) is not None
        assert await _check_tool_permission("get_drivers_list", {}, "fleet", ctx) is not None


# ── Resolver: Vehicle Access (All / Company / Vehicle) → vehicle set ─────────


class _FakeDB:
    def __init__(self, vehicle_nums=None, company_codes=None, role_scope=None):
        self._v = vehicle_nums or []
        self._c = company_codes or []
        self._role_scope = role_scope  # the account's ROLE-level width

    async def get_user_vehicle_nums(self, user_id):
        return list(self._v)

    async def get_user_company_codes(self, user_id):
        return list(self._c)

    async def get_role_vehicle_scope(self, account_id, role):
        return self._role_scope


class _FakeUser:
    """A member row: the three-layer width predicate, as the model has it."""
    def __init__(self, role, vehicle_scope=None):
        self.role = role
        self.vehicle_scope = vehicle_scope

    def scope_with_role_default(self, role_scope):
        from capabilities.permissions.fold import builtin_width
        return self.vehicle_scope or role_scope or builtin_width(self.role)


_ACCT = iter(range(1000, 100000))


def _acct() -> int:
    """A fresh account id per test: the role-width layer is cached per
    (account, role), and two tests sharing one id would share the cache."""
    return next(_ACCT)


@pytest.mark.asyncio
class TestResolveVehicleScope:
    async def test_owner_unrestricted(self):
        scope = await resolve_vehicle_scope(_FakeDB(), 1, 10, "owner")
        assert scope is None

    async def test_all_mode_non_driver_unrestricted(self):
        scope = await resolve_vehicle_scope(_FakeDB(), 1, 10, "fleet")
        assert scope is None

    async def test_assigned_width_uses_assigned_trucks(self):
        # A wide-by-default role narrowed by Team Management (own
        # override) reads exactly the assigned trucks.
        scope = await resolve_vehicle_scope(
            _FakeDB(vehicle_nums=["001", "002"]), _acct(), 10, "admin",
            db_user=_FakeUser("admin", vehicle_scope="assigned"),
        )
        assert scope == ["001", "002"]

    async def test_wide_member_with_assignments_is_not_restricted(self):
        # Width is Team Management's answer.  An admin left wide who
        # happens to have trucks assigned sees the account, as on every
        # other surface — the assignment is not a restriction.
        scope = await resolve_vehicle_scope(
            _FakeDB(vehicle_nums=["001", "002"]), _acct(), 10, "admin",
            db_user=_FakeUser("admin"),
        )
        assert scope is None

    async def test_role_layer_narrows_a_wide_role(self):
        # The account narrowed the whole dispatcher role; no own override.
        scope = await resolve_vehicle_scope(
            _FakeDB(vehicle_nums=["D-7"], role_scope="assigned"), _acct(), 10, "dispatcher",
            db_user=_FakeUser("dispatcher"),
        )
        assert scope == ["D-7"]

    async def test_role_layer_widens_a_driver(self):
        # The account widened its drivers: this one reads the account.
        scope = await resolve_vehicle_scope(
            _FakeDB(vehicle_nums=["T-1"], role_scope="all"), _acct(), 10, "driver",
            db_user=_FakeUser("driver"),
        )
        assert scope is None

    async def test_narrowed_member_without_trucks_gets_nothing(self):
        scope = await resolve_vehicle_scope(
            _FakeDB(), _acct(), 10, "dispatcher",
            db_user=_FakeUser("dispatcher", vehicle_scope="assigned"),
        )
        assert scope == []

    async def test_unknown_role_is_narrow(self):
        # A role string the platform does not know has no built-in
        # width: the member reads their trucks, or nothing — never the
        # account.
        assert await resolve_vehicle_scope(
            _FakeDB(vehicle_nums=["T-1"]), _acct(), 10, "ghost") == ["T-1"]
        assert await resolve_vehicle_scope(_FakeDB(), _acct(), 10, "ghost") == []

    async def test_no_member_row_falls_back_to_builtin_width(self):
        # No row (a script, a hiccup): the built-in width — a driver
        # stays assigned, an admin stays wide.
        assert await resolve_vehicle_scope(
            _FakeDB(vehicle_nums=["T-1"]), _acct(), 10, "driver") == ["T-1"]
        assert await resolve_vehicle_scope(
            _FakeDB(vehicle_nums=["T-1"]), _acct(), 10, "admin") is None

    async def test_driver_failsclosed_when_unassigned(self):
        # Driver with no assignment → empty (blocked), NEVER unrestricted.
        scope = await resolve_vehicle_scope(_FakeDB(), 1, 10, "driver")
        assert scope == []

    async def test_company_mode_expands_to_company_vehicles(self, monkeypatch):
        async def fake_fleet(account_id):
            return [
                {"name": "A-3", "company_code": "A"},
                {"name": "B-1", "company_code": "B"},
                {"name": "B-2", "_org": "B"},
            ]
        monkeypatch.setattr(
            "features.vehicles.service.get_vehicles_overview", fake_fleet,
        )
        scope = await resolve_vehicle_scope(
            _FakeDB(company_codes=["B"]), 1, 10, "fleet",
        )
        assert set(scope) == {"B-1", "B-2"}

    async def test_company_mode_failsclosed_on_fleet_error(self, monkeypatch):
        async def boom(account_id):
            raise RuntimeError("warehouse down")
        monkeypatch.setattr(
            "features.vehicles.service.get_vehicles_overview", boom,
        )
        scope = await resolve_vehicle_scope(
            _FakeDB(company_codes=["B"]), 1, 10, "fleet",
        )
        assert scope == []  # fail-closed, not None
