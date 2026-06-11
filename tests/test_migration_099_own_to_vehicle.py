"""Migration 099 — rename the vehicle-scoped ``can_*_own`` permission keys
to ``can_*_vehicle`` inside stored ``role_permissions`` blobs, while leaving
the self-service ``_own`` flags (coaching/payroll/docs/risk) untouched."""
import json

import pytest

from adapters.storage.migrations import migrate_permissions_own_to_vehicle


@pytest.mark.asyncio
async def test_migration_099_renames_only_vehicle_own_keys(seeded_db):
    db = seeded_db["db"]
    account = seeded_db["account"]
    company = seeded_db["company"]

    old_perms = {
        "can_alerts_own": True,          # vehicle-scoped → rename
        "can_maintenance_own": False,    # vehicle-scoped → rename (value kept)
        "can_parking_own": True,         # vehicle-scoped → rename
        "can_alerts_all": True,          # unrelated → untouched
        "can_risk_report_own": True,     # self-service → KEEP
        "can_coaching_view_own": True,   # self-service → KEEP
        "can_driver_docs_own": True,     # self-service → KEEP
    }
    await db._db.execute(
        "INSERT INTO role_permissions "
        "(account_id, role, company_id, permissions, updated_by, updated_at) "
        "VALUES (?, ?, ?, ?, ?, ?)",
        (account.id, "driver", company.id, json.dumps(old_perms), 0, ""),
    )
    await db._db.commit()

    # Idempotent re-run on top of the init-time run.
    await migrate_permissions_own_to_vehicle(db._db)

    row = await db.read_one(
        "SELECT permissions FROM role_permissions "
        "WHERE account_id = ? AND role = ? AND company_id = ?",
        (account.id, "driver", company.id),
    )
    perms = json.loads(row["permissions"])

    # vehicle-scoped keys renamed, values preserved
    assert "can_alerts_own" not in perms
    assert perms["can_alerts_vehicle"] is True
    assert "can_maintenance_own" not in perms
    assert perms["can_maintenance_vehicle"] is False
    assert "can_parking_own" not in perms
    assert perms["can_parking_vehicle"] is True

    # self-service _own flags must NOT be renamed
    assert perms["can_risk_report_own"] is True
    assert perms["can_coaching_view_own"] is True
    assert perms["can_driver_docs_own"] is True

    # unrelated flag untouched
    assert perms["can_alerts_all"] is True


@pytest.mark.asyncio
async def test_migration_099_is_idempotent(seeded_db):
    db = seeded_db["db"]
    account = seeded_db["account"]
    company = seeded_db["company"]

    new_perms = {"can_alerts_vehicle": True, "can_risk_report_own": True}
    await db._db.execute(
        "INSERT INTO role_permissions "
        "(account_id, role, company_id, permissions, updated_by, updated_at) "
        "VALUES (?, ?, ?, ?, ?, ?)",
        (account.id, "fleet", company.id, json.dumps(new_perms), 0, ""),
    )
    await db._db.commit()

    await migrate_permissions_own_to_vehicle(db._db)

    row = await db.read_one(
        "SELECT permissions FROM role_permissions "
        "WHERE account_id = ? AND role = ? AND company_id = ?",
        (account.id, "fleet", company.id),
    )
    assert json.loads(row["permissions"]) == new_perms
