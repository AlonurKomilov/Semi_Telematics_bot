"""A slow telematics provider must not delete the truck from the page.

The registry is the SSOT and the provider ENRICHES it, so a truck the
registry knows must still resolve when the provider cannot answer.
``vehicle_detail`` already fell back to the registry when the provider
answered with NOTHING (trailers and manual vehicles live only there),
but not when it RAISED — a 5s timeout, an HTTP error, or
SamsaraUnavailable once the breaker opens escaped as a 500.

That is what GET /api/vehicles/224/usage did on 2026-07-29, and the
request behind it got SamsaraUnavailable because five failures in sixty
seconds is the breaker's threshold. ``timeline`` and ``usage`` had no
fallback at all, so both cases 500'd there.

Six cases: three endpoints x {provider empty, provider raising}.
"""

from unittest.mock import AsyncMock, patch

import aiohttp
import pytest

from adapters.telematics.samsara.circuit_breaker import SamsaraUnavailable
import features.vehicles.router as vr


_REG_ROW = {"name": "224", "id": "reg-224", "_org": "ACME"}


def _registry_vehicle():
    v = AsyncMock()
    v.unit_number = "224"
    return v


def _tenant():
    t = AsyncMock()
    t.list_vehicles = AsyncMock(return_value=[_registry_vehicle()])
    return t


@pytest.fixture
def _wired():
    """Provider stubbed per-test; registry + permission filters pass through."""
    with patch.object(vr, "_get_tenant_db", AsyncMock(return_value=_tenant())), \
         patch.object(vr._wh_reader, "merge_registry_with_live",
                      lambda regs, live: [dict(_REG_ROW)] if regs else []), \
         patch.object(vr, "filter_by_allowed_companies", lambda rows, allowed: rows), \
         patch.object(vr, "filter_by_assigned_trucks", AsyncMock(side_effect=lambda rows, user: rows)):
        yield


_USER = {"account_id": 1, "role": "owner"}

_FAILURES = [
    pytest.param(TimeoutError(), id="provider-timeout"),
    pytest.param(aiohttp.ClientError("boom"), id="provider-http-error"),
    pytest.param(SamsaraUnavailable("breaker open"), id="breaker-open"),
]


@pytest.mark.asyncio
@pytest.mark.parametrize("exc", _FAILURES)
async def test_resolver_falls_back_when_the_provider_RAISES(_wired, exc):
    with patch.object(vr, "_svc_vehicle_detail", AsyncMock(side_effect=exc)):
        got = await vr._resolve_vehicle("224", None, _USER, [])
    assert got, "a provider failure must not delete a registry-known truck"
    assert got[0]["name"] == "224"


@pytest.mark.asyncio
async def test_resolver_falls_back_when_the_provider_is_EMPTY(_wired):
    """The pre-existing case — trailers and manual vehicles."""
    with patch.object(vr, "_svc_vehicle_detail", AsyncMock(return_value=[])):
        got = await vr._resolve_vehicle("224", None, _USER, [])
    assert got and got[0]["name"] == "224"


@pytest.mark.asyncio
async def test_live_data_still_wins_when_the_provider_answers(_wired):
    """The fallback must not shadow a healthy provider response."""
    live = [{"name": "224", "id": "live-224", "location": {"time": "now"}}]
    with patch.object(vr, "_svc_vehicle_detail", AsyncMock(return_value=live)):
        got = await vr._resolve_vehicle("224", None, _USER, [])
    assert got[0]["id"] == "live-224", "registry row must not replace live data"


@pytest.mark.asyncio
async def test_a_real_bug_is_NOT_swallowed(_wired):
    """The catch is narrow on purpose: a KeyError in our own code must
    still surface, not hide behind a silently degraded page."""
    with patch.object(vr, "_svc_vehicle_detail", AsyncMock(side_effect=KeyError("oops"))):
        with pytest.raises(KeyError):
            await vr._resolve_vehicle("224", None, _USER, [])


@pytest.mark.asyncio
async def test_unknown_truck_still_resolves_to_nothing(_wired):
    """Degrading gracefully must not invent a vehicle."""
    with patch.object(vr, "_svc_vehicle_detail", AsyncMock(side_effect=TimeoutError())):
        got = await vr._resolve_vehicle("does-not-exist", None, _USER, [])
    assert got == []


@pytest.mark.asyncio
@pytest.mark.parametrize("exc", _FAILURES)
async def test_all_three_endpoints_share_the_resolver(_wired, exc):
    """timeline and usage had no fallback at all — the regression this
    guards is one of them drifting back to a bare _svc_vehicle_detail."""
    import inspect
    for fn in (vr.vehicle_detail, vr.vehicle_timeline, vr.vehicle_usage):
        src = inspect.getsource(fn)
        assert "_resolve_vehicle(" in src, f"{fn.__name__} bypasses the resolver"
        assert "_svc_vehicle_detail(" not in src, f"{fn.__name__} calls the provider directly"
