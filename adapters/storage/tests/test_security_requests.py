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


@pytest.mark.asyncio
async def test_monitored_summary_lists_a_watched_account_even_at_zero(seeded_db):
    """Watching started is a fact the operator must SEE, not infer."""
    db, acct = seeded_db["db"], seeded_db["account"]
    await db.update_account(acct.id, kind="monitored")

    rows = {r["account_id"]: r for r in await db.security_monitored_summary(since_hours=24)}
    assert acct.id in rows
    assert rows[acct.id]["requests"] == 0
    assert rows[acct.id]["refused"] == 0
    assert rows[acct.id]["broke"] == 0
    assert rows[acct.id]["last_seen"] is None
    assert rows[acct.id]["kind"] == "monitored"


@pytest.mark.asyncio
async def test_monitored_summary_counts_the_window_and_only_monitored_accounts(seeded_db):
    db, acct = seeded_db["db"], seeded_db["account"]
    await _write(db, acct, status=200)
    await _write(db, acct, status=403)
    await _write(db, acct, status=500)

    # a real account with ledger rows must not appear
    assert await db.security_monitored_summary(since_hours=24) == [] or all(
        r["account_id"] != acct.id for r in await db.security_monitored_summary(since_hours=24))

    await db.update_account(acct.id, kind="monitored")
    rows = {r["account_id"]: r for r in await db.security_monitored_summary(since_hours=24)}
    assert rows[acct.id]["requests"] == 3
    assert rows[acct.id]["refused"] == 1
    assert rows[acct.id]["broke"] == 1
    assert rows[acct.id]["last_seen"] is not None


@pytest.mark.asyncio
async def test_status_class_is_a_range_not_a_list(seeded_db):
    db, acct = seeded_db["db"], seeded_db["account"]
    for st in (200, 204, 401, 403, 404, 429, 500, 502):
        await _write(db, acct, status=st)
    cls = lambda c: sorted(r["status"] for r in db_rows)  # noqa: E731 — local helper
    db_rows = await db.list_security_requests(account_id=acct.id, status_class="denied")
    assert cls("denied") == [401, 403, 429]
    db_rows = await db.list_security_requests(account_id=acct.id, status_class="broke")
    assert cls("broke") == [500, 502]
    db_rows = await db.list_security_requests(account_id=acct.id, status_class="ok")
    assert cls("ok") == [200, 204]
    db_rows = await db.list_security_requests(account_id=acct.id, status_class="all")
    assert len(cls("all")) == 8
    with pytest.raises(ValueError):
        await db.list_security_requests(account_id=acct.id, status_class="hostile")


@pytest.mark.asyncio
async def test_rows_carry_the_acting_users_name(seeded_db):
    """The operator reads a PERSON's timeline; a person has a name."""
    db, acct = seeded_db["db"], seeded_db["account"]
    owner = seeded_db.get("owner") or seeded_db.get("user")
    uid = owner.id if owner is not None else 1
    await _write(db, acct, user_id=uid, status=403)
    await _write(db, acct, user_id=999999, status=403)       # a user that no longer exists
    rows = {r["user_id"]: r for r in await db.list_security_requests(account_id=acct.id)}
    assert "user_name" in rows[uid]
    if owner is not None:
        assert rows[uid]["user_name"] == owner.display_name
    assert rows[999999]["user_name"] is None                  # LEFT JOIN: the row survives
