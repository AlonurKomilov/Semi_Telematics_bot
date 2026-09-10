"""The ledger is fed from the request path — driven as REQUESTS.

The recorder's policy has its own tests.  This file asks the one thing
those cannot: is the hook actually wired into the app, after the
handler, with the response status in hand?  A middleware that was never
registered records nothing and fails nothing — silence looks like
success.  So: build the real app, add two probe routes, and check the
table.
"""

from __future__ import annotations

import os

os.environ.setdefault("ENCRYPTION_KEY", "")

import pytest
import pytest_asyncio
from fastapi import APIRouter, HTTPException
from httpx import ASGITransport, AsyncClient

pytestmark = pytest.mark.asyncio


@pytest_asyncio.fixture
async def api(pg_db, monkeypatch):
    import infra.platform as plat
    monkeypatch.setattr(plat, "_db", pg_db, raising=False)
    from capabilities.security import recorder
    recorder.forget_kind()
    from interfaces.api.app import create_api
    app = create_api()
    probe = APIRouter()

    @probe.get("/_ledger/refused")
    async def _refused():
        raise HTTPException(status_code=403, detail="no")

    @probe.get("/_ledger/fine")
    async def _fine():
        return {"ok": True}

    app.include_router(probe, prefix="/api")
    return app, pg_db


async def _get(app, path, **headers):
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as c:
        return await c.get(path, headers=headers)


async def test_a_refusal_lands_in_the_ledger_with_the_true_ip(api):
    app, db = api
    r = await _get(app, "/api/_ledger/refused", **{"X-Forwarded-For": "203.0.113.9"})
    assert r.status_code == 403

    rows = await db.list_security_requests(statuses=(403,), limit=5)
    hit = next((x for x in rows if x["path"] == "/api/_ledger/refused"), None)
    assert hit is not None, "the middleware hook is not wired"
    assert hit["method"] == "GET" and hit["status"] == 403
    assert hit["ip"] == "203.0.113.9"
    assert hit["account_id"] is None      # no token → no account, still kept
    assert hit["duration_ms"] is not None and hit["duration_ms"] >= 0
    assert hit["request_id"]              # RequestIDMiddleware ran first


async def test_an_anonymous_success_is_not_kept(api):
    app, db = api
    r = await _get(app, "/api/_ledger/fine")
    assert r.status_code == 200
    rows = await db.list_security_requests(limit=50)
    assert all(x["path"] != "/api/_ledger/fine" for x in rows)
