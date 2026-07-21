"""Notification channel HTTP routes (phase 4b) — the FastAPI surface the
lifecycle tests reach only at the function level: auth enforcement on the
connect/list endpoints, the public token-authed verify + unsubscribe, and
the safety property that a GET must NOT mutate (mail-scanner prefetch must
not silently turn a fleet's alerts off).
"""

from __future__ import annotations

import os

os.environ.setdefault("ENCRYPTION_KEY", "")
os.environ.setdefault("JWT_SECRET", "test-jwt-secret-not-for-production-use-only")
os.environ.setdefault(
    "NOTIFICATION_SIGNING_SECRET", "test-notification-signing-secret-32b+")

import pytest_asyncio
from httpx import ASGITransport, AsyncClient

from capabilities.notifications.tokens import (
    UNSUB_PURPOSE,
    VERIFY_PURPOSE,
    make_token,
)
from capabilities.permissions.roles import Role
from interfaces.api.auth import create_jwt

API = "/api/v1/notifications"


@pytest_asyncio.fixture
async def api(pg_db):
    import infra.platform as _cp
    old = _cp._db
    _cp._db = pg_db

    acct = await pg_db.create_account("Routes Co")
    user = await pg_db.create_user(9001, acct.id, role=Role.FLEET)

    from interfaces.api.app import create_api
    app = create_api()
    yield {
        "app": app, "db": pg_db, "acct": acct.id, "uid": user.id,
        "token": create_jwt(user.telegram_id, acct.id, "fleet"),
    }
    _cp._db = old


def _h(token: str) -> dict:
    return {"Authorization": f"Bearer {token}"}


def _client(api):
    return AsyncClient(transport=ASGITransport(app=api["app"]), base_url="http://t")


# ── Connect (authed) ─────────────────────────────────────────────────

async def test_connect_requires_auth(api):
    async with _client(api) as c:
        r = await c.post(f"{API}/channels/email", json={"address": "me@x.com"})
    assert r.status_code == 401


async def test_connect_stores_unverified_and_reports(api):
    async with _client(api) as c:
        r = await c.post(f"{API}/channels/email", json={"address": "me@x.com"},
                         headers=_h(api["token"]))
    assert r.status_code == 200
    # SMTP off in tests → ok True, sent False; address stored UNVERIFIED.
    assert r.json()["ok"] is True
    ch = await api["db"].get_notification_channel(api["acct"], "user", api["uid"], "email")
    assert ch["address"] == "me@x.com" and ch["verified"] is False


async def test_connect_rejects_bad_email(api):
    async with _client(api) as c:
        r = await c.post(f"{API}/channels/email", json={"address": "nope"},
                         headers=_h(api["token"]))
    assert r.json()["ok"] is False and r.json()["error"] == "bad_email"


async def test_list_channels_shape(api):
    async with _client(api) as c:
        r = await c.get(f"{API}/channels", headers=_h(api["token"]))
    assert r.status_code == 200
    chans = r.json()["channels"]
    assert "email" in chans and "telegram_dm" in chans


# ── Verify (public, token-authed) ────────────────────────────────────

async def test_verify_confirms_valid_token(api):
    await api["db"].upsert_notification_channel(
        api["acct"], "user", api["uid"], "email", address="me@x.com", verified=False)
    token = make_token(VERIFY_PURPOSE, account_id=api["acct"], recipient_type="user",
                       recipient_id=api["uid"], channel="email", address="me@x.com",
                       ttl_seconds=3600)
    async with _client(api) as c:
        r = await c.get(f"{API}/verify", params={"token": token})
    assert r.status_code == 200 and "confirmed" in r.text.lower()
    ch = await api["db"].get_notification_channel(api["acct"], "user", api["uid"], "email")
    assert ch["verified"] is True


async def test_verify_bad_token_is_uniform_page(api):
    async with _client(api) as c:
        r = await c.get(f"{API}/verify", params={"token": "garbage"})
    assert r.status_code == 200 and "expired" in r.text.lower()   # no 4xx oracle


# ── Unsubscribe (public one-click + GET-does-not-mutate) ─────────────

async def test_unsubscribe_post_is_one_click(api):
    await api["db"].upsert_notification_channel(
        api["acct"], "user", api["uid"], "email", address="me@x.com", verified=True)
    token = make_token(UNSUB_PURPOSE, account_id=api["acct"], recipient_type="user",
                       recipient_id=api["uid"], channel="email")
    async with _client(api) as c:
        r = await c.post(f"{API}/unsubscribe", params={"token": token})
    assert r.status_code == 200 and r.json() == {"ok": True}
    ch = await api["db"].get_notification_channel(api["acct"], "user", api["uid"], "email")
    assert ch["enabled_master"] is False


async def test_unsubscribe_get_does_not_mutate(api):
    """A GET (mail-scanner prefetch / human click) must render a confirm
    page WITHOUT turning anything off — else Safe Links silently
    unsubscribes a fleet from its safety alerts."""
    await api["db"].upsert_notification_channel(
        api["acct"], "user", api["uid"], "email", address="me@x.com", verified=True)
    token = make_token(UNSUB_PURPOSE, account_id=api["acct"], recipient_type="user",
                       recipient_id=api["uid"], channel="email")
    async with _client(api) as c:
        r = await c.get(f"{API}/unsubscribe", params={"token": token})
    assert r.status_code == 200
    assert "<form" in r.text and "unsubscribe-confirmed" in r.text
    ch = await api["db"].get_notification_channel(api["acct"], "user", api["uid"], "email")
    assert ch["enabled_master"] is True          # NOT mutated by the GET


async def test_unsubscribe_confirmed_post_mutates(api):
    await api["db"].upsert_notification_channel(
        api["acct"], "user", api["uid"], "email", address="me@x.com", verified=True)
    token = make_token(UNSUB_PURPOSE, account_id=api["acct"], recipient_type="user",
                       recipient_id=api["uid"], channel="email")
    async with _client(api) as c:
        r = await c.post(f"{API}/unsubscribe-confirmed", params={"token": token})
    assert r.status_code == 200 and "unsubscribed" in r.text.lower()
    ch = await api["db"].get_notification_channel(api["acct"], "user", api["uid"], "email")
    assert ch["enabled_master"] is False


# ── Email per-type preferences (5b) ──────────────────────────────────

async def test_email_prefs_requires_auth(api):
    async with _client(api) as c:
        r = await c.get(f"{API}/prefs/email")
    assert r.status_code == 401


async def test_email_prefs_roundtrip(api):
    async with _client(api) as c:
        # Initial: role-tailored types, none on, default daily, disconnected.
        r = await c.get(f"{API}/prefs/email", headers=_h(api["token"]))
        body = r.json()
        assert body["relevant_types"] and body["email"]["connected"] is False
        assert body["email"]["cadence"] == "daily"
        a_type = body["relevant_types"][0]
        assert body["email"]["types"][a_type] is False

        # Enable one type → reflected on re-read.
        r = await c.put(f"{API}/prefs/email/type", headers=_h(api["token"]),
                        json={"alert_type": a_type, "enabled": True})
        assert r.json()["ok"] is True
        body = (await c.get(f"{API}/prefs/email", headers=_h(api["token"]))).json()
        assert body["email"]["types"][a_type] is True

        # Change cadence → applies to the enabled row.
        r = await c.put(f"{API}/prefs/email/cadence", headers=_h(api["token"]),
                        json={"cadence": "hourly"})
        assert r.json()["ok"] is True
        body = (await c.get(f"{API}/prefs/email", headers=_h(api["token"]))).json()
        assert body["email"]["cadence"] == "hourly"


async def test_email_type_rejects_irrelevant_and_bad_cadence(api):
    async with _client(api) as c:
        r = await c.put(f"{API}/prefs/email/type", headers=_h(api["token"]),
                        json={"alert_type": "not_a_real_type", "enabled": True})
        assert r.json() == {"ok": False, "error": "irrelevant_type"}

        # cadence is a Literal → an unknown value is a 422 at the schema
        # layer, never reaching the DB.
        r = await c.put(f"{API}/prefs/email/cadence", headers=_h(api["token"]),
                        json={"cadence": "weekly"})
        assert r.status_code == 422
