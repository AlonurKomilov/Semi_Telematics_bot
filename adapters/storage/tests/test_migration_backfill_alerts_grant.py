"""``migrate_backfill_alerts_grant`` — the Alerts inbox becomes a stored
grant without anyone gaining or losing it.

The derivation it replaces read the role's EFFECTIVE vehicle visibility
(stored override, else seed).  The new seed reads the SEED's.  Rows that
diverge are the only ones that would shift on deploy, and this migration
writes them what they used to resolve to.  The table gates real
authorization, so the transform is tested directly (the
``test_migration_config_perm_keys`` pattern): insert blobs, run, assert.
"""
import json

import pytest

from adapters.storage.platform_migrations import migrate_backfill_alerts_grant


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


@pytest.mark.asyncio
async def test_a_stored_narrowing_closes_the_inbox_and_a_stored_widening_opens_it(seeded_db):
    db, account, company = seeded_db["db"], seeded_db["account"], seeded_db["company"]
    # an owner took vehicles away from Accounting (seed sees vehicles)
    await _insert(db, account.id, "accounting", company.id, {"can_view_vehicles": False})
    # an owner gave Recruiter vehicles (seed does not) — under a tier key too
    await _insert(db, account.id, "recruiter__manager", company.id, {"can_view_vehicles": True})
    # a pre-sweep row that stores only a legacy spelling still diverges
    await _insert(db, account.id, "hr", company.id, {"can_vehicle_all": False, "can_vehicle_vehicle": True})

    await migrate_backfill_alerts_grant(db._db)

    assert (await _read(db, account.id, "accounting", company.id))["can_view_alerts"] is False
    assert (await _read(db, account.id, "recruiter__manager", company.id))["can_view_alerts"] is True
    hr = await _read(db, account.id, "hr", company.id)
    assert hr["can_view_alerts"] is True        # OR over the keys it stores
    assert hr["can_vehicle_all"] is False       # the vehicle keys are the sweep's, untouched


@pytest.mark.asyncio
async def test_rows_that_resolve_from_the_seed_are_left_alone(seeded_db):
    db, account, company = seeded_db["db"], seeded_db["account"], seeded_db["company"]
    # no vehicle key stored → seed == seed, no shift, nothing written
    await _insert(db, account.id, "dispatcher", company.id, {"can_view_loads": True})
    # already decided by an owner after the flip → not overwritten
    await _insert(db, account.id, "safety", company.id, {"can_view_vehicles": True, "can_view_alerts": False})

    await migrate_backfill_alerts_grant(db._db)

    assert "can_view_alerts" not in await _read(db, account.id, "dispatcher", company.id)
    assert (await _read(db, account.id, "safety", company.id))["can_view_alerts"] is False


@pytest.mark.asyncio
async def test_dead_alerts_keys_are_dropped_and_the_run_is_idempotent(seeded_db):
    db, account, company = seeded_db["db"], seeded_db["account"], seeded_db["company"]
    # a straggler the derived-flags sweep missed: it would OR-in as a grant now
    await _insert(db, account.id, "fleet", company.id, {"can_alerts_all": True, "can_view_vehicles": False})
    await _insert(db, account.id, "owner", company.id, None, raw='{"can_view_vehicles": tru')

    await migrate_backfill_alerts_grant(db._db)
    fleet = await _read(db, account.id, "fleet", company.id)
    assert fleet == {"can_view_vehicles": False, "can_view_alerts": False}

    await migrate_backfill_alerts_grant(db._db)     # second run: nothing changes
    assert await _read(db, account.id, "fleet", company.id) == fleet
