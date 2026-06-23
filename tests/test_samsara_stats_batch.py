"""Slice A — the per-minute overview folds its stats into one call.

`_fetch_enrichment_maps` must issue a SINGLE multi-type
`/fleet/vehicles/stats` request (faults + fuel + DEF) instead of separate
per-type calls, and split the merged response back into the same per-vehicle
maps the old per-type calls produced.
"""

from __future__ import annotations

import os

os.environ.setdefault("ENCRYPTION_KEY", "")

import pytest

from adapters.telematics.samsara.client import SamsaraClient


@pytest.mark.asyncio
async def test_fetch_enrichment_maps_batches_stats_into_one_call():
    client = SamsaraClient.__new__(SamsaraClient)  # bypass __init__/network

    calls: list[str] = []

    async def fake_safe_stats(types: str) -> dict:
        calls.append(types)
        return {"data": [
            {
                "id": "v1",
                "faultCodes": {"j1939": {"checkEngineLights": {"protect": True}}},
                "fuelPercents": {"value": 80},
                "defLevelMilliPercent": {"value": 55000, "time": "t1"},
            },
            {"id": "v2", "faultCodes": {}},  # no fuel / no DEF sensor
        ]}

    async def fake_locations() -> list[dict]:
        return [{"id": "v1", "location": {"latitude": 1.0}}]

    client._safe_stats = fake_safe_stats   # type: ignore[assignment]
    client.get_locations = fake_locations  # type: ignore[assignment]

    faults, locs, fuel, deff = await client._fetch_enrichment_maps()

    # ONE batched stats call, all three types folded in.
    assert calls == ["faultCodes,fuelPercents,defLevelMilliPercent"]

    # Split back out identically to the old per-type maps.
    assert faults["v1"] == {"j1939": {"checkEngineLights": {"protect": True}}}
    assert faults["v2"] == {}
    assert fuel["v1"] == {"value": 80}
    assert "v2" not in fuel or fuel["v2"] == {}
    assert deff["v1"] == {"value": 55.0, "time": "t1"}  # milli-percent / 1000
    assert "v2" not in deff                              # no DEF sensor → absent
    assert locs["v1"] == {"latitude": 1.0}
