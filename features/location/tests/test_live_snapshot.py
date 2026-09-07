"""One provider fan-out per account for ``/map/vehicles/live``, however
many tabs are polling it.

Three surfaces poll the endpoint every five seconds, and each poll used
to run its own fan-out (one provider call per company) with nothing
shared between them.  The load on the provider scaled with open tabs.
Now every poller reads one snapshot per account — this worker's copy
first, a neighbouring worker's via Redis second, and only then one
fan-out that concurrent callers wait for rather than repeat.

The snapshot is the raw account-wide rows; the router narrows per
request, so nothing a member may not see is ever cached under a key
another member could read.
"""

from __future__ import annotations

import asyncio
import json
import time

import pytest

import features.location.service as svc
from features.location import router as loc

ROWS = {
    "PTG": [{"id": "1", "name": "101",
             "location": {"latitude": 41.0, "longitude": -87.0,
                          "speedMilesPerHour": 55, "heading": 90, "time": "t1"}}],
    "OSY": [{"id": "2", "name": "202",
             "location": {"latitude": 42.0, "longitude": -88.0,
                          "speedMilesPerHour": 0, "heading": None, "time": "t2"}}],
}


def _rows():
    return {k: list(v) for k, v in ROWS.items()}


@pytest.fixture(autouse=True)
def _clean():
    svc.reset_live_snapshot_for_tests()
    yield
    svc.reset_live_snapshot_for_tests()


@pytest.fixture
def fanouts(monkeypatch):
    """Stand in for the provider fan-out; records every time it runs."""
    calls: list[int] = []

    async def _fetch(account_id: int):
        calls.append(account_id)
        await asyncio.sleep(0.01)   # long enough for callers to pile up
        return _rows()

    monkeypatch.setattr(svc, "_fetch_positions", _fetch)
    return calls


@pytest.fixture(autouse=True)
def shared_store(monkeypatch):
    """A fake of the Redis pair the snapshot uses, installed for EVERY
    test here.  infra.cache re-probes a real server on first use and
    this box has one, so without the fake these tests would publish
    snapshots into a live Redis and then read them back — which is
    exactly how the stale-serve test first "failed": its own fresh
    copy came back from the neighbour it did not know it had.

    Values round-trip through JSON the way Redis returns them, so a
    test that ages the local copy is not silently ageing the shared
    one through a shared reference."""
    store: dict[str, tuple[object, int]] = {}

    async def _get(key):
        hit = store.get(key)
        return json.loads(json.dumps(hit[0])) if hit else None

    async def _set(key, value, ttl=120):
        store[key] = (json.loads(json.dumps(value, default=str)), ttl)

    monkeypatch.setattr(svc.rcache, "get", _get)
    monkeypatch.setattr(svc.rcache, "cache_set", _set)
    return store


def _age_every_copy(store, account_id: int, seconds: float) -> None:
    """Move both homes of a snapshot into the past."""
    local = svc._live_local.get(account_id)
    if local:
        local["fetched_at"] -= seconds
    hit = store.get(svc._LIVE_KEY.format(account_id=account_id))
    if hit:
        hit[0]["fetched_at"] -= seconds


# ── the snapshot ─────────────────────────────────────────────────────


async def test_concurrent_pollers_share_one_fan_out(fanouts):
    outs = await asyncio.gather(*(svc.live_snapshot(7) for _ in range(6)))
    assert fanouts == [7]
    assert all(o == ROWS for o in outs)


async def test_a_snapshot_past_its_ttl_is_fetched_again(shared_store, fanouts):
    await svc.live_snapshot(7)
    _age_every_copy(shared_store, 7, svc.LIVE_TTL_S + 1)
    await svc.live_snapshot(7)
    assert fanouts == [7, 7]


async def test_accounts_never_share_a_snapshot(fanouts):
    await svc.live_snapshot(7)
    await svc.live_snapshot(8)
    assert fanouts == [7, 8]


async def test_redis_being_down_is_the_per_worker_cache(monkeypatch, fanouts):
    # What infra.cache does when the server is unreachable: both halves
    # are no-ops.  The snapshot must then be the per-worker cache, not
    # an error and not a fan-out per poll.
    async def _none(key):
        return None

    async def _drop(key, value, ttl=120):
        return None

    monkeypatch.setattr(svc.rcache, "get", _none)
    monkeypatch.setattr(svc.rcache, "cache_set", _drop)
    await asyncio.gather(*(svc.live_snapshot(7) for _ in range(4)))
    await svc.live_snapshot(7)
    assert fanouts == [7]


async def test_a_neighbouring_workers_snapshot_is_used_before_the_provider(shared_store, fanouts):
    theirs = {"fetched_at": time.time(), "companies": {"PTG": [{"id": "9"}]}}
    shared_store[svc._LIVE_KEY.format(account_id=7)] = (theirs, 30)
    assert await svc.live_snapshot(7) == theirs["companies"]
    assert fanouts == []
    # ...and it is now this worker's copy too.
    assert svc._live_local[7] == theirs


async def test_a_fresh_fan_out_is_published_for_the_other_workers(shared_store, fanouts):
    await svc.live_snapshot(7)
    value, ttl = shared_store[svc._LIVE_KEY.format(account_id=7)]
    assert value["companies"] == ROWS
    # Published for as long as it may be served stale; freshness is
    # judged by fetched_at inside the value, not by the key's expiry.
    assert ttl == int(svc.LIVE_STALE_OK_S)


async def test_a_stale_shared_snapshot_is_not_fresh(shared_store, fanouts):
    old = {"fetched_at": time.time() - svc.LIVE_TTL_S - 1, "companies": {"PTG": []}}
    shared_store[svc._LIVE_KEY.format(account_id=7)] = (old, 30)
    assert await svc.live_snapshot(7) == ROWS
    assert fanouts == [7]


async def test_a_value_that_is_not_a_snapshot_is_ignored(shared_store, fanouts):
    shared_store[svc._LIVE_KEY.format(account_id=7)] = ({"fetched_at": "soon"}, 30)
    assert await svc.live_snapshot(7) == ROWS
    assert fanouts == [7]


async def _boom(account_id):
    raise RuntimeError("provider down")


async def test_a_failed_fan_out_serves_the_recent_snapshot_then_gives_up(monkeypatch, shared_store, fanouts):
    await svc.live_snapshot(7)
    monkeypatch.setattr(svc, "_fetch_positions", _boom)

    _age_every_copy(shared_store, 7, svc.LIVE_TTL_S + 1)
    assert await svc.live_snapshot(7) == ROWS      # stale, but recent
    _age_every_copy(shared_store, 7, svc.LIVE_STALE_OK_S)
    with pytest.raises(RuntimeError):
        await svc.live_snapshot(7)


async def test_a_neighbours_recent_snapshot_covers_a_failed_fan_out_too(monkeypatch, shared_store):
    # This worker holds nothing; the one next door fetched ten seconds
    # ago; the provider is down.  Ten seconds old beats a blank map.
    monkeypatch.setattr(svc, "_fetch_positions", _boom)
    theirs = {"fetched_at": time.time() - 10, "companies": {"PTG": [{"id": "9"}]}}
    shared_store[svc._LIVE_KEY.format(account_id=7)] = (theirs, 30)
    assert await svc.live_snapshot(7) == theirs["companies"]


# ── the router on top of it ──────────────────────────────────────────

_USER = {"account_id": 7, "role": "dispatch", "user_id": 1}


@pytest.fixture
def router_on_snapshot(monkeypatch):
    async def _snap(account_id):
        return _rows()

    async def _wide(user, feature):
        return "all"

    monkeypatch.setattr(loc, "live_snapshot", _snap)
    monkeypatch.setattr(loc, "member_unit_scope", _wide)


async def _unrestricted(user):
    return []


async def _ptg_only(user):
    return ["ptg"]


async def test_positions_are_shaped_from_the_snapshot(monkeypatch, router_on_snapshot):
    monkeypatch.setattr(loc, "get_user_company_codes", _unrestricted)
    out = await loc.map_vehicles_live(company=None, user=_USER)
    assert out["positions"] == {
        "1": {"lat": 41.0, "lng": -87.0, "speed_mph": 55.0, "heading": 90, "updated_at": "t1"},
        "2": {"lat": 42.0, "lng": -88.0, "speed_mph": 0.0, "heading": None, "updated_at": "t2"},
    }


async def test_a_company_filter_is_drawn_from_the_snapshot(monkeypatch, router_on_snapshot):
    monkeypatch.setattr(loc, "get_user_company_codes", _unrestricted)
    out = await loc.map_vehicles_live(company="osy", user=_USER)
    assert set(out["positions"]) == {"2"}


async def test_a_member_restricted_to_one_company_sees_only_it(monkeypatch, router_on_snapshot):
    # Polling without naming a company used to hand this member every
    # company's positions; the list endpoint never did.
    monkeypatch.setattr(loc, "get_user_company_codes", _ptg_only)
    out = await loc.map_vehicles_live(company=None, user=_USER)
    assert set(out["positions"]) == {"1"}


async def test_a_restricted_member_cannot_name_another_company(monkeypatch, router_on_snapshot):
    from fastapi import HTTPException
    monkeypatch.setattr(loc, "get_user_company_codes", _ptg_only)
    with pytest.raises(HTTPException) as e:
        await loc.map_vehicles_live(company="OSY", user=_USER)
    assert e.value.status_code == 403


async def test_the_workers_copy_forgets_accounts_nobody_watches(shared_store, fanouts):
    await svc.live_snapshot(7)
    _age_every_copy(shared_store, 7, svc.LIVE_STALE_OK_S + 1)
    await svc.live_snapshot(8)
    assert set(svc._live_local) == {8}


class _Clock:
    """``time`` as the service sees it, with a dial: the tests above age
    a snapshot by editing its timestamp, which cannot age a copy the
    service already holds in a local variable — only the clock can."""
    def __init__(self):
        self.offset = 0.0
        self.monotonic = time.monotonic

    def time(self):
        return time.time() + self.offset


async def test_a_slow_failure_is_measured_from_when_it_ended(monkeypatch, fanouts):
    # The snapshot was 25 s old when the fetch began; the fetch itself
    # took ten.  Judged from the start it would have been served;
    # judged from the end it is past the bound and refused.
    clock = _Clock()
    monkeypatch.setattr(svc, "time", clock)
    await svc.live_snapshot(7)
    clock.offset = svc.LIVE_STALE_OK_S - 5

    async def _slow_boom(account_id):
        clock.offset += 10
        raise RuntimeError("provider timed out")
    monkeypatch.setattr(svc, "_fetch_positions", _slow_boom)
    with pytest.raises(RuntimeError):
        await svc.live_snapshot(7)
