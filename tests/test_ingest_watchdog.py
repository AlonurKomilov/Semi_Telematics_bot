"""The watchdog — the observer the five dead datasets never had.

Two shapes of death, both seen in production this arc:

  * SILENT — the job runs, writes nothing, forever (engine_state's
    column empty for the life of its table; vehicle_fault_log at
    zero rows platform-wide).
  * STALE — the writes flow but the world-time stops moving (the
    frozen odometer that read as fresh for 43 hours, then weeks).

And two shapes of quiet that must NOT alarm: a dataset that is ageless
by nature (cache tables carry no world-time), and an account that
simply has no data yet.
"""

from __future__ import annotations

from datetime import datetime, timezone

import pytest

from capabilities.data_lifecycle.ingest.registry import IngestDataset
from capabilities.data_lifecycle.ingest.watchdog import (
    SILENT_DAYS,
    evaluate_dataset,
)

_NOW = datetime(2026, 8, 2, 12, 0, tzinfo=timezone.utc)


async def _noop(account_id):        # pragma: no cover - never called
    return 0


def _ds(**over):
    base = dict(
        key="vehicles.state", owner="vehicles", job_id="warehouse_vehicle_state",
        capability="vehicle_state", cadence={"interval_min": 1}, run=_noop,
        tables=("vehicle_state",), freshness_sla_min=60, expect_rows=True,
    )
    base.update(over)
    return IngestDataset(**base)


class TestSilent:
    def test_days_of_nothing_breaches(self):
        got = evaluate_dataset(
            _ds(), zero_row_days=SILENT_DAYS,
            newest_source_ts="2026-08-02T11:59:00Z", has_any_rows=True, now=_NOW,
        )
        assert got["silent"]["state"] == "breach"
        assert "vehicles.state" in got["silent"]["message"]

    def test_writing_again_clears(self):
        got = evaluate_dataset(
            _ds(), zero_row_days=0,
            newest_source_ts="2026-08-02T11:59:00Z", has_any_rows=True, now=_NOW,
        )
        assert got["silent"]["state"] == "clear"

    def test_one_quiet_day_only_holds(self):
        # A single empty day is normal for many feeds; two is a pattern.
        got = evaluate_dataset(
            _ds(), zero_row_days=1,
            newest_source_ts="2026-08-02T11:59:00Z", has_any_rows=True, now=_NOW,
        )
        assert got["silent"]["state"] == "hold"

    def test_sparse_feeds_are_never_called_silent(self):
        # Faults exist only when trucks are broken — an honest zero.
        got = evaluate_dataset(
            _ds(key="vehicles.faults", expect_rows=False),
            zero_row_days=99, newest_source_ts=None,
            has_any_rows=False, now=_NOW,
        )
        assert "silent" not in got


class TestStale:
    def test_world_time_past_the_sla_breaches(self):
        got = evaluate_dataset(
            _ds(freshness_sla_min=60), zero_row_days=0,
            newest_source_ts="2026-08-02T09:00:00Z",   # 3 h old
            has_any_rows=True, now=_NOW,
        )
        assert got["stale"]["state"] == "breach"
        assert "3.0 h" in got["stale"]["message"]

    def test_fresh_again_clears_with_hysteresis(self):
        got = evaluate_dataset(
            _ds(freshness_sla_min=60), zero_row_days=0,
            newest_source_ts="2026-08-02T11:40:00Z",   # 20 min ≤ 50% of SLA
            has_any_rows=True, now=_NOW,
        )
        assert got["stale"]["state"] == "clear"

    def test_between_the_bands_holds(self):
        got = evaluate_dataset(
            _ds(freshness_sla_min=60), zero_row_days=0,
            newest_source_ts="2026-08-02T11:15:00Z",   # 45 min
            has_any_rows=True, now=_NOW,
        )
        assert got["stale"]["state"] == "hold"

    def test_ageless_tables_never_go_stale(self):
        # A cache whose provider exposes no world-time is honestly
        # ageless — alarming forever would just teach operators to
        # ignore the watchdog.
        got = evaluate_dataset(
            _ds(key="geofencing.definitions"), zero_row_days=0,
            newest_source_ts=None, has_any_rows=True, now=_NOW,
        )
        assert "stale" not in got

    def test_empty_dataset_is_not_stale(self):
        got = evaluate_dataset(
            _ds(), zero_row_days=0, newest_source_ts=None,
            has_any_rows=False, now=_NOW,
        )
        assert "stale" not in got


@pytest.mark.asyncio
async def test_pass_skips_entirely_when_redis_is_down(monkeypatch):
    """Without shared state there is no dedupe — and a message every
    five minutes is worse than a late one."""
    import infra.cache as cache
    from capabilities.data_lifecycle.ingest import watchdog

    monkeypatch.setattr(cache, "is_available", lambda: False)
    sent = await watchdog.check_and_alert(object(), object())
    assert sent == []


@pytest.mark.asyncio
async def test_breach_speaks_once_then_stays_quiet(monkeypatch):
    from capabilities.data_lifecycle.ingest import watchdog

    import infra.cache as cache
    monkeypatch.setattr(cache, "is_available", lambda: True)

    active: set[str] = set()
    monkeypatch.setattr(watchdog, "_is_active", lambda s: _as_bool(s in active))
    monkeypatch.setattr(watchdog, "_set_active", lambda s: _add(active, s))
    monkeypatch.setattr(watchdog, "_clear_active", lambda s: _discard(active, s))
    monkeypatch.setattr(watchdog, "all_datasets", lambda: (_ds(),))
    monkeypatch.setattr(watchdog, "discover", lambda: None, raising=False)

    async def _facts(db, dataset):
        return {"zero_row_days": SILENT_DAYS,
                "newest_source_ts": None, "has_any_rows": True}
    monkeypatch.setattr(watchdog, "_dataset_facts", _facts)

    seen: list[str] = []

    async def _send(app, text):
        seen.append(text)
    monkeypatch.setattr(watchdog, "_send_to_operators", _send)

    first = await watchdog.check_and_alert(object(), object())
    second = await watchdog.check_and_alert(object(), object())
    assert len(first) == 1 and second == [], "one message per breach, not per pass"
    assert len(seen) == 1


async def _as_bool(v):
    return v


async def _add(store, key):
    store.add(key)


async def _discard(store, key):
    store.discard(key)
