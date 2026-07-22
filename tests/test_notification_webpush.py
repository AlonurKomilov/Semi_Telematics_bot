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


# ── Endpoint validation (the anti-SSRF gate) ─────────────────────────

def test_validator_rejects_non_https_userinfo_and_private(monkeypatch):
    from capabilities.notifications import push_endpoint as pe

    monkeypatch.setattr(pe, "_resolve_host", lambda h, p: ["93.184.216.34"])
    assert pe.validate_push_endpoint("https://fcm.googleapis.com/wp/x") is None
    assert pe.validate_push_endpoint("http://fcm.googleapis.com/x") == "bad_endpoint"
    assert pe.validate_push_endpoint("https://a:b@evil.example/x") == "bad_endpoint"
    assert pe.validate_push_endpoint("") == "bad_endpoint"
    assert pe.validate_push_endpoint("https://x/" + "a" * 1100) == "bad_endpoint"

    # ANY private/loopback/link-local/metadata answer → rejected.
    for ip in ("10.0.0.5", "127.0.0.1", "169.254.169.254", "192.168.1.1",
               "fd00::1", "100.64.0.1"):
        monkeypatch.setattr(pe, "_resolve_host", lambda h, p, ip=ip: [ip])
        assert pe.validate_push_endpoint("https://host.example/x") == "private_endpoint", ip

    # Mixed public+private (rebinding-style) → still rejected.
    monkeypatch.setattr(pe, "_resolve_host",
                        lambda h, p: ["93.184.216.34", "10.0.0.5"])
    assert pe.validate_push_endpoint("https://host.example/x") == "private_endpoint"

    # NAT64-embedded internal IPv4 (64:ff9b::169.254.169.254) reads as
    # is_global True but decodes to the metadata address → rejected.
    monkeypatch.setattr(pe, "_resolve_host", lambda h, p: ["64:ff9b::a9fe:a9fe"])
    assert pe.validate_push_endpoint("https://host.example/x") == "private_endpoint"
    # NAT64-embedded PUBLIC v4 (64:ff9b::93.184.216.34) is legitimately fine.
    monkeypatch.setattr(pe, "_resolve_host", lambda h, p: ["64:ff9b::5db8:d822"])
    assert pe.validate_push_endpoint("https://host.example/x") is None

    monkeypatch.setattr(pe, "_resolve_host",
                        lambda h, p: (_ for _ in ()).throw(OSError("nx")))
    assert pe.validate_push_endpoint("https://nx.example/x") == "unresolvable"


async def test_send_time_validation_prunes_now_private_endpoint(pg_db):
    """DNS-rebinding blunt: an endpoint that turned private after
    subscribe is refused at send time AND pruned as dead."""
    acct, uid = await _seed(pg_db)
    await pg_db.add_push_subscription(acct, uid, **_sub(1))
    with patch("capabilities.notifications.push_endpoint._resolve_host",
               lambda h, p: ["10.0.0.5"]):
        res = await _send(pg_db, acct, uid)
    assert not res.ok
    assert await pg_db.list_push_subscriptions(acct, uid) == []   # pruned


# ── Reassign-with-repair (shared cab tablet) ─────────────────────────

async def test_endpoint_reassignment_repairs_previous_owner(pg_db):
    """Push API reuses one subscription per browser: on a shared tablet
    the endpoint legitimately moves to whoever enabled last.  The
    dispossessed owner must NOT stay 'verified' with zero devices — that
    would be a silent notification blackout."""
    acct_a = (await pg_db.create_account("Tablet Co A")).id
    user_a = (await pg_db.create_user(telegram_id=9401, account_id=acct_a,
                                      role=Role.FLEET)).id
    acct_b = (await pg_db.create_account("Tablet Co B")).id
    user_b = (await pg_db.create_user(telegram_id=9402, account_id=acct_b,
                                      role=Role.FLEET)).id

    # A owns the endpoint; channel verified.
    moved = await pg_db.add_push_subscription(acct_a, user_a, **_sub(7))
    assert moved is None
    await pg_db.upsert_notification_channel(
        acct_a, "user", user_a, "web_push", address="devices", verified=True)

    # B (different ACCOUNT — same physical browser) takes the endpoint.
    moved = await pg_db.add_push_subscription(acct_b, user_b, **_sub(7))
    assert moved == {"account_id": acct_a, "user_id": user_a}

    # Row moved to B; A has zero devices and their channel un-verified —
    # the delivery gate closes honestly instead of silently failing.
    assert await pg_db.list_push_subscriptions(acct_a, user_a) == []
    assert len(await pg_db.list_push_subscriptions(acct_b, user_b)) == 1
    ch_a = await pg_db.get_notification_channel(acct_a, "user", user_a, "web_push")
    assert ch_a["verified"] is False

    # Same-user re-subscribe is NOT a move.
    assert await pg_db.add_push_subscription(acct_b, user_b, **_sub(7)) is None


async def test_reassignment_keeps_owner_verified_with_other_devices(pg_db):
    """Losing ONE of several devices must not un-verify the channel."""
    acct, uid = await _seed(pg_db)
    other = (await pg_db.create_user(telegram_id=9403, account_id=acct,
                                     role=Role.FLEET)).id
    await pg_db.add_push_subscription(acct, uid, **_sub(1))
    await pg_db.add_push_subscription(acct, uid, **_sub(2))
    await pg_db.upsert_notification_channel(
        acct, "user", uid, "web_push", address="devices", verified=True)

    moved = await pg_db.add_push_subscription(acct, other, **_sub(1))
    assert moved == {"account_id": acct, "user_id": uid}
    ch = await pg_db.get_notification_channel(acct, "user", uid, "web_push")
    assert ch["verified"] is True     # still one device left


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


def test_push_one_passes_a_no_redirect_session():
    """Anti-SSRF: the real send hands pywebpush a session that refuses
    redirects, so a 'public' endpoint can't 302 the server-side POST to an
    internal host past the is_global gate."""
    from capabilities.notifications import webpush as wp
    captured: dict = {}

    def fake_webpush(**kw):
        captured.update(kw)
        return object()

    with patch("pywebpush.webpush", fake_webpush), \
         patch("capabilities.notifications.push_endpoint.validate_push_endpoint",
               return_value=None), \
         patch("capabilities.notifications.vapid.vapid_claims_sub",
               return_value="mailto:x@y.z"):
        ok, dead = wp._push_one(_sub(1), "{}", "PEM")

    assert ok and not dead
    assert captured["requests_session"].max_redirects == 0


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
