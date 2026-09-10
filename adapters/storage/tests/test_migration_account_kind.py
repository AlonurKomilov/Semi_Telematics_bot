"""accounts.kind — the trust class that superseded the is_test boolean.

The boolean could say "ours" or "not ours".  The 2026-09-08 probe left
thirty-three accounts that are neither a customer nor ours, and that we
want to keep ALIVE and watch rather than block — the third thing.  These
tests hold the migration and the alias: the column arrives with a safe
default, the flag backfills onto it, a rerun changes nothing, and the two
spellings can never disagree.
"""

from __future__ import annotations

import pytest

from adapters.storage.models import ACCOUNT_KINDS
from adapters.storage.platform_migrations import migrate_account_kind


@pytest.mark.asyncio
async def test_migration_is_idempotent_and_backfills_the_flag(seeded_db):
    db = seeded_db["db"]
    account = seeded_db["account"]
    await db.update_account(account.id, is_test=1)

    await migrate_account_kind(db._db)
    await migrate_account_kind(db._db)          # rerun: no error, no change

    got = await db.get_account(account.id)
    assert got.kind == "test"
    assert got.is_test is True


@pytest.mark.asyncio
async def test_default_kind_is_real(seeded_db):
    db = seeded_db["db"]
    fresh = await db.get_account(seeded_db["account"].id)
    assert fresh.kind == "real"
    assert fresh.is_test is False


@pytest.mark.parametrize("kind", ACCOUNT_KINDS)
@pytest.mark.asyncio
async def test_every_kind_round_trips_and_the_alias_follows(seeded_db, kind):
    db = seeded_db["db"]
    account = seeded_db["account"]
    assert await db.update_account(account.id, kind=kind) is True
    got = await db.get_account(account.id)
    assert got.kind == kind
    assert got.is_test is (kind == "test")
    assert got.is_active is True               # classification ≠ lifecycle


@pytest.mark.asyncio
async def test_an_unknown_kind_is_refused(seeded_db):
    db = seeded_db["db"]
    with pytest.raises(ValueError):
        await db.update_account(seeded_db["account"].id, kind="hostile")


@pytest.mark.asyncio
async def test_a_legacy_is_test_write_cannot_demote_a_monitored_account(seeded_db):
    """The old writer knows two states.  It may move test <-> real; it
    must not silently turn a watched account into a customer."""
    db = seeded_db["db"]
    account = seeded_db["account"]
    await db.update_account(account.id, kind="monitored")

    await db.update_account(account.id, is_test=0)      # legacy "not test"
    assert (await db.get_account(account.id)).kind == "monitored"

    await db.update_account(account.id, is_test=1)      # legacy "test"
    assert (await db.get_account(account.id)).kind == "test"

    await db.update_account(account.id, is_test=0)      # legacy back
    assert (await db.get_account(account.id)).kind == "real"


@pytest.mark.asyncio
async def test_user_counts_can_be_narrowed_to_customers(seeded_db):
    """The bot's health card: a probe's throwaway signups are not customers."""
    db = seeded_db["db"]
    account = seeded_db["account"]
    everyone = await db.count_all_users()
    assert everyone >= 1

    await db.update_account(account.id, kind="monitored")
    assert await db.count_all_users(kinds=("real",)) == everyone - (
        await db.count_all_users(kinds=("monitored",)))
    assert await db.count_all_users(kinds=("monitored",)) >= 1
    assert await db.count_all_users() == everyone      # default unchanged
