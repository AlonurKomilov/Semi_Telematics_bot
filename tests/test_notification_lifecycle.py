"""Email channel connection lifecycle (phase 4b): signed tokens, the
address-guarded verify / master-off disable storage methods, and the
end-to-end connect → verify → subscribe → unsubscribe loop proven through
the SAME delivery gate (get_notification_subscribers) the dispatcher uses.

Email is the first channel whose address must be PROVEN before it can
receive anything — these lock down that gate.
"""

from __future__ import annotations

import os

os.environ.setdefault("ENCRYPTION_KEY", "")
os.environ.setdefault("JWT_SECRET", "test-signing-secret-at-least-32-chars-long")

from capabilities.notifications import tokens
from capabilities.notifications.lifecycle import (
    apply_unsubscribe,
    confirm_channel_verification,
    start_email_connection,
    unsubscribe_url,
)
from capabilities.notifications.tokens import (
    UNSUB_PURPOSE,
    VERIFY_PURPOSE,
    make_token,
)
from capabilities.permissions.roles import Role


# ── Signed tokens ────────────────────────────────────────────────────

def test_token_roundtrip_and_expiry():
    t = make_token(VERIFY_PURPOSE, account_id=1, recipient_type="user",
                   recipient_id="7", channel="email", address="a@b.com",
                   ttl_seconds=3600)
    p = tokens.verify_token(VERIFY_PURPOSE, t)
    assert p and p["a"] == 1 and p["ad"] == "a@b.com" and p["ch"] == "email"

    expired = make_token(VERIFY_PURPOSE, account_id=1, recipient_type="user",
                         recipient_id="7", channel="email", ttl_seconds=-1)
    assert tokens.verify_token(VERIFY_PURPOSE, expired) is None


def test_token_purpose_separation():
    """A verify token must NOT validate as an unsubscribe token even
    though both share the signing key."""
    t = make_token(VERIFY_PURPOSE, account_id=1, recipient_type="user",
                   recipient_id="7", channel="email", ttl_seconds=3600)
    assert tokens.verify_token(UNSUB_PURPOSE, t) is None


def test_token_tamper_is_rejected():
    t = make_token(UNSUB_PURPOSE, account_id=1, recipient_type="user",
                   recipient_id="7", channel="email")
    body, _, sig = t.partition(".")
    assert tokens.verify_token(UNSUB_PURPOSE, f"{body}.{sig}x") is None   # bad sig
    assert tokens.verify_token(UNSUB_PURPOSE, f"{body}xx.{sig}") is None  # bad body
    assert tokens.verify_token(UNSUB_PURPOSE, "garbage") is None
    assert tokens.verify_token(UNSUB_PURPOSE, "") is None


def test_signing_falls_back_to_jwt_secret(monkeypatch):
    """No dedicated NOTIFICATION_SIGNING_SECRET → sign with JWT_SECRET, so
    a deployment needs zero extra config.  A dedicated secret, when set,
    takes over (and invalidates tokens signed under the fallback)."""
    monkeypatch.delenv("NOTIFICATION_SIGNING_SECRET", raising=False)
    monkeypatch.setenv("JWT_SECRET", "jwt-only-secret-at-least-32-chars-long!!")
    assert tokens.signing_secret_ok() is True
    t = make_token(UNSUB_PURPOSE, account_id=1, recipient_type="user",
                   recipient_id="7", channel="email")
    assert tokens.verify_token(UNSUB_PURPOSE, t) is not None    # fallback signs+verifies

    # A dedicated secret takes precedence → the JWT-signed token no longer verifies.
    monkeypatch.setenv("NOTIFICATION_SIGNING_SECRET",
                       "dedicated-secret-at-least-32-chars-long!!")
    assert tokens.verify_token(UNSUB_PURPOSE, t) is None


def test_unsubscribe_token_does_not_expire():
    """The one-click link lives in an inbox forever — it must keep working
    (no ttl)."""
    url = unsubscribe_url(1, "user", "7", "email")
    token = url.split("token=", 1)[1]
    p = tokens.verify_token(UNSUB_PURPOSE, token)
    assert p and "exp" not in p


# ── Storage methods ──────────────────────────────────────────────────

async def _seed(pg_db):
    acct = await pg_db.create_account("Lifecycle Co")
    u = await pg_db.create_user(telegram_id=8101, account_id=acct.id, role=Role.FLEET)
    return acct.id, u.id


async def test_verify_is_address_guarded(pg_db):
    """Verifying only counts if the STORED address still matches — a link
    for an old address can't verify a changed one."""
    acct, uid = await _seed(pg_db)
    await pg_db.upsert_notification_channel(
        acct, "user", uid, "email", address="new@x.com", verified=False)

    # A link issued for the OLD address does nothing now.
    assert await pg_db.verify_notification_channel(
        acct, "user", uid, "email", "old@x.com") is False
    assert (await pg_db.get_notification_channel(
        acct, "user", uid, "email"))["verified"] is False

    # The link for the current address verifies it.
    assert await pg_db.verify_notification_channel(
        acct, "user", uid, "email", "new@x.com") is True
    assert (await pg_db.get_notification_channel(
        acct, "user", uid, "email"))["verified"] is True


async def test_disable_keeps_address_and_prefs(pg_db):
    acct, uid = await _seed(pg_db)
    await pg_db.upsert_notification_channel(
        acct, "user", uid, "email", address="me@x.com", verified=True)
    assert await pg_db.disable_notification_channel(acct, "user", uid, "email") is True
    ch = await pg_db.get_notification_channel(acct, "user", uid, "email")
    assert ch["enabled_master"] is False and ch["address"] == "me@x.com"


# ── End-to-end lifecycle through the delivery gate ───────────────────

async def test_connect_verify_subscribe_unsubscribe(pg_db):
    acct, uid = await _seed(pg_db)
    # A pref exists, but the address isn't connected yet → no subscriber.
    await pg_db.set_notification_pref(acct, "user", uid, "email", "fuel", enabled=True)
    assert await pg_db.get_notification_subscribers(acct, "fuel", "email") == []

    # Connect: stores the address UNVERIFIED (SMTP off in tests → sent False,
    # ok True) and still not a subscriber until proven.
    res = await start_email_connection(pg_db, acct, uid, "me@x.com")
    assert res["ok"] is True and res["sent"] is False
    assert await pg_db.get_notification_subscribers(acct, "fuel", "email") == []

    # Verify with the RIGHT address token → becomes a subscriber.
    good = make_token(VERIFY_PURPOSE, account_id=acct, recipient_type="user",
                      recipient_id=uid, channel="email", address="me@x.com",
                      ttl_seconds=3600)
    assert await confirm_channel_verification(pg_db, good) is not None
    subs = await pg_db.get_notification_subscribers(acct, "fuel", "email")
    assert [s["recipient_id"] for s in subs] == [str(uid)]
    assert subs[0]["address"] == "me@x.com"

    # Unsubscribe (one-click) → master off → drops out, address kept.
    unsub = make_token(UNSUB_PURPOSE, account_id=acct, recipient_type="user",
                       recipient_id=uid, channel="email")
    assert await apply_unsubscribe(pg_db, unsub) is True
    assert await pg_db.get_notification_subscribers(acct, "fuel", "email") == []
    assert (await pg_db.get_notification_channel(
        acct, "user", uid, "email"))["address"] == "me@x.com"


async def test_confirm_rejects_wrong_address_token(pg_db):
    """A validly-signed token for a DIFFERENT address must not verify the
    connected one."""
    acct, uid = await _seed(pg_db)
    await start_email_connection(pg_db, acct, uid, "real@x.com")
    forged = make_token(VERIFY_PURPOSE, account_id=acct, recipient_type="user",
                        recipient_id=uid, channel="email", address="attacker@x.com",
                        ttl_seconds=3600)
    assert await confirm_channel_verification(pg_db, forged) is None
    assert (await pg_db.get_notification_channel(
        acct, "user", uid, "email"))["verified"] is False


async def test_confirm_and_unsubscribe_reject_bad_tokens(pg_db):
    acct, uid = await _seed(pg_db)
    assert await confirm_channel_verification(pg_db, "garbage") is None
    assert await apply_unsubscribe(pg_db, "garbage") is False


async def test_connect_rejects_bad_email(pg_db):
    acct, uid = await _seed(pg_db)
    for bad in ("not-an-email", "a@b", "a@b.c\r\nBcc: x@evil.com", ""):
        res = await start_email_connection(pg_db, acct, uid, bad)
        assert res["ok"] is False and res["error"] == "bad_email", bad
    assert await pg_db.get_notification_channel(acct, "user", uid, "email") is None


async def test_reconnect_same_verified_address_is_noop(pg_db):
    """Re-saving the same, already-verified address must NOT un-verify it
    (a settings-form re-save can't silently drop delivery)."""
    acct, uid = await _seed(pg_db)
    await start_email_connection(pg_db, acct, uid, "me@x.com")
    good = make_token(VERIFY_PURPOSE, account_id=acct, recipient_type="user",
                      recipient_id=uid, channel="email", address="me@x.com",
                      ttl_seconds=3600)
    assert await confirm_channel_verification(pg_db, good) is not None

    res = await start_email_connection(pg_db, acct, uid, "me@x.com")
    assert res.get("already_verified") is True and res["sent"] is False
    assert (await pg_db.get_notification_channel(
        acct, "user", uid, "email"))["verified"] is True    # still verified
