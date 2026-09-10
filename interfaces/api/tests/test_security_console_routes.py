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


async def _get(app, path):
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as c:
        return await c.get(path)


async def _seed(db, acct):
    await db.update_account(acct.id, kind="monitored")
    for st, path in ((200, "/api/vehicles"), (403, "/api/system/accounts"),
                     (429, "/api/auth/login"), (500, "/api/reports/export")):
        await db.record_security_request(method="GET", path=path, status=st, account_id=acct.id,
                                         kind="monitored", ip="1.2.3.4")
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
    assert row["kind"] == "monitored"
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
    await db.update_account(ids[0], kind="monitored")

    body = (await _get(app, "/api/system/security/candidates?hours=24")).json()
    assert {"items", "new", "watching", "count", "hours"} <= set(body)
    burst = next(c for c in body["new"] if c.get("group") == "burst" and c["ip"] == "203.0.113.88")
    assert {m["account_id"] for m in burst["members"]} == set(ids[1:]), "the watched one is out of the burst"
    assert any(w["account_id"] == ids[0] for w in body["watching"])
    assert all(c["account_id"] != ids[0] for c in body["new"])
