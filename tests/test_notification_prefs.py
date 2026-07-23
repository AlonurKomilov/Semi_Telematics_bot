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
    got = {(r["channel"], r["category"]): (r["enabled"], r["cadence"]) for r in rows}
    assert got[("telegram_dm", "fuel")] == (True, "immediate")
    assert got[("telegram_dm", "faults")] == (False, "immediate")
    assert got[("email", "fuel")] == (True, "daily")

    # Upsert overwrites in place (no duplicate row).
    await pg_db.set_notification_pref(acct, "user", uid, "telegram_dm", "fuel", enabled=False)
    rows = await pg_db.list_recipient_notification_prefs(acct, "user", uid)
    fuel = [r for r in rows if r["channel"] == "telegram_dm" and r["category"] == "fuel"]
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


async def test_subscribers_return_raw_rows_role_gate_moved_to_dispatch(pg_db):
    """get_notification_subscribers returns the RAW matrix match — the
    role/audience gate moved OUT of storage INTO dispatch, so this query no
    longer imports capabilities.alerting at all."""
    acct = (await pg_db.create_account("RoleGate Co")).id
    u = await pg_db.create_user(telegram_id=4242, account_id=acct, role=Role.DRIVER)
    await pg_db.set_notification_pref(acct, "user", u.id, "email", "alert.fuel", enabled=True)
    await pg_db.upsert_notification_channel(
        acct, "user", u.id, "email", address="d@x.com", verified=True)

    # The storage query now returns the RAW matrix match — it no longer
    # applies a role gate (that moved to dispatch's audience filter).  So a
    # DRIVER pref for a fleet-only type is INCLUDED here; it's dropped later
    # in dispatch (see test_dispatch_audience_drops_ineligible_role).
    subs = await pg_db.get_notification_subscribers(acct, "alert.fuel", "email")
    assert [s["recipient_id"] for s in subs] == [str(u.id)]

    # get_roles_for_users backs the dispatch audience gate.
    assert await pg_db.get_roles_for_users([u.id]) == {u.id: "driver"}


# ── Category rename migration (existing installs) ────────────────────

async def test_category_rename_migration_on_existing_install(pg_db):
    """Simulate a PRE-rename install: notification_pref with an
    ``alert_type`` column + bare values.  The migration renames the column
    to ``category`` and namespaces the values to ``alert.<type>`` (leaving
    ``'*'`` alone), and is idempotent."""
    from adapters.storage.platform_migrations import (
        migrate_notification_category_rename)
    db = pg_db._db
    await db.execute("DROP TABLE IF EXISTS notification_pref")
    await db.execute("""
        CREATE TABLE notification_pref (
            account_id INTEGER NOT NULL, recipient_type TEXT NOT NULL,
            recipient_id TEXT NOT NULL, channel TEXT NOT NULL,
            alert_type TEXT NOT NULL, enabled INTEGER NOT NULL DEFAULT 1,
            cadence TEXT NOT NULL DEFAULT 'immediate',
            updated_at TEXT NOT NULL DEFAULT '',
            PRIMARY KEY (account_id, recipient_type, recipient_id, channel, alert_type))""")
    for atype in ("fuel", "*"):
        await db.execute(
            "INSERT INTO notification_pref (account_id, recipient_type, "
            "recipient_id, channel, alert_type, enabled, cadence, updated_at) "
            "VALUES (1, 'user', '5', 'email', ?, 1, 'daily', '')", (atype,))
    await db.commit()

    await migrate_notification_category_rename(db)
    cur = await db.execute("SELECT category FROM notification_pref")
    assert {r[0] for r in await cur.fetchall()} == {"alert.fuel", "*"}  # '*' preserved

    # Idempotent — a second run finds `category` already and no-ops.
    await migrate_notification_category_rename(db)
    cur = await db.execute("SELECT category FROM notification_pref")
    assert {r[0] for r in await cur.fetchall()} == {"alert.fuel", "*"}


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
    by_cat = {r["category"]: r["enabled"]
              for r in rows if r["channel"] == "telegram_dm"}
    assert by_cat["alert.fuel"] is False       # the OFF column (namespaced)
    assert by_cat["alert.faults"] is True      # default-ON column
    assert by_cat["alert.camera"] is True
    # Channel connection carries telegram_id + the alerts_on master.
    ch = await pg_db.get_notification_channel(acct, "user", uid, "telegram_dm")
    assert ch and ch["enabled_master"] is True and ch["address"]

    # Idempotent: a user edit survives a re-run (ON CONFLICT DO NOTHING).
    await pg_db.set_notification_pref(acct, "user", uid, "telegram_dm", "alert.fuel", enabled=True)
    await migrate_notification_matrix(pg_db._db)
    rows2 = await pg_db.list_recipient_notification_prefs(acct, "user", uid)
    fuel = [r for r in rows2 if r["channel"] == "telegram_dm" and r["category"] == "alert.fuel"][0]
    assert fuel["enabled"] is True            # the re-run did NOT clobber the edit


# ── Shadow compare: matrix reader == column reader (the 2b safety) ────

async def _mk_user(pg_db, acct, tg, role, **prefs):
    u = await pg_db.create_user(telegram_id=tg, account_id=acct, role=role)
    if prefs:
        await pg_db.update_user(u.id, **prefs)   # sets columns + write-also mirror
    return u


async def test_matrix_reader_matches_column_reader(pg_db):
    """For every alert type, the matrix-backed reader returns EXACTLY the
    same subscribers as the legacy-column reader — the equivalence proof
    that makes the 2b reader flip safe."""
    from adapters.storage.platform_migrations import migrate_notification_matrix
    acct = (await pg_db.create_account("Shadow Co")).id
    await _mk_user(pg_db, acct, 2001, Role.FLEET,
                   alerts_on=True, alert_fuel=False)
    await _mk_user(pg_db, acct, 2002, Role.DISPATCHER,
                   alerts_on=True, alert_faults=False)
    await _mk_user(pg_db, acct, 2003, Role.SAFETY, alerts_on=True)
    await _mk_user(pg_db, acct, 2004, Role.FLEET,
                   alerts_on=False, alert_faults=True)   # master OFF

    # Backfill completes the matrix from the columns (write-also only
    # mirrored the CHANGED types); ON CONFLICT DO NOTHING leaves the
    # already-mirrored rows intact.
    await migrate_notification_matrix(pg_db._db)

    TYPES = ("faults", "health", "fuel", "geofence", "events",
             "parking", "camera")
    for atype in TYPES:
        col = {u.id for u in await pg_db.get_typed_alert_subscribers(acct, atype)}
        mat = {u.id for u in await pg_db.get_typed_alert_subscribers_via_matrix(acct, atype)}
        assert col == mat, (atype, col, mat)

    # Write-also keeps them in lockstep AFTER backfill: flip a live toggle
    # and both readers still agree with no re-backfill.
    u = await pg_db.get_user_by_telegram_id(2003)
    await pg_db.update_user(u.id, alert_events=False)
    for atype in TYPES:
        col = {x.id for x in await pg_db.get_typed_alert_subscribers(acct, atype)}
        mat = {x.id for x in await pg_db.get_typed_alert_subscribers_via_matrix(acct, atype)}
        assert col == mat, ("after-edit", atype, col, mat)


async def test_matrix_excludes_users_without_telegram(pg_db):
    """The only deliberate difference: a user with prefs but NO telegram
    address is a 'subscriber' in the columns yet correctly excluded by
    the matrix (nothing to DM) — documented, not a regression."""
    from adapters.storage.platform_migrations import migrate_notification_matrix
    from interfaces.api.auth import _hash_password
    acct = (await pg_db.create_account("NoTg Co")).id
    email_user = await pg_db.create_user_with_email(
        email=f"e.{acct}@x.com", password_hash=_hash_password("password12345"),
        account_id=acct, role=Role.FLEET,
    )
    await pg_db.update_user(email_user.id, alerts_on=True)   # no telegram_id
    await migrate_notification_matrix(pg_db._db)

    col = {u.id for u in await pg_db.get_typed_alert_subscribers(acct, "faults")}
    mat = {u.id for u in await pg_db.get_typed_alert_subscribers_via_matrix(acct, "faults")}
    assert email_user.id in col          # columns include the no-telegram user
    assert email_user.id not in mat      # matrix (correctly) does not


# ── Reader flip flag (2b-2): default OFF = columns, ON = matrix ───────

async def test_reader_flip_flag_switches_source(pg_db, monkeypatch):
    """NOTIFICATIONS_MATRIX_READER routes the typed reader to the matrix.
    Proven equivalent — this just confirms the flag actually switches
    the source and that a matrix-only edit is invisible until flipped."""
    from adapters.storage.platform_migrations import migrate_notification_matrix
    acct = (await pg_db.create_account("Flip Co")).id
    u = await pg_db.create_user(telegram_id=3001, account_id=acct, role=Role.FLEET)
    await pg_db.update_user(u.id, alerts_on=True, alert_faults=True)
    await migrate_notification_matrix(pg_db._db)

    # Default OFF → column reader.
    monkeypatch.delenv("NOTIFICATIONS_MATRIX_READER", raising=False)
    assert {x.id for x in await pg_db.get_typed_alert_subscribers(acct, "faults")} == {u.id}

    # Flip ON → matrix reader (same result here — proven equivalent).
    monkeypatch.setenv("NOTIFICATIONS_MATRIX_READER", "1")
    assert {x.id for x in await pg_db.get_typed_alert_subscribers(acct, "faults")} == {u.id}

    # A MATRIX-ONLY change (bypassing the columns) is seen only when ON —
    # proving the flag genuinely selects the source.
    await pg_db.set_notification_pref(acct, "user", u.id, "telegram_dm", "alert.faults", enabled=False)
    assert await pg_db.get_typed_alert_subscribers(acct, "faults") == []   # ON → matrix (now off)
    monkeypatch.delenv("NOTIFICATIONS_MATRIX_READER", raising=False)
    assert {x.id for x in await pg_db.get_typed_alert_subscribers(acct, "faults")} == {u.id}  # OFF → column still on
