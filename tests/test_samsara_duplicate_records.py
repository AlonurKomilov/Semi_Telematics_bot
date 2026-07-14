"""Duplicate Samsara vehicle records — the detail lookup must prefer the
live record (gateway + freshest GPS), never a re-provisioning relic.

Samsara orgs accumulate multiple records for one physical truck (same
name, often same VIN) when a gateway is swapped or the vehicle is moved
between orgs.  The relic keeps its last GPS fix forever.  The fleet
overview filters relics out via ``_is_active``, but the detail lookup
deliberately searches ALL records — so without an explicit preference it
showed a months-old address while the list/map showed the live truck
(observed in production: truck "110", a 2025 Anaheim relic vs the live
unit on I-65).
"""

from unittest.mock import AsyncMock

import pytest

from adapters.telematics.samsara.client import SamsaraClient


def _client() -> SamsaraClient:
    return SamsaraClient(api_key="test", base_url="https://api.example.com")


def _wire(client: SamsaraClient, vehicles: list[dict], loc_by_id: dict) -> None:
    client.get_vehicles = AsyncMock(return_value=vehicles)
    client._fetch_enrichment_maps = AsyncMock(
        return_value=({}, loc_by_id, {}, {}),
    )


RELIC = {
    "id": "v-relic", "name": "110", "vin": "VIN-SAME",
    # no "gateway" key — record lost its device on re-provisioning
}
LIVE = {
    "id": "v-live", "name": "110", "vin": "VIN-SAME",
    "gateway": {"serial": "G123"},
}
LOCS = {
    "v-relic": {"time": "2025-06-26T20:03:22Z",
                "reverseGeo": {"formattedLocation": "Anaheim, CA"}},
    "v-live": {"time": "2026-07-14T05:33:25Z",
               "reverseGeo": {"formattedLocation": "I 65, Seymour, IN"}},
}


@pytest.mark.asyncio
async def test_detail_prefers_gateway_record_over_relic():
    client = _client()
    # Relic FIRST in the raw list — the old first-match bug returned it.
    _wire(client, [RELIC, LIVE], LOCS)
    result = await client.get_vehicle_detail("110")
    assert result is not None
    assert result["id"] == "v-live"
    assert result["has_gateway"] is True
    assert result["location"]["reverseGeo"]["formattedLocation"] == "I 65, Seymour, IN"


@pytest.mark.asyncio
async def test_detail_prefers_freshest_gps_when_neither_has_gateway():
    stale = {"id": "v-a", "name": "7", "vin": "X"}
    fresher = {"id": "v-b", "name": "7", "vin": "X"}
    locs = {
        "v-a": {"time": "2026-01-01T00:00:00Z"},
        "v-b": {"time": "2026-07-01T00:00:00Z"},
    }
    client = _client()
    _wire(client, [stale, fresher], locs)
    result = await client.get_vehicle_detail("7")
    assert result is not None
    assert result["id"] == "v-b"


@pytest.mark.asyncio
async def test_detail_still_finds_gatewayless_single_record():
    """The deliberate 'search ALL records' behavior stays: a truck whose
    only record has no gateway must still resolve (direct lookups work)."""
    client = _client()
    _wire(client, [RELIC], {"v-relic": LOCS["v-relic"]})
    result = await client.get_vehicle_detail("110")
    assert result is not None
    assert result["id"] == "v-relic"
    assert result["has_gateway"] is False


@pytest.mark.asyncio
async def test_detail_returns_none_for_unknown_name():
    client = _client()
    _wire(client, [RELIC, LIVE], LOCS)
    assert await client.get_vehicle_detail("999") is None
