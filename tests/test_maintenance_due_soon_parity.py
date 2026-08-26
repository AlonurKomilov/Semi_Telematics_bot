"""The due-soon rule is written TWICE.  Keep the copies honest.

``classify_task_urgency`` (features/maintenance/service.py) decides
urgency for the Overview badge and the AI maintenance tools.
``makeUrgencyClassifier`` (interfaces/dashboard/src/features/maintenance/
useMaintenanceTasks.ts) decides the status chip on the Maintenance grid.
They are the same rule expressed in two languages, and the Python
docstring has always said so — with nothing enforcing it.

That is exactly how the drift this test now prevents got in.  The
thresholds matched; what did NOT match was a third opinion in
badges.tsx, where the progress bar coloured itself from ``pct >= 90`` of
the recur period instead of the shared window.  On a 200,000-mile
transmission service that turns the bar amber with 20,000 miles left —
six weeks of driving — while the chip beside it still read "pending".
One row, three different claims about the same task.

This file pins the two SURVIVING implementations to one another.  It
compares CONSTANTS and worked EXAMPLES rather than importing the
TypeScript, because the point is that a developer editing one file is
told to edit the other.
"""

from __future__ import annotations

import os
import re
import sys

os.environ.setdefault("ENCRYPTION_KEY", "")

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

from features.maintenance.service import (  # noqa: E402
    DUE_SOON_DAYS, DUE_SOON_HOURS, DUE_SOON_INTERVAL_FRACTION,
    DUE_SOON_MILES, classify_task_urgency, due_soon_hours_for,
    due_soon_miles_for,
)

from tests._repo import REPO as _REPO  # sentinel-anchored, not depth-counted  # noqa: E402
REPO = str(_REPO)
TS = os.path.join(
    REPO, "interfaces", "dashboard", "src", "features", "maintenance",
    "useMaintenanceTasks.ts",
)


def _ts_const(name: str) -> float:
    """Read a numeric constant out of the TypeScript mirror."""
    src = open(TS, encoding="utf-8").read()
    m = re.search(rf"export const {name} = ([0-9_.]+);", src)
    assert m, f"{name} not found in {TS} — was it renamed?"
    return float(m.group(1).replace("_", ""))


class TestConstantsMatch:
    """Same numbers on both sides, or the chip and the badge disagree."""

    def test_flat_floors_match(self):
        assert DUE_SOON_DAYS == _ts_const("DUE_SOON_DAYS")
        assert DUE_SOON_MILES == _ts_const("DUE_SOON_MILES")
        assert DUE_SOON_HOURS == _ts_const("DUE_SOON_HOURS")

    def test_interval_fraction_matches(self):
        assert DUE_SOON_INTERVAL_FRACTION == _ts_const(
            "DUE_SOON_INTERVAL_FRACTION")

    def test_the_ts_side_still_scales_by_interval(self):
        """A mirror that stopped scaling would pass the constant checks
        while quietly reverting to the flat window."""
        src = open(TS, encoding="utf-8").read()
        assert "dueSoonMilesFor(t.recur_interval_miles)" in src, (
            "the TS classifier no longer scales the mileage window by the "
            "recur interval — Python does, so the chip and the Overview "
            "badge would disagree"
        )


class TestScalingBehaviour:
    """Only LONG intervals move; short ones must not regress."""

    def test_short_intervals_keep_the_flat_window(self):
        assert due_soon_miles_for(30_000) == DUE_SOON_MILES     # 1,500 < floor
        assert due_soon_miles_for(100_000) == DUE_SOON_MILES    # 5,000 == floor
        assert due_soon_miles_for(None) == DUE_SOON_MILES
        assert due_soon_miles_for(0) == DUE_SOON_MILES

    def test_long_intervals_widen(self):
        assert due_soon_miles_for(200_000) == 10_000
        assert due_soon_hours_for(6_000) == 300

    def test_the_transmission_case_that_started_this(self):
        """Unit 225: a 200,000-mile transmission service, 9,680 mi out.

        Its bar had been amber (95% through the period) while the chip
        said "pending" (9,680 > the flat 5,000).  Under the scaled window
        both now say due_soon, which is what three weeks of runway on a
        major service actually is.
        """
        task = {
            "due_miles": 300_575,
            "last_odometer": 290_895,          # 9,680 remaining
            "recur_interval_miles": 200_000,
        }
        assert classify_task_urgency(task) == "due_soon"

    def test_an_oil_change_at_the_same_distance_is_still_pending(self):
        """The scaling must not simply widen everything: the same 9,680
        miles on a 30,000-mile interval is three months away."""
        task = {
            "due_miles": 300_575,
            "last_odometer": 290_895,
            "recur_interval_miles": 30_000,
        }
        assert classify_task_urgency(task) is None

    def test_overdue_still_wins_over_due_soon(self):
        task = {
            "due_miles": 100_000,
            "last_odometer": 105_000,          # 5,000 past
            "recur_interval_miles": 200_000,
        }
        assert classify_task_urgency(task) == "overdue"
