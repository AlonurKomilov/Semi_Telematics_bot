"""Driver-role isolation for camera alerts.

Regression test for the bug where drivers were receiving camera
issue reports for trucks they didn't own — even across different
companies inside the same account.  The fix in
``capabilities/alerting/cameras.py`` filters each subscriber's
issue list to their own assigned truck before dispatching.
"""

from __future__ import annotations

import os
import sys

os.environ.setdefault("ENCRYPTION_KEY", "")

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

from dataclasses import dataclass

from adapters.storage import Role
# Load the alerting hub before importing a feature's alert module in
# isolation.  Feature alert modules import ``capabilities.alerting.registry``
# for the @register_alert_source decorator, and the hub's __init__ imports
# them back to re-export their check_* helpers — a latent cycle that's
# harmless in the running app (the hub always loads first) but bites a test
# whose very first import is the feature module.
import capabilities.alerting  # noqa: F401  (load hub first)
from features.cameras.alert import _issues_for_subscriber


@dataclass
class _StubSub:
    """Minimal subscriber stub matching the fields _issues_for_subscriber reads."""
    role: Role
    truck_num: str | None = None
    id: int = 1


def _gate(*names, user_id: int = 1):
    """The account's restriction map, as the shared gate builds it.

    Name-only, which is the ladder's weakest rung and the one a substring
    match used to fake — so a test that passes here proves EXACT-name
    behaviour rather than the old ``in`` comparison.
    """
    from capabilities.permissions.vehicle_scope import VehicleScope
    return {user_id: VehicleScope(names=frozenset(n.lower() for n in names))}


# Camera-issue payload shape mirrors what ``analyze_snapshot`` returns:
# at minimum the per-vehicle dispatch needs ``vehicle``, ``status``,
# ``summary``, optionally ``company`` and ``image_bytes``.
SAMPLE_ISSUES = [
    {"vehicle": "201", "company": "CFT", "status": "PROBLEM"},
    {"vehicle": "221", "company": "CFT", "status": "WARNING"},
    {"vehicle": "107", "company": "G1",  "status": "WARNING"},
    {"vehicle": "112", "company": "G1",  "status": "WARNING"},
    {"vehicle": "238", "company": "OSY", "status": "PROBLEM"},
]


class TestCameraAlertIsolation:
    def test_driver_only_sees_own_truck(self):
        """A driver assigned to Truck 107 must see only the #107 issue —
        not the four others, even though they're in the same account."""
        sub = _StubSub(role=Role.DRIVER, truck_num="107")
        out = _issues_for_subscriber(sub, SAMPLE_ISSUES, _gate("107"))
        assert len(out) == 1
        assert out[0]["vehicle"] == "107"

    def test_driver_with_no_match_gets_empty(self):
        """A driver assigned to a truck with no issues this cycle gets
        an empty list — the dispatch loop will then skip them silently."""
        sub = _StubSub(role=Role.DRIVER, truck_num="999")
        out = _issues_for_subscriber(sub, SAMPLE_ISSUES, _gate("999"))
        assert out == []

    def test_owner_sees_every_issue(self):
        """Non-driver subscribers (owner / admin / fleet manager) keep
        the full-fleet view — driver-isolation only applies to drivers."""
        sub = _StubSub(role=Role.OWNER, truck_num=None)
        out = _issues_for_subscriber(sub, SAMPLE_ISSUES, _gate("107"))
        assert len(out) == len(SAMPLE_ISSUES)

    def test_admin_sees_every_issue(self):
        sub = _StubSub(role=Role.ADMIN, truck_num=None)
        out = _issues_for_subscriber(sub, SAMPLE_ISSUES, _gate("107"))
        assert len(out) == len(SAMPLE_ISSUES)

    def test_driver_with_no_truck_assignment_sees_every(self):
        """A driver with no truck_num set falls back to the unfiltered
        list — same behavior as today, prevents a NULL truck from
        silently muting their entire alert stream."""
        sub = _StubSub(role=Role.DRIVER, truck_num=None)
        # An unassigned driver is ABSENT from the gate — the map lists
        # walls, not people — so nothing narrows for them.  Legacy
        # behaviour, kept deliberately and matching deps.get_user_vehicle_scope.
        out = _issues_for_subscriber(sub, SAMPLE_ISSUES, {})
        assert len(out) == len(SAMPLE_ISSUES)

    def test_a_renamed_truck_still_matches_by_provider_id(self):
        """Display names drift — the provider may call the truck
        "TRUCK 107" while the registry knows it as unit 107.  The ladder
        settles that on rung 2 (provider id), which is why the gate
        resolves each assignment against the registry rather than
        comparing strings.

        This test used to assert a SUBSTRING match instead, and that is
        what made it a disclosure: see the next test.
        """
        from capabilities.permissions.vehicle_scope import VehicleScope
        sub = _StubSub(role=Role.DRIVER, truck_num="107")
        gate = {1: VehicleScope(names=frozenset({"107"}),
                                external_ids=frozenset({"veh-abc"}))}
        issues = [
            {"vehicle": "TRUCK 107", "vehicle_id": "veh-abc", "status": "WARNING"},
            {"vehicle": "201", "vehicle_id": "veh-201", "status": "PROBLEM"},
        ]
        out = _issues_for_subscriber(sub, issues, gate)
        assert len(out) == 1
        assert out[0]["vehicle"] == "TRUCK 107"

    def test_an_assignment_no_longer_admits_a_longer_number(self):
        """The reason the substring had to go, on the most sensitive
        alert in the product: a driver assigned 230 was shown DASHCAM
        images of truck 2303, and 100 was shown trailer AK1001, because
        ``"230" in "2303"`` is true.  These are the rows a driver is
        ALLOWED to see, so an over-match was a disclosure, not a
        convenience."""
        sub = _StubSub(role=Role.DRIVER, truck_num="230")
        issues = [
            {"vehicle": "230",  "vehicle_id": "veh-230",  "status": "WARNING"},
            {"vehicle": "2303", "vehicle_id": "veh-2303", "status": "PROBLEM"},
        ]
        out = _issues_for_subscriber(sub, issues, _gate("230"))
        assert [r["vehicle"] for r in out] == ["230"]

    def test_cross_company_isolation(self):
        """Regression for the reported bug: driver on G1/107 must not
        see issues for CFT, OSY, or any other company even within the
        same account."""
        sub = _StubSub(role=Role.DRIVER, truck_num="107")
        out = _issues_for_subscriber(sub, SAMPLE_ISSUES, _gate("107"))
        companies = {r.get("company") for r in out}
        assert companies == {"G1"}, (
            f"Driver on G1/107 leaked alerts for: {companies - {'G1'}}"
        )


class TestCompanyWall:
    """The per-subscriber company wall, keyed on the WIRE code.

    Its old rule was ``not r.get("company") or ...`` — an issue with no
    company reached EVERY restricted subscriber.  Before the storage fix
    that dropped ``_org`` between analysis and save, "no company" was
    every snapshot, so a fleet manager scoped to one company was seeing
    the whole account's dashcam problems.
    """

    def test_restricted_subscriber_sees_only_their_companies(self):
        from features.cameras.alert import _issues_for_companies
        issues = [
            {"vehicle": "201", "_org": "CFT"},
            {"vehicle": "107", "_org": "G1"},
            {"vehicle": "238", "_org": "OSY"},
        ]
        out = _issues_for_companies(issues, {"G1"})
        assert [r["vehicle"] for r in out] == ["107"]

    def test_unattributed_issue_is_withheld_not_broadcast(self):
        """THE regression.  A row we cannot place must not reach a
        subscriber restricted to a company — it may be another one's."""
        from features.cameras.alert import _issues_for_companies
        issues = [
            {"vehicle": "107", "_org": "G1"},
            {"vehicle": "999", "_org": ""},
            {"vehicle": "998"},
        ]
        out = _issues_for_companies(issues, {"G1"})
        assert [r["vehicle"] for r in out] == ["107"], (
            "an unattributed issue leaked to a company-restricted "
            "subscriber — blank company must mean denied, not allowed"
        )

    def test_the_display_label_cannot_open_the_wall(self):
        """``company`` carries a "?" placeholder and is not the key.  A
        row whose label happens to read like a code still needs the
        wire code to match."""
        from features.cameras.alert import _issues_for_companies
        issues = [{"vehicle": "666", "company": "G1", "_org": ""},
                  {"vehicle": "667", "company": "?", "_org": "G1"}]
        out = _issues_for_companies(issues, {"G1"})
        assert [r["vehicle"] for r in out] == ["667"]

    def test_match_is_case_insensitive_on_both_sides(self):
        from features.cameras.alert import _issues_for_companies
        issues = [{"vehicle": "107", "_org": "g1"}]
        assert len(_issues_for_companies(issues, {"G1"})) == 1
        assert len(_issues_for_companies(issues, {"g1"})) == 1
