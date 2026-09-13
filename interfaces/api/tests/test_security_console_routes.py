"""The security console's four reads, driven as requests.

require_system_owner is overridden by identity — the operator gate has
its own tests; here the question is whether the routes exist under
/system/security/*, take the documented parameters, and return the
shapes the page was written against.
"""

from __future__ import annotations

import os

os.environ.setdefault("ENCRYPTION_KEY", "")

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient

pytestmark = pytest.mark.asyncio


@pytest_asyncio.fixture
async def api(pg_db, seeded_db, monkeypatch):
    import infra.platform as plat
    monkeypatch.setattr(plat, "_db", pg_db, raising=False)
    from interfaces.api.app import create_api
    from interfaces.api.deps import require_system_owner
    app = create_api()
    app.dependency_overrides[require_system_owner] = lambda: {"sub": "1", "role": "owner"}
    return app, seeded_db["db"], seeded_db["account"]


async def _patch(app, path, body):
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as c:
        return await c.patch(path, json=body)


async def _get(app, path):
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as c:
        return await c.get(path)


async def _seed(db, acct):
    await db.update_account(acct.id, security="monitored")
    for st, path in ((200, "/api/vehicles"), (403, "/api/system/accounts"),
                     (429, "/api/auth/login"), (500, "/api/reports/export")):
        await db.record_security_request(method="GET", path=path, status=st, account_id=acct.id,
                                         security="monitored", ip="1.2.3.4")
    # a refusal from nobody — the probe's /system/* attempts looked like this
    await db.record_security_request(method="GET", path="/api/system/stats", status=403, account_id=None)


async def test_summary_tiles(api):
    app, db, acct = api
    await _seed(db, acct)
    r = await _get(app, "/api/system/security/summary?hours=24")
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["hours"] == 24
    assert body["refused"] >= 2 and body["throttled"] >= 1 and body["broke"] >= 1
    assert body["monitored_accounts"] >= 1


async def test_monitored_strip(api):
    app, db, acct = api
    await _seed(db, acct)
    r = await _get(app, "/api/system/security/monitored?hours=24")
    assert r.status_code == 200
    row = next(x for x in r.json()["items"] if x["account_id"] == acct.id)
    # The strip selects only monitored accounts, so asserting that would
    # be asserting its own WHERE clause. The useful fact is the OTHER
    # axis: it tells the operator whether the watched account is a
    # customer or one of ours.
    assert row["kind"] == "real"
    assert row["requests"] == 4 and row["refused"] == 1 and row["broke"] == 1
    assert set(row) >= {"account_id", "name", "kind", "created_at", "requests", "refused", "broke", "last_seen"}


async def test_timeline_scopes_by_account_and_denials(api):
    app, db, acct = api
    await _seed(db, acct)
    everything = (await _get(app, f"/api/system/security/requests?account_id={acct.id}&hours=24")).json()["items"]
    assert sorted(x["status"] for x in everything) == [200, 403, 429, 500]
    denied = (await _get(app, f"/api/system/security/requests?account_id={acct.id}&cls=denied&hours=24")).json()["items"]
    assert sorted(x["status"] for x in denied) == [403, 429]
    broke = (await _get(app, f"/api/system/security/requests?account_id={acct.id}&cls=broke&hours=24")).json()["items"]
    assert [x["status"] for x in broke] == [500]
    ok = (await _get(app, f"/api/system/security/requests?account_id={acct.id}&cls=ok&hours=24")).json()["items"]
    assert [x["status"] for x in ok] == [200]
    # the deprecated alias still means "denied"
    alias = (await _get(app, f"/api/system/security/requests?account_id={acct.id}&denials=1&hours=24")).json()["items"]
    assert sorted(x["status"] for x in alias) == [403, 429]
    assert (await _get(app, "/api/system/security/requests?cls=nonsense")).status_code == 422
    anyone = (await _get(app, "/api/system/security/requests?denials=1&hours=24")).json()["items"]
    assert any(x["account_id"] is None and x["path"] == "/api/system/stats" for x in anyone)


async def test_endpoint_map_puts_broke_first(api):
    app, db, acct = api
    await _seed(db, acct)
    items = (await _get(app, f"/api/system/security/map?account_id={acct.id}&hours=24")).json()["items"]
    assert items[0]["path"] == "/api/reports/export" and items[0]["broke"] == 1
    by_path = {x["path"]: x for x in items}
    assert by_path["/api/system/accounts"]["refused"] == 1
    assert by_path["/api/auth/login"]["throttled"] == 1
    assert by_path["/api/vehicles"]["ok"] == 1


async def test_the_gate_is_the_operator_gate(api):
    """Remove the override and the routes must refuse — they are cross-account."""
    app, _db, _acct = api
    from interfaces.api.deps import require_system_owner
    app.dependency_overrides.pop(require_system_owner, None)
    for p in ("summary", "monitored", "requests", "map"):
        assert (await _get(app, f"/api/system/security/{p}")).status_code == 401


async def test_candidates_are_operator_only_and_carry_their_reasons(api):
    """The detector's list is a read like the others — and gated like them."""
    app, db, acct = api
    # a signup burst from one IP: three accounts is calm, four is not
    from adapters.storage import Role
    for i in range(4):
        a = await db.create_account(f"Burst Co {i}")
        u = await db.create_user_with_email(
            email=f"burst{i}@guerrillamailblock.com", password_hash="x",
            account_id=a.id, role=Role.OWNER, display_name="o")
        await db._db.execute(
            "UPDATE users SET is_primary_owner = 1 WHERE id = ?", (u.id,))
        await db._db.commit()
        await db.add_platform_audit(
            "account_created", account_id=a.id, actor="self-serve",
            details=f"name='Burst Co {i}' owner=burst{i}@guerrillamailblock.com ip=203.0.113.77")

    r = await _get(app, "/api/system/security/candidates?hours=24")
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["hours"] == 24 and body["count"] == len(body["items"])
    top = body["items"][0]
    assert {"account_id", "ip", "subject", "name", "kind", "weight", "rules", "signals"} <= set(top)
    assert "signup_burst" in top["rules"]
    assert top["signals"] and "evidence" in top["signals"][0]
    # ranked, heaviest first
    weights = [c["weight"] for c in body["items"]]
    assert weights == sorted(weights, reverse=True)


async def test_the_candidates_route_needs_the_operator_gate(api):
    app, _db, _acct = api
    from interfaces.api.deps import require_system_owner
    app.dependency_overrides.pop(require_system_owner, None)
    assert (await _get(app, "/api/system/security/candidates")).status_code == 401


async def test_rules_endpoint_describes_every_rule(api):
    """The legend on the page is generated from this, so it must cover
    every rule the detector runs — and nothing else."""
    app, _db, _acct = api
    r = await _get(app, "/api/system/security/rules")
    assert r.status_code == 200, r.text
    from capabilities.security.detector import ALL_RULES
    items = r.json()["items"]
    assert {x["id"] for x in items} == {f.__name__.removeprefix("rule_") for f in ALL_RULES}
    assert all(x["label"] and x["means"] and x["severity"] in ("high", "med", "low") for x in items)


async def test_candidates_carry_the_board(api):
    """`new` folds a burst into one row; `watching` is keyed for the
    watching table; `items` stays for anyone reading the flat list."""
    app, db, _acct = api
    from adapters.storage import Role
    ids = []
    for i in range(4):
        a = await db.create_account(f"Board Co {i}")
        u = await db.create_user_with_email(
            email=f"board{i}@guerrillamailblock.com", password_hash="x",
            account_id=a.id, role=Role.OWNER, display_name="o")
        await db._db.execute("UPDATE users SET is_primary_owner = 1 WHERE id = ?", (u.id,))
        await db._db.commit()
        await db.add_platform_audit(
            "account_created", account_id=a.id, actor="self-serve",
            details=f"name='Board Co {i}' owner=board{i}@guerrillamailblock.com ip=203.0.113.88")
        ids.append(a.id)
    await db.update_account(ids[0], security="monitored")

    body = (await _get(app, "/api/system/security/candidates?hours=24")).json()
    assert {"items", "new", "watching", "count", "hours"} <= set(body)
    burst = next(c for c in body["new"] if c.get("group") == "burst" and c["ip"] == "203.0.113.88")
    assert {m["account_id"] for m in burst["members"]} == set(ids[1:]), "the watched one is out of the burst"
    assert any(w["account_id"] == ids[0] for w in body["watching"])
    assert all(c["account_id"] != ids[0] for c in body["new"])


async def test_the_two_axes_have_two_endpoints(api):
    """One call changes what an account IS, the other how it STANDS —
    so a request can never half-say what it means."""
    app, db, acct = api

    r = await _patch(app, f"/api/system/accounts/{acct.id}/security",
                     {"security": "monitored"})
    assert r.status_code == 200, r.text
    fresh = await db.get_account(acct.id)
    assert fresh.security == "monitored"
    assert fresh.kind == "real", "watching a customer must leave them a customer"

    # Each endpoint refuses the other's vocabulary.
    assert (await _patch(app, f"/api/system/accounts/{acct.id}/security",
                         {"security": "real"})).status_code == 422
    assert (await _patch(app, f"/api/system/accounts/{acct.id}/type",
                         {"type": "monitored"})).status_code == 422


async def test_the_list_filters_on_each_axis_and_on_both(api):
    app, db, acct = api
    await db.update_account(acct.id, security="monitored")

    def ids(body):
        return {x["id"] for x in body["items"]}

    watched = (await _get(app, "/api/system/accounts?security=monitored")).json()
    assert acct.id in ids(watched)
    assert (await _get(app, "/api/system/accounts?security=normal")).json()
    assert acct.id not in ids((await _get(app, "/api/system/accounts?security=normal")).json())
    # The combination is the useful one: a customer under observation.
    both = (await _get(app, "/api/system/accounts?type=real&security=monitored")).json()
    assert acct.id in ids(both)
    assert acct.id not in ids((await _get(app, "/api/system/accounts?type=test&security=monitored")).json())


async def test_a_security_change_is_written_to_the_audit_trail(api):
    """Marking someone monitored is a decision about a person."""
    app, db, acct = api
    await _patch(app, f"/api/system/accounts/{acct.id}/security", {"security": "monitored"})
    cur = await db._db.execute(
        "SELECT event, details FROM platform_audit_log "
        "WHERE account_id = ? AND event = 'account_security'", (acct.id,))
    rows = await cur.fetchall()
    assert rows, "no audit row for a security change"
    assert "normal -> monitored" in rows[-1]["details"]


async def test_a_person_can_be_watched_without_marking_their_employer(api):
    """The whole point of the per-user standing: an account is often fine
    while one person inside it is not, and watching the account records
    every request from everyone in it."""
    app, db, acct = api
    owner = (await db.list_account_users(acct.id))[0]

    r = await _patch(app, f"/api/system/users/{owner.id}/security",
                     {"security": "monitored"})
    assert r.status_code == 200, r.text

    assert (await db.get_user_by_id(owner.id)).security == "monitored"
    fresh = await db.get_account(acct.id)
    assert fresh.security == "normal", "their employer must be untouched"
    assert fresh.kind == "real"


async def test_the_user_endpoint_refuses_the_other_vocabulary(api):
    app, db, acct = api
    owner = (await db.list_account_users(acct.id))[0]
    assert (await _patch(app, f"/api/system/users/{owner.id}/security",
                         {"security": "real"})).status_code == 422
    assert (await _patch(app, "/api/system/users/999999/security",
                         {"security": "monitored"})).status_code == 404


async def test_the_user_list_filters_on_the_persons_standing(api):
    app, db, acct = api
    owner = (await db.list_account_users(acct.id))[0]
    await db.update_user(owner.id, security="monitored")

    watched = (await _get(app, "/api/system/users?security=monitored")).json()
    assert owner.id in {u["id"] for u in watched["items"]}
    # ...and the row shows BOTH standings, which is the pair the operator reads
    row = next(u for u in watched["items"] if u["id"] == owner.id)
    assert row["security"] == "monitored"
    assert row["account_security"] == "normal"

    quiet = (await _get(app, "/api/system/users?security=normal")).json()
    assert owner.id not in {u["id"] for u in quiet["items"]}


async def test_a_user_security_change_is_written_to_the_audit_trail(api):
    app, db, acct = api
    owner = (await db.list_account_users(acct.id))[0]
    await _patch(app, f"/api/system/users/{owner.id}/security", {"security": "monitored"})
    cur = await db._db.execute(
        "SELECT details FROM platform_audit_log WHERE event = 'user_security'")
    rows = await cur.fetchall()
    assert rows and f"user {owner.id}: normal -> monitored" in rows[-1]["details"]


async def test_the_board_reports_watched_people_beside_watched_accounts(api):
    """The console's two subjects, from one read. An account is often
    fine while one person inside it is not, and the page must be able to
    show that pair without claiming anything about the company."""
    app, db, acct = api
    owner = (await db.list_account_users(acct.id))[0]
    await db.update_user(owner.id, security="monitored")

    body = (await _get(app, "/api/system/security/candidates?hours=168")).json()
    assert "watching_people" in body, "the page reads this key"
    assert isinstance(body["watching_people"], list)
    # the shape the page joins on, whether or not a rule fired this run
    for person in body["watching_people"]:
        assert {"user_id", "email", "account_id", "account_security",
                "rules", "severity"} <= set(person)


async def test_the_watching_tile_counts_people_separately_from_accounts(api):
    """One number would hide whichever subject it left out: watching a
    person is not watching their company."""
    app, db, acct = api
    owner = (await db.list_account_users(acct.id))[0]

    body = (await _get(app, "/api/system/security/summary?hours=24")).json()
    assert body["monitored_users"] == 0

    await db.update_user(owner.id, security="monitored")
    body = (await _get(app, "/api/system/security/summary?hours=24")).json()
    assert body["monitored_users"] == 1
    assert body["monitored_accounts"] == 0, "their employer was never marked"

    # ...and deactivating them takes them out of the count
    await db.update_user(owner.id, is_active=0)
    body = (await _get(app, "/api/system/security/summary?hours=24")).json()
    assert body["monitored_users"] == 0
