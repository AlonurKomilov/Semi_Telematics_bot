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
