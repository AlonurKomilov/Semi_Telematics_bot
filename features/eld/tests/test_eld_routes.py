"""The HOS endpoints, gated and scoped.

Duty status is FMCSA-regulated information about a named person, so
these check the two ways a read like this leaks: a role that should
not have the feature at all, and a member narrowed to some trucks
reaching past that narrowing by asking for one driver by id.
"""

from __future__ import annotations

import os

os.environ.setdefault("ENCRYPTION_KEY", "test-key-32-chars-min-aaaaaaaaaaaaaaaaaaaa")
os.environ.setdefault("JWT_SECRET", "test-secret-32-chars-or-more-please-aaaaaaaaaaaa")
os.environ.setdefault("OBJECT_STORE_BACKEND", "disk")
os.environ.setdefault("OBJECT_STORE_ROOT", "/tmp/eld_route_test_store")

from datetime import datetime, timedelta, timezone

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient

from adapters.storage import Role


@pytest_asyncio.fixture
async def api(pg_db):
    import infra.platform as cp
    cp._db = pg_db
    from interfaces.api.app import create_api
    return create_api(), pg_db


def _headers(db_user, acct, role: str):
    from interfaces.api.auth import create_jwt
    token = create_jwt(
        db_user.telegram_id or 0, acct.id, role, user_id=db_user.id,
    )
    return {"Authorization": f"Bearer {token}"}


def _now(minutes_ago: float = 1) -> str:
    return (datetime.now(timezone.utc)
            - timedelta(minutes=minutes_ago)).isoformat(timespec="seconds")


async def _client(app):
    return AsyncClient(transport=ASGITransport(app=app), base_url="http://t")


@pytest_asyncio.fixture
async def world(api):
    """One account, two drivers on two trucks, both reporting."""
    app, db = api
    acct = await db.create_account("HOS Carrier")
    boss = await db.create_user(
        account_id=acct.id, telegram_id=880001, role=Role.OWNER,
        display_name="Owner")
    d1 = await db.create_user(
        account_id=acct.id, telegram_id=880002, role=Role.DRIVER,
        display_name="Jane Ruiz", truck_num="231")
    d2 = await db.create_user(
        account_id=acct.id, telegram_id=880003, role=Role.DRIVER,
        display_name="Sam Lee", truck_num="104")
    await db.upsert_driver_hos(acct.id, "samsara", [
        {"provider_driver_id": "p1", "duty_status": "driving",
         "drive_remaining_seconds": 3600, "shift_remaining_seconds": 7200,
         "cycle_remaining_seconds": 180000, "break_in_seconds": 25200,
         "last_status_change": _now(120), "driver_name": "Jane R",
         "source_ts": _now(1)},
        {"provider_driver_id": "p2", "duty_status": "off_duty",
         "drive_remaining_seconds": 0, "shift_remaining_seconds": 0,
         "cycle_remaining_seconds": 252000, "break_in_seconds": None,
         "last_status_change": _now(600), "driver_name": "Sam L",
         "source_ts": _now(1)},
    ], links={"p1": d1.id, "p2": d2.id})
    return app, db, acct, boss, d1, d2


# ── The gate ──────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_a_role_without_the_verb_is_refused(world):
    app, db, acct, _boss, _d1, _d2 = world
    hr = await db.create_user(
        account_id=acct.id, telegram_id=880010, role=Role.HR,
        display_name="HR")
    async with await _client(app) as c:
        r = await c.get("/api/v1/eld/hours", headers=_headers(hr, acct, "hr"))
    assert r.status_code == 403, (
        "duty hours are an ops question, not a personnel-file one"
    )


@pytest.mark.asyncio
async def test_an_ops_role_is_allowed(world):
    app, _db, acct, boss, _d1, _d2 = world
    async with await _client(app) as c:
        r = await c.get("/api/v1/eld/hours",
                        headers=_headers(boss, acct, "owner"))
    assert r.status_code == 200
    body = r.json()
    assert body["connected"] is True
    assert {d["driver"] for d in body["drivers"]} == {"Jane Ruiz", "Sam Lee"}


@pytest.mark.asyncio
async def test_there_is_no_write_route(world):
    """Nothing here is ours to edit — the certified device is the
    record.  A POST must not quietly land on some other router."""
    app, _db, acct, boss, _d1, _d2 = world
    async with await _client(app) as c:
        r = await c.post("/api/v1/eld/hours",
                         headers=_headers(boss, acct, "owner"), json={})
    assert r.status_code in (404, 405)


# ── What the answer carries ───────────────────────────────────────

@pytest.mark.asyncio
async def test_every_reading_carries_its_age_and_its_source(world):
    app, _db, acct, boss, _d1, _d2 = world
    async with await _client(app) as c:
        body = (await c.get("/api/v1/eld/hours",
                            headers=_headers(boss, acct, "owner"))).json()
    for d in body["drivers"]:
        assert d["as_of"]
        assert d["age_minutes"] is not None
        assert d["stale"] is False
        assert d["source"] == "samsara"
    assert "system of record" in body["record_of"]


@pytest.mark.asyncio
async def test_an_unreported_clock_is_null_not_zero(world):
    """Sam's break clock was never reported.  Zero would mean he is due
    to stop right now; null means we do not know."""
    app, _db, acct, boss, _d1, _d2 = world
    async with await _client(app) as c:
        body = (await c.get("/api/v1/eld/hours",
                            headers=_headers(boss, acct, "owner"))).json()
    sam = next(d for d in body["drivers"] if d["driver"] == "Sam Lee")
    assert sam["break_in"] is None
    assert sam["shift_remaining"] == {"seconds": 0, "hours": 0.0}
    assert sam["cycle_remaining"]["hours"] == 70.0


@pytest.mark.asyncio
async def test_an_account_with_no_eld_says_so_rather_than_looking_clear(api):
    app, db = api
    acct = await db.create_account("No ELD Carrier")
    boss = await db.create_user(
        account_id=acct.id, telegram_id=880020, role=Role.OWNER,
        display_name="Owner")
    async with await _client(app) as c:
        body = (await c.get("/api/v1/eld/hours",
                            headers=_headers(boss, acct, "owner"))).json()
    assert body["connected"] is False
    assert body["drivers"] == []


# ── The per-id hole ───────────────────────────────────────────────

@pytest.mark.asyncio
async def test_one_driver_can_be_read_by_id(world):
    app, _db, acct, boss, d1, _d2 = world
    async with await _client(app) as c:
        r = await c.get(f"/api/v1/eld/hours/{d1.id}",
                        headers=_headers(boss, acct, "owner"))
    assert r.status_code == 200
    assert [d["driver"] for d in r.json()["drivers"]] == ["Jane Ruiz"]


@pytest.mark.asyncio
async def test_an_unknown_driver_is_404_not_an_empty_list(world):
    app, _db, acct, boss, _d1, _d2 = world
    async with await _client(app) as c:
        r = await c.get("/api/v1/eld/hours/999999",
                        headers=_headers(boss, acct, "owner"))
    assert r.status_code == 404


@pytest.mark.asyncio
async def test_another_accounts_driver_is_not_readable(world, api):
    """The account wall, asked the way a per-id route invites: by
    guessing an id that exists somewhere else."""
    app, db, acct, boss, _d1, _d2 = world
    other = await db.create_account("Rival Carrier")
    theirs = await db.create_user(
        account_id=other.id, telegram_id=880030, role=Role.DRIVER,
        display_name="Their Driver", truck_num="999")
    await db.upsert_driver_hos(other.id, "samsara", [
        {"provider_driver_id": "x1", "duty_status": "driving",
         "source_ts": _now(1)},
    ], links={"x1": theirs.id})

    async with await _client(app) as c:
        r = await c.get(f"/api/v1/eld/hours/{theirs.id}",
                        headers=_headers(boss, acct, "owner"))
    assert r.status_code == 404
