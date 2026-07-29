"""``migrate_config_perm_keys`` — legacy permission keys become the config
FAMILY keys inside stored ``role_permissions`` JSON.

The three mappings (docs/architecture/config.md §History):
  can_manage_role_pages / can_manage_role_config → can_manage_config_role
  can_manage_scorecard_rules                     → can_manage_config_all

This table gates real authorization, so the transform is tested directly
(the ``test_migration_102_settings_flags`` pattern): insert legacy-shaped
blobs, run the migration, assert the rewritten rows.
"""
import json

import pytest

from adapters.storage.platform_migrations import migrate_config_perm_keys


async def _insert_blob(db, account_id, role, company_id, perms):
    await db._db.execute(
        "INSERT INTO role_permissions "
        "(account_id, role, company_id, permissions, updated_by, updated_at) "
        "VALUES (?, ?, ?, ?, ?, ?)",
        (account_id, role, company_id, json.dumps(perms), 0, ""),
    )
    await db._db.commit()


async def _insert_raw(db, account_id, role, company_id, raw):
    await db._db.execute(
        "INSERT INTO role_permissions "
        "(account_id, role, company_id, permissions, updated_by, updated_at) "
        "VALUES (?, ?, ?, ?, ?, ?)",
        (account_id, role, company_id, raw, 0, ""),
    )
    await db._db.commit()


async def _read_raw(db, account_id, role, company_id):
    row = await db.read_one(
        "SELECT permissions FROM role_permissions "
        "WHERE account_id = ? AND role = ? AND company_id = ?",
        (account_id, role, company_id),
    )
    return row["permissions"]


async def _read_blob(db, account_id, role, company_id):
    return json.loads(await _read_raw(db, account_id, role, company_id))


@pytest.mark.asyncio
async def test_each_legacy_key_lands_on_its_family_key(seeded_db):
    db, account, company = (
        seeded_db["db"], seeded_db["account"], seeded_db["company"])

    await _insert_blob(db, account.id, "fleet", company.id, {
        "can_manage_role_pages": True,       # oldest spelling
        "can_alerts_all": True,
    })
    await _insert_blob(db, account.id, "dispatcher", company.id, {
        "can_manage_role_config": True,      # middle spelling
    })
    await _insert_blob(db, account.id, "safety", company.id, {
        "can_manage_scorecard_rules": True,  # an owner's explicit delegation
    })

    await migrate_config_perm_keys(db._db)

    fleet = await _read_blob(db, account.id, "fleet", company.id)
    assert fleet["can_manage_config_role"] is True
    assert "can_manage_role_pages" not in fleet
    assert fleet["can_alerts_all"] is True   # unrelated flag untouched

    disp = await _read_blob(db, account.id, "dispatcher", company.id)
    assert disp["can_manage_config_role"] is True
    assert "can_manage_role_config" not in disp

    safety = await _read_blob(db, account.id, "safety", company.id)
    assert safety["can_manage_config_all"] is True
    assert "can_manage_scorecard_rules" not in safety


@pytest.mark.asyncio
async def test_or_merge_and_multi_legacy_row(seeded_db):
    """A row holding several legacy keys migrates in one pass, and a True
    from either spelling survives an OR-merge — an owner's explicit
    revocation (False) must not overwrite a grant."""
    db, account, company = (
        seeded_db["db"], seeded_db["account"], seeded_db["company"])

    await _insert_blob(db, account.id, "hr", company.id, {
        "can_manage_role_pages": False,
        "can_manage_role_config": True,      # the True side must win
        "can_manage_scorecard_rules": False,
    })

    await migrate_config_perm_keys(db._db)

    hr = await _read_blob(db, account.id, "hr", company.id)
    assert hr["can_manage_config_role"] is True
    assert hr["can_manage_config_all"] is False
    for legacy in ("can_manage_role_pages", "can_manage_role_config",
                   "can_manage_scorecard_rules"):
        assert legacy not in hr


@pytest.mark.asyncio
async def test_malformed_json_skipped_and_run_is_idempotent(seeded_db):
    db, account, company = (
        seeded_db["db"], seeded_db["account"], seeded_db["company"])

    # The LIKE prefilter matches this row (key appears in the raw text),
    # but the JSON is broken — the migration must skip it, not die and
    # strand every row after it.
    await _insert_raw(db, account.id, "accounting", company.id,
                      '{"can_manage_scorecard_rules": tru')
    await _insert_blob(db, account.id, "recruiter", company.id, {
        "can_manage_scorecard_rules": True,
    })

    await migrate_config_perm_keys(db._db)

    assert (await _read_raw(db, account.id, "accounting", company.id)
            == '{"can_manage_scorecard_rules": tru')     # untouched
    rec = await _read_blob(db, account.id, "recruiter", company.id)
    assert rec["can_manage_config_all"] is True

    # Second run: nothing legacy left (broken row aside) — a no-op.
    await migrate_config_perm_keys(db._db)
    rec2 = await _read_blob(db, account.id, "recruiter", company.id)
    assert rec2 == rec
