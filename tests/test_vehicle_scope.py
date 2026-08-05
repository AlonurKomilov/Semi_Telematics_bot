"""The driver-visibility walls, finally under test.

These filters decide what a restricted user may SEE — alerts, work
orders, the overview — and none of them had a single test while they
compared provider-editable name text by SUBSTRING.  Every case below
that asserts a non-match was an actual disclosure in production data:
a driver assigned truck 230 could read 2303's records, 100 matched
trailer AK1001, 301 matched five trailers.
"""

from __future__ import annotations

import pytest

from capabilities.permissions.vehicle_scope import (
    VehicleScope,
    build_vehicle_scope,
)


def _scope(ids=(), ext=(), names=()):
    return VehicleScope(
        registry_ids=frozenset(ids),
        external_ids=frozenset(ext),
        names=frozenset(n.lower() for n in names),
    )


class TestNameRung:
    def test_exact_name_matches(self):
        assert _scope(names=["230"]).allows(name="230")
        assert _scope(names=["230"]).allows(name=" 230 ")
        assert _scope(names=["AK230"]).allows(name="ak230")

    def test_substring_disclosures_are_dead(self):
        # Each of these matched before — the assigned number appearing
        # INSIDE another vehicle's name was enough to show its records.
        assert not _scope(names=["230"]).allows(name="2303")
        assert not _scope(names=["100"]).allows(name="AK1001")
        assert not _scope(names=["301"]).allows(name="T3011")
        assert not _scope(names=["229"]).allows(name="229 Idris Ahmed")

    def test_empty_scope_denies(self):
        assert not _scope().allows(name="230")
        assert _scope().empty


class TestIdentityLadder:
    def test_registry_id_outranks_name(self):
        # The renamed truck: name no longer equals the assignment, but
        # the row carries OUR id — rung 1 decides, rename costs nothing.
        s = _scope(ids=[60], names=["229"])
        assert s.allows(registry_id=60, name="229 Idris Ahmed")

    def test_registry_id_mismatch_denies_even_when_name_matches(self):
        # Cross-company twin: OSY's "103" carries a different registry id
        # than G1's "103".  A G1 driver's scope holds G1's id, so the OSY
        # row is denied by rung 1 BEFORE the identical name is consulted.
        s = _scope(ids=[10], names=["103"])
        assert not s.allows(registry_id=99, name="103")

    def test_row_without_id_falls_through_to_name(self):
        # Transition safety: pre-backfill rows carry no registry_id.
        # Absence of the id must not deny the driver's own truck.
        s = _scope(ids=[60], ext=["sam_60"], names=["229"])
        assert s.allows(registry_id=None, external_id=None, name="229")

    def test_external_id_rung(self):
        s = _scope(ext=["sam_60"], names=["229"])
        assert s.allows(external_id="sam_60", name="totally renamed")
        assert not s.allows(external_id="sam_99", name="totally renamed")

    def test_no_shared_rung_denies(self):
        # Wrong-hidden is an annoyance; wrong-shown is a breach.
        s = _scope(ids=[60])
        assert not s.allows(name="anything")

    def test_allows_row_reads_the_standard_keys(self):
        s = _scope(ids=[60])
        assert s.allows_row({"registry_id": 60, "vehicle_name": "x"})
        assert not s.allows_row({"registry_id": 61, "vehicle_name": "x"})


class TestWallIntegration:
    def test_alert_wall_uses_the_ladder(self):
        from capabilities.alerting.service import filter_alerts_by_access
        alerts = [
            {"vehicle_name": "230", "registry_id": 10},
            {"vehicle_name": "2303", "registry_id": 11},   # the old leak
            {"vehicle_name": "229 Idris Ahmed", "registry_id": 60},
        ]
        s = _scope(ids=[10, 60], names=["230", "229"])
        got = filter_alerts_by_access(alerts, ["230", "229"], scope=s)
        assert [a["vehicle_name"] for a in got] == ["230", "229 Idris Ahmed"]

    def test_alert_wall_without_scope_is_exact_not_substring(self):
        from capabilities.alerting.service import filter_alerts_by_access
        alerts = [{"vehicle_name": "2303"}, {"vehicle_name": "230"}]
        got = filter_alerts_by_access(alerts, ["230"])
        assert [a["vehicle_name"] for a in got] == ["230"]

    @pytest.mark.asyncio
    async def test_maintenance_wall(self, monkeypatch):
        """Maintenance kept its OWN copy of the wall, and that copy still
        compared by substring long after deps.py was corrected — across
        seven call sites, two of them the attachment routes, so a driver
        assigned 230 could read AND write 2303's files.

        This exercises the REAL helper: only its two I/O dependencies are
        faked, never the function under test.
        """
        from features.maintenance import router as mr

        built = _scope(ids=[10, 60], names=["230", "229"])

        async def _nums(user):
            return ["230", "229"]

        async def _build(tenant_db, account_id, trucks):
            assert trucks == ["230", "229"], trucks
            return built

        monkeypatch.setattr(mr, "get_user_vehicle_nums", _nums)
        monkeypatch.setattr(mr, "build_vehicle_scope", _build)

        # A _own caller gets the ladder...
        scope = await mr._maintenance_vehicle_scope(
            {"role": "driver", "account_id": 1}, object(),
        )
        assert scope is built
        assert scope.allows_row({"vehicle_name": "230"}, name_key="vehicle_name")
        # ...and the disclosure that started all this is gone.
        assert not scope.allows_row({"vehicle_name": "2303"}, name_key="vehicle_name")
        # A rename still resolves, via the registry rung.
        assert scope.allows_row(
            {"vehicle_name": "229 Idris Ahmed", "registry_id": 60},
            name_key="vehicle_name",
        )

        # can_maintenance_all short-circuits to unrestricted, and must not
        # even look up assignments.
        async def _boom(user):
            raise AssertionError("must not resolve trucks for an _all caller")
        monkeypatch.setattr(mr, "get_user_vehicle_nums", _boom)
        assert await mr._maintenance_vehicle_scope(
            {"role": "owner", "account_id": 1}, object(),
        ) is None

    @pytest.mark.asyncio
    async def test_maintenance_unassigned_own_user_is_denied_not_widened(
        self, monkeypatch,
    ):
        """The real helper's safe-deny: no assignments must yield an EMPTY
        scope (denies everything), never None (which every call site reads
        as unrestricted)."""
        from features.maintenance import router as mr

        async def _none(user):
            return []
        monkeypatch.setattr(mr, "get_user_vehicle_nums", _none)

        scope = await mr._maintenance_vehicle_scope(
            {"role": "driver", "account_id": 1}, object(),
        )
        assert scope is not None, "None would read as UNRESTRICTED at every wall"
        assert scope.empty
        assert not scope.allows_row({"vehicle_name": "230"}, name_key="vehicle_name")

    def test_maintenance_empty_assignment_denies_not_widens(self):
        """Maintenance safe-DENIES an unassigned _own user, the opposite
        of deps.py's "no assignments = unrestricted".  The two conventions
        are deliberate; collapsing them here would be a silent grant."""
        empty = VehicleScope()
        assert empty.empty
        assert not empty.allows_row({"vehicle_name": "230"}, name_key="vehicle_name")

    def test_parking_wall_uses_the_ladder(self):
        """Parking's predicate declared its substring DELIBERATE — the
        stated case was an assignment of "107" matching "Truck-107A".
        The id rungs cover that properly, so the over-match ("107" also
        matching "1107") no longer comes with it."""
        from features.parking import service as ps
        s = _scope(ids=[10], ext=["sam_107"], names=["107"])
        ok = {"vehicle_name": "Truck-107A", "vehicle_id": "sam_107", "company_code": "A"}
        bad = {"vehicle_name": "1107", "vehicle_id": "sam_1107", "company_code": "A"}
        assert ps.is_visible(ok, company_codes=None, truck_names=["107"], scope=s)
        assert not ps.is_visible(bad, company_codes=None, truck_names=["107"], scope=s)

    def test_parking_wall_without_scope_is_exact_not_substring(self):
        """The legacy list path must not let the over-match back in."""
        from features.parking import service as ps
        assert ps.is_visible({"vehicle_name": "107"},
                             company_codes=None, truck_names=["107"])
        assert not ps.is_visible({"vehicle_name": "1107"},
                                 company_codes=None, truck_names=["107"])

    def test_parking_company_wall_still_applies_first(self):
        from features.parking import service as ps
        s = _scope(names=["107"])
        assert not ps.is_visible({"vehicle_name": "107", "company_code": "B"},
                                 company_codes=["A"], truck_names=["107"], scope=s)

    def test_events_wall_ladder(self):
        """/events/{id}/video let a driver assigned 230 open 2303's
        dashcam footage — four walls in that file compared by substring."""
        s = _scope(ids=[10], ext=["sam_230"], names=["230"])
        assert s.allows_row({"vehicle_name": "230", "vehicle_id": "sam_230"},
                            name_key="vehicle_name")
        assert not s.allows_row({"vehicle_name": "2303", "vehicle_id": "sam_2303"},
                                name_key="vehicle_name")
        # The video route maps the provider's nested shape onto the ladder.
        assert s.allows(external_id="sam_230", name="230 Idris Ahmed")
        assert not s.allows(external_id="sam_2303", name="2303")

    def test_work_order_wall(self):
        from features.work_orders.router import _driver_owns_vehicle
        s = _scope(ids=[60], names=["230"])
        assert _driver_owns_vehicle(False, "230", ["230"], scope=s)
        assert not _driver_owns_vehicle(False, "2303", ["230"], scope=s)
        assert _driver_owns_vehicle(True, "anything", [], scope=s)  # can_all
        # Legacy path (no scope): exact equality, never substring.
        assert not _driver_owns_vehicle(False, "2303", ["230"])


@pytest.mark.asyncio
async def test_builder_resolves_assignments_through_the_registry(pg_db):
    """An assignment string becomes a full-ladder scope via the registry."""
    acct = 42
    await pg_db.add_vehicle(acct, unit_number="229", company_code="RMR")
    await pg_db.upsert_from_integration(
        acct,
        [{"company_code": "RMR", "unit_number": "229",
          "telematics_ref": "sam_229"}],
        source="samsara",
    )
    scope = await build_vehicle_scope(pg_db, acct, ["229", "  ", "ghost-77"])
    assert scope.external_ids == frozenset({"sam_229"})
    assert len(scope.registry_ids) == 1
    # The registry-linked truck matches by id even after a rename...
    rid = next(iter(scope.registry_ids))
    assert scope.allows(registry_id=rid, name="229 Idris Ahmed")
    # ...and an assignment the registry has never seen still works by
    # exact name, so a typo'd roster doesn't blank a driver's view.
    assert scope.allows(name="ghost-77")
    assert not scope.allows(name="ghost-777")
