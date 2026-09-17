"""Hours of service gets a history tier — the KEEP half it never had.

The live table is overwritten every five minutes; this cascade copies
each reading into ``warehouse.driver_hos_minute`` so "who was driving at
14:00 yesterday" has an answer.  These tests hold the declaration to the
hub's contract and the snapshot to what it may and may not copy.
"""

from __future__ import annotations

import pytest


def _stages():
    from capabilities.data_lifecycle.rollups import discover
    from capabilities.data_lifecycle.rollups.registry import all_cascades, all_stages
    discover()
    return {c.name for c in all_cascades()}, {s.job_id: s for s in all_stages()}


def test_the_cascade_is_declared_on_the_ingest_cadence():
    """Five minutes, wall-aligned, UTC — matching the ELD poll, so a
    reading is sampled once rather than four times."""
    names, stages = _stages()
    assert "driver_hos" in names
    st = stages["eld_hos_snapshot"]
    assert st.cadence == {"cron": "*/5 * * * *", "tz": "UTC"}
    assert (st.stream, st.grain) == ("driver.hos", "minute")
    assert callable(st.run)


def test_the_feature_is_on_every_roster():
    """Ingest alone was never enough: a stream with no BUILD roster
    entry never runs and no KEEP entry never prunes."""
    from capabilities.data_lifecycle.rollups import _CONTRIBUTORS as build
    from capabilities.data_lifecycle.retention import _CONTRIBUTORS as keep
    from capabilities.data_lifecycle.ingest import _CONTRIBUTORS as acquire
    for roster in (build, keep, acquire):
        assert "features.eld.lifecycle" in roster


def test_retention_is_declared_for_the_tier():
    from capabilities.data_lifecycle.retention import discover
    from capabilities.data_lifecycle.retention.registry import resolve
    discover()
    resolved = {r.target.key: r for r in resolve("tenant")}
    assert "driver.hos_minute" in resolved
    # the resolved window is the max across declared needs; ours is 30
    assert resolved["driver.hos_minute"].keep_days >= 30


@pytest.mark.asyncio
async def test_a_snapshot_copies_what_the_feed_wrote_and_labels_the_slot(monkeypatch):
    import features.eld.warehouse.snapshot as snap

    class _T:
        def __init__(self):
            self.written = None
        async def list_driver_hos_live_raw(self, account_id):
            return [{"provider_id": "orient_eld", "provider_driver_id": "17", "user_id": 84,
                     "duty_status": "driving", "source_ts": "2026-09-17T04:31:00+00:00",
                     "drive_remaining_seconds": None}]
        async def upsert_driver_hos_minutes(self, account_id, rows):
            self.written = rows; return len(rows)

    t = _T()
    async def _tenant(acct): return t
    monkeypatch.setattr(snap, "get_tenant_db", _tenant)

    n = await snap.snapshot_driver_hos(7)

    assert n == 1
    row = t.written[0]
    assert row["captured_at"].endswith(":00+00:00") or row["captured_at"].endswith(":00")
    # the slot label is OURS; the provider's own time rides unchanged
    assert row["source_ts"] == "2026-09-17T04:31:00+00:00"
    assert row["captured_at"] != row["source_ts"]
    # an unreported clock stays unknown — never a zero in history
    assert row["drive_remaining_seconds"] is None


@pytest.mark.asyncio
async def test_no_live_rows_means_no_history_rows(monkeypatch):
    import features.eld.warehouse.snapshot as snap
    class _T:
        async def list_driver_hos_live_raw(self, account_id): return []
        async def upsert_driver_hos_minutes(self, *a): raise AssertionError("must not write")
    async def _tenant(acct): return _T()
    monkeypatch.setattr(snap, "get_tenant_db", _tenant)
    assert await snap.snapshot_driver_hos(7) == 0
