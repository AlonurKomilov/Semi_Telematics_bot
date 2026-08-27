"""The vehicle-scope wall on alert DELIVERY.

A driver assigned truck 229 should not be DM'd about truck 5585.  The
company gate could never express that — a restricted driver's own company
is precisely what they are restricted WITHIN — so until this gate existed
a driver assigned one truck heard about every truck their company owns,
rows the dashboard already refuses to show them.

The two failure directions are opposite on purpose, and these tests pin
both: loading fails OPEN (a database hiccup must not newly silence an
alert), matching fails CLOSED (being shown someone else's truck is a
disclosure).
"""

from __future__ import annotations

import os

os.environ.setdefault("ENCRYPTION_KEY", "")

from dataclasses import dataclass

from adapters.storage import Role
from capabilities.alerting import vehicle_gate as vg
from capabilities.permissions.vehicle_scope import VehicleScope
import pytest


@dataclass
class _Sub:
    id: int
    role: Role
    truck_num: str | None = None


def _scope(names=(), external_ids=(), registry_ids=()):
    return VehicleScope(
        names=frozenset(n.lower() for n in names),
        external_ids=frozenset(external_ids),
        registry_ids=frozenset(registry_ids),
    )


class TestWhoIsScoped:
    def test_only_drivers_are_narrowed(self):
        """Every other role keeps the account-wide view it had.  Scoping
        a fleet manager here would be a second, competing definition of
        what their permissions already decide."""
        gate = {1: _scope(names=["229"])}
        truck = {"name": "5585", "id": "veh-5585"}
        for role in (Role.OWNER, Role.ADMIN, Role.FLEET, Role.DISPATCHER,
                     Role.SAFETY, Role.HR):
            assert vg.user_sees_vehicle(1, role, truck, gate) is True, role

    def test_a_driver_with_no_assignment_is_unrestricted(self):
        """Legacy behaviour, kept deliberately and matching
        deps.get_user_vehicle_scope: absent from the gate means no wall,
        not a blindfold."""
        assert vg.user_sees_vehicle(9, Role.DRIVER, {"name": "229"}, {}) is True
        gate = {1: _scope(names=["229"])}          # someone ELSE is scoped
        assert vg.user_sees_vehicle(9, Role.DRIVER, {"name": "229"}, gate) is True

    def test_an_empty_scope_is_not_a_wall(self):
        assert vg.user_sees_vehicle(
            1, Role.DRIVER, {"name": "229"}, {1: VehicleScope()}) is True


class TestMatchingFailsClosed:
    def test_a_driver_hears_only_about_their_own_truck(self):
        gate = {1: _scope(names=["229"])}
        assert vg.user_sees_vehicle(1, Role.DRIVER, {"name": "229"}, gate) is True
        assert vg.user_sees_vehicle(1, Role.DRIVER, {"name": "5585"}, gate) is False

    def test_an_assignment_does_not_admit_a_longer_number(self):
        """The disclosure the substring comparison allowed: 230 also
        matched 2303, and 100 matched trailer AK1001."""
        gate = {1: _scope(names=["230"])}
        assert vg.user_sees_vehicle(1, Role.DRIVER, {"name": "2303"}, gate) is False
        gate = {1: _scope(names=["100"])}
        assert vg.user_sees_vehicle(1, Role.DRIVER, {"name": "AK1001"}, gate) is False

    def test_a_renamed_truck_still_matches_by_the_stronger_rung(self):
        """Rung 2 is why the gate resolves assignments through the
        registry: the provider renames a truck to "229 Idris Ahmed" and
        the assignment keeps working."""
        gate = {1: _scope(names=["229"], external_ids=["veh-229"])}
        renamed = {"name": "229 Idris Ahmed", "id": "veh-229"}
        assert vg.user_sees_vehicle(1, Role.DRIVER, renamed, gate) is True

    def test_a_vehicle_with_no_identity_at_all_is_not_admitted(self):
        """Nothing to match on is not a reason to deliver: a restricted
        driver hearing about an unattributable vehicle is the same
        disclosure as hearing about a named one."""
        gate = {1: _scope(names=["229"])}
        assert vg.user_sees_vehicle(1, Role.DRIVER, {}, gate) is False


class TestLoadingFailsOpen:
    def test_an_empty_gate_narrows_nothing(self):
        subs = [_Sub(1, Role.DRIVER, "229"), _Sub(2, Role.OWNER)]
        import asyncio

        async def go():
            # load_vehicle_gate returning {} is what a failed load looks
            # like; the filter must then be a no-op.
            orig = vg.load_vehicle_gate
            try:
                vg.load_vehicle_gate = lambda a: _empty()
                return await vg.filter_subscribers_by_vehicle(
                    subs, {"name": "5585"}, 1)
            finally:
                vg.load_vehicle_gate = orig

        async def _empty():
            return {}

        assert len(asyncio.run(go())) == 2

    def test_no_vehicle_identity_means_no_filtering(self):
        import asyncio
        subs = [_Sub(1, Role.DRIVER, "229")]
        out = asyncio.run(vg.filter_subscribers_by_vehicle(subs, {}, 1))
        assert out == subs


class TestFilter:
    def test_the_restricted_are_dropped_and_the_rest_kept(self):
        import asyncio

        async def go():
            orig = vg.load_vehicle_gate
            try:
                async def gate(_a):
                    return {1: _scope(names=["229"]), 3: _scope(names=["5585"])}
                vg.load_vehicle_gate = gate
                subs = [
                    _Sub(1, Role.DRIVER, "229"),     # not this truck
                    _Sub(2, Role.OWNER),             # unrestricted
                    _Sub(3, Role.DRIVER, "5585"),    # this truck
                    _Sub(4, Role.DRIVER),            # unassigned
                ]
                return await vg.filter_subscribers_by_vehicle(
                    subs, {"name": "5585"}, 1)
            finally:
                vg.load_vehicle_gate = orig

        kept = [s.id for s in asyncio.run(go())]
        assert kept == [2, 3, 4]


class TestLoadAtTheSource:
    """The fail-open direction, pinned where it actually happens rather
    than through a stand-in."""

    def test_unreadable_assignments_narrow_nothing(self, monkeypatch):
        import asyncio

        class _Boom:
            async def get_account_vehicle_nums_map(self, account_id):
                raise RuntimeError("pool exhausted")

        monkeypatch.setattr(vg, "get_platform_db", lambda: _Boom())
        assert asyncio.run(vg.load_vehicle_gate(1)) == {}

    def test_an_unreadable_registry_still_yields_name_scopes(self, monkeypatch):
        """The registry gives rungs 1 and 2.  Losing it costs rename
        tolerance, not the wall — a name-only scope still matches by
        exact equality, which is the ladder's own documented floor."""
        import asyncio

        class _Assignments:
            async def get_account_vehicle_nums_map(self, account_id):
                return {5: ["229"]}

        async def _no_tenant(account_id):
            raise RuntimeError("tenant unavailable")

        monkeypatch.setattr(vg, "get_platform_db", lambda: _Assignments())
        monkeypatch.setattr(vg, "get_tenant_db", _no_tenant)
        gate = asyncio.run(vg.load_vehicle_gate(1))
        assert set(gate) == {5}
        assert gate[5].names == frozenset({"229"})
        assert vg.user_sees_vehicle(5, Role.DRIVER, {"name": "229"}, gate) is True
        assert vg.user_sees_vehicle(5, Role.DRIVER, {"name": "5585"}, gate) is False

    def test_an_unkeyable_user_id_never_raises_inside_the_predicate(self):
        """A predicate that raises takes the whole fan-out with it."""
        gate = {1: _scope(names=["229"])}
        assert vg.user_sees_vehicle("not-an-id", Role.DRIVER,
                                    {"name": "5585"}, gate) is True


# ── the legacy column, from a REAL driver (security review 2026-08-26) ──


class TestLegacyTruckNumFallback:
    """A driver onboarded by invite has users.truck_num and NO junction row.

    Every other test in this file injects the gate dict by hand, which is
    exactly why none of them caught the regression this class exists for:
    the bug was in how the gate is BUILT, not in how it is read.

    driver_trucks is the record, but create_user(truck_num=...) — the
    invite-redemption path — writes only the denormalized column, and
    assign_vehicle_to_driver writes driver_vehicle_assignments plus that
    same column.  Absent from the gate means UNRESTRICTED, so such a
    driver saw every vehicle in the account until a restart ran the
    seeding migration.  For camera alerts that is dashcam imagery,
    inward-facing cab views included.
    """

    @pytest.mark.asyncio
    async def test_gate_restricts_a_driver_with_only_the_legacy_column(self, pg_db):
        from adapters.storage import Role as _Role
        db = pg_db
        acct = await db.create_account("Legacy Gate Co")
        driver = await db.create_user(
            telegram_id=770001, account_id=acct.id,
            role=_Role.DRIVER, truck_num="229",
        )

        # Precondition: the junction table really is empty for them.
        junction = await db.get_account_vehicle_nums_map(acct.id)
        assert driver.id not in junction, (
            "this test is meaningless unless the invite path leaves "
            "driver_trucks empty — if that changed, the fallback may be "
            "removable"
        )

        legacy = await db.get_account_legacy_truck_nums(acct.id)
        assert legacy.get(driver.id) == "229"

    @pytest.mark.asyncio
    async def test_such_a_driver_does_not_see_another_truck(self, pg_db, monkeypatch):
        from adapters.storage import Role as _Role
        db = pg_db
        acct = await db.create_account("Legacy Gate Co 2")
        driver = await db.create_user(
            telegram_id=770002, account_id=acct.id,
            role=_Role.DRIVER, truck_num="229",
        )

        monkeypatch.setattr(vg, "get_platform_db", lambda: db)

        async def _no_tenant(account_id):
            return None
        monkeypatch.setattr(vg, "get_tenant_db", _no_tenant)

        gate = await vg.load_vehicle_gate(acct.id)
        assert driver.id in gate, (
            "a driver with users.truck_num set must appear in the gate — "
            "absent means unrestricted, which is how account-wide dashcam "
            "photos reached a freshly invited driver"
        )
        assert vg.user_sees_vehicle(driver.id, Role.DRIVER, {"name": "229"}, gate) is True
        assert vg.user_sees_vehicle(driver.id, Role.DRIVER, {"name": "5585"}, gate) is False
