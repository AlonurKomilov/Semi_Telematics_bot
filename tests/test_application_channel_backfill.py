"""The one-time seed that keeps recruiting email/DM reaching people.

Recruiting used to send mail and Telegram straight from the feature, so
nobody ever had to connect a notification channel.  Routing it through
``notify_user`` makes those connection rows load-bearing — and a straight
move would have silently ended the emails.  The backfill
(``migrate_seed_application_notification_channels``) seeds exactly the
audience that already had reach, and nothing wider.  Pins:

  • only holders of ``can_manage_applications`` are seeded,
  • only the channels the user's OWN recruiting preference includes,
  • an existing connection row is never touched — a verify, a mute or a
    master switch someone actually set is theirs,
  • consent stays narrow: each seeded channel gets a '*' blanket-OFF pref
    plus one ON row for ``applications.received``, so the address can't
    be inherited by a future category,
  • running it twice changes nothing.

The two opt-out tests re-create the retired ``application_notify_prefs``
table themselves: it is dropped once the cutover lands (migration 190),
so the only database that still holds those rows is one restored from a
pre-cutover backup — the one case the read still exists for.
"""

from __future__ import annotations

import os

os.environ.setdefault("ENCRYPTION_KEY", "")

from adapters.storage.platform_migrations import (
    migrate_seed_application_notification_channels as backfill,
)
from capabilities.permissions.roles import Role


async def _user(pg_db, acct_id, role, *, email, telegram_id=None, manager=False):
    from interfaces.api.auth import _hash_password
    u = await pg_db.create_user_with_email(
        email=email, password_hash=_hash_password("password12345"),
        account_id=acct_id, role=role, display_name=role.value.title(),
    )
    # telegram_id and is_manager aren't in update_user's allowed set
    # (they're set at creation / by the team-management endpoints), so
    # write them straight to the row this fixture is building.
    if telegram_id is not None:
        await pg_db._db.execute(
            "UPDATE users SET telegram_id = ? WHERE id = ?", (telegram_id, u.id))
    if manager:
        await pg_db._db.execute(
            "UPDATE users SET is_manager = 1 WHERE id = ?", (u.id,))
    await pg_db._db.commit()
    return u


async def _legacy_prefs(pg_db, acct_id, uid, channels):
    """Re-create the retired ``application_notify_prefs`` row this
    migration still reads on a PRE-CUTOVER database.

    The table is dropped once the cutover lands (migration 190), so the
    only database that still carries a row is one restored from a backup
    taken before it — which is exactly the case worth testing, since
    honouring those opt-outs is the whole point of the seed.
    """
    await pg_db._db.execute("""
        CREATE TABLE IF NOT EXISTS application_notify_prefs (
            user_id     INTEGER PRIMARY KEY,
            account_id  INTEGER NOT NULL,
            channels    TEXT    NOT NULL DEFAULT 'telegram,email,dashboard',
            updated_at  TEXT    NOT NULL
        )""")
    await pg_db._db.execute(
        "INSERT INTO application_notify_prefs (user_id, account_id, channels, updated_at) "
        "VALUES (?, ?, ?, '2026-01-01T00:00:00Z') "
        "ON CONFLICT(user_id) DO UPDATE SET channels = excluded.channels",
        (uid, acct_id, ",".join(channels)))
    await pg_db._db.commit()


async def _conn_row(pg_db, acct_id, uid, channel):
    return await pg_db.get_notification_channel(acct_id, "user", uid, channel)


async def test_seeds_only_permission_holders(pg_db):
    acct = await pg_db.create_account("Backfill Co")
    rec = await _user(pg_db, acct.id, Role.RECRUITER,
                      email=f"rec.{acct.id}@x.com", telegram_id=90010001)
    drv = await _user(pg_db, acct.id, Role.DRIVER, email=f"drv.{acct.id}@x.com",
                      telegram_id=90010002)

    await backfill(pg_db._db)

    # The recruiter reviews applications → both channels seeded, verified,
    # addressed from what the account already knew about them.
    email_row = await _conn_row(pg_db, acct.id, rec.id, "email")
    assert email_row and email_row["verified"] is True
    assert email_row["address"] == f"rec.{acct.id}@x.com"
    tg_row = await _conn_row(pg_db, acct.id, rec.id, "telegram_dm")
    assert tg_row and tg_row["address"] == "90010001"

    # The driver holds no can_manage_applications — untouched.
    assert await _conn_row(pg_db, acct.id, drv.id, "email") is None
    assert await _conn_row(pg_db, acct.id, drv.id, "telegram_dm") is None


async def test_consent_stays_scoped_to_this_one_category(pg_db):
    acct = await pg_db.create_account("Scope Co")
    rec = await _user(pg_db, acct.id, Role.RECRUITER, email=f"s.{acct.id}@x.com")

    await backfill(pg_db._db)

    prefs = await pg_db.get_pref_categories(acct.id, "user", rec.id, "email")
    # Blanket off + this category on: notify_user reads the specific row
    # first, so exactly one notice type can use a seeded address.
    assert prefs.get("*") is False
    assert prefs.get("applications.received") is True


async def test_honours_the_users_own_channel_opt_out(pg_db):
    acct = await pg_db.create_account("Optout Co")
    rec = await _user(pg_db, acct.id, Role.RECRUITER,
                      email=f"o.{acct.id}@x.com", telegram_id=90010003)
    # They already turned recruiting email off in the feature's own panel.
    await _legacy_prefs(pg_db, acct.id, rec.id, ["telegram", "dashboard"])

    await backfill(pg_db._db)

    assert await _conn_row(pg_db, acct.id, rec.id, "email") is None
    assert await _conn_row(pg_db, acct.id, rec.id, "telegram_dm") is not None


async def test_dashboard_opt_out_survives_as_an_in_app_mute(pg_db):
    """The in-app channel is intrinsic — there is no connection row to
    withhold, so someone who had switched the dashboard notice OFF needs
    an explicit mute or the move would switch it back on for them."""
    acct = await pg_db.create_account("NoDash Co")
    rec = await _user(pg_db, acct.id, Role.RECRUITER, email=f"n.{acct.id}@x.com")
    await _legacy_prefs(pg_db, acct.id, rec.id, ["email"])

    await backfill(pg_db._db)

    prefs = await pg_db.get_pref_categories(acct.id, "user", rec.id, "in_app")
    assert prefs.get("applications.received") is False
    # Someone who kept the dashboard channel gets no mute at all.
    keeper = await _user(pg_db, acct.id, Role.RECRUITER, email=f"k.{acct.id}@x.com")
    await backfill(pg_db._db)
    assert await pg_db.get_pref_categories(
        acct.id, "user", keeper.id, "in_app") == {}


async def test_never_overwrites_an_existing_connection(pg_db):
    acct = await pg_db.create_account("Existing Co")
    rec = await _user(pg_db, acct.id, Role.RECRUITER, email=f"e.{acct.id}@x.com")
    # This person turned their email channel OFF at the master switch.
    await pg_db.upsert_notification_channel(
        acct.id, "user", rec.id, "email",
        address="chosen@x.com", verified=True, enabled_master=False)

    await backfill(pg_db._db)

    row = await _conn_row(pg_db, acct.id, rec.id, "email")
    assert row["enabled_master"] is False, "a user's own switch must survive"
    assert row["address"] == "chosen@x.com"
    # And no consent rows were written over their choice.
    prefs = await pg_db.get_pref_categories(acct.id, "user", rec.id, "email")
    assert prefs == {}


async def test_is_idempotent(pg_db):
    acct = await pg_db.create_account("Twice Co")
    rec = await _user(pg_db, acct.id, Role.RECRUITER, email=f"t.{acct.id}@x.com")

    await backfill(pg_db._db)
    # A deliberate change AFTER the first run must survive the second.
    await pg_db.set_notification_pref(acct.id, "user", rec.id, "email",
                                      "applications.received", enabled=False)
    await backfill(pg_db._db)

    prefs = await pg_db.get_pref_categories(acct.id, "user", rec.id, "email")
    assert prefs.get("applications.received") is False


async def test_hr_manager_tier_counts_as_audience(pg_db):
    """can_manage_applications reaches HR through the senior tier, not the
    base role — the backfill has to read the tier, or team leads lose the
    mail they get today.  A base HR is NOT audience, which is what makes
    this a real test of the tier and not of the role."""
    acct = await pg_db.create_account("Tier Co")
    lead = await _user(pg_db, acct.id, Role.HR, email=f"lead.{acct.id}@x.com",
                       manager=True)
    staff = await _user(pg_db, acct.id, Role.HR, email=f"staff.{acct.id}@x.com")

    await backfill(pg_db._db)

    assert await _conn_row(pg_db, acct.id, lead.id, "email") is not None
    assert await _conn_row(pg_db, acct.id, staff.id, "email") is None
