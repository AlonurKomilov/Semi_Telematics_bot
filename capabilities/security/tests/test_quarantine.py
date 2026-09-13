"""What a hold actually holds, and what it must never hold.

`quarantined` was a word the console offered and nothing enforced.  These
tests are the difference: each one names a way a held person could still
have reached the platform, or a way the hold could have reached someone
it had no business touching.
"""

from __future__ import annotations

import os

os.environ.setdefault("ENCRYPTION_KEY", "")

import pytest

from capabilities.security import quarantine as Q

pytestmark = pytest.mark.asyncio


@pytest.fixture(autouse=True)
def _fresh(monkeypatch):
    Q.forget()
    monkeypatch.setenv("QUARANTINE_ENFORCEMENT_ENABLED", "1")
    yield
    Q.forget()


# ── the switch ────────────────────────────────────────────────────

async def test_the_hold_does_nothing_until_it_is_switched_on(monkeypatch):
    """This can lock out a paying customer, so it lands dark and an
    operator turns it on — the same shape billing enforcement took."""
    monkeypatch.delenv("QUARANTINE_ENFORCEMENT_ENABLED", raising=False)
    assert Q.enabled() is False
    monkeypatch.setenv("QUARANTINE_ENFORCEMENT_ENABLED", "0")
    assert Q.enabled() is False
    monkeypatch.setenv("QUARANTINE_ENFORCEMENT_ENABLED", "1")
    assert Q.enabled() is True


# ── what stays open, and why each one ─────────────────────────────

@pytest.mark.parametrize("path,reason", [
    ("/api/auth/login", "they must be able to arrive to be told"),
    ("/api/auth/logout", "and to leave"),
    ("/api/auth/refresh", "a live page must survive long enough to read"),
    ("/api/user/me", "the banner has to be able to name them"),
    ("/api/billing/invoices", "a held account is still a billed account"),
    ("/api/billing/portal", "blocking payment while charging is indefensible"),
    ("/api/health", "monitors"),
    # The prefix varies — /api and /api/v1 both have to hit.
    ("/api/v1/auth/login", "both API prefixes"),
    ("/api/v1/user/me", "both API prefixes"),
])
def test_these_stay_open_to_a_held_person(path, reason):
    assert Q.is_open_path(path) is True, reason


@pytest.mark.parametrize("path", [
    "/api/user/preferences", "/api/loads", "/api/reports/export",
    "/api/drivers", "/api/work-orders", "/api/kpi/fleet",
    "/api/applications/apply", "/api/system/accounts",
])
def test_everything_else_is_refused(path):
    assert Q.is_open_path(path) is False


@pytest.mark.parametrize("path", [
    # The operator console's own probe, which a suffix match on
    # "/health" was already exempting by accident.
    "/api/system/health",
    # ...and the shapes that would join it the day somebody adds them.
    "/api/vehicles/7/health",
    "/api/accounts/3/user/me",
    "/api/bots/4/version",
])
def test_a_path_that_merely_ENDS_like_an_open_one_is_not_open(path):
    """The list says which endpoints are open, not which endings are.
    A suffix match is open to anything that happens to finish the same
    way, and nothing would catch the drift."""
    assert Q.is_open_path(path) is False


def test_a_trailing_slash_is_the_same_route():
    """Refusing a held person their own profile over a slash would
    leave them at a page that cannot explain itself."""
    assert Q.is_open_path("/api/user/me/") is True
    assert Q.is_open_path("/api/v1/user/me/") is True


def test_the_inbound_webhooks_are_never_held():
    """These are Stripe and Resend talking to US, not the subject
    talking to us.  Refusing them holds nobody and corrupts our own
    billing and bounce state instead."""
    for path in ("/api/billing/webhook", "/api/billing/stripe/webhook",
                 "/api/webhooks/resend"):
        assert Q.is_open_path(path) is True


# ── the standing itself ───────────────────────────────────────────

async def test_a_held_person_is_held_and_everyone_else_is_not(seeded_db, monkeypatch):
    db = seeded_db["db"]
    monkeypatch.setattr("infra.platform.get_platform_db", lambda: db)
    owner = (await db.list_account_users(seeded_db["account"].id))[0]

    assert await Q.is_held(owner.id) is False
    await db.update_user(owner.id, security="quarantined")
    Q.forget(owner.id)
    assert await Q.is_held(owner.id) is True

    # monitored is NOT a hold: it records and changes nothing
    await db.update_user(owner.id, security="monitored")
    Q.forget(owner.id)
    assert await Q.is_held(owner.id) is False


async def test_nobody_is_held_when_there_is_nobody(seeded_db):
    assert await Q.is_held(None) is False
    assert await Q.is_held(999999) is False


async def test_a_database_hiccup_does_not_lock_out_the_platform(monkeypatch):
    """Fail-open, deliberately.  A hold that is not in force is a gap an
    operator can see and act on; a platform that refuses every customer
    because Postgres blinked is an outage we caused ourselves."""
    import infra.platform as plat

    def boom():
        raise RuntimeError("no database")
    monkeypatch.setattr(plat, "get_platform_db", boom)
    assert await Q.is_held(1) is False
    # ...and the failure is NOT cached, so the next call tries again
    assert 1 not in Q._cache


async def test_the_operators_click_takes_effect_now_not_in_a_minute(seeded_db, monkeypatch):
    """The answer is cached for sixty seconds because it is asked on
    every request.  An operator's decision must not wait for it."""
    db = seeded_db["db"]
    monkeypatch.setattr("infra.platform.get_platform_db", lambda: db)
    owner = (await db.list_account_users(seeded_db["account"].id))[0]
    assert await Q.is_held(owner.id) is False      # now cached as not-held

    await db.update_user(owner.id, security="quarantined")
    assert await Q.is_held(owner.id) is False, "the cache is doing its job"
    Q.forget(owner.id)                             # what the PATCH handler does
    assert await Q.is_held(owner.id) is True


# ── ending the live sessions ──────────────────────────────────────

async def test_holding_someone_ends_the_sessions_they_already_have(seeded_db):
    """Without this the hold would begin at their next sign-in — eight
    hours away, thirty days if they ticked "remember me".  That is the
    hole the account-suspension gate has carried since it was written."""
    db = seeded_db["db"]
    acct = seeded_db["account"]
    owner = (await db.list_account_users(acct.id))[0]

    pushed: list[str] = []
    import interfaces.api.auth as auth_mod

    async def fake_push(jti, expires_at=None):
        pushed.append(jti)
    orig = auth_mod.mark_jti_revoked
    auth_mod.mark_jti_revoked = fake_push
    try:
        from datetime import datetime, timedelta, timezone
        later = (datetime.now(timezone.utc) + timedelta(hours=8)).isoformat()
        now = datetime.now(timezone.utc).isoformat()
        for jti in ("live-a", "live-b"):
            await db.create_user_session(
                user_id=owner.id, jti=jti, device_label="test",
                user_agent="t", ip="1.2.3.4",
                created_at=now, last_seen=now, expires_at=later)

        ended = await Q.hold_sessions(db, owner.id)
        assert ended == 2, "both live sessions end"
        assert set(pushed) == {"live-a", "live-b"}, (
            "each jti reaches the denylist the request path checks"
        )
        left = await db.list_user_sessions(owner.id)
        assert left == [], "and none is left active"
    finally:
        auth_mod.mark_jti_revoked = orig


async def test_a_hold_with_no_sessions_is_still_a_hold(seeded_db):
    db = seeded_db["db"]
    owner = (await db.list_account_users(seeded_db["account"].id))[0]
    assert await Q.hold_sessions(db, owner.id) == 0


# ── telling the owner ─────────────────────────────────────────────

async def test_the_owner_is_told_by_name_and_not_told_why(seeded_db):
    """They start receiving the held person's alerts, so they are owed
    an explanation — but an operator opens a review on a SUSPICION, the
    owner may repeat what they read, and a suspicion repeated as a
    finding is how an innocent employee gets fired over our wording."""
    db = seeded_db["db"]
    acct = seeded_db["account"]
    owner = (await db.list_account_users(acct.id))[0]
    await db._db.execute(
        "UPDATE users SET is_primary_owner = 1 WHERE id = ?", (owner.id,))
    await db._db.commit()

    from adapters.storage import Role
    person = await db.create_user_with_email(
        email="aziz@premiertruckinggroup.com", password_hash="x",
        account_id=acct.id, role=Role.DISPATCHER, display_name="Aziz")

    sent: list = []
    import capabilities.security.quarantine as qmod
    import capabilities.notifications.service as svc
    orig = svc.notify_user

    async def fake_notify(db_, account_id, user_id, content, **kw):
        sent.append((account_id, user_id, content))
        return []
    svc.notify_user = fake_notify
    try:
        told = await qmod.tell_the_owner(
            db, account_id=acct.id, person=person, ended=2)
    finally:
        svc.notify_user = orig

    assert told is True
    assert len(sent) == 1, "said once, not pasted onto every later alert"
    account_id, target_id, content = sent[0]
    assert account_id == acct.id
    assert target_id == owner.id, "the owner, not the held person"
    said = f"{content.title}\n{content.body}"
    assert "Aziz" in said, "named, so the owner knows who"
    assert "under review" in said, "and what kind of thing this is"
    assert "come to you" in said, "and that the work is covered"
    lowered = (content.title + content.body).lower()
    for accusation in ("suspicious", "attack", "probing", "breach",
                       "malicious", "fraud"):
        assert accusation not in lowered, (
            f"{accusation!r} states a finding we do not have"
        )


async def test_holding_the_owner_themselves_tells_nobody(seeded_db):
    """There is nobody above them to hand the work to, and telling them
    about their own hold is the login banner's job."""
    db = seeded_db["db"]
    acct = seeded_db["account"]
    owner = (await db.list_account_users(acct.id))[0]
    await db._db.execute(
        "UPDATE users SET is_primary_owner = 1 WHERE id = ?", (owner.id,))
    await db._db.commit()
    fresh = await db.get_user_by_id(owner.id)

    sent: list = []
    import capabilities.notifications.service as svc
    orig = svc.notify_user

    async def fake_notify(*a, **kw):
        sent.append(a)
        return []
    svc.notify_user = fake_notify
    try:
        told = await Q.tell_the_owner(
            db, account_id=acct.id, person=fresh, ended=0)
    finally:
        svc.notify_user = orig
    assert told is False
    assert sent == []


async def test_a_notice_that_fails_does_not_undo_the_hold(seeded_db):
    """The standing is already written. A hold whose notice failed is
    still a hold — it must not raise its way back out."""
    db = seeded_db["db"]
    acct = seeded_db["account"]
    person = (await db.list_account_users(acct.id))[0]

    class Broken:
        async def get_primary_owner(self, _):
            raise RuntimeError("down")

    assert await Q.tell_the_owner(
        Broken(), account_id=acct.id, person=person, ended=1) is False


# ── the hold as a request actually meets it ───────────────────────

@pytest.fixture
async def api(pg_db, seeded_db, monkeypatch):
    import infra.platform as plat
    monkeypatch.setattr(plat, "_db", pg_db, raising=False)
    monkeypatch.setattr("infra.platform.get_platform_db", lambda: seeded_db["db"])
    from interfaces.api.app import create_api
    return create_api(), seeded_db["db"], seeded_db["account"]


def _token_for(user_id: int, account_id: int) -> str:
    from interfaces.api.auth import create_jwt
    return create_jwt(100001, account_id, "owner", user_id=user_id, jti="t-1")


async def _get(app, path, token):
    from httpx import ASGITransport, AsyncClient
    async with AsyncClient(transport=ASGITransport(app=app),
                           base_url="http://t") as c:
        return await c.get(path, headers={"Authorization": f"Bearer {token}"})


async def test_a_held_person_is_refused_at_the_door(api):
    app, db, acct = api
    person = (await db.list_account_users(acct.id))[0]
    await db.update_user(person.id, security="quarantined")
    Q.forget(person.id)
    token = _token_for(person.id, acct.id)

    r = await _get(app, "/api/user/preferences", token)
    assert r.status_code == 403, r.text
    body = r.json()
    assert body["error_code"] == "user_quarantined"
    assert "under review" in body["detail"]
    # ...and it says nothing about their company, which is unaffected
    assert "unaffected" in body["detail"]


async def test_a_held_person_can_still_be_told_who_they_are(api):
    """/user/me is what the banner reads. Refusing it would leave a held
    person staring at a page that cannot explain itself."""
    app, db, acct = api
    person = (await db.list_account_users(acct.id))[0]
    await db.update_user(person.id, security="quarantined")
    Q.forget(person.id)

    r = await _get(app, "/api/user/me", _token_for(person.id, acct.id))
    assert r.status_code == 200, r.text
    assert r.json()["security"] == "quarantined", (
        "read live, so the banner is right the moment the standing changes"
    )


async def test_nobody_else_is_touched_by_one_persons_hold(api):
    """The whole reason this is per-person: a colleague in the same
    account keeps working."""
    app, db, acct = api
    held = (await db.list_account_users(acct.id))[0]
    from adapters.storage import Role
    colleague = await db.create_user_with_email(
        email="clean@premiertruckinggroup.com", password_hash="x",
        account_id=acct.id, role=Role.DISPATCHER, display_name="Clean")
    await db.update_user(held.id, security="quarantined")
    Q.forget()

    assert (await _get(app, "/api/user/preferences",
                       _token_for(held.id, acct.id))).status_code == 403
    assert (await _get(app, "/api/user/preferences",
                       _token_for(colleague.id, acct.id))).status_code != 403
    fresh = await db.get_account(acct.id)
    assert (fresh.security or "normal") == "normal", "the company is untouched"


async def test_the_switch_off_means_off(api, monkeypatch):
    """It lands dark. Until an operator turns it on, a quarantined
    person behaves exactly as before — which is what makes shipping it
    safe."""
    app, db, acct = api
    person = (await db.list_account_users(acct.id))[0]
    await db.update_user(person.id, security="quarantined")
    Q.forget(person.id)
    monkeypatch.setenv("QUARANTINE_ENFORCEMENT_ENABLED", "0")

    held = await _get(app, "/api/user/preferences", _token_for(person.id, acct.id))
    assert held.status_code != 403
    # ...and specifically: the SAME answer an unheld person gets, so this
    # cannot pass just because the route happened to redirect.
    from adapters.storage import Role
    other = await db.create_user_with_email(
        email="off@premiertruckinggroup.com", password_hash="x",
        account_id=acct.id, role=Role.DISPATCHER, display_name="Off")
    clean = await _get(app, "/api/user/preferences", _token_for(other.id, acct.id))
    assert held.status_code == clean.status_code


# ── the held person as a RECIPIENT ────────────────────────────────
#
# A hold that covered only their requests would leave the platform
# mailing them safety alerts, a daily digest and their scheduled
# reports every morning while we believe they are a problem. That is
# not a hold, it is a redirect.

async def _reroute(db, account_id, subs, channel="telegram_dm"):
    from capabilities.notifications.service import _reroute_quarantined
    return await _reroute_quarantined(db, account_id, subs, channel)


def _user_sub(uid: int, address: str = "x"):
    return {"recipient_type": "user", "recipient_id": uid,
            "address": address, "cadence": "immediate"}


async def test_a_held_person_stops_receiving_and_the_owner_covers(seeded_db, monkeypatch):
    db = seeded_db["db"]
    acct = seeded_db["account"]
    owner = (await db.list_account_users(acct.id))[0]
    await db._db.execute(
        "UPDATE users SET is_primary_owner = 1 WHERE id = ?", (owner.id,))
    await db._db.commit()
    monkeypatch.setattr("infra.platform.get_platform_db", lambda: db)

    from adapters.storage import Role
    person = await db.create_user_with_email(
        email="held@premiertruckinggroup.com", password_hash="x",
        account_id=acct.id, role=Role.DISPATCHER, display_name="Held")
    await db.update_user(person.id, security="quarantined")
    Q.forget()

    async def fake_channel(account_id, rtype, uid, key):
        return {"address": "owner@x", "verified": 1, "enabled_master": 1}
    monkeypatch.setattr(db, "get_notification_channel", fake_channel,
                        raising=False)

    out = await _reroute(db, acct.id, [_user_sub(person.id)])
    ids = [int(s["recipient_id"]) for s in out]
    assert person.id not in ids, "the held person is out"
    assert ids == [owner.id], "and the owner is in, exactly once"


async def test_holding_a_solo_owner_does_not_hand_them_their_own_alerts(
        seeded_db, monkeypatch):
    """The sole-owner case. There is nobody above them to cover the
    work, and substituting them for themselves would make the hold a
    no-op on the one axis this function exists for."""
    db = seeded_db["db"]
    acct = seeded_db["account"]
    owner = (await db.list_account_users(acct.id))[0]
    await db._db.execute(
        "UPDATE users SET is_primary_owner = 1 WHERE id = ?", (owner.id,))
    await db._db.commit()
    monkeypatch.setattr("infra.platform.get_platform_db", lambda: db)
    await db.update_user(owner.id, security="quarantined")
    Q.forget()

    # Their channel IS connected — so if they come back into the list it
    # is the missing self-check that put them there, not a lucky
    # failure to resolve an address.
    async def fake_channel(account_id, rtype, uid, key):
        return {"address": "owner@x", "verified": 1, "enabled_master": 1}
    monkeypatch.setattr(db, "get_notification_channel", fake_channel,
                        raising=False)

    out = await _reroute(db, acct.id, [_user_sub(owner.id)])
    assert out == [], "held, and not covered by themselves"
    assert owner.id not in [int(x["recipient_id"]) for x in out]


async def test_the_owner_is_not_sent_two_copies(seeded_db, monkeypatch):
    """They were already a recipient — a substitute on top would deliver
    the same alert twice."""
    db = seeded_db["db"]
    acct = seeded_db["account"]
    owner = (await db.list_account_users(acct.id))[0]
    await db._db.execute(
        "UPDATE users SET is_primary_owner = 1 WHERE id = ?", (owner.id,))
    await db._db.commit()
    monkeypatch.setattr("infra.platform.get_platform_db", lambda: db)

    from adapters.storage import Role
    person = await db.create_user_with_email(
        email="held2@premiertruckinggroup.com", password_hash="x",
        account_id=acct.id, role=Role.DISPATCHER, display_name="Held2")
    await db.update_user(person.id, security="quarantined")
    Q.forget()

    out = await _reroute(db, acct.id,
                         [_user_sub(person.id), _user_sub(owner.id)])
    ids = [int(s["recipient_id"]) for s in out]
    assert ids == [owner.id], "one copy, not two"


async def test_a_shared_topic_is_never_touched(seeded_db, monkeypatch):
    """Group topics have no person behind them to hold."""
    db = seeded_db["db"]
    acct = seeded_db["account"]
    monkeypatch.setattr("infra.platform.get_platform_db", lambda: db)
    topic = {"recipient_type": "topic", "recipient_id": "-100:7",
             "address": "-100:7", "cadence": "immediate"}
    assert await _reroute(db, acct.id, [topic]) == [topic]


async def test_nothing_moves_when_nobody_is_held(seeded_db, monkeypatch):
    db = seeded_db["db"]
    acct = seeded_db["account"]
    owner = (await db.list_account_users(acct.id))[0]
    monkeypatch.setattr("infra.platform.get_platform_db", lambda: db)
    subs = [_user_sub(owner.id)]
    assert await _reroute(db, acct.id, subs) is subs, (
        "the untouched list is returned as-is, not rebuilt"
    )


async def test_the_reroute_is_off_while_the_switch_is_off(seeded_db, monkeypatch):
    db = seeded_db["db"]
    acct = seeded_db["account"]
    owner = (await db.list_account_users(acct.id))[0]
    monkeypatch.setattr("infra.platform.get_platform_db", lambda: db)
    await db.update_user(owner.id, security="quarantined")
    Q.forget()
    monkeypatch.setenv("QUARANTINE_ENFORCEMENT_ENABLED", "0")
    subs = [_user_sub(owner.id)]
    assert await _reroute(db, acct.id, subs) is subs


async def test_an_uncovered_hold_is_logged_not_silent(seeded_db, monkeypatch, caplog):
    """When the owner cannot be reached, somebody's alerts are going
    unread — that must be findable, not swallowed."""
    db = seeded_db["db"]
    acct = seeded_db["account"]
    owner = (await db.list_account_users(acct.id))[0]
    await db._db.execute(
        "UPDATE users SET is_primary_owner = 1 WHERE id = ?", (owner.id,))
    await db._db.commit()
    monkeypatch.setattr("infra.platform.get_platform_db", lambda: db)

    from adapters.storage import Role
    person = await db.create_user_with_email(
        email="held3@premiertruckinggroup.com", password_hash="x",
        account_id=acct.id, role=Role.DISPATCHER, display_name="Held3")
    await db.update_user(person.id, security="quarantined")
    Q.forget()

    async def no_channel(account_id, rtype, uid, key):
        return None                     # the owner has nothing connected
    monkeypatch.setattr(db, "get_notification_channel", no_channel,
                        raising=False)

    import logging
    with caplog.at_level(logging.INFO, logger="capabilities.notifications.service"):
        out = await _reroute(db, acct.id, [_user_sub(person.id)])
    assert out == []
    assert any("not covered" in r.getMessage() for r in caplog.records), caplog.text
