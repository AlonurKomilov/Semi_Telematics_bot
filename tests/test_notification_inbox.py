"""N5-P1 — the in-app inbox: intrinsic channel + opt-out store.

The bell's persisted record.  The ``in_app`` channel's ``send()`` INSERTs
a ``notification_inbox`` row instead of transmitting, so every row passes
the same dispatch()/notify_user() gates as a real delivery.  Pins:

  • intrinsic contract — registered personal+intrinsic; send() persists;
    non-user recipients refused
  • notify_user delivers to the inbox with NO connection row (intrinsic
    skips the connect/verify check), and an explicit mute still wins
  • dispatch broadcast is OPT-OUT for the intrinsic channel: every active
    user gets a row, specific/star mutes excluded (specific beats star),
    the audience gate and the recipient_filter predicate still apply
  • read model — unread count, mark-read scoped to own rows, read-all
  • feed — newest-first, source filter, keyset pagination
  • retention — prune drops old rows regardless of read state
"""

from __future__ import annotations

import pytest

from capabilities.notifications import NotificationContent, dispatch, notify_user
from capabilities.notifications.categories import (
    BROADCAST,
    TARGETED,
    NotificationCategory,
    register_category,
)
from capabilities.notifications.channels import Recipient, get_channel
from capabilities.permissions.roles import Role


@pytest.fixture(autouse=True)
def _platform_db(pg_db, monkeypatch):
    """The in_app channel's send() resolves the db via infra.platform
    (same pattern as webpush) — point it at the test database."""
    import infra.platform as cp
    monkeypatch.setattr(cp, "_db", pg_db)


async def _seed(pg_db, name="Inbox Co", tg=7301):
    acct = await pg_db.create_account(name)
    u = await pg_db.create_user(telegram_id=tg, account_id=acct.id, role=Role.FLEET)
    return acct.id, u.id


# ── Intrinsic channel contract ───────────────────────────────────────

async def test_in_app_send_persists_row(pg_db):
    ch = get_channel("in_app")
    assert ch is not None and ch.personal and ch.intrinsic
    acct, uid = await _seed(pg_db)
    content = NotificationContent(
        title="Invite accepted", body="Dana joined as Driver.",
        category="team.invite_accepted", severity="info", url="/team")
    rcpt = Recipient(account_id=acct, type="user", id=str(uid))
    res = await ch.send(rcpt, ch.render(rcpt, content))
    assert res.ok

    rows = await pg_db.list_inbox_notices(acct, uid)
    assert len(rows) == 1
    r = rows[0]
    assert r["category"] == "team.invite_accepted"
    assert r["source"] == "team"                  # derived from the namespace
    assert r["title"] == "Invite accepted"
    assert r["body"] == "Dana joined as Driver."
    assert r["url"] == "/team"
    assert r["read_at"] == ""                     # unread


async def test_in_app_refuses_non_user_recipient(pg_db):
    ch = get_channel("in_app")
    rcpt = Recipient(account_id=1, type="topic", id="55")
    res = await ch.send(rcpt, ch.render(
        rcpt, NotificationContent(title="x", category="team.t")))
    assert not res.ok and res.error == "not_a_user"


# ── notify_user (targeted) through the inbox ─────────────────────────

async def test_notify_user_reaches_inbox_without_connection(pg_db):
    """Intrinsic = nothing to connect: a user with NO notification_channel
    row still gets the inbox record (the other channels are skipped)."""
    register_category(NotificationCategory("test.inb1", "T", TARGETED))
    acct, uid = await _seed(pg_db, tg=7302)
    res = await notify_user(
        pg_db, acct, uid,
        NotificationContent(title="Hello", category="test.inb1"))
    assert any(r.ok for r in res)
    rows = await pg_db.list_inbox_notices(acct, uid)
    assert [r["category"] for r in rows] == ["test.inb1"]


async def test_notify_user_inbox_respects_mute(pg_db):
    register_category(NotificationCategory("test.inb2", "T", TARGETED))
    acct, uid = await _seed(pg_db, tg=7303)
    await pg_db.set_notification_pref(
        acct, "user", uid, "in_app", "test.inb2", enabled=False)
    await notify_user(
        pg_db, acct, uid,
        NotificationContent(title="Hello", category="test.inb2"),
        channels=["in_app"])
    assert await pg_db.list_inbox_notices(acct, uid) == []


# ── dispatch (broadcast, opt-out) through the inbox ──────────────────

async def test_dispatch_inbox_opt_out_all_users_minus_mutes(pg_db):
    register_category(NotificationCategory("test.inb3", "T", BROADCAST))
    acct = (await pg_db.create_account("Opt Co")).id
    a = (await pg_db.create_user(telegram_id=7304, account_id=acct, role=Role.FLEET)).id
    b = (await pg_db.create_user(telegram_id=7305, account_id=acct, role=Role.FLEET)).id
    c = (await pg_db.create_user(telegram_id=7306, account_id=acct, role=Role.FLEET)).id
    # b mutes this category; c blanket-mutes the channel.
    await pg_db.set_notification_pref(acct, "user", b, "in_app", "test.inb3", enabled=False)
    await pg_db.set_notification_pref(acct, "user", c, "in_app", "*", enabled=False)

    await dispatch(pg_db, acct,
                   NotificationContent(title="hi", category="test.inb3"),
                   channels=["in_app"])
    got = {u: [r["category"] for r in await pg_db.list_inbox_notices(acct, u)]
           for u in (a, b, c)}
    assert got[a] == ["test.inb3"]      # no pref rows at all → opt-out default ON
    assert got[b] == []                 # specific mute
    assert got[c] == []                 # star mute


async def test_optout_specific_beats_star(pg_db):
    """'Muted everything but re-enabled X' delivers X (specific row wins)."""
    register_category(NotificationCategory("test.inb4", "T", BROADCAST))
    acct, uid = await _seed(pg_db, name="Star Co", tg=7307)
    await pg_db.set_notification_pref(acct, "user", uid, "in_app", "*", enabled=False)
    await pg_db.set_notification_pref(acct, "user", uid, "in_app", "test.inb4", enabled=True)
    await dispatch(pg_db, acct,
                   NotificationContent(title="hi", category="test.inb4"),
                   channels=["in_app"])
    assert [r["category"] for r in await pg_db.list_inbox_notices(acct, uid)] \
        == ["test.inb4"]


async def test_dispatch_inbox_applies_audience_and_recipient_filter(pg_db):
    """The intrinsic path reuses the same _filter_recipients pass — role
    audience and the caller's scoping predicate both gate inbox rows."""
    register_category(NotificationCategory(
        "test.inb5", "T", BROADCAST, audience=lambda role: role == "fleet"))
    acct = (await pg_db.create_account("Gate Co")).id
    fleet = (await pg_db.create_user(telegram_id=7308, account_id=acct, role=Role.FLEET)).id
    driver = (await pg_db.create_user(telegram_id=7309, account_id=acct, role=Role.DRIVER)).id
    fleet2 = (await pg_db.create_user(telegram_id=7310, account_id=acct, role=Role.FLEET)).id

    await dispatch(pg_db, acct,
                   NotificationContent(title="hi", category="test.inb5"),
                   channels=["in_app"],
                   recipient_filter=lambda uid, role: uid != fleet2)
    assert [r["category"] for r in await pg_db.list_inbox_notices(acct, fleet)] \
        == ["test.inb5"]
    assert await pg_db.list_inbox_notices(acct, driver) == []   # audience
    assert await pg_db.list_inbox_notices(acct, fleet2) == []   # predicate


# ── Read model + feed ────────────────────────────────────────────────

async def test_unread_count_mark_read_and_read_all(pg_db):
    acct, uid = await _seed(pg_db, name="Read Co", tg=7311)
    for i in range(3):
        await pg_db.add_inbox_notice(acct, uid, category="team.t", title=f"n{i}")
    assert await pg_db.count_inbox_unread(acct, uid) == 3

    rows = await pg_db.list_inbox_notices(acct, uid)
    n = await pg_db.mark_inbox_read(acct, uid, [rows[0]["id"]])
    assert n == 1
    assert await pg_db.count_inbox_unread(acct, uid) == 2

    # A foreign user can't mark someone else's rows.
    other_acct, other_uid = await _seed(pg_db, name="Other Co", tg=7312)
    assert await pg_db.mark_inbox_read(other_acct, other_uid, [rows[1]["id"]]) == 0
    assert await pg_db.count_inbox_unread(acct, uid) == 2

    assert await pg_db.mark_inbox_all_read(acct, uid) == 2
    assert await pg_db.count_inbox_unread(acct, uid) == 0


async def test_feed_newest_first_source_filter_and_pagination(pg_db):
    acct, uid = await _seed(pg_db, name="Feed Co", tg=7313)
    await pg_db.add_inbox_notice(acct, uid, category="team.a", title="t1")
    await pg_db.add_inbox_notice(acct, uid, category="system.b", title="s1")
    await pg_db.add_inbox_notice(acct, uid, category="team.c", title="t2")

    all_rows = await pg_db.list_inbox_notices(acct, uid)
    assert [r["title"] for r in all_rows] == ["t2", "s1", "t1"]   # newest first

    team = await pg_db.list_inbox_notices(acct, uid, source="team")
    assert [r["title"] for r in team] == ["t2", "t1"]

    page1 = await pg_db.list_inbox_notices(acct, uid, limit=2)
    page2 = await pg_db.list_inbox_notices(
        acct, uid, limit=2, before_id=page1[-1]["id"])
    assert [r["title"] for r in page1] == ["t2", "s1"]
    assert [r["title"] for r in page2] == ["t1"]


async def test_feed_source_accepts_a_bucket_list(pg_db):
    """The UI filters by the BUCKET it names ("Activity" = team + ai), so
    ``source`` takes a comma list — one namespace still works."""
    acct, uid = await _seed(pg_db, name="Bucket Co", tg=7316)
    await pg_db.add_inbox_notice(acct, uid, category="team.a", title="t")
    await pg_db.add_inbox_notice(acct, uid, category="ai.b", title="a")
    await pg_db.add_inbox_notice(acct, uid, category="system.c", title="s")

    activity = await pg_db.list_inbox_notices(acct, uid, source="team,ai")
    assert {r["title"] for r in activity} == {"t", "a"}      # bucket
    assert [r["title"] for r in
            await pg_db.list_inbox_notices(acct, uid, source="system")] == ["s"]
    # Whitespace tolerated; unknown namespaces simply match nothing.
    assert len(await pg_db.list_inbox_notices(acct, uid, source=" team , ai ")) == 2
    assert await pg_db.list_inbox_notices(acct, uid, source="nope") == []
    # Empty string keeps the unfiltered behaviour.
    assert len(await pg_db.list_inbox_notices(acct, uid, source="")) == 3


async def test_applications_is_a_filterable_source(pg_db):
    """The bell's Applications tab filters on this namespace.  A source the
    allow-list doesn't know matches NOTHING (never the unfiltered feed), so
    a missing entry here would quietly show every notice in that tab."""
    acct, uid = await _seed(pg_db, name="Recruiting Co", tg=7317)
    await pg_db.add_inbox_notice(
        acct, uid, category="applications.received", title="new app")
    await pg_db.add_inbox_notice(acct, uid, category="team.a", title="t")

    got = await pg_db.list_inbox_notices(acct, uid, source="applications")
    assert [r["title"] for r in got] == ["new app"]
    assert got[0]["source"] == "applications"
    # Not swept into Activity's bucket.
    assert [r["title"] for r in
            await pg_db.list_inbox_notices(acct, uid, source="team,ai")] == ["t"]


async def test_category_splits_one_source_into_two_tabs(pg_db):
    """A bucket cannot separate a fired alert trigger from an engine
    fault: both live in the ``alert`` namespace by design, because one
    capability owns one inbox source.  The bell still has to show them
    under different tabs, so the filter goes one level finer — and its
    complement is what stops the SAME notice appearing under both.
    """
    acct, uid = await _seed(pg_db, name="Split Co", tg=7318)
    await pg_db.add_inbox_notice(acct, uid, category="alert.trigger", title="mine")
    await pg_db.add_inbox_notice(acct, uid, category="alert.faults", title="ours")
    await pg_db.add_inbox_notice(acct, uid, category="team.a", title="t")

    mine = await pg_db.list_inbox_notices(acct, uid, category="alert.trigger")
    assert [r["title"] for r in mine] == ["mine"]

    rest = await pg_db.list_inbox_notices(acct, uid, exclude_category="alert.trigger")
    assert {r["title"] for r in rest} == {"ours", "t"}

    # Between them they cover the feed exactly once — no notice in both
    # tabs, none in neither.
    everything = await pg_db.list_inbox_notices(acct, uid)
    assert len(mine) + len(rest) == len(everything)


async def test_an_unknown_category_matches_nothing_not_everything(pg_db):
    """Unlike ``source``, a category is never checked against a registry —
    it is a stable wire key, and the safe reading of one nobody knows is
    an empty tab rather than the whole feed relabelled."""
    acct, uid = await _seed(pg_db, name="Unknown Cat Co", tg=7319)
    await pg_db.add_inbox_notice(acct, uid, category="team.a", title="t")
    assert await pg_db.list_inbox_notices(acct, uid, category="no.such") == []
    # And excluding one nobody has changes nothing.
    assert len(await pg_db.list_inbox_notices(
        acct, uid, exclude_category="no.such")) == 1


async def test_prune_drops_old_rows(pg_db):
    acct, uid = await _seed(pg_db, name="Prune Co", tg=7314)
    await pg_db.add_inbox_notice(acct, uid, category="team.t", title="fresh")
    # Back-date one row past the window.
    await pg_db._db.execute(
        "UPDATE notification_inbox SET created_at = '2001-01-01T00:00:00' "
        "WHERE account_id = ? AND title = 'fresh'", (acct,))
    await pg_db._db.commit()
    await pg_db.add_inbox_notice(acct, uid, category="team.t", title="new")

    dropped = await pg_db.prune_notification_inbox(days=60)
    assert dropped == 1
    rows = await pg_db.list_inbox_notices(acct, uid)
    assert [r["title"] for r in rows] == ["new"]


async def test_mandatory_broadcast_ignores_mutes(pg_db):
    """A MANDATORY broadcast category (system.billing model) reaches every
    audience-passing user even with an explicit mute row — the locked-on
    toggle in the UI must be the truth."""
    from capabilities.notifications.categories import (
        BROADCAST, NotificationCategory, register_category)
    register_category(NotificationCategory(
        "test.mand1", "M", BROADCAST, mandatory=True))
    acct, uid = await _seed(pg_db, name="Mand Co", tg=7315)
    await pg_db.set_notification_pref(
        acct, "user", uid, "in_app", "test.mand1", enabled=False)   # mute try
    await pg_db.set_notification_pref(
        acct, "user", uid, "in_app", "*", enabled=False)            # blanket too

    await dispatch(pg_db, acct,
                   NotificationContent(title="pay up", category="test.mand1"),
                   channels=["in_app"])
    rows = await pg_db.list_inbox_notices(acct, uid)
    assert [r["category"] for r in rows] == ["test.mand1"]          # delivered
