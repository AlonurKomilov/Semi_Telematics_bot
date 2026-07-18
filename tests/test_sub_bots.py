"""Role Sub bots ("Sub bot" mode) — storage, permissions, sender pick.

The contract under test (docs/architecture/bot-topology.md):
- one IDENTITY bot per account; a Sub bot is a per-persona SENDER only
- owner/admin manage all Sub bots; a role manager (is_manager on the
  matching base role) manages exactly their own role's
- delivery fail-open: no/inactive/down Sub bot → the primary bot sends
- the owner_admin AGGREGATE cross-post always rides the primary bot
"""
from __future__ import annotations

import os
import sys
from types import SimpleNamespace

os.environ.setdefault("ENCRYPTION_KEY", "")
os.environ.setdefault("JWT_SECRET", "test-secret-32-chars-or-more-please-aaaaaaaaaaaa")
sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

import pytest
import pytest_asyncio
from httpx import AsyncClient, ASGITransport

from adapters.storage import Role


# ── Storage mixin ──────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_mixin_crud_roundtrip(db):
    acct = await db.create_account("SubBot Co")
    row = await db.upsert_bot_instance(
        account_id=acct.id, persona="safety",
        token_encrypted="tok-1", bot_username="safety_bot",
        webhook_secret="s1",
    )
    assert row.persona == "safety" and row.is_active

    # replace token/username in place (same composite key)
    row2 = await db.upsert_bot_instance(
        account_id=acct.id, persona="safety",
        token_encrypted="tok-2", bot_username="safety_bot_v2",
    )
    assert row2.id == row.id
    assert row2.bot_username == "safety_bot_v2"

    got = await db.get_bot_instance(acct.id, "safety")
    assert got is not None and got.token_encrypted == "tok-2"
    # another persona/account stays isolated
    assert await db.get_bot_instance(acct.id, "fleet") is None
    assert await db.get_bot_instance(acct.id + 1, "safety") is None

    everyone = await db.list_all_active_bot_instances()
    assert any(r.account_id == acct.id and r.persona == "safety" for r in everyone)

    await db.delete_bot_instance(acct.id, "safety")
    assert await db.get_bot_instance(acct.id, "safety") is None


# ── Sender pick (pipeline fallback contract) ───────────────────────

def test_pick_sender_fallback_rules(monkeypatch):
    from capabilities.alerting.pipeline import _pick_sender
    import infra.bot_registry as reg

    primary = object()
    sub = object()

    class _StubRegistry:
        def get_sub(self, account_id, persona):
            return sub if persona == "safety" else None

        def get(self, account_id):
            return primary

    monkeypatch.setattr(reg, "_registry", _StubRegistry())

    t = lambda persona, agg=False: SimpleNamespace(persona=persona, is_aggregate=agg)
    # attached + running → the role's Sub bot
    assert _pick_sender(primary, 1, t("safety")) is sub
    # no sub for this persona → primary
    assert _pick_sender(primary, 1, t("dispatcher")) is primary
    # legacy single_group targets carry no persona → primary
    assert _pick_sender(primary, 1, t("")) is primary
    # the owner_admin aggregate ALWAYS rides the primary, even if a sub
    # exists for that persona
    assert _pick_sender(primary, 1, t("safety", agg=True)) is primary


# ── HTTP endpoints (permission model) ──────────────────────────────

@pytest_asyncio.fixture
async def api(pg_db):
    import infra.platform as _cp
    _cp._db = pg_db
    from interfaces.api.app import create_api
    return create_api(), pg_db


async def _seed(db, name="SubBot API Co"):
    from interfaces.api.auth import _hash_password
    acct = await db.create_account(name)
    users = {}
    for role in ("owner", "safety", "driver"):
        users[role] = await db.create_user_with_email(
            email=f"{role}.{acct.id}@example.com",
            password_hash=_hash_password("pass12345678"),
            account_id=acct.id, role=getattr(Role, role.upper()),
            display_name=role,
        )
    return acct, users


def _headers(user, acct, role, *, is_manager=False):
    from interfaces.api.auth import create_jwt
    token = create_jwt(user.telegram_id or 0, acct.id, role,
                       user_id=user.id, is_manager=is_manager)
    return {"Authorization": f"Bearer {token}"}


class _FakeGetMe:
    """aiohttp.ClientSession stub answering getMe."""

    class _Resp:
        def __init__(self, payload):
            self._p = payload

        async def json(self):
            return self._p

        async def __aenter__(self):
            return self

        async def __aexit__(self, *a):
            return False

    def __init__(self, *a, **kw):
        pass

    def get(self, url, **kw):
        return self._Resp({"ok": True, "result": {"id": 42, "username": "dept_bot"}})

    async def __aenter__(self):
        return self

    async def __aexit__(self, *a):
        return False


VALID_TOKEN = "123456:ABC-DEF1234ghIkl-zyx57W2v1u123ew11"


async def test_attach_requires_primary_bot(api):
    app, db = api
    acct, users = await _seed(db, "No Primary Co")
    h = _headers(users["owner"], acct, "owner")
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
        r = await c.post("/api/admin/bot-instances", headers=h,
                         json={"persona": "safety", "bot_token": VALID_TOKEN})
        assert r.status_code == 400


async def test_permission_model_and_roundtrip(api, monkeypatch):
    app, db = api
    acct, users = await _seed(db)
    from infra.crypto import encrypt
    await db.update_account(acct.id, bot_token_encrypted=encrypt("999:PRIMARY"),
                            bot_username="primary_bot")
    import aiohttp
    monkeypatch.setattr(aiohttp, "ClientSession", _FakeGetMe)

    owner_h = _headers(users["owner"], acct, "owner")
    mgr_h = _headers(users["safety"], acct, "safety", is_manager=True)
    plain_h = _headers(users["safety"], acct, "safety")           # not a manager
    driver_h = _headers(users["driver"], acct, "driver")

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
        # a safety MANAGER attaches the safety Sub bot
        r = await c.post("/api/admin/bot-instances", headers=mgr_h,
                         json={"persona": "safety", "bot_token": VALID_TOKEN})
        assert r.status_code == 200
        assert r.json()["bot_username"] == "dept_bot"

        # …but NOT another role's, nor the owner_admin aggregate
        for persona in ("dispatcher", "owner_admin"):
            r = await c.post("/api/admin/bot-instances", headers=mgr_h,
                             json={"persona": persona, "bot_token": VALID_TOKEN})
            assert r.status_code == 403, persona

        # non-manager staff and drivers manage nothing
        for h in (plain_h, driver_h):
            r = await c.post("/api/admin/bot-instances", headers=h,
                             json={"persona": "safety", "bot_token": VALID_TOKEN})
            assert r.status_code == 403

        # list: everyone sees status; manageable reflects the caller
        body = (await c.get("/api/admin/bot-instances", headers=driver_h)).json()
        assert body["personas"]["safety"]["bot_username"] == "dept_bot"
        assert body["manageable"] == []
        body = (await c.get("/api/admin/bot-instances", headers=mgr_h)).json()
        assert body["manageable"] == ["safety"]
        body = (await c.get("/api/admin/bot-instances", headers=owner_h)).json()
        assert set(body["manageable"]) == {"owner_admin", "dispatcher", "safety", "fleet", "hr"}

        # the manager detaches their own; delivery falls back to primary
        assert (await c.delete("/api/admin/bot-instances/safety",
                               headers=mgr_h)).status_code == 200
        body = (await c.get("/api/admin/bot-instances", headers=mgr_h)).json()
        assert body["personas"]["safety"] is None


async def test_primary_token_rejected_as_sub(api, monkeypatch):
    app, db = api
    acct, users = await _seed(db, "Same Token Co")
    from infra.crypto import encrypt
    # primary's username matches what the fake getMe returns
    await db.update_account(acct.id, bot_token_encrypted=encrypt("999:PRIMARY"),
                            bot_username="dept_bot")
    import aiohttp
    monkeypatch.setattr(aiohttp, "ClientSession", _FakeGetMe)
    h = _headers(users["owner"], acct, "owner")
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
        r = await c.post("/api/admin/bot-instances", headers=h,
                         json={"persona": "safety", "bot_token": VALID_TOKEN})
        assert r.status_code == 422


def test_manager_tier_grants_role_bot_flag():
    """The Settings-page door: every persona-manager tier carries
    can_manage_role_bot; non-persona tiers (recruiter) don't."""
    from capabilities.permissions.roles import MANAGER_GRANTS
    for role in (Role.FLEET, Role.SAFETY, Role.DISPATCHER, Role.HR):
        assert "can_manage_role_bot" in MANAGER_GRANTS[role], role
    assert "can_manage_role_bot" not in MANAGER_GRANTS.get(Role.RECRUITER, frozenset())


async def test_manager_binds_own_group_and_topics(api, monkeypatch):
    """The owner's decision end-to-end: a role manager binds THEIR
    group, flips THEIR topics, and cannot touch another role's."""
    app, db = api
    acct, users = await _seed(db, "Mgr Owns Row Co")
    from infra.crypto import encrypt
    await db.update_account(acct.id, bot_token_encrypted=encrypt("999:PRIMARY"),
                            bot_username="primary_bot")

    import aiohttp

    class _FakeGetChat(_FakeGetMe):
        def get(self, url, **kw):
            return self._Resp({"ok": True, "result": {"title": "Safety Group"}})

    monkeypatch.setattr(aiohttp, "ClientSession", _FakeGetChat)

    mgr_h = _headers(users["safety"], acct, "safety", is_manager=True)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
        # group bind: own persona OK, another's 403
        r = await c.post("/api/admin/alert-routing/persona-groups", headers=mgr_h,
                         json={"persona": "safety", "chat_id": -777})
        assert r.status_code == 200 and r.json()["chat_title"] == "Safety Group"
        assert (await c.post("/api/admin/alert-routing/persona-groups", headers=mgr_h,
                             json={"persona": "fleet", "chat_id": -778})).status_code == 403

        # topics: grouped by role, filtered to the role's own types
        body = (await c.get("/api/admin/alert-routing/persona-topics", headers=mgr_h)).json()
        safety_types = {r["alert_type"] for r in body["personas"]["safety"]}
        assert safety_types == {"events", "camera"}
        assert {r["alert_type"] for r in body["personas"]["hr"]} == {"documents"}
        assert body["manageable"] == ["safety"]

        # toggle own type OK; another role's type 403; unknown 422
        assert (await c.put("/api/admin/alert-routing/persona-topics/events", headers=mgr_h,
                            json={"field": "enabled", "value": False})).status_code == 200
        assert (await c.put("/api/admin/alert-routing/persona-topics/faults", headers=mgr_h,
                            json={"field": "enabled", "value": False})).status_code == 403
        assert (await c.put("/api/admin/alert-routing/persona-topics/pirates", headers=mgr_h,
                            json={"field": "enabled", "value": False})).status_code == 422

        # the toggle round-trips
        body = (await c.get("/api/admin/alert-routing/persona-topics", headers=mgr_h)).json()
        ev = next(r for r in body["personas"]["safety"] if r["alert_type"] == "events")
        assert ev["enabled"] is False

        # manager unbinds their own group
        assert (await c.delete("/api/admin/alert-routing/persona-groups/safety",
                               headers=mgr_h)).status_code == 200
