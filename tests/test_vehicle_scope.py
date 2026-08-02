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
