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

from system.security import quarantine as Q

pytestmark = pytest.mark.asyncio


@pytest.fixture(autouse=True)
def _fresh(monkeypatch):
    """The customer layers ask ``infra.policy``; the system layer fills
    those slots at boot.  These tests exercise the hold THROUGH the
    customer layers, so they install what the entrypoints install."""
    from system import bootstrap
    Q.forget(); Q.forget_account()
    monkeypatch.setenv("QUARANTINE_ENFORCEMENT_ENABLED", "1")
    bootstrap.reset_for_tests(); bootstrap.install()
    yield
    bootstrap.reset_for_tests()
    Q.forget(); Q.forget_account()


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
    import system.security.quarantine as qmod
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


# ── holding a whole company ───────────────────────────────────────
#
# The heavier instrument by far: it refuses everyone in the account,
# most of whom nobody accused. What it reaches that the person hold
# cannot is the public links — no JWT, owned by the company, collecting
# strangers' FMCSA data.

async def test_a_held_account_holds_everyone_in_it(seeded_db, monkeypatch):
    db = seeded_db["db"]
    acct = seeded_db["account"]
    monkeypatch.setattr("infra.platform.get_platform_db", lambda: db)
    person = (await db.list_account_users(acct.id))[0]

    assert await Q.is_request_held(person.id, acct.id) is False
    await db.update_account(acct.id, security="quarantined")
    Q.forget(); Q.forget_account()

    assert await Q.is_account_held(acct.id) is True
    assert await Q.is_request_held(person.id, acct.id) is True
    assert await Q.is_held(person.id) is False, (
        "the PERSON was never accused — only their employer"
    )


async def test_the_two_holds_are_independent(seeded_db, monkeypatch):
    """Neither implies the other. A person held inside a clean company
    and a clean person inside a held company are both real, and the
    banner has to be able to tell them apart."""
    db = seeded_db["db"]
    acct = seeded_db["account"]
    monkeypatch.setattr("infra.platform.get_platform_db", lambda: db)
    person = (await db.list_account_users(acct.id))[0]

    await db.update_user(person.id, security="quarantined")
    Q.forget(); Q.forget_account()
    assert await Q.is_held(person.id) is True
    assert await Q.is_account_held(acct.id) is False

    await db.update_user(person.id, security="normal")
    await db.update_account(acct.id, security="quarantined")
    Q.forget(); Q.forget_account()
    assert await Q.is_held(person.id) is False
    assert await Q.is_account_held(acct.id) is True


async def test_monitoring_a_company_is_not_holding_it(seeded_db, monkeypatch):
    """`monitored` must stay invisible to the account — that is what
    makes it safe for the detector to apply on its own."""
    db = seeded_db["db"]
    acct = seeded_db["account"]
    monkeypatch.setattr("infra.platform.get_platform_db", lambda: db)
    await db.update_account(acct.id, security="monitored")
    Q.forget_account()
    assert await Q.is_account_held(acct.id) is False


async def test_an_account_hold_ends_every_session_in_the_company(seeded_db):
    db = seeded_db["db"]
    acct = seeded_db["account"]
    owner = (await db.list_account_users(acct.id))[0]
    from adapters.storage import Role
    other = await db.create_user_with_email(
        email="two@premiertruckinggroup.com", password_hash="x",
        account_id=acct.id, role=Role.DISPATCHER, display_name="Two")

    pushed: list[str] = []
    import interfaces.api.auth as auth_mod
    orig = auth_mod.mark_jti_revoked

    async def fake_push(jti, expires_at=None):
        pushed.append(jti)
    auth_mod.mark_jti_revoked = fake_push
    try:
        from datetime import datetime, timedelta, timezone
        now = datetime.now(timezone.utc)
        later = (now + timedelta(hours=8)).isoformat()
        for uid, jti in ((owner.id, "a-1"), (other.id, "a-2")):
            await db.create_user_session(
                user_id=uid, jti=jti, device_label="d", user_agent="u",
                ip="1.2.3.4", created_at=now.isoformat(),
                last_seen=now.isoformat(), expires_at=later)

        ended = await Q.hold_account_sessions(db, acct.id)
        assert ended == 2, "both people, not just the one accused"
        assert set(pushed) == {"a-1", "a-2"}
    finally:
        auth_mod.mark_jti_revoked = orig


async def test_a_held_company_stops_collecting_strangers_data(seeded_db, monkeypatch):
    """The recruiter link carries no JWT, so no middleware can see it.
    It belongs to the company and collects a stranger's full FMCSA
    application — which is usually the real reason to hold a company."""
    db = seeded_db["db"]
    acct = seeded_db["account"]
    monkeypatch.setattr("infra.platform.get_platform_db", lambda: db)

    token = await db.create_application_link(
        account_id=acct.id, created_by=1, label="Drivers wanted")
    tok = token if isinstance(token, str) else token.get("token")

    assert await db.resolve_application_link(tok) is not None

    await db.update_account(acct.id, security="quarantined")
    Q.forget_account()
    assert await db.resolve_application_link(tok) is None, (
        "the link stops serving the moment the company is held"
    )


async def test_a_watched_company_keeps_recruiting(seeded_db, monkeypatch):
    """`monitored` changes nothing the account can see — including its
    links. Only the hold closes them."""
    db = seeded_db["db"]
    acct = seeded_db["account"]
    monkeypatch.setattr("infra.platform.get_platform_db", lambda: db)
    token = await db.create_application_link(
        account_id=acct.id, created_by=1, label="Still hiring")
    tok = token if isinstance(token, str) else token.get("token")

    await db.update_account(acct.id, security="monitored")
    Q.forget_account()
    assert await db.resolve_application_link(tok) is not None


async def test_the_links_stay_open_while_the_switch_is_off(seeded_db, monkeypatch):
    db = seeded_db["db"]
    acct = seeded_db["account"]
    monkeypatch.setattr("infra.platform.get_platform_db", lambda: db)
    token = await db.create_application_link(
        account_id=acct.id, created_by=1, label="Dark")
    tok = token if isinstance(token, str) else token.get("token")
    await db.update_account(acct.id, security="quarantined")
    Q.forget_account()
    monkeypatch.setenv("QUARANTINE_ENFORCEMENT_ENABLED", "0")
    assert await db.resolve_application_link(tok) is not None


# ── what a held company stops SENDING ─────────────────────────────
#
# Thirty-two scheduled jobs run with no request behind them, so no
# middleware sees them. Every one that reaches a person goes through
# dispatch(), notify_user() or deliver() — so the jobs answer for
# themselves, and none of them needed touching.

async def test_a_held_company_delivers_nothing_to_anyone(seeded_db, monkeypatch):
    db = seeded_db["db"]
    acct = seeded_db["account"]
    monkeypatch.setattr("infra.platform.get_platform_db", lambda: db)
    from capabilities.notifications.service import _account_is_held

    assert await _account_is_held(acct.id) is False
    await db.update_account(acct.id, security="quarantined")
    Q.forget_account()
    assert await _account_is_held(acct.id) is True


async def test_the_group_topics_close_with_the_company(seeded_db, monkeypatch):
    """deliver()'s shared fan-out calls the channels directly instead of
    going through dispatch, so it needs its own gate — otherwise a
    safety alert still lands in the Telegram group of a company we have
    stopped trusting."""
    db = seeded_db["db"]
    acct = seeded_db["account"]
    monkeypatch.setattr("infra.platform.get_platform_db", lambda: db)
    await db.update_account(acct.id, security="quarantined")
    Q.forget_account()

    from capabilities.notifications.plan import DeliveryPlan, deliver
    from capabilities.notifications.channels import NotificationContent
    sent: list = []

    plan = DeliveryPlan(
        contents=[NotificationContent(category="alert.faults",
                                      title="t", body="b")],
        shared=[], personal=[],
    )
    res = await deliver(db, acct.id, plan)
    assert res.shared == [] and res.personal == []
    assert sent == []


async def test_ingest_is_not_what_a_hold_stops(seeded_db, monkeypatch):
    """Telemetry keeps flowing in, so the evidence keeps accumulating
    and lifting the hold is instant. Freezing ingest would punch a
    permanent hole in the trucks' history of a company that may well
    turn out innocent — a cost they carry forever for a review that
    took an afternoon."""
    db = seeded_db["db"]
    acct = seeded_db["account"]
    monkeypatch.setattr("infra.platform.get_platform_db", lambda: db)
    await db.update_account(acct.id, security="quarantined")
    Q.forget_account()

    # Nothing in the hold touches a write path: the account's own rows
    # keep being writable by the ingest workers.
    await db.update_account(acct.id, timezone="America/Chicago")
    fresh = await db.get_account(acct.id)
    assert fresh.timezone == "America/Chicago"
    assert (fresh.security or "normal") == "quarantined"


# ── the senders that never touch the notification core ────────────
#
# "Every job reaches people through dispatch/notify_user/deliver" was
# NOT true. Four bot modules call the Telegram and email transports
# directly, using the alerting pipeline only as a first attempt and
# falling back to a direct send when no group route is configured —
# the ordinary case for a smaller fleet. Those fallbacks were exactly
# the deliveries a held company would most notice still arriving.

async def test_the_guard_answers_for_a_held_company(seeded_db, monkeypatch):
    db = seeded_db["db"]
    acct = seeded_db["account"]
    monkeypatch.setattr("infra.platform.get_platform_db", lambda: db)

    assert await Q.delivery_blocked(acct.id) is False
    assert await Q.delivery_blocked(None) is False

    await db.update_account(acct.id, security="quarantined")
    Q.forget_account()
    assert await Q.delivery_blocked(acct.id) is True

    # monitored is not a hold, and neither is the switch being off
    await db.update_account(acct.id, security="monitored")
    Q.forget_account()
    assert await Q.delivery_blocked(acct.id) is False


async def test_the_guard_is_off_with_the_switch(seeded_db, monkeypatch):
    db = seeded_db["db"]
    acct = seeded_db["account"]
    monkeypatch.setattr("infra.platform.get_platform_db", lambda: db)
    await db.update_account(acct.id, security="quarantined")
    Q.forget_account()
    monkeypatch.setenv("QUARANTINE_ENFORCEMENT_ENABLED", "0")
    assert await Q.delivery_blocked(acct.id) is False


async def test_the_pti_transport_sends_nothing_for_a_held_company(
        seeded_db, monkeypatch):
    """Every PTI helper — reminders, digests, the real-time submission
    ping to the fleet — funnels through this one send."""
    db = seeded_db["db"]
    acct = seeded_db["account"]
    monkeypatch.setattr("infra.platform.get_platform_db", lambda: db)

    import interfaces.bot.pti as pti
    reached: list = []
    monkeypatch.setattr(pti, "get_app_for_account",
                        lambda _a: reached.append(_a) or None, raising=False)

    await db.update_account(acct.id, security="quarantined")
    Q.forget_account()
    assert await pti._send_to_telegram(acct.id, 12345, "hi") is False
    assert reached == [], "it never even reached for the bot"


async def test_the_pti_transport_still_serves_everyone_else(
        seeded_db, monkeypatch):
    db = seeded_db["db"]
    acct = seeded_db["account"]
    monkeypatch.setattr("infra.platform.get_platform_db", lambda: db)
    import interfaces.bot.pti as pti
    reached: list = []
    monkeypatch.setattr(pti, "get_app_for_account",
                        lambda _a: reached.append(_a) or None, raising=False)
    Q.forget_account()
    await pti._send_to_telegram(acct.id, 12345, "hi")
    assert reached == [acct.id], "a clean company is still sent to"


async def test_every_direct_sender_asks_before_sending():
    """A structural guard, because the gap was a MISSING call, not a
    wrong one — and the next module someone adds will have the same
    shape. Each of these reaches people without going through the
    notification core, so each must ask on its own."""
    import pathlib
    from tests._repo import REPO
    for name in ("maintenance.py", "pti.py", "scheduled_reports.py",
                 "driver_samsara_sync.py"):
        src = (REPO / "interfaces" / "bot" / name).read_text()
        assert "delivery_blocked" in src or "is_request_held" in src, (
            f"interfaces/bot/{name} sends directly and never asks whether "
            f"the company is held"
        )


async def test_a_held_company_is_still_told_its_card_failed(seeded_db, monkeypatch):
    """A held account is still a BILLED account. The inbound half of
    this already keeps /billing/* open on exactly that ground —
    swallowing the notice on the way out would charge them for a
    problem we refused to tell them about."""
    db = seeded_db["db"]
    acct = seeded_db["account"]
    monkeypatch.setattr("infra.platform.get_platform_db", lambda: db)
    await db.update_account(acct.id, security="quarantined")
    Q.forget_account()

    from capabilities.notifications.categories import (
        NotificationCategory, TARGETED, get_category, register_category)
    if get_category("billing.payment_failed") is None:
        register_category(NotificationCategory(
            key="billing.payment_failed", label="Payment failed",
            kind=TARGETED, mandatory=True))

    from capabilities.notifications.service import notify_user
    from capabilities.notifications.channels import NotificationContent
    person = (await db.list_account_users(acct.id))[0]

    # It gets past the account gate — it reaches the channel loop and
    # stops only for want of a connected channel, not for the hold.
    res = await notify_user(
        db, acct.id, person.id,
        NotificationContent(category="billing.payment_failed",
                            title="Payment failed", body="Update your card."))
    assert isinstance(res, list), "not refused outright by the hold"

    # ...while ordinary operational traffic on the same account is
    # still stopped.
    from capabilities.notifications.service import _account_is_held
    assert await _account_is_held(acct.id) is True
