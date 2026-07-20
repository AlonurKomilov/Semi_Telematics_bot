"""Notification preference matrix (phase 2a) — the mixin CRUD, the
subscriber query's two-level gate (per-type rule AND channel master AND
verified address), and the backfill migration from the legacy alert_*
columns.  Additive layer: nothing reads these tables for live delivery
yet, so these prove the store in isolation.
"""

from __future__ import annotations

import os

os.environ.setdefault("ENCRYPTION_KEY", "")

from capabilities.permissions.roles import Role


async def _seed(pg_db):
    from interfaces.api.auth import _hash_password
    acct = await pg_db.create_account("Notif Co")
    u = await pg_db.create_user_with_email(
        email=f"o.{acct.id}@x.com", password_hash=_hash_password("password12345"),
        account_id=acct.id, role=Role.OWNER, display_name="O",
    )
    return acct.id, (u.telegram_id or u.id)


# ── Mixin CRUD ───────────────────────────────────────────────────────

async def test_set_and_list_prefs(pg_db):
    acct, uid = await _seed(pg_db)
    await pg_db.set_notification_pref(acct, "user", uid, "telegram_dm", "fuel", enabled=True)
    await pg_db.set_notification_pref(acct, "user", uid, "telegram_dm", "faults", enabled=False)
    await pg_db.set_notification_pref(acct, "user", uid, "email", "fuel", enabled=True, cadence="daily")
    rows = await pg_db.list_recipient_notification_prefs(acct, "user", uid)
    got = {(r["channel"], r["alert_type"]): (r["enabled"], r["cadence"]) for r in rows}
    assert got[("telegram_dm", "fuel")] == (True, "immediate")
    assert got[("telegram_dm", "faults")] == (False, "immediate")
    assert got[("email", "fuel")] == (True, "daily")

    # Upsert overwrites in place (no duplicate row).
    await pg_db.set_notification_pref(acct, "user", uid, "telegram_dm", "fuel", enabled=False)
    rows = await pg_db.list_recipient_notification_prefs(acct, "user", uid)
    fuel = [r for r in rows if r["channel"] == "telegram_dm" and r["alert_type"] == "fuel"]
    assert len(fuel) == 1 and fuel[0]["enabled"] is False


async def test_channel_connection_roundtrip(pg_db):
    acct, uid = await _seed(pg_db)
    assert await pg_db.get_notification_channel(acct, "user", uid, "email") is None
    await pg_db.upsert_notification_channel(
        acct, "user", uid, "email", address="me@x.com", verified=True)
    ch = await pg_db.get_notification_channel(acct, "user", uid, "email")
    assert ch["address"] == "me@x.com" and ch["verified"] and ch["enabled_master"]
    # Master switch off, address kept.
    await pg_db.upsert_notification_channel(
        acct, "user", uid, "email", address="me@x.com", verified=True, enabled_master=False)
    assert (await pg_db.get_notification_channel(acct, "user", uid, "email"))["enabled_master"] is False


async def test_subscribers_gate_type_master_and_verified(pg_db):
    """A recipient is a subscriber only when the per-type rule is on AND
    the channel master is on AND the address is verified."""
    acct, uid = await _seed(pg_db)
    await pg_db.set_notification_pref(acct, "user", uid, "telegram_dm", "fuel", enabled=True)

    # No channel connection yet → not a subscriber.
    assert await pg_db.get_notification_subscribers(acct, "fuel", "telegram_dm") == []

    # Verified + master on → subscriber.
    await pg_db.upsert_notification_channel(
        acct, "user", uid, "telegram_dm", address="55501", verified=True)
    subs = await pg_db.get_notification_subscribers(acct, "fuel", "telegram_dm")
    assert [s["recipient_id"] for s in subs] == [str(uid)]
    assert subs[0]["address"] == "55501"

    # Master off → drops out.
    await pg_db.upsert_notification_channel(
        acct, "user", uid, "telegram_dm", address="55501", verified=True, enabled_master=False)
    assert await pg_db.get_notification_subscribers(acct, "fuel", "telegram_dm") == []

    # Master back on but pref for a DIFFERENT type → not for 'faults'.
    await pg_db.upsert_notification_channel(
        acct, "user", uid, "telegram_dm", address="55501", verified=True)
    assert await pg_db.get_notification_subscribers(acct, "faults", "telegram_dm") == []
    assert len(await pg_db.get_notification_subscribers(acct, "fuel", "telegram_dm")) == 1


async def test_wildcard_pref_matches_any_type(pg_db):
    acct, uid = await _seed(pg_db)
    await pg_db.set_notification_pref(acct, "user", uid, "telegram_dm", "*", enabled=True)
    await pg_db.upsert_notification_channel(
        acct, "user", uid, "telegram_dm", address="99", verified=True)
    for atype in ("fuel", "faults", "events"):
        assert len(await pg_db.get_notification_subscribers(acct, atype, "telegram_dm")) == 1


# ── Backfill migration ───────────────────────────────────────────────

async def test_backfill_from_legacy_columns(pg_db):
    """migrate_notification_matrix copies the legacy per-user alert_*
    columns → telegram_dm rows + a channel connection, idempotently."""
    from adapters.storage.platform_migrations import migrate_notification_matrix
    acct, uid = await _seed(pg_db)
    # The seeded user: fuel OFF, everything else default ON; alerts_on ON;
    # link a telegram_id so the channel-connection backfill has an address
    # (users without one can't receive Telegram DMs and are skipped).
    await pg_db._db.execute(
        "UPDATE users SET alert_fuel = 0, alerts_on = 1, telegram_id = 55501 "
        "WHERE account_id = ?",
        (acct,))
    await pg_db._db.commit()

    await migrate_notification_matrix(pg_db._db)

    rows = await pg_db.list_recipient_notification_prefs(acct, "user", uid)
    by_type = {r["alert_type"]: r["enabled"]
               for r in rows if r["channel"] == "telegram_dm"}
    assert by_type["fuel"] is False           # the OFF column
    assert by_type["faults"] is True          # default-ON column
    assert by_type["camera"] is True
    # Channel connection carries telegram_id + the alerts_on master.
    ch = await pg_db.get_notification_channel(acct, "user", uid, "telegram_dm")
    assert ch and ch["enabled_master"] is True and ch["address"]

    # Idempotent: a user edit survives a re-run (ON CONFLICT DO NOTHING).
    await pg_db.set_notification_pref(acct, "user", uid, "telegram_dm", "fuel", enabled=True)
    await migrate_notification_matrix(pg_db._db)
    rows2 = await pg_db.list_recipient_notification_prefs(acct, "user", uid)
    fuel = [r for r in rows2 if r["channel"] == "telegram_dm" and r["alert_type"] == "fuel"][0]
    assert fuel["enabled"] is True            # the re-run did NOT clobber the edit
