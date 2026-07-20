"""Digest engine (phase 3) — the batched-cadence buffer, the cadence
branch in dispatch(), and the flush that turns N buffered items into ONE
message per (recipient, channel).

This is the gate before Email: at fleet alert volume a per-alert email is
unusable, so the digest path has to be proven before a channel that needs
it goes live.  Nothing here touches the live Telegram pipeline (Telegram
stays 'immediate' and never enqueues).
"""

from __future__ import annotations

import os

os.environ.setdefault("ENCRYPTION_KEY", "")

import pytest

from capabilities.notifications.channels import (
    DeliveryResult,
    Payload,
    Recipient,
    register_channel,
)
from capabilities.notifications.service import (
    MAX_DIGEST_CHARS,
    MAX_DIGEST_LINES,
    dispatch,
    flush_digests,
    render_digest,
)
from capabilities.permissions.roles import Role


class FakeChannel:
    """A recording transport — proves WHAT was sent without a network."""

    def __init__(self, key: str, *, personal: bool = True, ok: bool = True):
        self.key, self.personal, self._ok = key, personal, ok
        self.sent: list[tuple[Recipient, Payload]] = []

    async def send(self, recipient, payload) -> DeliveryResult:
        self.sent.append((recipient, payload))
        return DeliveryResult(ok=self._ok, error="" if self._ok else "boom")


@pytest.fixture
def fake_channel():
    ch = FakeChannel("fake_test")
    register_channel(ch)
    return ch


async def _seed(pg_db, name="Digest Co"):
    acct = await pg_db.create_account(name)
    u = await pg_db.create_user(telegram_id=7101, account_id=acct.id, role=Role.FLEET)
    return acct.id, u.id


async def _subscribe(pg_db, acct, uid, channel, atype, cadence):
    await pg_db.set_notification_pref(
        acct, "user", uid, channel, atype, enabled=True, cadence=cadence)
    await pg_db.upsert_notification_channel(
        acct, "user", uid, channel, address="me@x.com", verified=True)


# ── Queue store ──────────────────────────────────────────────────────

async def test_enqueue_fetch_clear_roundtrip(pg_db):
    acct, uid = await _seed(pg_db)
    for t in ("fuel", "faults"):
        await pg_db.enqueue_digest_item(
            acct, "user", uid, "email", "daily", t, f"{t} on Truck 22", "me@x.com")

    assert await pg_db.fetch_due_digest_items("hourly") == []   # cadence-scoped
    items = await pg_db.fetch_due_digest_items("daily")
    assert len(items) == 2
    assert {i["alert_type"] for i in items} == {"fuel", "faults"}
    assert items[0]["address"] == "me@x.com"

    assert await pg_db.clear_digest_items([i["id"] for i in items]) == 2
    assert await pg_db.fetch_due_digest_items("daily") == []
    assert await pg_db.clear_digest_items([]) == 0


# ── Cadence branch in dispatch ───────────────────────────────────────

async def test_immediate_sends_now_and_queues_nothing(pg_db, fake_channel):
    acct, uid = await _seed(pg_db)
    await _subscribe(pg_db, acct, uid, "fake_test", "fuel", "immediate")

    res = await dispatch(pg_db, acct, "fuel", Payload(text="Low fuel · Truck 22"),
                         channels=["fake_test"])

    assert [r.ok for r in res] == [True]
    assert fake_channel.sent[0][0].address == "me@x.com"
    assert fake_channel.sent[0][1].text == "Low fuel · Truck 22"
    assert await pg_db.fetch_due_digest_items("daily") == []


async def test_batched_cadence_queues_instead_of_sending(pg_db, fake_channel):
    acct, uid = await _seed(pg_db)
    await _subscribe(pg_db, acct, uid, "fake_test", "fuel", "daily")

    await dispatch(pg_db, acct, "fuel",
                   Payload(text="Low fuel · Truck 22\nsecond line"),
                   channels=["fake_test"])

    assert fake_channel.sent == []                      # nothing delivered now
    items = await pg_db.fetch_due_digest_items("daily")
    assert len(items) == 1
    assert items[0]["summary"] == "Low fuel · Truck 22"  # first line by default
    assert items[0]["channel"] == "fake_test"


async def test_unknown_channel_is_skipped_not_fatal(pg_db, fake_channel):
    acct, uid = await _seed(pg_db)
    await _subscribe(pg_db, acct, uid, "fake_test", "fuel", "immediate")
    res = await dispatch(pg_db, acct, "fuel", Payload(text="x"),
                         channels=["nope", "fake_test"])
    assert len(res) == 1 and res[0].ok        # the good channel still went out


async def test_non_subscriber_gets_nothing(pg_db, fake_channel):
    """dispatch honours the same gate as the matrix reader — a pref for a
    DIFFERENT type doesn't leak."""
    acct, uid = await _seed(pg_db)
    await _subscribe(pg_db, acct, uid, "fake_test", "faults", "immediate")
    await dispatch(pg_db, acct, "fuel", Payload(text="x"), channels=["fake_test"])
    assert fake_channel.sent == []


# ── Rendering ────────────────────────────────────────────────────────

def test_render_groups_counts_and_caps():
    items = [{"alert_type": "fuel", "summary": f"fuel {i}"} for i in range(3)]
    items += [{"alert_type": "faults", "summary": "fault A"}]
    text = render_digest(items, "daily")
    assert "4 notifications" in text
    assert "1 faults, 3 fuel" in text          # grouped counts
    assert text.count("•") == 4

    many = [{"alert_type": "fuel", "summary": f"s{i}"}
            for i in range(MAX_DIGEST_LINES + 5)]
    capped = render_digest(many, "hourly")
    assert capped.count("•") == MAX_DIGEST_LINES
    assert "and 5 more" in capped              # overflow counted, not hidden


# ── Flush ────────────────────────────────────────────────────────────

async def test_flush_sends_one_message_per_recipient_and_clears(pg_db, fake_channel):
    acct, uid = await _seed(pg_db)
    await _subscribe(pg_db, acct, uid, "fake_test", "*", "daily")
    for i in range(3):
        await dispatch(pg_db, acct, "fuel", Payload(text=f"alert {i}"),
                       channels=["fake_test"])
    assert len(await pg_db.fetch_due_digest_items("daily")) == 3

    sent = await flush_digests(pg_db, "daily")

    assert sent == 1                                   # 3 items → ONE message
    rcpt, payload = fake_channel.sent[0]
    assert rcpt.id == str(uid) and rcpt.address == "me@x.com"
    assert "3 notifications" in payload.text
    for i in range(3):
        assert f"alert {i}" in payload.text
    assert await pg_db.fetch_due_digest_items("daily") == []   # drained


async def test_flush_separates_recipients(pg_db, fake_channel):
    acct, uid = await _seed(pg_db)
    other = (await pg_db.create_user(telegram_id=7102, account_id=acct, role=Role.FLEET)).id
    await _subscribe(pg_db, acct, uid, "fake_test", "*", "daily")
    await _subscribe(pg_db, acct, other, "fake_test", "*", "daily")

    await dispatch(pg_db, acct, "fuel", Payload(text="shared alert"),
                   channels=["fake_test"])
    assert await flush_digests(pg_db, "daily") == 2      # one digest each
    assert {r.id for r, _ in fake_channel.sent} == {str(uid), str(other)}


async def test_failed_send_keeps_items_for_next_run(pg_db):
    """A transport outage must not swallow the batch."""
    broken = FakeChannel("fake_broken", ok=False)
    register_channel(broken)
    acct, uid = await _seed(pg_db)
    await _subscribe(pg_db, acct, uid, "fake_broken", "*", "hourly")
    await dispatch(pg_db, acct, "fuel", Payload(text="alert"), channels=["fake_broken"])

    assert await flush_digests(pg_db, "hourly") == 0
    assert len(await pg_db.fetch_due_digest_items("hourly")) == 1   # still buffered

    broken._ok = True
    assert await flush_digests(pg_db, "hourly") == 1
    assert await pg_db.fetch_due_digest_items("hourly") == []


async def test_flush_empty_queue_is_a_noop(pg_db, fake_channel):
    assert await flush_digests(pg_db, "daily") == 0
    assert fake_channel.sent == []


# ── Wedge / poison-pill guards ───────────────────────────────────────

def test_render_stays_under_the_transport_limit():
    """A digest longer than the transport's cap would be rejected on every
    run, and since items clear only on success that wedges the recipient's
    queue forever.  Long summaries must be dropped by COUNT, not blindly
    concatenated."""
    fat = [{"alert_type": "fuel", "summary": "x" * 500}
           for _ in range(MAX_DIGEST_LINES)]
    text = render_digest(fat, "daily")
    assert len(text) <= MAX_DIGEST_CHARS
    assert "more" in text                       # the remainder is reported


def test_render_does_not_double_escape():
    """Summaries arrive already transport-safe (the dispatch text
    contract).  Re-escaping here would turn 'A & B' into 'A &amp;amp; B'."""
    text = render_digest(
        [{"alert_type": "fuel", "summary": "Truck A &amp; B"}], "daily")
    assert "&amp; B" in text and "&amp;amp;" not in text


async def test_unknown_cadence_is_delivered_not_buried(pg_db, fake_channel, monkeypatch):
    """No flush job drains an unrecognised cadence, so enqueueing would be
    a black hole — dispatch sends instead."""
    acct, uid = await _seed(pg_db)
    await _subscribe(pg_db, acct, uid, "fake_test", "fuel", "immediate")
    # Force a bad cadence past the write-boundary guard.
    await pg_db._db.execute(
        "UPDATE notification_pref SET cadence = 'weekly' WHERE account_id = ?", (acct,))
    await pg_db._db.commit()

    await dispatch(pg_db, acct, "fuel", Payload(text="x"), channels=["fake_test"])
    assert len(fake_channel.sent) == 1                      # delivered
    assert await pg_db.fetch_due_digest_items("weekly") == []   # not buried


async def test_write_boundary_rejects_unknown_cadence(pg_db):
    acct, uid = await _seed(pg_db)
    with pytest.raises(ValueError):
        await pg_db.set_notification_pref(
            acct, "user", uid, "email", "fuel", enabled=True, cadence="weekly")


def test_cadence_lists_agree_across_layers():
    """adapters/ deliberately mirrors the service's cadence list rather
    than importing capabilities/ — pin them so they can't drift."""
    from adapters.storage.notification_prefs import _VALID_CADENCES
    from capabilities.notifications.service import DIGEST_CADENCES, IMMEDIATE
    assert set(_VALID_CADENCES) == {IMMEDIATE, *DIGEST_CADENCES}


async def test_revoked_consent_drops_the_backlog(pg_db, fake_channel):
    """A daily item can sit for 24h.  If the recipient switches the channel
    off meanwhile, the buffered backlog must NOT arrive afterwards."""
    acct, uid = await _seed(pg_db)
    await _subscribe(pg_db, acct, uid, "fake_test", "*", "daily")
    await dispatch(pg_db, acct, "fuel", Payload(text="alert"), channels=["fake_test"])

    await pg_db.upsert_notification_channel(
        acct, "user", uid, "fake_test", address="me@x.com",
        verified=True, enabled_master=False)

    assert await flush_digests(pg_db, "daily") == 0
    assert fake_channel.sent == []                              # nothing sent
    assert await pg_db.fetch_due_digest_items("daily") == []    # and not left to rot


async def test_flush_uses_current_address_not_the_enqueued_one(pg_db, fake_channel):
    acct, uid = await _seed(pg_db)
    await _subscribe(pg_db, acct, uid, "fake_test", "*", "daily")
    await dispatch(pg_db, acct, "fuel", Payload(text="alert"), channels=["fake_test"])
    await pg_db.upsert_notification_channel(
        acct, "user", uid, "fake_test", address="corrected@x.com", verified=True)

    await flush_digests(pg_db, "daily")
    assert fake_channel.sent[0][0].address == "corrected@x.com"


async def test_flush_never_merges_across_accounts(pg_db, fake_channel):
    """Multi-tenant: two accounts on the same cadence+channel get separate
    digests carrying only their own items."""
    a1, u1 = await _seed(pg_db, "Tenant One")
    a2 = (await pg_db.create_account("Tenant Two")).id
    u2 = (await pg_db.create_user(telegram_id=7303, account_id=a2, role=Role.FLEET)).id
    await _subscribe(pg_db, a1, u1, "fake_test", "*", "daily")
    await _subscribe(pg_db, a2, u2, "fake_test", "*", "daily")

    await dispatch(pg_db, a1, "fuel", Payload(text="tenant one alert"),
                   channels=["fake_test"])
    await dispatch(pg_db, a2, "fuel", Payload(text="tenant two alert"),
                   channels=["fake_test"])

    assert await flush_digests(pg_db, "daily") == 2
    by_acct = {r.account_id: p.text for r, p in fake_channel.sent}
    assert "tenant one alert" in by_acct[a1] and "tenant two" not in by_acct[a1]
    assert "tenant two alert" in by_acct[a2] and "tenant one" not in by_acct[a2]


async def test_retention_sweep_clears_undrainable_residue(pg_db):
    """Items whose channel vanished are unreachable by any flush — the
    Retention hub target is what stops them accumulating."""
    import capabilities.notifications.retention  # noqa: F401  (registers)
    from capabilities.data_lifecycle.retention.registry import resolve

    acct, uid = await _seed(pg_db)
    await pg_db.enqueue_digest_item(
        acct, "user", uid, "gone_channel", "daily", "fuel", "orphan", "x")
    await pg_db._db.execute(
        "UPDATE notification_digest_queue SET created_at = '2020-01-01T00:00:00'")
    await pg_db._db.commit()

    assert await pg_db.prune_notification_digest_queue(days=14) == 1
    assert await pg_db.fetch_due_digest_items("daily") == []

    keys = {r.target.key for r in resolve("platform")}
    assert "notifications.digest_queue" in keys      # claimed, so it's swept


# ── Telegram stays immediate (the invariant this phase must not break) ─

def test_telegram_channels_are_not_digestable_by_default():
    """No code path here changes Telegram's cadence: a DM is only useful
    when it's live.  Digest is opt-in per pref row, and the backfill
    writes 'immediate' for every telegram_dm rule."""
    import inspect

    from adapters.storage import platform_migrations
    src = inspect.getsource(platform_migrations.migrate_notification_matrix)
    assert "'immediate'" in src or '"immediate"' in src
