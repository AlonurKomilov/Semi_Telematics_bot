"""Customer self-serve alert-routing endpoints (features/settings/account).

Owner/admin configures where the account's bot posts alerts: the
single_group ↔ per_persona_groups mode switch and per-department chat
bindings (validated against Telegram getChat).  Pins: response shape,
mode round-trip, bind requires a configured bot, getChat-validated bind +
unbind round-trip, and the can_manage_account gate.
"""
from __future__ import annotations

import os
import sys

os.environ.setdefault("ENCRYPTION_KEY", "")
os.environ.setdefault("JWT_SECRET", "test-secret-32-chars-or-more-please-aaaaaaaaaaaa")
sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

import pytest_asyncio
from httpx import AsyncClient, ASGITransport

from adapters.storage import Role


@pytest_asyncio.fixture
async def api(pg_db):
    import infra.platform as _cp
    _cp._db = pg_db
    from interfaces.api.app import create_api
    return create_api(), pg_db


async def _seed_owner(db, name="Routing Co"):
    from interfaces.api.auth import _hash_password
    acct = await db.create_account(name)
    owner = await db.create_user_with_email(
        email=f"owner.{acct.id}@example.com",
        password_hash=_hash_password("ownerpass123"),
        account_id=acct.id, role=Role.OWNER, display_name="Owner",
    )
    return acct, owner


def _headers(user, acct, role="owner"):
    from interfaces.api.auth import create_jwt
    token = create_jwt(user.telegram_id or 0, acct.id, role, user_id=user.id)
    return {"Authorization": f"Bearer {token}"}


class _FakeTelegram:
    """aiohttp.ClientSession stand-in answering getChat with a title."""

    class _Resp:
        def __init__(self, payload):
            self._payload = payload

        async def json(self):
            return self._payload

        async def __aenter__(self):
            return self

        async def __aexit__(self, *a):
            return False

    def __init__(self, *a, **kw):
        pass

    def get(self, url, **kw):
        return self._Resp({"ok": True, "result": {"title": "Dispatch Group"}})

    async def __aenter__(self):
        return self

    async def __aexit__(self, *a):
        return False


async def test_get_shape_and_mode_roundtrip(api):
    app, db = api
    acct, owner = await _seed_owner(db)
    h = _headers(owner, acct)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
        r = await c.get("/api/admin/alert-routing", headers=h)
        assert r.status_code == 200
        body = r.json()
        assert body["mode"] == "single_group"
        assert set(body["personas"]) == {"owner_admin", "dispatcher", "safety", "fleet", "hr"}
        assert all(v is None for v in body["personas"].values())
        assert body["bot_configured"] is False
        assert body["nudge_threshold"] == 30

        r = await c.put("/api/admin/alert-routing", headers=h,
                        json={"mode": "per_persona_groups"})
        assert r.status_code == 200
        assert (await c.get("/api/admin/alert-routing", headers=h)).json()["mode"] == "per_persona_groups"

        # invalid mode never reaches storage — pydantic pattern rejects it
        assert (await c.put("/api/admin/alert-routing", headers=h,
                            json={"mode": "chaos"})).status_code == 422


async def test_bind_requires_configured_bot(api):
    app, db = api
    acct, owner = await _seed_owner(db, "No Bot Co")
    h = _headers(owner, acct)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
        r = await c.post("/api/admin/alert-routing/persona-groups", headers=h,
                         json={"persona": "dispatcher", "chat_id": -100})
        assert r.status_code == 400


async def test_bind_and_unbind_roundtrip(api, monkeypatch):
    app, db = api
    acct, owner = await _seed_owner(db, "Bound Co")
    from infra.crypto import encrypt
    await db.update_account(acct.id, bot_token_encrypted=encrypt("123:ABC"))
    import aiohttp
    monkeypatch.setattr(aiohttp, "ClientSession", _FakeTelegram)

    h = _headers(owner, acct)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
        r = await c.post("/api/admin/alert-routing/persona-groups", headers=h,
                         json={"persona": "dispatcher", "chat_id": -100200})
        assert r.status_code == 200
        assert r.json()["chat_title"] == "Dispatch Group"

        body = (await c.get("/api/admin/alert-routing", headers=h)).json()
        assert body["personas"]["dispatcher"]["chat_id"] == -100200

        assert (await c.delete("/api/admin/alert-routing/persona-groups/dispatcher",
                               headers=h)).status_code == 200
        body = (await c.get("/api/admin/alert-routing", headers=h)).json()
        assert body["personas"]["dispatcher"] is None

        # unknown persona slug 400s (bind is caught by pydantic → 422)
        assert (await c.delete("/api/admin/alert-routing/persona-groups/pirates",
                               headers=h)).status_code == 400


async def test_requires_manage_account_permission(api):
    app, db = api
    acct, owner = await _seed_owner(db, "Gate Co")
    h = _headers(owner, acct, role="driver")  # role without can_manage_account
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
        # READ opened to all staff (role managers render their row from
        # it); the mode WRITE stays owner/admin.
        assert (await c.get("/api/admin/alert-routing", headers=h)).status_code == 200
        assert (await c.put("/api/admin/alert-routing", headers=h,
                            json={"mode": "single_group"})).status_code == 403
