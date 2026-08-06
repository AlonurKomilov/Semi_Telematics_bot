"""Readers fall back on AGE, not just emptiness — Contract 2.

A present-but-ancient warehouse row is worse than an empty table: the
empty case falls back loudly, the stale case served a 43-hour-old
fleet as "current" through the whole 07-27 outage.
"""

from __future__ import annotations

from datetime import datetime, timezone

import pytest

from capabilities.warehouse.telemetry import warehouse_reader as wr


class _FakeTenant:
    def __init__(self, rows):
        self._rows = rows

    async def get_vehicle_state(self, account_id, **kw):
        return self._rows

    async def get_driver_efficiency_window(self, account_id, **kw):
        return self._rows


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
