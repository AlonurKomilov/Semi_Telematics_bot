"""The single-truck lookup is cached, like the twelve calls beside it.

``MultiCompanyClient.get_vehicle_detail`` resolves ONE truck by pulling
the entire ``/fleet/vehicles`` list plus the enrichment maps, and it was
one of the only read paths on that client with no cache — while
get_vehicles_overview, get_drivers, get_vehicle_health and nine others
all had one.

Every Vehicle detail / usage / timeline view hit it uncached against a
5s total HTTP budget.  On 07-29 it timed out, and the circuit breaker
opens on five failures in sixty seconds — so one slow single-truck page
took every Samsara-backed endpoint down with it for thirty seconds.  The
breaker was behaving correctly; it was being fed.
"""

from unittest.mock import AsyncMock

import pytest

from adapters.telematics.samsara.client import MultiCompanyClient


def _client(detail):
    c = AsyncMock()
    c.get_vehicle_detail = AsyncMock(return_value=detail)
    c._org_id = "org-1"
    return c


_DEFAULT = object()


def _mc(detail=_DEFAULT):
    if detail is _DEFAULT:
        detail = {
            "id": "v1", "name": "224", "has_gateway": True,
            "location": {"time": "2026-07-29T12:00:00Z"},
        }
    inner = _client(detail)
    mc = MultiCompanyClient({"ACME": inner}, account_id=1)
    return mc, inner


@pytest.mark.asyncio
async def test_second_lookup_is_served_from_cache():
    mc, inner = _mc()
    first = await mc.get_vehicle_detail("224")
    second = await mc.get_vehicle_detail("224")

    assert first == second
    assert inner.get_vehicle_detail.await_count == 1, (
        "the second view must not re-pull the fleet from Samsara"
    )


@pytest.mark.asyncio
async def test_a_different_truck_is_a_different_key():
    """A per-truck cache that collided would serve one truck's detail
    for another — worse than no cache at all."""
    mc, inner = _mc()
    await mc.get_vehicle_detail("224")
    await mc.get_vehicle_detail("104")
    assert inner.get_vehicle_detail.await_count == 2


@pytest.mark.asyncio
async def test_the_key_is_case_and_space_insensitive():
    mc, inner = _mc()
    await mc.get_vehicle_detail("224")
    await mc.get_vehicle_detail("  224  ")
    assert inner.get_vehicle_detail.await_count == 1


@pytest.mark.asyncio
async def test_company_scoped_lookups_do_not_share_a_key():
    """``company=None`` searches every org and ``company="ACME"`` one —
    different questions, so they must not answer each other."""
    mc, inner = _mc()
    await mc.get_vehicle_detail("224")
    await mc.get_vehicle_detail("224", company="ACME")
    assert inner.get_vehicle_detail.await_count == 2


@pytest.mark.asyncio
async def test_an_empty_result_is_still_returned_correctly():
    mc, _ = _mc(detail=None)
    assert await mc.get_vehicle_detail("nope") == []
