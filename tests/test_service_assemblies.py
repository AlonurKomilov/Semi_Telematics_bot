"""Assemblies — level 2 of System → Assembly → Part.

Pins the advisor rules that keep the hierarchy trustworthy: key and
system parent are immutable, labels fail open, new assignments need an
active key while archived keys survive on existing rows, and THE
DELEGATION RULE — labor always follows the task's system; parts follow
the task's system too, EXCEPT on activity-system tasks
(pm/inspection/other/untagged) where they delegate to their assembly's
system, so a PM oil filter counts as Engine spend rather than "PM"
forever.
"""
from __future__ import annotations

import os
import sys

os.environ.setdefault("ENCRYPTION_KEY", "test-key-32-chars-min-aaaaaaaaaaaaaaaaaaaa")
os.environ.setdefault("JWT_SECRET", "test-secret-32-chars-or-more-please-aaaaaaaaaaaa")
sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

import pytest
import pytest_asyncio

from adapters.storage.service_assemblies import (
    SERVICE_ASSEMBLIES, normalize_assembly_key, suggest_assembly_for,
)
from adapters.storage.service_tasks import SYSTEM_KEYS


@pytest_asyncio.fixture
async def acct(db):
    return (await db.create_account("Assembly Co")).id


# ── The vocabulary itself ───────────────────────────────────────────

def test_every_assembly_parents_a_real_system():
    for key, _label, system in SERVICE_ASSEMBLIES:
        assert system in SYSTEM_KEYS, f"{key} → {system}"
    keys = [k for k, _, _ in SERVICE_ASSEMBLIES]
    assert len(keys) == len(set(keys)), "duplicate assembly keys"


def test_normalize_assembly_key():
    assert normalize_assembly_key("Fifth Wheel & Coupling") == "fifth_wheel_coupling"
    assert normalize_assembly_key("  RADIATOR ") == "radiator"
    assert normalize_assembly_key("!!!") == ""


def test_suggest_assembly_for_parts():
    assert suggest_assembly_for("Radiator hose, upper") == "coolant_hoses"
    assert suggest_assembly_for("Brake pad set DD15") == "pads_shoes"
    assert suggest_assembly_for("DEF doser valve") == "scr_dosing"
    assert suggest_assembly_for("Wiper blade 22in") == "wipers"
    assert suggest_assembly_for("Thermo King belt") == "reefer_unit" or \
        suggest_assembly_for("Thermo King belt") == "belts_tensioners"
    # No specific match → no suggestion, never a guess.
    assert suggest_assembly_for("Grease tube") == ""
    assert suggest_assembly_for("") == ""


# ── Library CRUD + immutability ─────────────────────────────────────

@pytest.mark.asyncio
async def test_library_seeded_and_crud(db):
    rows = await db.list_service_assemblies()
    assert len(rows) >= len(SERVICE_ASSEMBLIES)
    radiator = next(r for r in rows if r["key"] == "radiator")
    assert radiator["system_key"] == "cooling"

    made = await db.create_service_assembly("Cab Ladder", "body_cab")
    assert made and made["key"] == "cab_ladder"
    # Duplicate key refused.
    assert await db.create_service_assembly("Cab  Ladder!", "body_cab") is None
    # Unknown system refused.
    assert await db.create_service_assembly("Widget", "spaceship") is None

    # Label renames; key and system are NOT updatable fields at all.
    assert await db.update_service_assembly(made["id"], label="Cab Ladders") is True
    assert await db.update_service_assembly(
        made["id"], system_key="engine") is False        # not an allowed field
    assert await db.update_service_assembly(
        made["id"], key="hacked") is False               # nor the key
    fresh = next(r for r in await db.list_service_assemblies()
                 if r["id"] == made["id"])
    assert fresh["label"] == "Cab Ladders"
    assert fresh["key"] == "cab_ladder" and fresh["system_key"] == "body_cab"


@pytest.mark.asyncio
async def test_assignment_validation_and_archived_grace(db, acct):
    part = await db.resolve_or_create_part(acct, "Radiator Hose Upper")
    # Active key assigns.
    assert await db.update_catalog_part(
        part["id"], acct, assembly_key="coolant_hoses") is True
    # Unknown key fails closed.
    assert await db.update_catalog_part(
        part["id"], acct, assembly_key="not_a_thing") is False

    # Archive the assembly: the EXISTING row keeps its key (re-saving
    # the same value stays legal), but a NEW assignment is refused.
    entry = next(r for r in await db.list_service_assemblies()
                 if r["key"] == "coolant_hoses")
    assert await db.update_service_assembly(entry["id"], status="archived")
    assert await db.update_catalog_part(
        part["id"], acct, assembly_key="coolant_hoses") is True   # unchanged
    other = await db.resolve_or_create_part(acct, "Heater Hose Lower")
    assert await db.update_catalog_part(
        other["id"], acct, assembly_key="coolant_hoses") is False  # new → no

    # Labels fail open: archived keys still resolve for display.
    labels = await db.assembly_labels()
    assert labels["coolant_hoses"]["label"] == "Hoses & Piping"
    # Clearing back to Unassigned is always allowed.
    assert await db.update_catalog_part(
        part["id"], acct, assembly_key="") is True


# ── THE DELEGATION RULE in the reports ──────────────────────────────

async def _line(db, acct, wo, part_name, assembly, task_slug, cost):
    part = await db.resolve_or_create_part(acct, part_name)
    if assembly:
        assert await db.update_catalog_part(
            part["id"], acct, assembly_key=assembly) is True
    await db.add_work_order_part(
        wo, part_name=part_name, part_id=part["id"],
        service_task=task_slug, total_cost=cost)


@pytest.mark.asyncio
async def test_delegation_rule_in_cost_by_system(db, acct):
    wo = await db.add_work_order(acct, "", "234", "Shop",
                                 service_date="2026-07-01")
    # Component-system task: TASK WINS even though the part's assembly
    # says Cooling — the radiator hose bought during a brake job stays
    # Brake spend (owner rule 3).
    await _line(db, acct, wo, "Radiator Hose A", "coolant_hoses", "brakes", 100)
    # Activity-system task (PM): parts DELEGATE to the assembly's
    # system — the oil filter in a PM is Engine spend, not "PM".
    await _line(db, acct, wo, "Oil Filter B", "oil_lubrication", "pm_service", 40)
    # Activity task, part with NO assembly → stays under PM.
    await _line(db, acct, wo, "Shop Rag C", "", "pm_service", 10)
    # Untagged line with a known assembly → delegates too.
    await _line(db, acct, wo, "Wiper Blade D", "wipers", "", 25)
    # Labor on the PM task: labor ALWAYS follows the task's system.
    await db.add_work_order_labor(
        wo, acct, description="PM labor", service_task="pm_service",
        total_cost=200)

    rows = {r["system"]: r for r in await db.cost_by_system(acct)}
    assert rows["Brakes"]["total_spent"] == 100          # task won
    assert rows["Engine"]["total_spent"] == 40           # delegated
    assert rows["Body & Cab"]["total_spent"] == 25       # untagged delegated
    assert rows["Preventive Maintenance"]["total_spent"] == 10   # no assembly
    assert rows["Preventive Maintenance"]["labor_spent"] == 200  # labor stays


@pytest.mark.asyncio
async def test_cost_by_assembly_agrees_with_its_bar(db, acct):
    """A bar and its drill-down must contain the same lines."""
    wo = await db.add_work_order(acct, "", "234", "Shop",
                                 service_date="2026-07-01")
    await _line(db, acct, wo, "Rad Hose", "coolant_hoses", "coolant", 60)
    await _line(db, acct, wo, "Water Pump X", "water_pump", "coolant", 300)
    await _line(db, acct, wo, "Mystery Fitting", "", "coolant", 15)
    # An ENGINE-task line whose assembly is cooling: task wins, so it
    # appears under Cooling's bar AND its drill-down.
    await _line(db, acct, wo, "Thermostat Y", "thermostat", "coolant", 45)
    # A PM-task oil filter delegates to Engine — must NOT appear here.
    await _line(db, acct, wo, "Oil Filter Z", "oil_lubrication", "pm_service", 40)

    # Labor on the coolant task: reaches the drill-down via the
    # TASK's assembly (none here → Unassigned), and both measures
    # must reconcile with the bar exactly.
    await db.add_work_order_labor(
        wo, acct, description="Flush labor", service_task="coolant",
        total_cost=150)

    rows = await db.cost_by_assembly(acct, "cooling")
    total = round(sum(r["total_spent"] for r in rows), 2)
    labor = round(sum(r["labor_spent"] for r in rows), 2)
    bar = next(r for r in await db.cost_by_system(acct)
               if r["system_key"] == "cooling")
    assert total == bar["total_spent"] == 420.0
    assert labor == bar["labor_spent"] == 150.0
    by_label = {r["assembly"]: r["total_spent"] for r in rows}
    assert by_label["Water Pump"] == 300
    assert by_label["Unassigned"] == 15                  # visible, reconciles
    unass = next(r for r in rows if r["assembly_key"] == "")
    assert unass["labor_spent"] == 150.0   # coolant task has no assembly


# ── Bulk apply is confirm-gated and blank-only ──────────────────────

@pytest.mark.asyncio
async def test_bulk_apply_only_touches_blanks(db, acct):
    from adapters.storage.service_assemblies import suggest_assembly_for
    hosed = await db.resolve_or_create_part(acct, "Radiator Hose Kit")
    manual = await db.resolve_or_create_part(acct, "Brake Pad Set FR")
    # A human already chose something the matcher would disagree with.
    assert await db.update_catalog_part(
        manual["id"], acct, assembly_key="drums_rotors") is True

    # Simulate the endpoint's core loop.
    applied = 0
    for part in await db.list_parts_catalog(acct):
        if part.get("assembly_key"):
            continue
        hint = suggest_assembly_for(part.get("name") or "")
        if hint and await db.update_catalog_part(
                int(part["id"]), acct, assembly_key=hint):
            applied += 1
    assert applied >= 1
    assert (await db.get_catalog_part(hosed["id"], acct))["assembly_key"] \
        == "coolant_hoses"
    # The hand-set value survived.
    assert (await db.get_catalog_part(manual["id"], acct))["assembly_key"] \
        == "drums_rotors"


# ── Phase 1: the task's assembly — labor's route into level 2 ───────

async def _assembly_task(db, acct, name, system, assembly):
    t = await db.create_service_task(acct, name)
    assert await db.update_service_task(
        t["id"], acct, system_key=system, assembly_key=assembly) is True
    return t


@pytest.mark.asyncio
async def test_labor_lands_on_the_tasks_assembly(db, acct):
    """The gap this phase closes: a brake job's labor was invisible in
    every assembly drill-down because labor has no part."""
    await _assembly_task(db, acct, "Water Pump Replacement",
                         "cooling", "water_pump")
    wo = await db.add_work_order(acct, "", "234", "Shop",
                                 service_date="2026-07-01")
    await db.add_work_order_labor(
        wo, acct, description="R&R water pump",
        service_task="Water Pump Replacement", total_cost=400)

    rows = await db.cost_by_assembly(acct, "cooling")
    wp = next(r for r in rows if r["assembly_key"] == "water_pump")
    assert wp["labor_spent"] == 400.0
    bar = next(r for r in await db.cost_by_system(acct)
               if r["system_key"] == "cooling")
    assert round(sum(r["labor_spent"] for r in rows), 2) == bar["labor_spent"]


@pytest.mark.asyncio
async def test_part_own_assembly_always_beats_the_tasks(db, acct):
    """Fleetio stamps every line with the task's assembly — the lumping
    bug we refused.  A part that KNOWS its assembly keeps it; only a
    blank part falls back to the task's, display-time only."""
    await _assembly_task(db, acct, "Water Pump Replacement",
                         "cooling", "water_pump")
    wo = await db.add_work_order(acct, "", "234", "Shop",
                                 service_date="2026-07-01")
    # The gasket bought during the pump job is tagged thermostat by its
    # own catalog entry — it must NOT be lumped under water_pump.
    await _line(db, acct, wo, "Thermostat Gasket", "thermostat",
                "Water Pump Replacement", 60)
    # A blank part on the same task borrows the task's assembly.
    await _line(db, acct, wo, "Mystery Clamp", "",
                "Water Pump Replacement", 15)

    rows = {r["assembly_key"]: r for r in
            await db.cost_by_assembly(acct, "cooling")}
    assert rows["thermostat"]["total_spent"] == 60
    assert rows["water_pump"]["total_spent"] == 15
    # Nothing was WRITTEN to the blank part — fallback is display-only.
    part = next(p for p in await db.list_parts_catalog(acct)
                if p["name"] == "Mystery Clamp")
    assert not part.get("assembly_key")


@pytest.mark.asyncio
async def test_the_pair_rule(db, acct):
    """A task's assembly must live under the task's system — otherwise
    the same labor dollars file under two different systems (bar via
    task.system, drill via assembly.system)."""
    t = await db.create_service_task(acct, "Pair Rule Probe")
    assert await db.update_service_task(
        t["id"], acct, system_key="cooling") is True
    # Assembly from ANOTHER system → refused.
    assert await db.update_service_task(
        t["id"], acct, assembly_key="pads_shoes") is False
    # Matching pair → accepted.
    assert await db.update_service_task(
        t["id"], acct, assembly_key="water_pump") is True
    # Moving the system out from under the assembly → refused whole.
    assert await db.update_service_task(
        t["id"], acct, system_key="brakes") is False
    # System + matching assembly together → fine.
    assert await db.update_service_task(
        t["id"], acct, system_key="brakes", assembly_key="pads_shoes") is True
    # Clearing is always allowed; then the system may move freely.
    assert await db.update_service_task(
        t["id"], acct, assembly_key="") is True
    assert await db.update_service_task(
        t["id"], acct, system_key="cooling") is True
    # A task with NO system can't carry an assembly.
    bare = await db.create_service_task(acct, "Systemless Probe")
    assert await db.update_service_task(
        bare["id"], acct, assembly_key="water_pump") is False


@pytest.mark.asyncio
async def test_assembly_locked_on_standard_tasks(db, acct):
    """Same ownership split as name and system_key: on a Shared task
    the assembly is the operator's (fan-out delivers it in Phase 2)."""
    t = await db.create_service_task(acct, "Fauxnonical")
    await db._db.execute(
        "UPDATE service_tasks SET canonical_key = 'fauxnonical', "
        "system_key = 'cooling' WHERE id = ?", (t["id"],))
    await db._db.commit()
    assert await db.update_service_task(
        t["id"], acct, assembly_key="water_pump") is False
    # Their tuning fields still work on the same task.
    assert await db.update_service_task(
        t["id"], acct, expected_labor_hours=2.0) is True
