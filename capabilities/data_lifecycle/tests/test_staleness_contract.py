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


class TestOneNumber:
    """"How old is too old" is declared once, on the dataset, and read
    everywhere — a private copy in a consumer is a second policy.

    Before ``sla_minutes`` existed, vehicle state carried four answers
    (registry 15, reader 30, ELD 15 for its own dataset that said 30,
    dashboard dot 60).  These tests hold the consumers to the registry
    and refuse a numeric staleness literal reappearing in them.
    """

    CONSUMERS = {
        "features/vehicles/warehouse/readers.py": ("_rows_are_stale",),
        "features/eld/service.py": ("STALE_AFTER_MINUTES",),
    }

    def test_the_helper_reads_the_registry(self):
        from capabilities.data_lifecycle.ingest import discover, get_dataset
        from capabilities.data_lifecycle.staleness import sla_minutes
        discover()
        for key in ("vehicles.state", "drivers.efficiency", "eld.driver_hos"):
            assert sla_minutes(key) == float(get_dataset(key).freshness_sla_min), key

    def test_an_unknown_dataset_raises_rather_than_defaulting(self):
        """A typo must not become "never stale"."""
        from capabilities.data_lifecycle.staleness import sla_minutes
        with pytest.raises(KeyError):
            sla_minutes("vehicles.stat")

    def test_the_eld_feature_agrees_with_its_dataset(self):
        from capabilities.data_lifecycle.staleness import sla_minutes
        from features.eld.service import STALE_AFTER_MINUTES
        assert STALE_AFTER_MINUTES == sla_minutes("eld.driver_hos")

    def test_no_consumer_carries_a_numeric_staleness_literal(self):
        """AST, not grep: a comment recording the OLD constant by name is
        allowed to exist; a call or assignment that hands a NUMBER to a
        staleness gate is not."""
        import ast
        from tests._repo import REPO
        offenders: list[str] = []
        for rel, names in self.CONSUMERS.items():
            tree = ast.parse((REPO / rel).read_text(encoding="utf-8"))
            for node in ast.walk(tree):
                # _rows_are_stale(rows, <literal or arithmetic of literals>, ...)
                if isinstance(node, ast.Call) and getattr(node.func, "id", "") in names:
                    if len(node.args) > 1 and _is_numeric(node.args[1]):
                        offenders.append(f"{rel}:{node.lineno} literal SLA in {node.func.id}()")
                # STALE_AFTER_MINUTES = <number>
                if isinstance(node, ast.Assign):
                    for t in node.targets:
                        if getattr(t, "id", "") in names and _is_numeric(node.value):
                            offenders.append(f"{rel}:{node.lineno} {t.id} = literal")
        assert not offenders, "\n".join(offenders)


def _is_numeric(node) -> bool:
    """A number, or arithmetic made only of numbers (``2 * 24 * 60.0``)."""
    import ast
    if isinstance(node, ast.Constant):
        return isinstance(node.value, (int, float)) and not isinstance(node.value, bool)
    if isinstance(node, ast.BinOp):
        return _is_numeric(node.left) and _is_numeric(node.right)
    if isinstance(node, ast.UnaryOp):
        return _is_numeric(node.operand)
    return False
