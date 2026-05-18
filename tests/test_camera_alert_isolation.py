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
from capabilities.alerting.cameras import _issues_for_subscriber


@dataclass
class _StubSub:
    """Minimal subscriber stub matching the fields _issues_for_subscriber reads."""
    role: Role
    truck_num: str | None = None


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
        out = _issues_for_subscriber(sub, SAMPLE_ISSUES)
        assert len(out) == 1
        assert out[0]["vehicle"] == "107"

    def test_driver_with_no_match_gets_empty(self):
        """A driver assigned to a truck with no issues this cycle gets
        an empty list — the dispatch loop will then skip them silently."""
        sub = _StubSub(role=Role.DRIVER, truck_num="999")
        out = _issues_for_subscriber(sub, SAMPLE_ISSUES)
        assert out == []

    def test_owner_sees_every_issue(self):
        """Non-driver subscribers (owner / admin / fleet manager) keep
        the full-fleet view — driver-isolation only applies to drivers."""
        sub = _StubSub(role=Role.OWNER, truck_num=None)
        out = _issues_for_subscriber(sub, SAMPLE_ISSUES)
        assert len(out) == len(SAMPLE_ISSUES)

    def test_admin_sees_every_issue(self):
        sub = _StubSub(role=Role.ADMIN, truck_num=None)
        out = _issues_for_subscriber(sub, SAMPLE_ISSUES)
        assert len(out) == len(SAMPLE_ISSUES)

    def test_driver_with_no_truck_assignment_sees_every(self):
        """A driver with no truck_num set falls back to the unfiltered
        list — same behavior as today, prevents a NULL truck from
        silently muting their entire alert stream."""
        sub = _StubSub(role=Role.DRIVER, truck_num=None)
        out = _issues_for_subscriber(sub, SAMPLE_ISSUES)
        assert len(out) == len(SAMPLE_ISSUES)

    def test_filter_is_case_insensitive_and_substring(self):
        """Truck-name forms vary ("Truck 107" vs "107") — match the
        same case-insensitive substring rule pipeline.py uses for
        consistency between the two alert paths."""
        sub = _StubSub(role=Role.DRIVER, truck_num="107")
        # Simulate a name format like "TRUCK 107" — must still match.
        issues = [
            {"vehicle": "TRUCK 107", "status": "WARNING"},
            {"vehicle": "201",       "status": "PROBLEM"},
        ]
        out = _issues_for_subscriber(sub, issues)
        assert len(out) == 1
        assert out[0]["vehicle"] == "TRUCK 107"

    def test_cross_company_isolation(self):
        """Regression for the reported bug: driver on G1/107 must not
        see issues for CFT, OSY, or any other company even within the
        same account."""
        sub = _StubSub(role=Role.DRIVER, truck_num="107")
        out = _issues_for_subscriber(sub, SAMPLE_ISSUES)
        companies = {r.get("company") for r in out}
        assert companies == {"G1"}, (
            f"Driver on G1/107 leaked alerts for: {companies - {'G1'}}"
        )
