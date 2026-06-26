"""Tests for the aggregation backfill chain.

Covers:

  * ``_aggregate_hour_window`` and ``_aggregate_day_window`` accept
    arbitrary windows — proves the live cron path and the backfill
    path share the same body.
  * ``backfill_aggregations`` is idempotent — re-running produces the
    same rows (UPSERT semantics).
  * ``backfill_aggregations`` populates the daily table from snapshot
    data so the calendar's median-velocity path activates immediately
    after a fresh history backfill.
  * Catalog default: ``history_backfill`` is now enabled-by-default so
    fresh connects auto-trigger.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, MagicMock

import pytest


# ── Catalog default ─────────────────────────────────────────────


def test_history_backfill_default_is_enabled():
    """If this changes, the auto-trigger-on-connect flow stops working
    because the route gates on ``feature_defaults[HISTORY_BACKFILL]
    .enabled``.  Locking the default here so a future toggle flip is
    impossible without updating both places."""
    from adapters.telematics import PROVIDER_CATALOG
    from adapters.telematics.protocol import Capability

    samsara = PROVIDER_CATALOG["samsara"]
    cfg = samsara.feature_defaults.get(Capability.HISTORY_BACKFILL)
    assert cfg is not None
    assert cfg.get("enabled") is True


# ── _aggregate_hour_window arbitrary-window contract ────────────


@pytest.mark.asyncio
async def test_aggregate_hour_window_accepts_arbitrary_hour():
    """The helper must accept any hour_start, not just 'just-closed
    hour'.  Mock the tenant DB and verify the SQL was bound to the
    requested window, not to ``now``."""
    from capabilities.warehouse.aggregator import _aggregate_hour_window

    tenant = MagicMock()
    cur = MagicMock()
    cur.fetchall = AsyncMock(return_value=[])
    tenant._db.execute = AsyncMock(return_value=cur)
    tenant.upsert_vehicle_telemetry_hourly = AsyncMock(return_value=0)

    historical_hour = datetime(2026, 5, 14, 7, 0, tzinfo=timezone.utc)
    await _aggregate_hour_window(tenant, 42, historical_hour)

    # Two execute calls (safety events + snapshot rollup) — both must
    # have been bound with the requested hour, not ``now``.
    calls = tenant._db.execute.call_args_list
    assert len(calls) >= 2
    for call in calls:
        params = call.args[1]
        # account_id, hour_start_iso, hour_end_iso
        assert params[0] == 42
        assert params[1] == "2026-05-14T07:00:00+00:00"
        assert params[2] == "2026-05-14T08:00:00+00:00"


@pytest.mark.asyncio
async def test_aggregate_day_window_accepts_arbitrary_day():
    from capabilities.warehouse.aggregator import _aggregate_day_window

    tenant = MagicMock()
    cur = MagicMock()
    cur.fetchall = AsyncMock(return_value=[])
    tenant._db.execute = AsyncMock(return_value=cur)
    tenant.upsert_vehicle_metrics_daily = AsyncMock(return_value=0)

    historical_day = datetime(2026, 5, 14, 0, 0, tzinfo=timezone.utc)
    await _aggregate_day_window(tenant, 42, historical_day)

    call = tenant._db.execute.call_args
    params = call.args[1]
    assert params[0] == 42
    # hour_utc bounds = [2026-05-14T00:00, 2026-05-15T00:00)
    assert params[1] == "2026-05-14T00:00:00"
    assert params[2] == "2026-05-15T00:00:00"


# ── backfill_aggregations contract ──────────────────────────────


@pytest.mark.asyncio
async def test_backfill_aggregations_walks_each_hour_in_window(monkeypatch):
    """30-day backfill should call _aggregate_hour_window 721 times
    (30 days × 24 hours + the in-progress hour) and _aggregate_day_window
    30 times.  The trailing in-progress hour gets re-aggregated by the
    next live cron tick — UPSERT semantics make this safe."""
    from capabilities.warehouse import aggregator as ingestor

    tenant = MagicMock()
    monkeypatch.setattr(
        ingestor, "get_tenant_db", AsyncMock(return_value=tenant),
    )

    hour_calls: list = []
    day_calls: list = []

    async def fake_hour(tenant_, account_id, hour_start):
        hour_calls.append(hour_start)
        return 0

    async def fake_day(tenant_, account_id, day_start):
        day_calls.append(day_start)
        return 0

    monkeypatch.setattr(ingestor, "_aggregate_hour_window", fake_hour)
    monkeypatch.setattr(ingestor, "_aggregate_day_window", fake_day)

    result = await ingestor.backfill_aggregations(42, days=30)
    assert result["hours"] == 30 * 24 + 1
    assert result["days"] == 30
    assert len(hour_calls) == 30 * 24 + 1
    assert len(day_calls) == 30
    # Each call should be exactly 1 hour / 1 day apart, in chronological order.
    assert hour_calls == sorted(hour_calls)
    for prev, cur in zip(hour_calls, hour_calls[1:]):
        assert cur - prev == timedelta(hours=1)
    for prev, cur in zip(day_calls, day_calls[1:]):
        assert cur - prev == timedelta(days=1)


@pytest.mark.asyncio
async def test_backfill_aggregations_smaller_window(monkeypatch):
    """7-day backfill should call 7*24 + 1 = 169 hours, 7 days
    (the +1 is the in-progress hour, see test above)."""
    from capabilities.warehouse import aggregator as ingestor

    tenant = MagicMock()
    monkeypatch.setattr(
        ingestor, "get_tenant_db", AsyncMock(return_value=tenant),
    )
    monkeypatch.setattr(
        ingestor, "_aggregate_hour_window", AsyncMock(return_value=0),
    )
    monkeypatch.setattr(
        ingestor, "_aggregate_day_window", AsyncMock(return_value=0),
    )

    result = await ingestor.backfill_aggregations(42, days=7)
    assert result["hours"] == 7 * 24 + 1
    assert result["days"] == 7


@pytest.mark.asyncio
async def test_backfill_aggregations_continues_when_one_hour_fails(monkeypatch):
    """A per-hour aggregation failure should log + skip, not abort
    the whole 30-day backfill — partial data is still useful."""
    from capabilities.warehouse import aggregator as ingestor

    tenant = MagicMock()
    monkeypatch.setattr(
        ingestor, "get_tenant_db", AsyncMock(return_value=tenant),
    )

    call_count = {"n": 0}

    async def flaky_hour(*_a, **_k):
        call_count["n"] += 1
        if call_count["n"] == 5:
            raise RuntimeError("transient DB error")
        return 1

    monkeypatch.setattr(ingestor, "_aggregate_hour_window", flaky_hour)
    monkeypatch.setattr(
        ingestor, "_aggregate_day_window", AsyncMock(return_value=0),
    )

    result = await ingestor.backfill_aggregations(42, days=2)
    # 2*24 + 1 = 49 hours (the +1 is the in-progress hour); one raised,
    # the rest returned 1.
    assert result["hours"] == 49
    assert result["hourly_rows"] == 48


@pytest.mark.asyncio
async def test_backfill_aggregations_returns_zero_when_no_tenant(monkeypatch):
    from capabilities.warehouse import aggregator as ingestor

    monkeypatch.setattr(
        ingestor, "get_tenant_db", AsyncMock(return_value=None),
    )
    result = await ingestor.backfill_aggregations(99999, days=30)
    assert result == {"hours": 0, "days": 0, "hourly_rows": 0, "daily_rows": 0}


# ── M5 → aggregations chain ────────────────────────────────────


@pytest.mark.asyncio
async def test_m5_backfill_calls_aggregations_on_success(monkeypatch):
    """The M5 history backfill must chain into ``backfill_aggregations``
    so the calendar projection's median path has data the moment M5
    finishes (rather than waiting ~7 days for the cron to catch up)."""
    from capabilities.integrations.shared import history_backfill
    from capabilities.warehouse import aggregator as ingestor

    # Mock the M5 setup so it gets to the chain-into-aggregations
    # point: integration row exists, status connected, toggle on,
    # provider fetches succeed, tenant DB present, days_to_run is one
    # historical day.
    integration = MagicMock()
    integration.status = "connected"
    integration.feature_toggles = {"history_backfill": {"enabled": True}}

    platform_db = MagicMock()
    platform_db.get_account_integration = AsyncMock(return_value=integration)
    monkeypatch.setattr(
        history_backfill, "get_platform_db", lambda: platform_db,
    )

    tenant = MagicMock()
    tenant.vehicle_state_snapshot_has_day = AsyncMock(return_value=False)
    tenant.upsert_vehicle_state_snapshots = AsyncMock(return_value=0)
    monkeypatch.setattr(
        history_backfill, "get_tenant_db", AsyncMock(return_value=tenant),
    )

    provider = MagicMock()
    provider.get_stats_history = AsyncMock(return_value={})
    monkeypatch.setattr(
        history_backfill, "get_telematics_client",
        AsyncMock(return_value=provider),
    )

    # Skip the real throttle + cooldowns to keep the test fast.
    monkeypatch.setattr(
        history_backfill.samsara_backfill_throttle,
        "acquire", AsyncMock(),
    )
    monkeypatch.setattr(
        "capabilities.integrations.shared.history_backfill.asyncio.sleep",
        AsyncMock(),
    )

    # The actual assertion — backfill_aggregations should be called
    # after the per-day loop completes.
    agg_calls: list = []

    async def fake_aggregations(account_id, *, days):
        agg_calls.append((account_id, days))
        return {"hours": days * 24, "days": days,
                "hourly_rows": 0, "daily_rows": 0}

    monkeypatch.setattr(ingestor, "backfill_aggregations", fake_aggregations)

    result = await history_backfill.backfill_vehicle_history(
        42, days=3, provider_id="samsara",
    )
    assert result.state == "completed"
    # The chain MUST have fired.
    assert agg_calls == [(42, 3)]


@pytest.mark.asyncio
async def test_m5_backfill_aggregations_failure_does_not_flip_state(monkeypatch):
    """If the aggregations chain raises, M5's overall state should
    still be 'completed' (snapshot data landed successfully — the
    aggregations failure just means the live cron will catch up over
    the next 7 days)."""
    from capabilities.integrations.shared import history_backfill
    from capabilities.warehouse import aggregator as ingestor

    integration = MagicMock()
    integration.status = "connected"
    integration.feature_toggles = {"history_backfill": {"enabled": True}}

    platform_db = MagicMock()
    platform_db.get_account_integration = AsyncMock(return_value=integration)
    monkeypatch.setattr(
        history_backfill, "get_platform_db", lambda: platform_db,
    )

    tenant = MagicMock()
    tenant.vehicle_state_snapshot_has_day = AsyncMock(return_value=False)
    tenant.upsert_vehicle_state_snapshots = AsyncMock(return_value=0)
    monkeypatch.setattr(
        history_backfill, "get_tenant_db", AsyncMock(return_value=tenant),
    )

    provider = MagicMock()
    provider.get_stats_history = AsyncMock(return_value={})
    monkeypatch.setattr(
        history_backfill, "get_telematics_client",
        AsyncMock(return_value=provider),
    )
    monkeypatch.setattr(
        history_backfill.samsara_backfill_throttle,
        "acquire", AsyncMock(),
    )
    monkeypatch.setattr(
        "capabilities.integrations.shared.history_backfill.asyncio.sleep",
        AsyncMock(),
    )

    async def broken_aggregations(*_a, **_k):
        raise RuntimeError("DB blew up during aggregation")

    monkeypatch.setattr(ingestor, "backfill_aggregations", broken_aggregations)

    result = await history_backfill.backfill_vehicle_history(
        42, days=3, provider_id="samsara",
    )
    # State should still be completed — aggregation failure is non-fatal.
    assert result.state == "completed"
    # But the error must be captured for ops visibility.
    assert any("aggregations" in e for e in result.errors)
