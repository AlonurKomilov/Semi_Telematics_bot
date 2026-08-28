"""Migration 102 — default the granular Settings-component flags
(``can_manage_permissions`` / ``can_manage_integrations`` /
``can_manage_storage`` / ``can_manage_work_hours``) from the stored
``can_manage_account`` value in customised role_permissions blobs."""
import json

import pytest

from adapters.storage.migrations import migrate_settings_component_flags

NEW_KEYS = (
    "can_manage_permissions", "can_manage_integrations",
    "can_manage_storage", "can_manage_work_hours",
)


async def _insert_blob(db, account_id, role, company_id, perms):
    await db._db.execute(
        "INSERT INTO role_permissions "
        "(account_id, role, company_id, permissions, updated_by, updated_at) "
        "VALUES (?, ?, ?, ?, ?, ?)",
        (account_id, role, company_id, json.dumps(perms), 0, ""),
    )
    await db._db.commit()


async def _read_blob(db, account_id, role, company_id):
    row = await db.read_one(
        "SELECT permissions FROM role_permissions "
        "WHERE account_id = ? AND role = ? AND company_id = ?",
        (account_id, role, company_id),
    )
    return json.loads(row["permissions"])


@pytest.mark.asyncio
async def test_migration_101_inherits_can_manage_account(seeded_db):
    db = seeded_db["db"]
    account = seeded_db["account"]
    company = seeded_db["company"]

    await _insert_blob(db, account.id, "admin", company.id, {
        "can_manage_account": True,
        "can_alerts_all": True,
    })

    await migrate_settings_component_flags(db._db)

    perms = await _read_blob(db, account.id, "admin", company.id)
    for k in NEW_KEYS:
        assert perms[k] is True, k
    assert perms["can_alerts_all"] is True  # unrelated flag untouched


@pytest.mark.asyncio
async def test_migration_101_false_base_and_existing_keys_kept(seeded_db):
    db = seeded_db["db"]
    account = seeded_db["account"]
    company = seeded_db["company"]

    # can_manage_account=False → new keys default False; a key already
    # set (e.g. by a pre-migration manual grant) must NOT be overwritten.
    await _insert_blob(db, account.id, "dispatcher", company.id, {
        "can_manage_account": False,
        "can_manage_integrations": True,   # pre-existing grant — keep
    })

    await migrate_settings_component_flags(db._db)

    perms = await _read_blob(db, account.id, "dispatcher", company.id)
    assert perms["can_manage_integrations"] is True   # not overwritten
    assert perms["can_manage_permissions"] is False
    assert perms["can_manage_storage"] is False
    assert perms["can_manage_work_hours"] is False


@pytest.mark.asyncio
async def test_migration_101_skips_blobs_without_base_key(seeded_db):
    db = seeded_db["db"]
    account = seeded_db["account"]
    company = seeded_db["company"]

    # No can_manage_account in the blob → resolve-time role defaults
    # apply; the migration must not invent keys.
    await _insert_blob(db, account.id, "driver", company.id, {
        "can_alerts_vehicle": True,
    })

    await migrate_settings_component_flags(db._db)

    perms = await _read_blob(db, account.id, "driver", company.id)
    for k in NEW_KEYS:
        assert k not in perms, k
    assert perms["can_alerts_vehicle"] is True
