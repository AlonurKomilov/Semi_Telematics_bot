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
    in every account makes cross-account comparison a GROUP BY."""
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


# ── Systems: the reporting axis above a task ────────────────────────

@pytest.mark.asyncio
async def test_seeded_standards_carry_a_system(db, acct):
    from adapters.storage.service_tasks import _STANDARD_SYSTEMS
    tasks = {t["canonical_key"]: t for t in await db.list_service_tasks(acct)}
    assert tasks["brakes"]["system_key"] == "brakes"
    assert tasks["oil"]["system_key"] == "engine"
    assert tasks["dot_inspection"]["system_key"] == "inspection"
    # Every standard task is mapped — an unmapped one silently lands in
    # 'Unassigned' and quietly breaks the rollup it exists for.
    for key in _STANDARD_SYSTEMS:
        if key in tasks:
            assert tasks[key]["system_key"], f"{key} has no system"


@pytest.mark.asyncio
async def test_system_assignment_and_validation(db, acct):
    t = await db.create_service_task(acct, "Kingpin Job", system_key="trailer")
    assert t["system_key"] == "trailer"
    # An unknown system is refused on update…
    assert await db.update_service_task(t["id"], acct, system_key="spaceship") is False
    # …and silently dropped on create (a bad value must not block a write).
    t2 = await db.create_service_task(acct, "Odd Job", system_key="nonsense")
    assert t2["system_key"] == ""
    # Clearing back to unassigned is allowed.
    assert await db.update_service_task(t["id"], acct, system_key="") is True


@pytest.mark.asyncio
async def test_spend_by_system_answers_the_real_question(db, acct):
    """'What are brakes costing us?' — the report a flat task list
    can't produce."""
    wo = await db.add_work_order(acct, "", "234", "Shop A",
                                 service_date="2026-07-01")
    await db.add_work_order_part(
        wo, part_name="Pad", service_task="brakes", total_cost=300)
    await db.add_work_order_labor(
        wo, acct, description="Brake job", service_task="brakes", total_cost=200)
    await db.add_work_order_part(
        wo, part_name="Filter", service_task="oil", total_cost=50)
    # A line with no task at all still has to show up somewhere.
    await db.add_work_order_part(wo, part_name="Misc", total_cost=25)

    rows = {r["system"]: r for r in await db.cost_by_system(acct)}
    assert rows["Brakes"]["total_spent"] == 300
    assert rows["Brakes"]["labor_spent"] == 200
    assert rows["Engine"]["total_spent"] == 50
    assert rows["Unassigned"]["total_spent"] == 25   # nothing vanishes
    # Sorted by biggest spend first.
    assert (await db.cost_by_system(acct))[0]["system"] == "Brakes"


@pytest.mark.asyncio
async def test_spend_by_system_excludes_void(db, acct):
    wo = await db.add_work_order(acct, "", "234", "Shop", service_date="2026-07-01",
                                 payment_status="void")
    await db.add_work_order_part(
        wo, part_name="Pad", service_task="brakes", total_cost=9999)
    assert all(r["total_spent"] == 0 for r in await db.cost_by_system(acct))


@pytest.mark.asyncio
async def test_api_serves_the_system_vocabulary(api):
    """Served, not hardcoded in the dashboard — a second copy is how
    the old task vocabulary drifted into three disagreeing lists."""
    from adapters.storage.service_tasks import SERVICE_TASK_SYSTEMS
    async with AsyncClient(transport=ASGITransport(app=api["app"]),
                           base_url="http://t") as c:
        r = await c.get("/api/service-tasks/systems", headers=_h(api["owner"]))
        assert r.status_code == 200, r.text
        got = r.json()["systems"]
        assert len(got) == len(SERVICE_TASK_SYSTEMS)
        assert {"key": "brakes", "label": "Brakes"} in got


# ── Phase 1 of the 3-level scheme: the L1 holes ─────────────────────

@pytest.mark.asyncio
async def test_spn_faults_land_in_real_systems(db, acct):
    """The SPN→task map predates the vocabulary — 6 of 10 faults were
    mapped to 'custom' and landed fault-driven spend in the report's
    junk bucket.  A coolant fault must now roll up under Cooling."""
    from features.maintenance.service import _SPN_MAINTENANCE_MAP
    from adapters.storage.service_tasks import _STANDARD_SYSTEMS

    landings = {
        spn: _STANDARD_SYSTEMS.get(task, "")
        for spn, task in _SPN_MAINTENANCE_MAP.items()
    }
    assert landings[110] == "cooling" and landings[111] == "cooling"
    assert landings[97] == "fuel"
    assert landings[4331] == landings[3031] == landings[5246] == "exhaust"
    assert landings[100] == landings[101] == "engine"
    assert landings[91] == "brakes"
    # Owner decision: overspeed is a driver event, not a repair — it
    # STAYS in Other so it never pollutes maintenance spend.
    assert landings[190] == "other"

    # And end-to-end: writing a task with the mapped slug resolves to
    # a real service task in the right system.
    tid = await db.add_maintenance_task(
        acct, "", "234", _SPN_MAINTENANCE_MAP[110], "Coolant temperature issue")
    task = await db.get_maintenance_task(tid, acct)
    st = await db.get_service_task(task["service_task_id"], acct)
    assert st["system_key"] == "cooling"


@pytest.mark.asyncio
async def test_body_cab_task_seeded_and_reachable(db, acct):
    """Body & Cab was the one system with no task pointing at it —
    a cab-damage invoice could only land in Custom/Other."""
    tasks = {t["canonical_key"]: t for t in await db.list_service_tasks(acct)}
    assert "body_cab_repair" in tasks
    assert tasks["body_cab_repair"]["system_key"] == "body_cab"

    # The migration reaches EXISTING accounts (idempotently).
    from adapters.storage.migrations import migrate_body_cab_task
    await migrate_body_cab_task(db._db)
    await migrate_body_cab_task(db._db)
    again = [t for t in await db.list_service_tasks(acct)
             if t["canonical_key"] == "body_cab_repair"]
    assert len(again) == 1


@pytest.mark.asyncio
async def test_per_system_report_is_reexported(api):
    """The endpoint existed with no UI and no /reports re-export — the
    system layer produced a report nobody could see.  Pin the mount."""
    async with AsyncClient(transport=ASGITransport(app=api["app"]),
                           base_url="http://t") as c:
        r = await c.get("/api/reports/cost-reports/per-system?days=90",
                        headers=_h(api["owner"]))
        assert r.status_code == 200, r.text
        assert "rows" in r.json()


# ── Phase 2: suggest-confirm fill + the catch-all guard ─────────────

def test_suggest_system_for_names():
    from adapters.storage.service_tasks import suggest_system_for
    assert suggest_system_for("Radiator hose replacement") == "cooling"
    assert suggest_system_for("Alternator swap") == "electrical"
    assert suggest_system_for("Landing gear repair") == "trailer"
    assert suggest_system_for("Windshield crack") == "body_cab"
    # Longest keyword wins, not declaration order.
    assert suggest_system_for("Kingpin grease") == "steering"
    # Word boundary: 'def' must not hit 'defrost'.
    assert suggest_system_for("Defrost switch") != "exhaust"
    # Nothing specific → no suggestion, never a guess.
    assert suggest_system_for("Mystery thing") == ""
    assert suggest_system_for("") == ""


def test_system_bucket_collision():
    from adapters.storage.service_tasks import system_bucket_collision
    # A name that is just a system plus filler = a duplicate bucket.
    assert system_bucket_collision("Brakes Repair") == "brakes"
    assert system_bucket_collision("electrical") == "electrical"
    assert system_bucket_collision("Cooling System Work") == "cooling"
    # Specific jobs pass.
    assert system_bucket_collision("Brake Caliper Replacement") == ""
    assert system_bucket_collision("Battery Service") == ""
    assert system_bucket_collision("Tire Service") == ""


@pytest.mark.asyncio
async def test_list_offers_suggestions_only_where_needed(db, acct):
    """Unassigned active tasks get a hint; assigned ones don't; the
    hint is a SUGGESTION — nothing is written until confirmed."""
    hinted = await db.create_service_task(acct, "Radiator Hose Swap")
    assigned = await db.create_service_task(
        acct, "Coolant Flush Special", system_key="cooling")

    import infra.platform as cp
    cp._db = db
    from adapters.storage import Role
    from interfaces.api.app import create_api
    a2 = await db.create_user(970001, acct, role=Role.OWNER)
    tok = create_jwt(a2.telegram_id, acct, "owner")
    async with AsyncClient(transport=ASGITransport(app=create_api()),
                           base_url="http://t") as c:
        r = await c.get("/api/service-tasks", headers=_h(tok))
        rows = {t["id"]: t for t in r.json()["service_tasks"]}
        assert rows[hinted["id"]].get("suggested_system") == "cooling"
        assert "suggested_system" not in rows[assigned["id"]]
        # Still unassigned in storage — suggestion wrote nothing.
        assert (await db.get_service_task(hinted["id"], acct))["system_key"] == ""


@pytest.mark.asyncio
async def test_api_blocks_duplicate_system_buckets(api):
    """'Brakes Repair' isn't a job — it's the Brakes bucket again, and
    a second bucket splits the report the system layer exists for."""
    async with AsyncClient(transport=ASGITransport(app=api["app"]),
                           base_url="http://t") as c:
        r = await c.post("/api/service-tasks", headers=_h(api["owner"]),
                         json={"name": "Brakes Repair"})
        assert r.status_code == 422 and "Brakes" in r.json()["detail"]

        # The specific job sails through.
        r = await c.post("/api/service-tasks", headers=_h(api["owner"]),
                         json={"name": "Brake Caliper Replacement"})
        assert r.status_code == 200
        tid = r.json()["id"]

        # …and can't be RENAMED into a bucket either.
        r = await c.put(f"/api/service-tasks/{tid}", headers=_h(api["owner"]),
                        json={"name": "Suspension Repairs"})
        assert r.status_code == 422 and "Suspension" in r.json()["detail"]
