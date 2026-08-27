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
        assert set(body["personas"]) == {"owner_admin", "dispatcher", "safety", "fleet", "hr", "accounting", "recruiter"}
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


class _FakeCreateTopic:
    """aiohttp.ClientSession stub — createForumTopic returns a thread."""

    class _Resp:
        def __init__(self, payload):
            self._p = payload

        async def json(self):
            return self._p

        async def __aenter__(self):
            return self

        async def __aexit__(self, *a):
            return False

    ok = True

    def __init__(self, *a, **kw):
        pass

    def get(self, url, **kw):
        if "createForumTopic" in url and _FakeCreateTopic.ok:
            return self._Resp({"ok": True, "result": {"message_thread_id": 4242}})
        return self._Resp({"ok": False, "description": "not enough rights"})

    async def __aenter__(self):
        return self

    async def __aexit__(self, *a):
        return False


async def _seed_single_forum(db, owner, acct):
    from infra.crypto import encrypt
    await db.update_account(acct.id, bot_token_encrypted=encrypt("123:ABC"),
                            bot_username="primary_bot")
    await db.upsert_forum_group(
        account_id=acct.id, chat_id=-100777, chat_title="Forum",
        is_forum_enabled=True, created_by_user_id=owner.id,
        setup_status="provisioned",
    )


async def test_forum_custom_topics_and_subtypes(api, monkeypatch):
    """Single-mode custom topics: create needs a real thread (502 without
    one), the row is sentinel-isolated from the per-role view, and the
    sub-category selection round-trips."""
    app, db = api
    acct, owner = await _seed_owner(db, "Forum Topics Co")
    await _seed_single_forum(db, owner, acct)
    import aiohttp
    monkeypatch.setattr(aiohttp, "ClientSession", _FakeCreateTopic)
    h = _headers(owner, acct)

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
        # createForumTopic fails → 502, nothing persisted.
        _FakeCreateTopic.ok = False
        r = await c.post("/api/admin/forum-routing/custom-topics", headers=h,
                         json={"name": "Crashes", "alert_type": "events", "subtypes": ["crash"]})
        assert r.status_code == 502
        body = (await c.get("/api/admin/forum-routing", headers=h)).json()
        assert body["custom_topics"] == []

        # thread creates → 200 with a thread; row appears in the forum view.
        _FakeCreateTopic.ok = True
        r = await c.post("/api/admin/forum-routing/custom-topics", headers=h,
                         json={"name": "Crashes only", "alert_type": "events", "subtypes": ["crash"]})
        assert r.status_code == 200, r.text
        topic = r.json()
        assert topic["has_thread"] is True and topic["subtypes"] == ["crash"]

        body = (await c.get("/api/admin/forum-routing", headers=h)).json()
        assert len(body["custom_topics"]) == 1
        # events row carries the sub-category vocabulary for the chips.
        ev = next(rr for rr in body["routes"] if rr["alert_type"] == "events")
        assert set(ev["subtypes"]["all"]) >= {"crash", "braking"}

        # SENTINEL ISOLATION — the forum topic must NOT leak into the
        # per-role custom-topics view…
        persona_view = (await c.get("/api/admin/alert-routing/custom-topics", headers=h)).json()
        assert all(not p.startswith("__") for p in persona_view["personas"])
        # …and the per-role DELETE endpoint must refuse the sentinel row
        # (scope guard is symmetric — can't delete a forum topic there).
        assert (await c.delete(f"/api/admin/alert-routing/custom-topics/{topic['id']}",
                               headers=h)).status_code == 404

        # sub-category selection round-trips; full set normalizes to ALL.
        r = await c.put("/api/admin/forum-routing/events/subtypes", headers=h,
                        json={"selected": ["crash", "braking"]})
        assert r.status_code == 200 and r.json()["selected"] == ["crash", "braking"]
        body = (await c.get("/api/admin/forum-routing", headers=h)).json()
        ev = next(rr for rr in body["routes"] if rr["alert_type"] == "events")
        assert ev["subtypes"]["selected"] == ["crash", "braking"]

        r = await c.put("/api/admin/forum-routing/events/subtypes", headers=h,
                        json={"selected": ev["subtypes"]["all"]})
        assert r.json()["selected"] is None  # full = ALL

        # delete returns the alert to its default type-topic.
        assert (await c.delete(f"/api/admin/forum-routing/custom-topics/{topic['id']}",
                               headers=h)).status_code == 200
        body = (await c.get("/api/admin/forum-routing", headers=h)).json()
        assert body["custom_topics"] == []
