"""Readers fall back on AGE, not just emptiness — Contract 2.

A present-but-ancient warehouse row is worse than an empty table: the
empty case falls back loudly, the stale case served a 43-hour-old
fleet as "current" through the whole 07-27 outage.
"""

from __future__ import annotations

from datetime import datetime, timezone

import pytest

from features.vehicles.warehouse import readers as wr


class _FakeTenant:
    """``rows`` is what the getter returns; ``newest`` is the table's newest
    source_ts (what get_feed_freshness would report); ``last_ran`` is the
    ledger's last_ran_at for the dataset.  Each reader is gated by exactly
    one of the last two, and the tests below say which."""

    def __init__(self, rows, *, newest=None, last_ran=None, total=1):
        self._rows = rows
        self._newest = newest
        self._last_ran = last_ran
        self._total = total

    async def get_vehicle_state(self, account_id, **kw):
        return self._rows

    async def get_driver_efficiency_window(self, account_id, **kw):
        return self._rows

    async def get_vehicle_health_live(self, account_id, **kw):
        return self._rows

    async def get_weather_live(self, account_id, **kw):
        return self._rows

    async def get_vehicles_with_faults_warehouse(self, account_id, **kw):
        return self._rows, self._total, {}

    async def get_vehicle_fault_live_by_name(self, account_id, name):
        return {"fault_codes": {"j1939": {"diagnosticTroubleCodes": [{"spn": 1}]}}}

    async def get_feed_freshness(self, account_id, specs):
        return {t: {"count": len(self._rows), "last_at": self._newest} for t, _ in specs}

    async def last_ingest_run_at(self, account_id, dataset_key):
        return self._last_ran


async def _live():
    return [{"from": "live"}]


@pytest.fixture(autouse=True)
def _flag_on(monkeypatch):
    monkeypatch.setattr(wr, "_enabled", lambda: True)


def _tenant(monkeypatch, rows):
    async def fake(acct):
        return _FakeTenant(rows)
    monkeypatch.setattr(wr, "get_tenant_db", fake)


@pytest.mark.asyncio
async def test_stale_vehicle_state_falls_back_to_live(monkeypatch):
    _tenant(monkeypatch, [
        {"vehicle_id": "v1", "source_ts": "2026-07-27T00:00:00Z"},
    ])
    out = await wr.get_current_vehicles(1, samsara_fallback=_live)
    assert out == [{"from": "live"}]


@pytest.mark.asyncio
async def test_fresh_vehicle_state_serves_from_warehouse(monkeypatch):
    _tenant(monkeypatch, [
        {"vehicle_id": "v1", "vehicle_name": "401",
         "source_ts": datetime.now(timezone.utc).isoformat()},
    ])
    out = await wr.get_current_vehicles(1, samsara_fallback=_live)
    assert len(out) == 1 and out[0].get("from") != "live"


@pytest.mark.asyncio
async def test_stalled_efficiency_feed_falls_back(monkeypatch):
    _tenant(monkeypatch, [{"driver_id": "d1", "day": "2026-07-01"}])
    out = await wr.get_driver_efficiency_window(1, samsara_fallback=_live)
    assert out == [{"from": "live"}]


# ── The four readers that used to fall back on EMPTY only ──────────
#
# Health and weather rows are the vendor's JSON and carry no stamp, so
# their gate reads the table's newest source_ts; faults can be empty
# for a healthy fleet, so their gate reads the ingest ledger.

_OLD = "2026-07-27T00:00:00Z"
_NOW = lambda: datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S")  # noqa: E731 — ledger format, naive UTC


def _tenant_kw(monkeypatch, rows, **kw):
    async def fake(acct):
        return _FakeTenant(rows, **kw)
    monkeypatch.setattr(wr, "get_tenant_db", fake)


@pytest.mark.asyncio
async def test_stale_health_rows_fall_back_even_though_present(monkeypatch):
    _tenant_kw(monkeypatch, [{"battery_v": 14.1}], newest=_OLD)
    out = await wr.get_vehicle_health(1, samsara_fallback=_live)
    assert out == [{"from": "live"}]


@pytest.mark.asyncio
async def test_fresh_health_rows_serve_from_warehouse(monkeypatch):
    _tenant_kw(monkeypatch, [{"battery_v": 14.1}], newest=_NOW())
    out = await wr.get_vehicle_health(1, samsara_fallback=_live)
    assert out == [{"battery_v": 14.1}]


@pytest.mark.asyncio
async def test_a_failed_freshness_read_is_stale_not_fresh(monkeypatch):
    """Unknown age cannot be proven fresh — Contract 2."""
    class _Broken(_FakeTenant):
        async def get_feed_freshness(self, *a, **k):
            raise RuntimeError("db hiccup")
    async def fake(acct):
        return _Broken([{"battery_v": 14.1}])
    monkeypatch.setattr(wr, "get_tenant_db", fake)
    out = await wr.get_vehicle_health(1, samsara_fallback=_live)
    assert out == [{"from": "live"}]


@pytest.mark.asyncio
async def test_stale_weather_falls_back(monkeypatch):
    _tenant_kw(monkeypatch, [{"temp_f": 71}], newest=_OLD)
    assert await wr.get_fleet_weather(1, samsara_fallback=_live) == [{"from": "live"}]


@pytest.mark.asyncio
async def test_fresh_weather_serves(monkeypatch):
    _tenant_kw(monkeypatch, [{"temp_f": 71}], newest=_NOW())
    assert await wr.get_fleet_weather(1, samsara_fallback=_live) == [{"temp_f": 71}]


@pytest.mark.asyncio
async def test_a_clean_fleet_with_a_running_faults_job_is_served(monkeypatch):
    """Zero fault rows + the job ran a minute ago = healthy, not cold."""
    _tenant_kw(monkeypatch, [], total=40, last_ran=_NOW())
    faulted, total, _ = await wr.get_vehicles_with_faults(1, samsara_fallback=_live)
    assert faulted == [] and total == 40


@pytest.mark.asyncio
async def test_a_faults_job_that_stopped_falls_back(monkeypatch):
    _tenant_kw(monkeypatch, [{"name": "101"}], total=40, last_ran=_OLD)
    out = await wr.get_vehicles_with_faults(1, samsara_fallback=_live)
    assert out == [{"from": "live"}]


@pytest.mark.asyncio
async def test_a_faults_job_that_never_ran_falls_back(monkeypatch):
    _tenant_kw(monkeypatch, [{"name": "101"}], total=40, last_ran=None)
    out = await wr.get_vehicles_with_faults(1, samsara_fallback=_live)
    assert out == [{"from": "live"}]


@pytest.mark.asyncio
async def test_stale_fault_detail_is_withheld_not_served(monkeypatch):
    """None sends the route to count-only placeholders — never old DTC
    names presented as current."""
    _tenant_kw(monkeypatch, [], last_ran=_OLD)
    assert await wr.get_vehicle_fault_live(1, "101") is None


@pytest.mark.asyncio
async def test_fresh_fault_detail_is_served(monkeypatch):
    _tenant_kw(monkeypatch, [], last_ran=_NOW())
    snap = await wr.get_vehicle_fault_live(1, "101")
    assert snap and snap["fault_codes"]["j1939"]["diagnosticTroubleCodes"]
