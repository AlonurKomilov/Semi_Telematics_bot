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

import pytest
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


# ── Web push: vapid key + subscribe/unsubscribe + prefs (6-2) ────────

@pytest.fixture(autouse=True)
def _public_dns(monkeypatch):
    """The anti-SSRF gate resolves endpoint hosts; test endpoints are
    fake, so resolve them to a public address (the private-IP rejection
    has its own dedicated test)."""
    monkeypatch.setattr(
        "capabilities.notifications.push_endpoint._resolve_host",
        lambda host, port: ["93.184.216.34"])


def _sub_body(n: int) -> dict:
    # Real-shaped keys: p256dh ~87 chars, auth ~22 (the endpoint enforces
    # plausible minimums so a broken client can't store a dead device).
    return {"endpoint": f"https://push.example/ep{n}",
            "keys": {"p256dh": "B" + "x" * 86, "auth": "a" * 22}}


async def test_push_subscribe_requires_auth(api):
    async with _client(api) as c:
        r = await c.post(f"{API}/push/subscribe", json=_sub_body(1))
    assert r.status_code == 401


async def test_push_subscribe_flow_verifies_channel(api):
    async with _client(api) as c:
        # VAPID key is stable across calls.
        k1 = (await c.get(f"{API}/push/vapid-key", headers=_h(api["token"]))).json()
        k2 = (await c.get(f"{API}/push/vapid-key", headers=_h(api["token"]))).json()
        assert k1["public_key"] and k1 == k2

        r = await c.post(f"{API}/push/subscribe", json=_sub_body(1),
                         headers=_h(api["token"]),
                         # UA → server-derived device label
                         )
        assert r.json()["ok"] is True

    ch = await api["db"].get_notification_channel(api["acct"], "user", api["uid"], "web_push")
    assert ch and ch["verified"] is True          # permission grant = verification
    subs = await api["db"].list_push_subscriptions(api["acct"], api["uid"])
    assert len(subs) == 1


async def test_push_subscribe_rejects_bad_endpoint(api):
    async with _client(api) as c:
        r = await c.post(f"{API}/push/subscribe",
                         json={"endpoint": "http://not-https.example",
                               "keys": _sub_body(1)["keys"]},
                         headers=_h(api["token"]))
    assert r.json() == {"ok": False, "error": "bad_endpoint"}


async def test_push_subscribe_rejects_private_endpoint(api, monkeypatch):
    """The SSRF gate: an endpoint resolving to an internal address is
    refused — the server must never POST into its own network."""
    monkeypatch.setattr(
        "capabilities.notifications.push_endpoint._resolve_host",
        lambda host, port: ["10.0.0.5"])
    async with _client(api) as c:
        r = await c.post(f"{API}/push/subscribe", json=_sub_body(1),
                         headers=_h(api["token"]))
    assert r.json() == {"ok": False, "error": "private_endpoint"}
    assert await api["db"].list_push_subscriptions(api["acct"], api["uid"]) == []


async def test_push_subscribe_rejects_implausible_keys(api):
    """Empty/short keys would store a device that 'counts' in the UI but
    can never deliver."""
    async with _client(api) as c:
        r = await c.post(f"{API}/push/subscribe",
                         json={"endpoint": "https://push.example/epk",
                               "keys": {"p256dh": "", "auth": ""}},
                         headers=_h(api["token"]))
    assert r.json() == {"ok": False, "error": "bad_keys"}


@pytest.fixture
def _no_rate_limit():
    """The device-cap test needs 12 requests — more than the route's own
    10/min per-IP limit (which is correct in production and covered by
    slowapi itself, not re-tested here)."""
    from interfaces.api.rate_limit import limiter
    limiter.enabled = False
    yield
    limiter.enabled = True


async def test_push_subscribe_device_cap(api, _no_rate_limit):
    from capabilities.notifications.push_endpoint import MAX_DEVICES_PER_USER
    async with _client(api) as c:
        for n in range(MAX_DEVICES_PER_USER):
            r = await c.post(f"{API}/push/subscribe", json=_sub_body(100 + n),
                             headers=_h(api["token"]))
            assert r.json()["ok"] is True
        # One more NEW endpoint → capped.
        r = await c.post(f"{API}/push/subscribe", json=_sub_body(999),
                         headers=_h(api["token"]))
        assert r.json() == {"ok": False, "error": "device_limit"}
        # Re-subscribing an EXISTING endpoint still works (key refresh).
        r = await c.post(f"{API}/push/subscribe", json=_sub_body(100),
                         headers=_h(api["token"]))
        assert r.json()["ok"] is True


async def test_push_unsubscribe_last_device_unverifies(api):
    async with _client(api) as c:
        await c.post(f"{API}/push/subscribe", json=_sub_body(1), headers=_h(api["token"]))
        await c.post(f"{API}/push/subscribe", json=_sub_body(2), headers=_h(api["token"]))

        r = await c.post(f"{API}/push/unsubscribe",
                         json={"endpoint": _sub_body(1)["endpoint"]},
                         headers=_h(api["token"]))
        assert r.json()["removed"] is True and r.json()["devices_left"] == 1
        ch = await api["db"].get_notification_channel(
            api["acct"], "user", api["uid"], "web_push")
        assert ch["verified"] is True             # one device still on

        r = await c.post(f"{API}/push/unsubscribe",
                         json={"endpoint": _sub_body(2)["endpoint"]},
                         headers=_h(api["token"]))
        assert r.json()["devices_left"] == 0
        ch = await api["db"].get_notification_channel(
            api["acct"], "user", api["uid"], "web_push")
        assert ch["verified"] is False            # gate closes with the last device


async def test_web_push_prefs_roundtrip_default_immediate(api):
    async with _client(api) as c:
        body = (await c.get(f"{API}/prefs/web_push", headers=_h(api["token"]))).json()
        assert body["web_push"]["cadence"] == "immediate"   # glance channel default
        assert body["web_push"]["connected"] is False
        a_type = body["relevant_types"][0]

        r = await c.put(f"{API}/prefs/web_push/type", headers=_h(api["token"]),
                        json={"alert_type": a_type, "enabled": True})
        assert r.json()["ok"] is True
        body = (await c.get(f"{API}/prefs/web_push", headers=_h(api["token"]))).json()
        assert body["web_push"]["types"][a_type] is True

        # Device list rides the prefs payload once subscribed.
        await c.post(f"{API}/push/subscribe", json=_sub_body(5), headers=_h(api["token"]))
        body = (await c.get(f"{API}/prefs/web_push", headers=_h(api["token"]))).json()
        assert len(body["web_push"]["devices"]) == 1
        assert body["web_push"]["devices"][0]["device_label"]


async def test_prefs_unknown_channel_is_422(api):
    async with _client(api) as c:
        r = await c.get(f"{API}/prefs/telegram_dm", headers=_h(api["token"]))
    assert r.status_code == 422        # Literal allowlist — legacy path owns telegram


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


# ── Account activity — targeted (opt-out) category prefs (N4) ─────────
# team.invite_accepted is registered when the invites feature imports.
import features.settings.invites  # noqa: E402,F401


async def test_account_activity_lists_targeted_categories_on_by_default(api):
    async with _client(api) as c:
        r = await c.get(f"{API}/preferences/account-activity", headers=_h(api["token"]))
    assert r.status_code == 200
    body = r.json()
    cats = {c["key"]: c for c in body["categories"]}
    assert "team.invite_accepted" in cats            # the first non-alert source
    cat = cats["team.invite_accepted"]
    assert cat["source"] == "team"
    assert cat["mandatory"] is False
    # Opt-out: ON for every personal channel until muted (in_app is the
    # intrinsic inbox channel — always present).
    assert cat["channels"] == {"telegram_dm": True, "email": True,
                               "web_push": True, "in_app": True}
    # Channel connection states ride along so the UI can grey a column.
    assert set(body["channels"]) == {"telegram_dm", "email", "web_push", "in_app"}
    # A brand-new user hasn't connected anything (alerts_on defaults off, so
    # the telegram_dm channel isn't provisioned yet) — nothing external is
    # "ready"; the intrinsic inbox always is.
    assert body["channels"]["in_app"]["ready"] is True
    assert all(ch["ready"] is False
               for k, ch in body["channels"].items() if k != "in_app")


async def test_account_activity_mute_then_reflected(api):
    async with _client(api) as c:
        # Mute invite-accepted on email only.
        r = await c.put(f"{API}/preferences/account-activity", headers=_h(api["token"]),
                        json={"category": "team.invite_accepted",
                              "channel": "email", "enabled": False})
        assert r.json() == {"ok": True}

        body = (await c.get(f"{API}/preferences/account-activity",
                            headers=_h(api["token"]))).json()
    cat = next(c for c in body["categories"] if c["key"] == "team.invite_accepted")
    assert cat["channels"]["email"] is False          # muted
    assert cat["channels"]["telegram_dm"] is True     # others untouched
    assert cat["channels"]["web_push"] is True
    # A real pref row was written for the mute.
    prefs = await api["db"].get_pref_categories(api["acct"], "user", api["uid"], "email")
    assert prefs.get("team.invite_accepted") is False


async def test_account_activity_rejects_broadcast_category(api):
    async with _client(api) as c:
        # alert.faults is BROADCAST — it belongs to the matrix, not here.
        r = await c.put(f"{API}/preferences/account-activity", headers=_h(api["token"]),
                        json={"category": "alert.faults",
                              "channel": "email", "enabled": False})
    assert r.json() == {"ok": False, "error": "unknown_category"}


async def test_account_activity_requires_auth(api):
    async with _client(api) as c:
        r = await c.get(f"{API}/preferences/account-activity")
    assert r.status_code == 401


# ── In-app inbox routes (N5-P2) ──────────────────────────────────────

async def test_inbox_feed_unread_and_mark_read(api):
    db, acct, uid = api["db"], api["acct"], api["uid"]
    await db.add_inbox_notice(acct, uid, category="team.invite_accepted",
                              title="Invite accepted", body="Dana joined.",
                              meta='{"context": "Team"}')
    await db.add_inbox_notice(acct, uid, category="system.security",
                              title="New sign-in", severity="warning")
    async with _client(api) as c:
        body = (await c.get(f"{API}/inbox", headers=_h(api["token"]))).json()
        assert body["unread"] == 2
        assert [n["title"] for n in body["notices"]] == ["New sign-in", "Invite accepted"]
        assert body["notices"][0]["source"] == "system"
        assert body["notices"][0]["read"] is False
        assert body["notices"][0]["context"] == ""          # no meta → no chip
        assert body["notices"][1]["context"] == "Team"      # meta.context chip

        # Source tab filter.
        team = (await c.get(f"{API}/inbox?source=team",
                            headers=_h(api["token"]))).json()
        assert [n["source"] for n in team["notices"]] == ["team"]

        # Mark one read → unread drops; row flips.
        first_id = body["notices"][0]["id"]
        r = (await c.post(f"{API}/inbox/read", json={"ids": [first_id]},
                          headers=_h(api["token"]))).json()
        assert r == {"ok": True, "marked": 1, "unread": 1}

        # Read-all clears the rest.
        r = (await c.post(f"{API}/inbox/read-all",
                          headers=_h(api["token"]))).json()
        assert r["ok"] is True and r["unread"] == 0
        body = (await c.get(f"{API}/inbox", headers=_h(api["token"]))).json()
        assert body["unread"] == 0
        assert all(n["read"] for n in body["notices"])


async def test_inbox_requires_auth(api):
    async with _client(api) as c:
        assert (await c.get(f"{API}/inbox")).status_code == 401
        assert (await c.post(f"{API}/inbox/read", json={"ids": [1]})).status_code == 401


async def test_inbox_cannot_read_other_users_rows(api):
    """Another user's notices never appear in my feed, and my mark-read
    can't flip their rows."""
    db, acct = api["db"], api["acct"]
    other = await db.create_user(9002, acct, role=Role.FLEET)
    await db.add_inbox_notice(acct, other.id, category="team.t", title="theirs")
    async with _client(api) as c:
        body = (await c.get(f"{API}/inbox", headers=_h(api["token"]))).json()
        assert body["notices"] == [] and body["unread"] == 0
        # Try to mark their row read by id — scoped UPDATE matches nothing.
        theirs = await db.list_inbox_notices(acct, other.id)
        r = (await c.post(f"{API}/inbox/read", json={"ids": [theirs[0]["id"]]},
                          headers=_h(api["token"]))).json()
        assert r["marked"] == 0
    assert await db.count_inbox_unread(acct, other.id) == 1


async def test_inbox_action_field_validated(api):
    """meta.action rides through only when it's a labeled RELATIVE url —
    an absolute/protocol-relative url is dropped (no off-site redirect)."""
    db, acct, uid = api["db"], api["acct"], api["uid"]
    await db.add_inbox_notice(
        acct, uid, category="system.security", title="New sign-in",
        meta='{"context": "Security", "action": {"label": "Review sessions", "url": "/profile"}}')
    await db.add_inbox_notice(
        acct, uid, category="system.security", title="Evil",
        meta='{"action": {"label": "x", "url": "https://evil.example"}}')
    async with _client(api) as c:
        body = (await c.get(f"{API}/inbox", headers=_h(api["token"]))).json()
    by_title = {n["title"]: n for n in body["notices"]}
    assert by_title["New sign-in"]["action"] == {
        "label": "Review sessions", "url": "/profile"}
    assert by_title["Evil"]["action"] is None
