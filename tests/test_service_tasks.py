"""Service tasks — the SSOT vocabulary shared by Maintenance and Work
Orders.

Pins the contracts that make it safe to depend on: seeding is
idempotent, the resolver NEVER rejects a write, standard tasks are
archive-only + name-locked, names are unique per account, nesting is
one level, and the migration backfills FROM THE DATA (so values that
drifted between our hardcoded lists survive).
"""
from __future__ import annotations

import os
import sys

os.environ.setdefault("ENCRYPTION_KEY", "test-key-32-chars-min-aaaaaaaaaaaaaaaaaaaa")
os.environ.setdefault("JWT_SECRET", "test-secret-32-chars-or-more-please-aaaaaaaaaaaa")
sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient

from adapters.storage import Role
from adapters.storage.service_tasks import (
    STANDARD_SERVICE_TASKS, service_task_name_key,
)
from interfaces.api.auth import create_jwt


@pytest_asyncio.fixture
async def acct(db):
    a = await db.create_account("Service Task Co")
    return a.id


# ── Seeding + identity ──────────────────────────────────────────────

@pytest.mark.asyncio
async def test_new_account_is_seeded_and_seeding_is_idempotent(db, acct):
    rows = await db.list_service_tasks(acct)
    assert len(rows) == len(STANDARD_SERVICE_TASKS)
    assert await db.seed_service_tasks(acct) == 0      # nothing new
    assert len(await db.list_service_tasks(acct)) == len(STANDARD_SERVICE_TASKS)


@pytest.mark.asyncio
async def test_canonical_key_is_stable_across_accounts(db, acct):
    """The whole reason we skipped a platform directory: the same key
    in every account makes fleet-wide comparison a GROUP BY."""
    other = (await db.create_account("Second Co")).id
    a_keys = {t["canonical_key"] for t in await db.list_service_tasks(acct)}
    b_keys = {t["canonical_key"] for t in await db.list_service_tasks(other)}
    assert a_keys == b_keys and "oil" in a_keys


@pytest.mark.asyncio
async def test_name_key_normalizes():
    assert service_task_name_key("  Brake   Service ") == "brake service"
    assert service_task_name_key("BRAKE SERVICE") == "brake service"


# ── The resolver: fail-open is the contract ─────────────────────────

@pytest.mark.asyncio
async def test_resolver_matches_key_then_name(db, acct):
    by_key = await db.resolve_service_task(acct, "oil")
    assert by_key["canonical_key"] == "oil"
    by_name = await db.resolve_service_task(acct, "engine oil & filter replacement")
    assert by_name["id"] == by_key["id"]


@pytest.mark.asyncio
async def test_resolver_never_drops_an_unknown_value(db, acct):
    """A writer must never be rejected by the vocabulary — unknown
    values land as ARCHIVED customs so they don't pollute pickers."""
    task = await db.resolve_service_task(acct, "custom_brake_job")
    assert task is not None
    assert task["canonical_key"] == ""
    assert task["status"] == "archived"
    assert task["name"] == "Brake Job"                 # de-namespaced
    # Stable: resolving again returns the same row, not a duplicate.
    again = await db.resolve_service_task(acct, "custom_brake_job")
    assert again["id"] == task["id"]


@pytest.mark.asyncio
async def test_resolver_blank_is_none(db, acct):
    assert await db.resolve_service_task(acct, "") is None
    assert await db.resolve_service_task_id(acct, "   ") is None


# ── Dual-write through the real writers ─────────────────────────────

@pytest.mark.asyncio
async def test_maintenance_task_write_populates_both(db, acct):
    tid = await db.add_maintenance_task(acct, "", "234", "brakes", "Front brakes")
    task = await db.get_maintenance_task(tid, acct)
    assert task["task_type"] == "brakes"               # legacy kept
    assert task["service_task_id"]                     # reference written
    st = await db.get_service_task(task["service_task_id"], acct)
    assert st["canonical_key"] == "brakes"


@pytest.mark.asyncio
async def test_drifted_slug_still_writes(db, acct):
    """'electrical' existed only in the frontend list — the AI tool
    would coerce it to custom.  It must now resolve properly."""
    tid = await db.add_maintenance_task(acct, "", "234", "electrical", "Wiring")
    task = await db.get_maintenance_task(tid, acct)
    st = await db.get_service_task(task["service_task_id"], acct)
    assert st["canonical_key"] == "electrical"


@pytest.mark.asyncio
async def test_work_order_lines_populate_both(db, acct):
    wo = await db.add_work_order(acct, "", "234", "Shop A")
    pid = await db.add_work_order_part(wo, part_name="Pad", service_task="brakes")
    lid = await db.add_work_order_labor(
        wo, acct, description="Brake job", service_task="brakes",
    )
    parts = await db.list_work_order_parts(wo)
    labor = await db.list_work_order_labor(wo, acct)
    part = next(p for p in parts if p["id"] == pid)
    line = next(l for l in labor if l["id"] == lid)
    assert part["service_task"] == "brakes" and part["service_task_id"]
    assert line["service_task"] == "brakes" and line["service_task_id"]
    assert part["service_task_id"] == line["service_task_id"]


@pytest.mark.asyncio
async def test_untagged_lines_stay_null(db, acct):
    wo = await db.add_work_order(acct, "", "234", "Shop A")
    pid = await db.add_work_order_part(wo, part_name="Misc")
    part = next(p for p in await db.list_work_order_parts(wo) if p["id"] == pid)
    assert part["service_task_id"] is None       # the 'untagged' bucket


# ── Standard-vs-custom rules ────────────────────────────────────────

@pytest.mark.asyncio
async def test_standard_task_is_name_locked_but_editable(db, acct):
    std = next(t for t in await db.list_service_tasks(acct)
               if t["canonical_key"] == "oil")
    assert await db.update_service_task(std["id"], acct, name="Renamed") is False
    assert await db.update_service_task(
        std["id"], acct, description="Full synthetic", expected_labor_hours=1.5,
    ) is True
    fresh = await db.get_service_task(std["id"], acct)
    assert fresh["name"] == std["name"] and fresh["expected_labor_hours"] == 1.5


@pytest.mark.asyncio
async def test_standard_task_cannot_be_deleted_only_archived(db, acct):
    std = next(t for t in await db.list_service_tasks(acct)
               if t["canonical_key"] == "oil")
    assert await db.delete_service_task(std["id"], acct) is False
    assert await db.update_service_task(std["id"], acct, status="archived") is True
    assert std["id"] not in {t["id"] for t in await db.list_service_tasks(acct)}
    assert std["id"] in {t["id"] for t in
                         await db.list_service_tasks(acct, include_archived=True)}


@pytest.mark.asyncio
async def test_custom_task_delete_guarded_by_usage(db, acct):
    task = await db.create_service_task(acct, "Air Line Repair")
    assert task and task["canonical_key"] == ""
    tid = await db.add_maintenance_task(acct, "", "234", "Air Line Repair", "Leak")
    assert await db.service_task_usage(task["id"], acct) == 1
    assert await db.delete_service_task(task["id"], acct) is False   # in use
    await db.delete_maintenance_task(tid, acct) \
        if hasattr(db, "delete_maintenance_task") else None
    # Unused custom deletes cleanly.
    spare = await db.create_service_task(acct, "Never Used Task")
    assert await db.delete_service_task(spare["id"], acct) is True


@pytest.mark.asyncio
async def test_duplicate_name_rejected(db, acct):
    assert await db.create_service_task(acct, "Widget Service") is not None
    assert await db.create_service_task(acct, "  widget   SERVICE ") is None


@pytest.mark.asyncio
async def test_nesting_is_one_level_only(db, acct):
    parent = await db.create_service_task(acct, "Brake Overhaul")
    child = await db.create_service_task(acct, "Bleed Lines", parent_id=parent["id"])
    assert child and child["parent_id"] == parent["id"]
    # A subtask may not itself have subtasks.
    assert await db.create_service_task(
        acct, "Deeper", parent_id=child["id"],
    ) is None
    # …nor via update.
    other = await db.create_service_task(acct, "Another Task")
    assert await db.update_service_task(
        other["id"], acct, parent_id=child["id"],
    ) is False


# ── API surface ─────────────────────────────────────────────────────

@pytest_asyncio.fixture
async def api(db):
    import infra.platform as cp
    cp._db = db
    a = await db.create_account("ST API Co")
    owner = await db.create_user(930001, a.id, role=Role.OWNER)
    driver = await db.create_user(930002, a.id, role=Role.DRIVER)
    from interfaces.api.app import create_api
    return {
        "app": create_api(), "acct": a.id,
        "owner": create_jwt(owner.telegram_id, a.id, "owner"),
        "driver": create_jwt(driver.telegram_id, a.id, "driver"),
    }


def _h(tok):
    return {"Authorization": f"Bearer {tok}"}


@pytest.mark.asyncio
async def test_api_crud_and_gates(api):
    async with AsyncClient(transport=ASGITransport(app=api["app"]),
                           base_url="http://t") as c:
        r = await c.get("/api/service-tasks", headers=_h(api["owner"]))
        assert r.status_code == 200
        assert r.json()["count"] == len(STANDARD_SERVICE_TASKS)

        r = await c.post("/api/service-tasks", headers=_h(api["owner"]),
                         json={"name": "Kingpin Service"})
        assert r.status_code == 200, r.text
        new_id = r.json()["id"]

        # Unique-name rule surfaces as a conflict, not a second row.
        r = await c.post("/api/service-tasks", headers=_h(api["owner"]),
                         json={"name": "kingpin service"})
        assert r.status_code == 409

        r = await c.put(f"/api/service-tasks/{new_id}", headers=_h(api["owner"]),
                        json={"description": "Grease + inspect"})
        assert r.status_code == 200
        assert r.json()["description"] == "Grease + inspect"

        r = await c.delete(f"/api/service-tasks/{new_id}", headers=_h(api["owner"]))
        assert r.status_code == 200

        # Drivers hold none of the read permissions → no vocabulary.
        r = await c.get("/api/service-tasks", headers=_h(api["driver"]))
        assert r.status_code == 403
        r = await c.post("/api/service-tasks", headers=_h(api["driver"]),
                         json={"name": "Sneaky"})
        assert r.status_code == 403


@pytest.mark.asyncio
async def test_api_standard_task_rename_rejected(api):
    async with AsyncClient(transport=ASGITransport(app=api["app"]),
                           base_url="http://t") as c:
        r = await c.get("/api/service-tasks", headers=_h(api["owner"]))
        std = next(t for t in r.json()["service_tasks"]
                   if t["canonical_key"] == "oil")
        r = await c.put(f"/api/service-tasks/{std['id']}", headers=_h(api["owner"]),
                        json={"name": "My Oil Change"})
        assert r.status_code == 422
        r = await c.delete(f"/api/service-tasks/{std['id']}", headers=_h(api["owner"]))
        assert r.status_code == 422


# ── Migration 162: backfill reads the DATA, not our code lists ──────

@pytest.mark.asyncio
async def test_migration_backfills_from_actual_data(db):
    from adapters.storage.migrations import migrate_service_tasks_ssot
    a = (await db.create_account("Backfill Co")).id

    # Simulate pre-migration rows: strip the ids the dual-write added
    # so the backfill has real work to do.
    t_oil = await db.add_maintenance_task(a, "", "234", "oil", "Oil due")
    t_odd = await db.add_maintenance_task(a, "", "234", "flux capacitor", "???")
    wo = await db.add_work_order(a, "", "234", "Shop")
    pid = await db.add_work_order_part(wo, part_name="Pad", service_task="brakes")
    lid = await db.add_work_order_labor(wo, a, description="Job", service_task="brakes")
    await db._db.execute(
        "UPDATE maintenance_tasks SET service_task_id = NULL WHERE account_id = ?", (a,))
    await db._db.execute("UPDATE work_order_parts SET service_task_id = NULL")
    await db._db.execute(
        "UPDATE work_order_labor SET service_task_id = NULL WHERE account_id = ?", (a,))
    await db._db.commit()

    await migrate_service_tasks_ssot(db._db)
    await migrate_service_tasks_ssot(db._db)          # idempotent

    assert (await db.get_maintenance_task(t_oil, a))["service_task_id"]
    # The unrecognised free-typed value survived rather than being dropped.
    odd = await db.get_maintenance_task(t_odd, a)
    assert odd["service_task_id"]
    odd_task = await db.get_service_task(odd["service_task_id"], a)
    assert odd_task["status"] == "archived" and odd_task["canonical_key"] == ""

    part = next(p for p in await db.list_work_order_parts(wo) if p["id"] == pid)
    line = next(l for l in await db.list_work_order_labor(wo, a) if l["id"] == lid)
    assert part["service_task_id"] and line["service_task_id"]


@pytest.mark.asyncio
async def test_sweep_catches_deploy_window_stragglers(db):
    """Migration 165: rows a not-yet-restarted worker wrote string-only
    during the rolling deploy get their reference filled in."""
    from adapters.storage.migrations import migrate_service_task_backfill_sweep
    a = (await db.create_account("Sweep Co")).id

    mt = await db.add_maintenance_task(a, "", "234", "brakes", "Squeal")
    odd = await db.add_maintenance_task(a, "", "234", "gremlin removal", "???")
    wo = await db.add_work_order(a, "", "234", "Shop")
    pid = await db.add_work_order_part(wo, part_name="Pad", service_task="brakes")
    lid = await db.add_work_order_labor(wo, a, description="Bleed",
                                        service_task="brakes")
    # Simulate the old code path: tag written, reference not.
    await db._db.execute(
        "UPDATE maintenance_tasks SET service_task_id = NULL WHERE account_id = ?",
        (a,))
    await db._db.execute(
        "UPDATE work_order_labor SET service_task_id = NULL WHERE account_id = ?",
        (a,))
    await db._db.execute(
        "UPDATE work_order_parts SET service_task_id = NULL "
        "WHERE work_order_id = ?", (wo,))
    await db._db.commit()

    await migrate_service_task_backfill_sweep(db._db)

    assert (await db.get_maintenance_task(mt, a))["service_task_id"]
    part = next(p for p in await db.list_work_order_parts(wo) if p["id"] == pid)
    line = next(l for l in await db.list_work_order_labor(wo, a) if l["id"] == lid)
    assert part["service_task_id"] and line["service_task_id"]

    # An unrecognised free-typed value is preserved, not dropped.
    odd_row = await db.get_maintenance_task(odd, a)
    assert odd_row["service_task_id"]
    odd_task = await db.get_service_task(odd_row["service_task_id"], a)
    assert odd_task["status"] == "archived" and odd_task["canonical_key"] == ""

    # Idempotent: a second pass is a no-op, not a duplicate-maker.
    before = len(await db.list_service_tasks(a, include_archived=True))
    await migrate_service_task_backfill_sweep(db._db)
    assert len(await db.list_service_tasks(a, include_archived=True)) == before


@pytest.mark.asyncio
async def test_sweep_leaves_the_legacy_strings_alone(db):
    """The sweep fills references; retiring the strings is a SEPARATE
    step, blocked until every reader moves (the DOT binder still
    selects inspections by task_type)."""
    from adapters.storage.migrations import migrate_service_task_backfill_sweep
    a = (await db.create_account("Sweep Keep Co")).id
    mt = await db.add_maintenance_task(a, "", "234", "dot_inspection", "Annual")
    await migrate_service_task_backfill_sweep(db._db)
    row = await db.get_maintenance_task(mt, a)
    assert row["task_type"] == "dot_inspection"      # compliance filter intact
    assert row["service_task_id"]


# ── The reference is now authoritative on READ ──────────────────────
#
# Consumers still ask for ``task_type`` / ``service_task``; the value
# they get is derived from service_task_id.  That's what lets the DOT
# binder, CSV export, report PDFs, AI tool and dashboard grid follow
# the reference without any of them changing.

@pytest.mark.asyncio
async def test_reads_derive_task_type_from_the_reference(db, acct):
    tid = await db.add_maintenance_task(acct, "", "234", "brakes", "Squeal")
    # Corrupt ONLY the legacy string; the reference still points at the
    # real task, so reads must ignore the stale text.
    await db._db.execute(
        "UPDATE maintenance_tasks SET task_type = 'STALE-GARBAGE' WHERE id = ?",
        (tid,))
    await db._db.commit()

    one = await db.get_maintenance_task(tid, acct)
    assert one["task_type"] == "brakes"          # reference wins
    listed = next(t for t in await db.get_maintenance_tasks(acct) if t["id"] == tid)
    assert listed["task_type"] == "brakes"


@pytest.mark.asyncio
async def test_dot_compliance_filter_follows_the_reference(db, acct):
    """capabilities/reporting/dot_binder.py selects the FMCSA binder's
    inspections with task_type == 'dot_inspection'.  It must keep
    matching once the reference is the source of truth."""
    tid = await db.add_maintenance_task(
        acct, "", "234", "dot_inspection", "Annual inspection")
    await db._db.execute(
        "UPDATE maintenance_tasks SET task_type = 'drifted' WHERE id = ?", (tid,))
    await db._db.commit()
    row = next(t for t in await db.get_maintenance_tasks(acct) if t["id"] == tid)
    assert (row.get("task_type") or "") == "dot_inspection"


@pytest.mark.asyncio
async def test_rows_without_a_reference_keep_their_string(db, acct):
    """A row written before the reference existed must not lose its
    label just because the join finds nothing."""
    tid = await db.add_maintenance_task(acct, "", "234", "brakes", "Old row")
    await db._db.execute(
        "UPDATE maintenance_tasks SET service_task_id = NULL, "
        "task_type = 'legacy_value' WHERE id = ?", (tid,))
    await db._db.commit()
    row = await db.get_maintenance_task(tid, acct)
    assert row["task_type"] == "legacy_value"


@pytest.mark.asyncio
async def test_work_order_lines_derive_their_task_too(db, acct):
    wo = await db.add_work_order(acct, "", "234", "Shop")
    pid = await db.add_work_order_part(wo, part_name="Pad", service_task="brakes")
    lid = await db.add_work_order_labor(
        wo, acct, description="Bleed", service_task="brakes")
    await db._db.execute(
        "UPDATE work_order_parts SET service_task = 'stale' WHERE id = ?", (pid,))
    await db._db.execute(
        "UPDATE work_order_labor SET service_task = 'stale' WHERE id = ?", (lid,))
    await db._db.commit()

    part = next(p for p in await db.list_work_order_parts(wo) if p["id"] == pid)
    line = next(l for l in await db.list_work_order_labor(wo, acct) if l["id"] == lid)
    assert part["service_task"] == "brakes" and line["service_task"] == "brakes"
