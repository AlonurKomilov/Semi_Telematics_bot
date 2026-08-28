"""The staleness contract's arithmetic and its one flooring rule.

``source_ts`` is when the provider last saw the world move.  Write times
advance every tick whether or not the world does — that is how a truck
parked since May stayed indistinguishable from one reporting this
minute, and how a 43-hour outage read as normal data for weeks.

These are the pure half: what the helpers in
``capabilities.data_lifecycle.staleness`` compute, and the single
flooring rule ``timegrid`` defines.  The WAREHOUSE half — that the value
propagates snapshot → hourly → daily and that every contract table
carries the column — spans features/, adapters/ and this package at
once, so it stays in the root suite as ``tests/test_source_ts.py``.

TestTimeGrid reaches into ``integrations.shared.history_backfill``, and
still belongs here: the flooring rule is OWNED by timegrid, and the
backfill writer is the consumer this checks has not forked it.
"""

from __future__ import annotations

from datetime import datetime, timezone

import pytest

from capabilities.data_lifecycle.staleness import (
    data_age_minutes,
    freshest,
    is_stale,
)



_NOW = datetime(2026, 8, 2, 12, 0, tzinfo=timezone.utc)


class TestHelpers:
    def test_age_of_a_known_time(self):
        assert data_age_minutes("2026-08-02T11:30:00Z", now=_NOW) == pytest.approx(30.0)
        assert data_age_minutes("2026-08-02T11:30:00+00:00", now=_NOW) == pytest.approx(30.0)

    def test_unknown_age_is_none_and_stale(self):
        # NULL / garbage cannot be proven fresh — and these helpers
        # exist for callers deciding whether to trust a number.
        for bad in (None, "", "not-a-time"):
            assert data_age_minutes(bad, now=_NOW) is None
            assert is_stale(bad, 999999, now=_NOW)

    def test_sla_boundary(self):
        assert not is_stale("2026-08-02T11:30:00Z", 31, now=_NOW)
        assert is_stale("2026-08-02T11:30:00Z", 29, now=_NOW)

    def test_freshest_mixes_suffix_styles_and_keeps_originals(self):
        newest = freshest(
            "2026-08-02T10:00:00Z",
            "2026-08-02T11:00:00+00:00",
            "",
            None,
        )
        assert newest == "2026-08-02T11:00:00+00:00"
        assert freshest(None, "") is None


class TestTimeGrid:
    """Sample labels sit ON the grid; the honest moment rides in
    source_ts.  Both minute-tier writers must share ONE flooring rule —
    two local rules is how the grid forked into :00 backfill rows and
    :13 live rows in the first place."""

    def test_label_floors_to_the_minute(self):
        from capabilities.data_lifecycle.timegrid import floor_to_slot
        ts = datetime(2026, 8, 3, 7, 26, 13, tzinfo=timezone.utc)
        assert floor_to_slot(ts) == "2026-08-03T07:26:00+00:00"

    def test_both_minute_writers_agree(self):
        from capabilities.data_lifecycle.timegrid import floor_to_slot
        from capabilities.integrations.shared import history_backfill as hb
        ts = datetime(2026, 8, 3, 7, 59, 59, tzinfo=timezone.utc)
        assert hb._floor_to_slot(ts) == floor_to_slot(ts, hb.SLOT_SECONDS)
