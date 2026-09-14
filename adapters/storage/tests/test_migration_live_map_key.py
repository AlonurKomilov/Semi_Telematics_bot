"""The sweep behind the live-map rename: rows stored under
``can_view_location`` come to say ``can_view_live_map`` themselves.

The alias makes the old key READ right; this makes the rows say it, so
the alias layer can die without taking every owner's revocation of the
map with it.  The table gates real authorization, so the transform is
tested directly (the ``test_migration_backfill_alerts_grant`` pattern):
insert blobs, run, assert — on both sides, platform and tenant, since
the table exists in both schemas.
"""
import json

import pytest

from adapters.storage.migrations import migrate_live_map_flag_rename
from adapters.storage.platform_migrations import migrate_live_map_key_rename

OLD, NEW = "can_view_location", "can_view_live_map"
SWEEPS = (migrate_live_map_key_rename, migrate_live_map_flag_rename)


async def _insert(db, account_id, role, company_id, perms, raw=None):
    await db._db.execute(
        "INSERT INTO role_permissions "
        "(account_id, role, company_id, permissions, updated_by, updated_at) "
        "VALUES (?, ?, ?, ?, ?, ?)",
        (account_id, role, company_id, raw if raw is not None else json.dumps(perms), 0, ""),
    )
    await db._db.commit()


async def _read(db, account_id, role, company_id):
    row = await db.read_one(
        "SELECT permissions FROM role_permissions "
        "WHERE account_id = ? AND role = ? AND company_id = ?",
        (account_id, role, company_id),
    )
    return json.loads(row["permissions"])


@pytest.mark.parametrize("sweep", SWEEPS, ids=["platform", "tenant"])
@pytest.mark.asyncio
async def test_a_revocation_under_the_old_key_becomes_one_under_the_new(seeded_db, sweep):
    db, account, company = seeded_db["db"], seeded_db["account"], seeded_db["company"]
    # the owner's revocation, as the pre-rename matrix wrote it
    await _insert(db, account.id, "dispatcher", company.id, {OLD: False, "can_view_loads": True})
    # a grant under the old key, under a tier key
    await _insert(db, account.id, "hr__manager", company.id, {OLD: True})
    # both spellings on one row: a grant from either side grants
    await _insert(db, account.id, "accounting", company.id, {OLD: False, NEW: True})
    # a row that never spoke the old name is not rewritten
    await _insert(db, account.id, "safety", company.id, {"can_view_faults": False})

    await sweep(db._db)

    d = await _read(db, account.id, "dispatcher", company.id)
    assert d[NEW] is False and OLD not in d and d["can_view_loads"] is True
    assert (await _read(db, account.id, "hr__manager", company.id)) == {NEW: True}
    assert (await _read(db, account.id, "accounting", company.id)) == {NEW: True}
    assert (await _read(db, account.id, "safety", company.id)) == {"can_view_faults": False}


@pytest.mark.parametrize("sweep", SWEEPS, ids=["platform", "tenant"])
@pytest.mark.asyncio
async def test_a_second_run_changes_nothing_and_bad_json_is_skipped(seeded_db, sweep):
    db, account, company = seeded_db["db"], seeded_db["account"], seeded_db["company"]
    await _insert(db, account.id, "dispatcher", company.id, {OLD: False})
    await _insert(db, account.id, "hr", company.id, {}, raw="{not json")
    await sweep(db._db)
    once = await _read(db, account.id, "dispatcher", company.id)
    await sweep(db._db)
    assert (await _read(db, account.id, "dispatcher", company.id)) == once == {NEW: False}
    row = await db.read_one(
        "SELECT permissions FROM role_permissions WHERE account_id = ? AND role = ?",
        (account.id, "hr"))
    assert row["permissions"] == "{not json", "an unparseable row is left for a person, not rewritten"
