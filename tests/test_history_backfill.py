"""Smoke tests for the M5 vehicle history backfill.

Covers:

  * The 5-minute slot floor (``_floor_to_slot``).
  * The resampler (``_resample_to_snapshot_rows``) — single sample,
    multiple samples in one slot collapse to last-value, GPS fans
    out into lat/lon/speed_mph columns.
  * The day-cursor storage helpers (``vehicle_state_snapshot_has_day``,
    ``vehicle_state_snapshot_day_summary``).
  * The backfill capability end-to-end with a fake provider:
    - happy path inserts rows
    - resume skips already-populated days
    - toggle off short-circuits with a clear reason
    - integration not connected short-circuits
    - per-batch failure doesn't abort the whole run

Tests stub the throttle's sleep and ``asyncio.sleep`` so the suite
runs in seconds rather than the ~45-minute realistic backfill wall-
clock.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, MagicMock

import pytest

from adapters.storage.account_integrations import AccountIntegration
from adapters.telematics.protocol import Capability
from capabilities.integrations.shared.history_backfill import (
    _floor_to_slot,
    _merge_batch,
    _resample_to_snapshot_rows,
    backfill_vehicle_history,
)


# ── Slot floor ───────────────────────────────────────────────────


def test_floor_to_slot_snaps_down_to_5min_boundary():
    t = datetime(2026, 5, 15, 14, 23, 45, tzinfo=timezone.utc)
    assert _floor_to_slot(t) == "2026-05-15T14:20:00+00:00"


def test_floor_to_slot_exact_slot_preserved():
    t = datetime(2026, 5, 15, 14, 25, 0, tzinfo=timezone.utc)
    assert _floor_to_slot(t) == "2026-05-15T14:25:00+00:00"


def test_floor_to_slot_seconds_microseconds_zeroed():
    t = datetime(2026, 5, 15, 14, 27, 59, 999_999, tzinfo=timezone.utc)
    assert _floor_to_slot(t) == "2026-05-15T14:25:00+00:00"


# ── Resampler ────────────────────────────────────────────────────


def test_resample_single_sample_produces_one_row():
    samples = {
        "vid-1": {
            "obdOdometerMeters": [
                {"time": "2026-05-15T14:23:00Z", "value": 1609344.0},  # 1000 mi
            ],
        },
    }
    rows = _resample_to_snapshot_rows(samples)
    assert len(rows) == 1
    assert rows[0]["vehicle_id"] == "vid-1"
    assert rows[0]["captured_at"] == "2026-05-15T14:20:00+00:00"
    # Conversion meters → miles
    assert rows[0]["odometer_mi"] == pytest.approx(1000.0, rel=1e-6)


def test_resample_multiple_samples_in_one_slot_collapse_to_last():
    samples = {
        "vid-1": {
            "fuelPercents": [
                {"time": "2026-05-15T14:21:00Z", "value": 50},
                {"time": "2026-05-15T14:23:00Z", "value": 45},
                {"time": "2026-05-15T14:24:30Z", "value": 40},
            ],
        },
    }
    rows = _resample_to_snapshot_rows(samples)
    assert len(rows) == 1
    # Last-write-wins within the slot.
    assert rows[0]["fuel_pct"] == 40.0


def test_resample_engine_state_maps_on_to_moving():
    samples = {
        "vid-1": {
            "engineStates": [
                {"time": "2026-05-15T14:23:00Z", "value": "On"},
            ],
        },
    }
    rows = _resample_to_snapshot_rows(samples)
    assert rows[0]["engine_state"] == "moving"


def test_resample_gps_fans_out_into_three_columns():
    samples = {
        "vid-1": {
            "gps": [
                {
                    "time": "2026-05-15T14:23:00Z",
                    "value": {
                        "latitude": 39.25,
                        "longitude": -84.45,
                        "speedMilesPerHour": 62.5,
                    },
                },
            ],
        },
    }
    rows = _resample_to_snapshot_rows(samples)
    assert rows[0]["lat"] == 39.25
    assert rows[0]["lon"] == -84.45
    assert rows[0]["speed_mph"] == 62.5


def test_resample_merges_distinct_stat_types_into_one_row():
    samples = {
        "vid-1": {
            "obdOdometerMeters": [
                {"time": "2026-05-15T14:23:00Z", "value": 1609344.0},
            ],
            "fuelPercents": [
                {"time": "2026-05-15T14:23:00Z", "value": 50},
            ],
        },
    }
    rows = _resample_to_snapshot_rows(samples)
    assert len(rows) == 1
    assert rows[0]["odometer_mi"] == pytest.approx(1000.0, rel=1e-6)
    assert rows[0]["fuel_pct"] == 50.0


def test_resample_ignores_unknown_stat_types():
    samples = {
        "vid-1": {
            "someFutureMetric": [
                {"time": "2026-05-15T14:23:00Z", "value": 1.0},
            ],
        },
    }
    rows = _resample_to_snapshot_rows(samples)
    assert rows == []


def test_merge_batch_concatenates_per_vehicle_lists():
    acc: dict[str, dict] = {}
    _merge_batch(acc, {
        "vid-1": {
            "obdOdometerMeters": [{"time": "t1", "value": 1}],
            "name": "alpha",
        },
    })
    _merge_batch(acc, {
        "vid-1": {
            "fuelPercents": [{"time": "t2", "value": 50}],
        },
    })
    assert acc["vid-1"]["obdOdometerMeters"] == [{"time": "t1", "value": 1}]
    assert acc["vid-1"]["fuelPercents"] == [{"time": "t2", "value": 50}]
    assert acc["vid-1"]["name"] == "alpha"


# ── Storage helpers ──────────────────────────────────────────────


@pytest.mark.asyncio
async def test_snapshot_has_day_false_when_empty(seeded_db):
    db = seeded_db["db"]
    account = seeded_db["account"]
    today = datetime.now(timezone.utc).date()
    assert await db.vehicle_state_snapshot_has_day(account.id, today) is False


@pytest.mark.asyncio
async def test_snapshot_has_day_true_after_insert(seeded_db):
    db = seeded_db["db"]
    account = seeded_db["account"]

    day = datetime.now(timezone.utc).date() - timedelta(days=2)
    captured = datetime.combine(
        day, datetime.min.time(), tzinfo=timezone.utc,
    ).replace(hour=14, minute=20).isoformat()

    await db.upsert_vehicle_state_snapshots(account.id, [{
        "vehicle_id": "vid-1",
        "captured_at": captured,
        "odometer_mi": 1000.0,
    }])
    assert await db.vehicle_state_snapshot_has_day(account.id, day) is True
    # Adjacent day still empty.
    assert await db.vehicle_state_snapshot_has_day(
        account.id, day - timedelta(days=1),
    ) is False


@pytest.mark.asyncio
async def test_snapshot_day_summary_reports_per_day_counts(seeded_db):
    db = seeded_db["db"]
    account = seeded_db["account"]

    today = datetime.now(timezone.utc).date()
    day_with_data = today - timedelta(days=3)
    captured = datetime.combine(
        day_with_data, datetime.min.time(), tzinfo=timezone.utc,
    ).replace(hour=10).isoformat()
    await db.upsert_vehicle_state_snapshots(account.id, [{
        "vehicle_id": "vid-1",
        "captured_at": captured,
        "odometer_mi": 1000.0,
    }])

    coverage = await db.vehicle_state_snapshot_day_summary(
        account.id, days_back=7,
    )
    assert len(coverage) == 7
    by_day = {row["day_utc"]: row["row_count"] for row in coverage}
    assert by_day[day_with_data.isoformat()] >= 1
    assert by_day[(today - timedelta(days=4)).isoformat()] == 0


# ── End-to-end capability ────────────────────────────────────────


def _make_integration(
    account_id: int,
    *,
    status: str = "connected",
    backfill_enabled: bool = True,
) -> AccountIntegration:
    return AccountIntegration(
        account_id=account_id,
        provider_id="samsara",
        status=status,
        connected_at="2026-01-01T00:00:00+00:00",
        credentials={},
        feature_toggles={
            Capability.HISTORY_BACKFILL: {"enabled": backfill_enabled},
        },
        cadence_overrides={},
        last_health_at="",
        last_health_error="",
        last_backfill_at="",
        created_by=0,
        created_at="2026-01-01T00:00:00+00:00",
        updated_at="2026-01-01T00:00:00+00:00",
    )


def _fake_provider_response(vid: str = "vid-1", days_ago: int = 1) -> dict:
    """One sample per stat type at a fixed timestamp for the given day."""
    t = (datetime.now(timezone.utc) - timedelta(days=days_ago)).replace(
        hour=14, minute=23, second=0, microsecond=0,
    )
    iso = t.isoformat()
    return {
        vid: {
            "obdOdometerMeters": [{"time": iso, "value": 1609344.0}],
            "obdEngineSeconds": [{"time": iso, "value": 3600.0}],
            "fuelPercents":     [{"time": iso, "value": 75}],
            "defLevelMilliPercent": [{"time": iso, "value": 50000}],
            "batteryMilliVolts":    [{"time": iso, "value": 13_500}],
            "engineCoolantTemperatureMilliC": [
                {"time": iso, "value": 91_000},
            ],
            "engineOilPressureKPa": [{"time": iso, "value": 270}],
            "engineLoadPercent":    [{"time": iso, "value": 50}],
            "engineRpm": [{"time": iso, "value": 1500}],
            "engineStates": [{"time": iso, "value": "On"}],
            "gps": [
                {
                    "time": iso,
                    "value": {
                        "latitude": 39.25,
                        "longitude": -84.45,
                        "speedMilesPerHour": 60,
                    },
                },
            ],
        },
    }


@pytest.fixture
def _no_sleep(monkeypatch):
    """Replace asyncio.sleep + throttle.acquire with no-ops so the
    backfill runs in milliseconds instead of minutes."""
    async def _instant(*_args, **_kwargs):
        return None
    monkeypatch.setattr(
        "capabilities.integrations.shared.history_backfill.asyncio.sleep", _instant,
    )
    monkeypatch.setattr(
        "capabilities.integrations.shared.history_backfill.samsara_backfill_throttle.acquire",
        AsyncMock(),
    )
    yield


@pytest.fixture
def _stub_platform_services(monkeypatch, seeded_db):
    """Wire the capability's platform / tenant / provider lookups to
    the seeded test DB + a mocked Samsara provider."""
    db = seeded_db["db"]
    account = seeded_db["account"]

    # Insert a connected samsara integration row with backfill enabled.
    async def _setup():
        await db.upsert_account_integration(
            account.id, "samsara",
            credentials={"api_token": "test"},
            feature_toggles={
                Capability.HISTORY_BACKFILL: {"enabled": True},
            },
        )

    import asyncio as _a
    _a.get_event_loop_policy().new_event_loop().run_until_complete(_setup())

    monkeypatch.setattr(
        "capabilities.integrations.shared.history_backfill.get_platform_db",
        lambda: db,
    )
    monkeypatch.setattr(
        "capabilities.integrations.shared.history_backfill.get_tenant_db",
        AsyncMock(return_value=db),
    )
    yield account


@pytest.mark.asyncio
async def test_backfill_skips_when_no_integration(monkeypatch, _no_sleep):
    """Account without a samsara integration row → state='skipped'."""
    db = MagicMock()
    db.get_account_integration = AsyncMock(return_value=None)
    monkeypatch.setattr(
        "capabilities.integrations.shared.history_backfill.get_platform_db",
        lambda: db,
    )
    result = await backfill_vehicle_history(99999, days=3)
    assert result.state == "skipped"
    assert "no samsara integration" in result.reason.lower()


@pytest.mark.asyncio
async def test_backfill_skips_when_status_not_connected(
    monkeypatch, _no_sleep,
):
    db = MagicMock()
    db.get_account_integration = AsyncMock(
        return_value=_make_integration(42, status="disabled"),
    )
    monkeypatch.setattr(
        "capabilities.integrations.shared.history_backfill.get_platform_db",
        lambda: db,
    )
    result = await backfill_vehicle_history(42, days=3)
    assert result.state == "skipped"
    assert "disabled" in result.reason


@pytest.mark.asyncio
async def test_backfill_skips_when_toggle_disabled(monkeypatch, _no_sleep):
    db = MagicMock()
    db.get_account_integration = AsyncMock(
        return_value=_make_integration(42, backfill_enabled=False),
    )
    monkeypatch.setattr(
        "capabilities.integrations.shared.history_backfill.get_platform_db",
        lambda: db,
    )
    result = await backfill_vehicle_history(42, days=3)
    assert result.state == "skipped"
    assert "toggle is disabled" in result.reason


@pytest.mark.asyncio
async def test_backfill_completes_and_inserts_rows(monkeypatch, _no_sleep):
    """Happy path — provider returns samples for each day, capability
    resamples + persists.  No real DB; we mock the tenant calls."""
    integration = _make_integration(42)
    db = MagicMock()
    db.get_account_integration = AsyncMock(return_value=integration)
    monkeypatch.setattr(
        "capabilities.integrations.shared.history_backfill.get_platform_db",
        lambda: db,
    )

    tenant = MagicMock()
    tenant.vehicle_state_snapshot_has_day = AsyncMock(return_value=False)
    tenant.upsert_vehicle_state_snapshots = AsyncMock(return_value=3)
    monkeypatch.setattr(
        "capabilities.integrations.shared.history_backfill.get_tenant_db",
        AsyncMock(return_value=tenant),
    )

    provider = MagicMock()
    provider.get_stats_history = AsyncMock(
        return_value=_fake_provider_response(),
    )
    monkeypatch.setattr(
        "capabilities.integrations.shared.history_backfill.get_telematics_client",
        AsyncMock(return_value=provider),
    )

    result = await backfill_vehicle_history(42, days=3)
    assert result.state == "completed"
    assert result.days_done == 3
    assert result.rows_inserted > 0
    # 3 days × 3 batches per day = 9 API calls.
    assert result.api_calls == 9


@pytest.mark.asyncio
async def test_backfill_skips_already_populated_days(monkeypatch, _no_sleep):
    """Resume: days the snapshot table already covers are skipped."""
    integration = _make_integration(42)
    db = MagicMock()
    db.get_account_integration = AsyncMock(return_value=integration)
    monkeypatch.setattr(
        "capabilities.integrations.shared.history_backfill.get_platform_db",
        lambda: db,
    )

    # Even days already covered, odd days empty.
    today = datetime.now(timezone.utc).date()

    async def _has_day(account_id, day):
        offset = (today - day).days
        return offset % 2 == 0

    tenant = MagicMock()
    tenant.vehicle_state_snapshot_has_day = _has_day
    tenant.upsert_vehicle_state_snapshots = AsyncMock(return_value=3)
    monkeypatch.setattr(
        "capabilities.integrations.shared.history_backfill.get_tenant_db",
        AsyncMock(return_value=tenant),
    )

    provider = MagicMock()
    provider.get_stats_history = AsyncMock(
        return_value=_fake_provider_response(),
    )
    monkeypatch.setattr(
        "capabilities.integrations.shared.history_backfill.get_telematics_client",
        AsyncMock(return_value=provider),
    )

    result = await backfill_vehicle_history(42, days=10)
    assert result.state == "completed"
    # 10 days requested; ~5 already populated, ~5 backfilled.
    assert result.days_skipped_already_present + result.days_done == 10
    assert result.days_done >= 4


@pytest.mark.asyncio
async def test_backfill_continues_after_per_day_batch_failure(
    monkeypatch, _no_sleep,
):
    """A transient Samsara error on one batch logs + skips, doesn't
    abort the whole 3-day backfill."""
    integration = _make_integration(42)
    db = MagicMock()
    db.get_account_integration = AsyncMock(return_value=integration)
    monkeypatch.setattr(
        "capabilities.integrations.shared.history_backfill.get_platform_db",
        lambda: db,
    )

    tenant = MagicMock()
    tenant.vehicle_state_snapshot_has_day = AsyncMock(return_value=False)
    tenant.upsert_vehicle_state_snapshots = AsyncMock(return_value=2)
    monkeypatch.setattr(
        "capabilities.integrations.shared.history_backfill.get_tenant_db",
        AsyncMock(return_value=tenant),
    )

    # First call raises, all subsequent succeed.
    call_count = {"n": 0}
    fake_response = _fake_provider_response()

    async def _flaky(*_a, **_k):
        call_count["n"] += 1
        if call_count["n"] == 1:
            raise RuntimeError("boom — transient upstream error")
        return fake_response

    provider = MagicMock()
    provider.get_stats_history = _flaky
    monkeypatch.setattr(
        "capabilities.integrations.shared.history_backfill.get_telematics_client",
        AsyncMock(return_value=provider),
    )

    result = await backfill_vehicle_history(42, days=3)
    assert result.state == "completed"
    assert result.days_done == 3
    assert len(result.errors) >= 1
    assert any("boom" in e for e in result.errors)
