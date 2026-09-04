"""A dead address is not a busy one.

The flush loop kept EVERY failed batch for the next hour. That is right
for a network blip and wrong for an address that no longer exists: on the
live account five users' Telegram answered "Chat not found" 1,493 times
between 2026-08-14 and 2026-09-04 — once an hour, per user — while their
notifications sat undelivered until the 14-day retention sweep quietly
deleted them unread.

Nobody was ever told. Not the recipient, whose alerts simply stopped
arriving, and not the operator, because a warning logged hourly for three
weeks is indistinguishable from wallpaper.

These pin the three halves of the answer: recognise the failure, stop the
loop, and say so where the person will actually see it.
"""

from __future__ import annotations

import os

os.environ.setdefault("ENCRYPTION_KEY", "")

import pytest

from capabilities.notifications.channels import (
    DeliveryResult,
    Payload,
    channel_label,
    is_permanent_failure,
    register_channel,
)
from capabilities.notifications.service import flush_quiet_deferrals
from capabilities.permissions.roles import Role

pytestmark = pytest.mark.asyncio


class DeadChannel:
    """A transport whose address no longer exists — the live symptom."""

    def __init__(self, key: str, error: str):
        self.key, self.personal, self._error = key, True, error
        self.attempts = 0

    def render(self, recipient, content) -> Payload:
        return Payload(text=content.title, subject=content.title)

    async def send(self, recipient, payload) -> DeliveryResult:
        self.attempts += 1
        return DeliveryResult(ok=False, error=self._error)


class TestTheClassifier:
    """Conservative on purpose: an unfamiliar error keeps its messages
    queued.  Dropping a notification that would have arrived is the worse
    mistake, and the retention sweep bounds the queue anyway."""

    def test_a_dead_address_is_permanent(self):
        assert is_permanent_failure("BadRequest: Chat not found")
        assert is_permanent_failure("Forbidden: bot was blocked by the user")
        assert is_permanent_failure("Unauthorized: user is deactivated")

    def test_a_busy_transport_is_not(self):
        assert not is_permanent_failure("RetryAfter: 30")
        assert not is_permanent_failure("TimedOut: read timeout")
        assert not is_permanent_failure("NetworkError: connection reset")

    def test_the_unknown_is_treated_as_transient(self):
        assert not is_permanent_failure("SomethingNobodyHasSeenYet: 42")
        assert not is_permanent_failure("")
        assert not is_permanent_failure(None)

    def test_a_channel_has_a_name_a_person_would_say(self):
        assert channel_label("telegram_dm") == "Telegram"
        assert channel_label("carrier_pigeon") == "carrier pigeon"


class TestTheLoopStops:

    async def _seed(self, pg_db, error: str, key: str):
        acct = await pg_db.create_account(f"Dead {key} Co")
        u = await pg_db.create_user(telegram_id=7301, account_id=acct.id,
                                    role=Role.FLEET)
        ch = DeadChannel(key, error)
        register_channel(ch)
        await pg_db.upsert_notification_channel(
            acct.id, "user", u.id, key, address="123", verified=True)
        for t in ("faults", "events"):
            await pg_db.enqueue_digest_item(
                acct.id, "user", u.id, key, "quiet", t,
                f"{t} on Truck 22", "123", severity="warning")
        return acct.id, u.id, ch

    async def test_a_permanent_failure_drops_the_batch_and_disables(self, pg_db):
        acct, uid, ch = await self._seed(
            pg_db, "BadRequest: Chat not found", "dead_perm")
        await flush_quiet_deferrals(pg_db)

        assert ch.attempts == 1                      # tried once
        left = await pg_db.fetch_due_digest_items("quiet", 100)
        assert [x for x in left if x["account_id"] == acct] == []   # not requeued
        conn = await pg_db.get_notification_channel(
            acct, "user", uid, "dead_perm")
        assert not conn["enabled_master"]             # nothing queues behind it

    async def test_the_person_is_told_in_the_one_place_that_cannot_fail(self, pg_db):
        """Mailing someone to say their mail is broken is useless — the
        bell is a database write with no address to be wrong."""
        acct, uid, _ = await self._seed(
            pg_db, "BadRequest: Chat not found", "dead_tell")
        await flush_quiet_deferrals(pg_db)

        notices = await pg_db.list_inbox_notices(acct, uid, limit=20)
        broken = [n for n in notices
                  if n["category"] == "system.channel_broken"]
        assert len(broken) == 1                       # ONE per incident...
        assert "disconnected" in broken[0]["title"].lower()
        assert "/notifications/preferences" in (broken[0]["url"] or "")

    async def test_a_transient_failure_still_keeps_its_batch(self, pg_db):
        """The original behaviour, unchanged: a blip must not cost the
        message.  This is the half the fix must not break."""
        acct, uid, ch = await self._seed(
            pg_db, "TimedOut: read timeout", "dead_soft")
        await flush_quiet_deferrals(pg_db)

        left = await pg_db.fetch_due_digest_items("quiet", 100)
        assert [x for x in left if x["account_id"] == acct]        # kept
        conn = await pg_db.get_notification_channel(
            acct, "user", uid, "dead_soft")
        assert conn["enabled_master"]                 # still enabled
        notices = await pg_db.list_inbox_notices(acct, uid, limit=20)
        assert not [n for n in notices
                    if n["category"] == "system.channel_broken"]


class TestBothDrainsShareTheRule:
    """``flush_digests`` carried the identical always-keep branch.

    It is dormant only because no recipient currently chooses a batched
    cadence — a bug waiting for its first user is still a bug, and two
    copies of one rule is how the fixed half drifts from the unfixed one.
    """

    async def test_the_batched_flush_retires_a_dead_channel_too(self, pg_db):
        from capabilities.notifications.service import flush_digests
        acct = await pg_db.create_account("Dead Digest Co")
        u = await pg_db.create_user(telegram_id=7302, account_id=acct.id,
                                    role=Role.FLEET)
        ch = DeadChannel("dead_daily", "BadRequest: Chat not found")
        register_channel(ch)
        await pg_db.upsert_notification_channel(
            acct.id, "user", u.id, "dead_daily", address="123", verified=True)
        await pg_db.enqueue_digest_item(
            acct.id, "user", u.id, "dead_daily", "daily", "faults",
            "Fault on Truck 9", "123", severity="warning")

        await flush_digests(pg_db, "daily")

        left = await pg_db.fetch_due_digest_items("daily", 100)
        assert [x for x in left if x["account_id"] == acct.id] == []
        conn = await pg_db.get_notification_channel(
            acct.id, "user", u.id, "dead_daily")
        assert not conn["enabled_master"]
        notices = await pg_db.list_inbox_notices(acct.id, u.id, limit=20)
        assert [n for n in notices if n["category"] == "system.channel_broken"]


class TestEmailGetsTheSameTreatment:
    """Nothing in this machinery is Telegram-specific but the label.

    Email had the identical blind spot: "Verified" from the moment you
    confirm the address, and never revisited. A mailbox that starts
    bouncing is verified AND unreachable at the same time, and the card
    showed only the first half — the exact shape that let five users'
    Telegram sit dead for three weeks.
    """

    def test_a_refused_mailbox_is_permanent(self):
        assert is_permanent_failure(
            "SMTPRecipientsRefused: 550 5.1.1 no such user")
        assert is_permanent_failure("bad_email")

    def test_a_server_problem_is_not_the_recipients_fault(self):
        """The one that would be a disaster to get wrong: SMTP being
        unconfigured must never disable every user's email and tell them
        all to reconnect."""
        assert not is_permanent_failure("email_not_configured")
        assert not is_permanent_failure("SMTPServerDisconnected: bye")

    async def test_the_smtp_reason_survives_the_bool_wrapper(self):
        """``send_email`` returns a bare bool for ~20 existing callers,
        which is why the reason never reached the classifier. The
        detailed sibling carries it; the wrapper keeps their contract."""
        from capabilities.email import send_email, send_email_detailed
        ok, reason = send_email_detailed(
            to="nobody@example.invalid", subject="s", body="b")
        assert ok is False and reason           # a REASON, not just False
        assert send_email(to="nobody@example.invalid",
                          subject="s", body="b") is False   # unchanged

    async def test_the_card_is_told_only_on_evidence(self, pg_db):
        """A channel is called broken on the flush's notice, never on the
        switch being off — off is usually a choice, and telling someone
        their own decision is a fault is how a page earns being ignored."""
        import json as _json
        from capabilities.notifications.router import channel_health
        acct = await pg_db.create_account("Email Health Co")
        u = await pg_db.create_user(telegram_id=7401, account_id=acct.id,
                                    role=Role.FLEET)

        # Switched off, nothing on record → the person's own choice.
        assert (await channel_health(
            pg_db, acct.id, u.id, "email", {"enabled_master": 0}))["state"] == "ok"

        # The flush records a failure for THIS channel → needs attention.
        await pg_db.add_inbox_notice(
            acct.id, u.id, category="system.channel_broken",
            title="Email is disconnected", body="We couldn't deliver there.",
            severity="warning", meta=_json.dumps({"channel": "email"}))
        h = await channel_health(pg_db, acct.id, u.id, "email",
                                 {"enabled_master": 0})
        assert h["state"] == "needs_attention"
        assert "couldn" in h["reason"]

        # A failure on a DIFFERENT channel must not accuse this one.
        h2 = await channel_health(pg_db, acct.id, u.id, "web_push",
                                  {"enabled_master": 0})
        assert h2["state"] == "ok"
