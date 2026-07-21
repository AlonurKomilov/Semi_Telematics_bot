"""Web-push channel (phase 6-1): per-device subscription storage, the
zero-config persisted VAPID keypair, the short-form render, and the
fan-out send with inline dead-endpoint pruning — all without a real push
service (pywebpush is mocked at the seam).
"""

from __future__ import annotations

import os

os.environ.setdefault("ENCRYPTION_KEY", "")
os.environ.setdefault("JWT_SECRET", "test-jwt-secret-not-for-production-use-only")

from unittest.mock import patch

import pytest

import capabilities.notifications as notif
from capabilities.notifications import vapid as vapid_mod
from capabilities.notifications.channels import NotificationContent, Recipient
from capabilities.notifications.webpush import WebPushChannel
from capabilities.permissions.roles import Role


@pytest.fixture(autouse=True)
def _fresh_vapid_cache():
    vapid_mod.reset_cache_for_tests()
    yield
    vapid_mod.reset_cache_for_tests()


async def _seed(pg_db):
    acct = await pg_db.create_account("Push Co")
    u = await pg_db.create_user(telegram_id=9301, account_id=acct.id, role=Role.FLEET)
    return acct.id, u.id


def _sub(n: int) -> dict:
    return {"endpoint": f"https://push.example/ep{n}",
            "p256dh": f"p{n}", "auth": f"a{n}"}


# ── Registry ─────────────────────────────────────────────────────────

def test_web_push_registered_personal():
    ch = notif.get_channel("web_push")
    assert ch is not None and ch.personal is True
    assert "web_push" in {c.key for c in notif.personal_channels()}


# ── Subscription storage ─────────────────────────────────────────────

async def test_subscription_roundtrip_and_upsert(pg_db):
    acct, uid = await _seed(pg_db)
    await pg_db.add_push_subscription(
        acct, uid, **_sub(1), device_label="Chrome · Windows")
    await pg_db.add_push_subscription(acct, uid, **_sub(2))
    subs = await pg_db.list_push_subscriptions(acct, uid)
    assert len(subs) == 2
    assert subs[0]["device_label"] == "Chrome · Windows"

    # Same endpoint re-subscribes (refreshed keys) — no duplicate device.
    await pg_db.add_push_subscription(
        acct, uid, endpoint=_sub(1)["endpoint"], p256dh="NEW", auth="NEW")
    subs = await pg_db.list_push_subscriptions(acct, uid)
    assert len(subs) == 2
    assert next(s for s in subs if s["endpoint"].endswith("ep1"))["p256dh"] == "NEW"

    # Scoped remove: wrong user can't drop it, owner can.
    assert await pg_db.remove_push_subscription(acct, uid + 999, _sub(1)["endpoint"]) is False
    assert await pg_db.remove_push_subscription(acct, uid, _sub(1)["endpoint"]) is True
    assert len(await pg_db.list_push_subscriptions(acct, uid)) == 1


# ── VAPID keypair (zero-config, persisted) ───────────────────────────

async def test_vapid_generated_once_and_persisted(pg_db):
    v1 = await vapid_mod.ensure_vapid(pg_db)
    assert v1["private_pem"].startswith("-----BEGIN")
    assert len(v1["public_key"]) == 87          # b64url of a 65-byte P-256 point

    # Second call (fresh cache → forced re-read from the DB) = SAME pair;
    # a new key would kill every existing browser subscription.
    vapid_mod.reset_cache_for_tests()
    v2 = await vapid_mod.ensure_vapid(pg_db)
    assert v2 == v1


def test_vapid_claims_sub_parses_smtp_from(monkeypatch):
    monkeypatch.setenv("SMTP_FROM", '"4truck" <noreply@4truck.us>')
    assert vapid_mod.vapid_claims_sub() == "mailto:noreply@4truck.us"
    monkeypatch.delenv("SMTP_FROM", raising=False)
    assert vapid_mod.vapid_claims_sub().startswith("mailto:")


# ── Render (short form) ──────────────────────────────────────────────

def test_render_is_short_with_url_and_tag():
    ch = WebPushChannel()
    p = ch.render(
        Recipient(account_id=1, type="user", id="7"),
        NotificationContent(title="Low fuel · Truck 22", body="x" * 500,
                            alert_type="fuel", severity="warning",
                            url="/alerts"))
    assert p.subject == "Low fuel · Truck 22"
    assert len(p.text) <= 181 and p.text.endswith("…")
    assert p.extra["url"] == "/alerts"
    assert p.extra["tag"] == "notif-fuel"
    assert p.extra["severity"] == "warning"


# ── Send: fan-out + prune (pywebpush mocked at _push_one's seam) ─────

def _wire(pg_db):
    """Patch the channel's db resolution to the test db."""
    return patch("infra.platform.get_platform_db", return_value=pg_db)


async def _send(pg_db, acct, uid):
    ch = notif.get_channel("web_push")
    rcpt = Recipient(account_id=acct, type="user", id=str(uid))
    payload = ch.render(rcpt, NotificationContent(title="T", alert_type="fuel"))
    with _wire(pg_db):
        return await ch.send(rcpt, payload)


async def test_send_no_devices_fails_closed(pg_db):
    acct, uid = await _seed(pg_db)
    res = await _send(pg_db, acct, uid)
    assert not res.ok and res.error == "no_devices"


async def test_send_fans_out_to_every_device(pg_db):
    acct, uid = await _seed(pg_db)
    await pg_db.add_push_subscription(acct, uid, **_sub(1))
    await pg_db.add_push_subscription(acct, uid, **_sub(2))

    sent = []
    def fake_push(sub, data, pem):
        sent.append(sub["endpoint"])
        return True, False

    with patch("capabilities.notifications.webpush._push_one", fake_push):
        res = await _send(pg_db, acct, uid)
    assert res.ok and res.provider_ref == "2"
    assert len(sent) == 2
    # Success stamps freshness on the device rows.
    subs = await pg_db.list_push_subscriptions(acct, uid)
    assert all(s["last_ok_at"] for s in subs)


async def test_send_prunes_dead_endpoint_and_still_delivers(pg_db):
    acct, uid = await _seed(pg_db)
    await pg_db.add_push_subscription(acct, uid, **_sub(1))   # will be dead
    await pg_db.add_push_subscription(acct, uid, **_sub(2))   # alive

    def fake_push(sub, data, pem):
        dead = sub["endpoint"].endswith("ep1")
        return (not dead, dead)

    with patch("capabilities.notifications.webpush._push_one", fake_push):
        res = await _send(pg_db, acct, uid)
    assert res.ok
    subs = await pg_db.list_push_subscriptions(acct, uid)
    assert [s["endpoint"] for s in subs] == [_sub(2)["endpoint"]]   # self-healed


async def test_send_all_failed_reports_failure_keeps_devices(pg_db):
    """A transient failure (push service 5xx) is NOT a dead endpoint —
    devices are kept for the next send."""
    acct, uid = await _seed(pg_db)
    await pg_db.add_push_subscription(acct, uid, **_sub(1))
    with patch("capabilities.notifications.webpush._push_one",
               lambda *a: (False, False)):
        res = await _send(pg_db, acct, uid)
    assert not res.ok and res.error == "all_failed"
    assert len(await pg_db.list_push_subscriptions(acct, uid)) == 1
