"""security_requests: the ledger round-trips, and the map reads the way the console needs.

Real Postgres (seeded_db): the SQL is what is under test — the CASE
buckets of the endpoint map, the ordering, the text-cutoff prune.
"""

from __future__ import annotations

import pytest


async def _write(db, acct, **kw):
    base = dict(method="GET", path="/api/vehicles", status=200, account_id=acct.id,
                user_id=1, role="owner", kind="monitored", query=None,
                duration_ms=5, ip="1.2.3.4", ua="ua", request_id="r")
    base.update(kw)
    await db.record_security_request(**base)


@pytest.mark.asyncio
async def test_round_trip_newest_first(seeded_db):
    db, acct = seeded_db["db"], seeded_db["account"]
    await _write(db, acct, path="/api/a", status=200)
    await _write(db, acct, path="/api/b", status=403)
    rows = await db.list_security_requests(account_id=acct.id, limit=10)
    assert [r["path"] for r in rows] == ["/api/b", "/api/a"]
    assert rows[0]["status"] == 403 and rows[0]["kind"] == "monitored"


@pytest.mark.asyncio
async def test_status_filter_gives_the_denials(seeded_db):
    db, acct = seeded_db["db"], seeded_db["account"]
    for st in (200, 401, 403, 429, 500):
        await _write(db, acct, status=st)
    denials = await db.list_security_requests(account_id=acct.id, statuses=(401, 403, 429))
    assert sorted(r["status"] for r in denials) == [401, 403, 429]


@pytest.mark.asyncio
async def test_the_endpoint_map_buckets_held_broke_and_ok(seeded_db):
    """401/403 = held, 500 = a bug they found, 2xx = look here."""
    db, acct = seeded_db["db"], seeded_db["account"]
    await _write(db, acct, method="GET", path="/api/system/accounts", status=403)
    await _write(db, acct, method="GET", path="/api/system/accounts", status=401)
    await _write(db, acct, method="GET", path="/api/reports/export", status=500)
    await _write(db, acct, method="GET", path="/api/reports/export", status=500)
    await _write(db, acct, method="POST", path="/api/billing/update-vehicles", status=200)
    await _write(db, acct, method="POST", path="/api/auth/login", status=429)
    await _write(db, acct, method="POST", path="/api/applications/apply", status=422)

    rows = {(r["method"], r["path"]): r for r in await db.security_endpoint_map(account_id=acct.id)}
    assert rows[("GET", "/api/system/accounts")]["refused"] == 2
    assert rows[("GET", "/api/reports/export")]["broke"] == 2
    assert rows[("POST", "/api/billing/update-vehicles")]["ok"] == 1
    assert rows[("POST", "/api/auth/login")]["throttled"] == 1
    assert rows[("POST", "/api/applications/apply")]["rejected"] == 1
    # the bugs they found sort first — that is what an operator opens the map for
    first = (await db.security_endpoint_map(account_id=acct.id))[0]
    assert first["path"] == "/api/reports/export"


@pytest.mark.asyncio
async def test_count_and_prune(seeded_db):
    db, acct = seeded_db["db"], seeded_db["account"]
    await _write(db, acct, status=403)
    await _write(db, acct, status=200)
    assert await db.count_security_requests(since_hours=1) >= 2
    assert await db.count_security_requests(since_hours=1, statuses=(403,)) >= 1
    # nothing is older than 90 days yet → prune deletes nothing
    assert await db.prune_security_requests(90) == 0
    # everything is older than "minus one day in the future" → prune deletes all
    assert await db.prune_security_requests(-1) >= 2
    assert await db.list_security_requests(account_id=acct.id) == []
