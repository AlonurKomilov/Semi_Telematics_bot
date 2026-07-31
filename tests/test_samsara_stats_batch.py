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
                "engineStates": {"value": "On", "time": "t1"},
            },
            {"id": "v2", "faultCodes": {}},  # no fuel / no DEF sensor
        ]}

    async def fake_locations() -> list[dict]:
        return [{"id": "v1", "location": {"latitude": 1.0}}]

    client._safe_stats = fake_safe_stats   # type: ignore[assignment]
    client.get_locations = fake_locations  # type: ignore[assignment]

    faults, locs, fuel, deff, engine = await client._fetch_enrichment_maps()

    # ONE batched stats call, all four types folded in.  Engine state
    # rides this request rather than paying for its own every minute —
    # four types is the provider's per-call cap, so it fits exactly.
    assert calls == [
        "faultCodes,fuelPercents,defLevelMilliPercent,engineStates",
    ]

    # Split back out identically to the old per-type maps.
    assert faults["v1"] == {"j1939": {"checkEngineLights": {"protect": True}}}
    assert faults["v2"] == {}
    assert fuel["v1"] == {"value": 80}
    assert "v2" not in fuel or fuel["v2"] == {}
    assert deff["v1"] == {"value": 55.0, "time": "t1"}  # milli-percent / 1000
    assert "v2" not in deff                              # no DEF sensor → absent
    assert locs["v1"] == {"latitude": 1.0}
    # The provider's own word is carried through unresolved — what it
    # means depends on road speed, which this layer does not know.
    assert engine["v1"] == "On"
    assert "v2" not in engine                            # no engine feed


@pytest.mark.asyncio
async def test_low_fuel_reuses_cached_overview_not_per_company_fanout():
    """get_low_fuel_vehicles derives from the (cached) merged overview —
    one overview read, no per-company fan-out — and filters identically."""
    from adapters.telematics.samsara.client import MultiCompanyClient

    mc = MultiCompanyClient.__new__(MultiCompanyClient)  # bypass __init__

    overview_calls: list = []

    async def fake_overview(company=None):
        overview_calls.append(company)
        return [
            {"id": "v1", "name": "T1", "_org": "CFT", "fuel": {"value": 10, "time": "t1"}},
            {"id": "v2", "name": "T2", "_org": "CFT", "fuel": {"value": 50, "time": "t2"}},
            {"id": "v3", "name": "T3", "_org": "G1",  "fuel": {"value": 25, "time": "t3"}},
            {"id": "v4", "name": "T4", "_org": "G1"},  # no fuel data → skipped
        ]

    async def fake_cache_get(_key):
        return None

    async def fake_cache_set(_key, _result, ttl=None):
        return None

    mc.get_vehicles_overview = fake_overview      # type: ignore[assignment]
    mc._cache_get = fake_cache_get                # type: ignore[assignment]
    mc._cache_set = fake_cache_set                # type: ignore[assignment]

    low = await mc.get_low_fuel_vehicles(threshold=30)

    # Reused the overview once — no independent per-company fetch.
    assert overview_calls == [None]
    # Only ≤30%, sorted ascending by fuel, with _fuel_pct/_fuel_time set.
    assert [v["id"] for v in low] == ["v1", "v3"]
    assert low[0]["_fuel_pct"] == 10 and low[0]["_fuel_time"] == "t1"
    assert low[1]["_fuel_pct"] == 25 and low[1]["_org"] == "G1"
