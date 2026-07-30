"""Operator-curated standard task library.

The contracts that make it safe for one operator edit to reach every
tenant: adding fans out to accounts that ALREADY exist, renaming keeps
the shared name in sync, archiving does NOT reach into accounts that
have history, and an account's own decision to archive its copy
survives a fan-out.
"""
from __future__ import annotations

import os
import sys

os.environ.setdefault("ENCRYPTION_KEY", "test-key-32-chars-min-aaaaaaaaaaaaaaaaaaaa")
os.environ.setdefault("JWT_SECRET", "test-secret-32-chars-or-more-please-aaaaaaaaaaaa")
sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

import pytest
import pytest_asyncio

from adapters.storage.service_task_library import canonical_key_from


@pytest_asyncio.fixture
async def lib(db):
    """Library table + its seed (mirrors the platform migration)."""
    from adapters.storage.platform_migrations import migrate_service_task_library
    await migrate_service_task_library(db._db)
    return db


def test_canonical_key_from_is_stable_and_boring():
    assert canonical_key_from("Kingpin Service") == "kingpin_service"
    assert canonical_key_from("  HVAC / A-C  Service ") == "hvac_a_c_service"
    assert canonical_key_from("Brake Service") == canonical_key_from("brake service")
    assert canonical_key_from("!!!") == ""


@pytest.mark.asyncio
async def test_library_seeded_from_code_tuple(lib):
    from adapters.storage.service_tasks import STANDARD_SERVICE_TASKS
    entries = await lib.list_service_task_library()
    keys = {e["canonical_key"] for e in entries}
    assert {t["key"] for t in STANDARD_SERVICE_TASKS} <= keys


@pytest.mark.asyncio
async def test_adding_fans_out_to_accounts_that_already_exist(lib):
    """The bug this endpoint exists to avoid: a new standard task that
    only ever reaches accounts created afterwards."""
    a = (await lib.create_account("Fanout A")).id
    b = (await lib.create_account("Fanout B")).id
    assert not [t for t in await lib.list_service_tasks(a)
                if t["canonical_key"] == "kingpin_service"]

    entry = await lib.create_service_task_library_entry(
        "Kingpin Service", description="Grease + inspect",
        expected_labor_hours=1.5, vehicle_type="trailer",
    )
    assert entry and entry["canonical_key"] == "kingpin_service"

    for acct in (a, b):
        got = next(t for t in await lib.list_service_tasks(acct)
                   if t["canonical_key"] == "kingpin_service")
        assert got["name"] == "Kingpin Service"
        assert got["expected_labor_hours"] == 1.5
        assert got["vehicle_type"] == "trailer"


@pytest.mark.asyncio
async def test_duplicate_key_rejected(lib):
    assert await lib.create_service_task_library_entry("Widget Job") is not None
    assert await lib.create_service_task_library_entry("widget   job") is None
    assert await lib.create_service_task_library_entry("   ") is None


@pytest.mark.asyncio
async def test_rename_pushes_the_new_name_everywhere(lib):
    """A shared key only means something if the name stays in sync —
    otherwise fleets drift apart again, which is the whole bug."""
    a = (await lib.create_account("Rename Co")).id
    entry = await lib.create_service_task_library_entry("Fifth Wheel Job")
    assert await lib.update_service_task_library_entry(
        entry["id"], name="Fifth Wheel Service",
    ) is True
    got = next(t for t in await lib.list_service_tasks(a)
               if t["canonical_key"] == "fifth_wheel_job")
    assert got["name"] == "Fifth Wheel Service"     # key unchanged, label moved


@pytest.mark.asyncio
async def test_account_archive_survives_a_fanout(lib):
    """Hiding a task they don't do is the account's decision — an
    operator edit refreshes the label, never the state."""
    a = (await lib.create_account("Archive Keeper")).id
    entry = await lib.create_service_task_library_entry("Reefer Service")
    mine = next(t for t in await lib.list_service_tasks(a)
                if t["canonical_key"] == "reefer_service")
    assert await lib.update_service_task(mine["id"], a, status="archived") is True

    await lib.update_service_task_library_entry(
        entry["id"], name="Reefer Unit Service",
    )
    fresh = await lib.get_service_task(mine["id"], a)
    assert fresh["status"] == "archived"            # their call stands
    assert fresh["name"] == "Reefer Unit Service"   # label still synced


@pytest.mark.asyncio
async def test_archiving_the_entry_does_not_touch_existing_accounts(lib):
    a = (await lib.create_account("History Co")).id
    entry = await lib.create_service_task_library_entry("Legacy Job")
    mine = next(t for t in await lib.list_service_tasks(a)
                if t["canonical_key"] == "legacy_job")

    assert await lib.update_service_task_library_entry(
        entry["id"], status="archived") is True
    still = await lib.get_service_task(mine["id"], a)
    assert still is not None and still["status"] == "active"

    # …but a NEW account no longer receives it.
    b = (await lib.create_account("Fresh Co")).id
    assert not [t for t in await lib.list_service_tasks(b, include_archived=True)
                if t["canonical_key"] == "legacy_job"]


@pytest.mark.asyncio
async def test_new_accounts_seed_from_the_library_not_the_tuple(lib):
    entry = await lib.create_service_task_library_entry(
        "Post-Library Task", expected_labor_hours=2,
    )
    assert entry
    a = (await lib.create_account("Late Joiner")).id
    got = next(t for t in await lib.list_service_tasks(a)
               if t["canonical_key"] == "post_library_task")
    assert got["expected_labor_hours"] == 2


@pytest.mark.asyncio
async def test_resync_is_idempotent_and_repairs_gaps(lib):
    a = (await lib.create_account("Resync Co")).id
    entry = await lib.create_service_task_library_entry("Repair Me")
    mine = next(t for t in await lib.list_service_tasks(a)
                if t["canonical_key"] == "repair_me")
    # Simulate a fan-out that never landed for this account.
    await lib._db.execute("DELETE FROM service_tasks WHERE id = ?", (mine["id"],))
    await lib._db.commit()

    added = await lib.fan_out_service_task_library_entry(entry)
    assert added == 1
    assert [t for t in await lib.list_service_tasks(a)
            if t["canonical_key"] == "repair_me"]
    # Second run adds nothing.
    assert await lib.fan_out_service_task_library_entry(entry) == 0


@pytest.mark.asyncio
async def test_account_counts_reported_for_the_operator(lib):
    await lib.create_account("Counted A")
    await lib.create_account("Counted B")
    await lib.create_service_task_library_entry("Counted Task")
    entry = next(e for e in await lib.list_service_task_library()
                 if e["canonical_key"] == "counted_task")
    assert entry["accounts"] >= 2


@pytest.mark.asyncio
async def test_bad_updates_rejected(lib):
    entry = await lib.create_service_task_library_entry("Validate Me")
    assert await lib.update_service_task_library_entry(
        entry["id"], status="nonsense") is False
    assert await lib.update_service_task_library_entry(
        entry["id"], vehicle_type="spaceship") is False
    assert await lib.update_service_task_library_entry(entry["id"]) is False
    assert await lib.update_service_task_library_entry(999_999, name="X") is False


# ── My task ↔ Public: adopt-on-promote + the candidates signal ──────

@pytest.mark.asyncio
async def test_promote_adopts_matching_customs_in_place(lib):
    """The owner's 'my task merges with the public one' path: when the
    operator promotes a name 2+ accounts invented, each account's
    matching custom is UPGRADED IN PLACE — same row id, so every
    maintenance/work-order reference keeps pointing at it — rather
    than being skipped and left as a duplicate."""
    a = (await lib.create_account("Adopt A")).id
    b = (await lib.create_account("Adopt B")).id
    mine_a = await lib.create_service_task(a, "Kingpin Service")
    mine_b = await lib.create_service_task(b, "kingpin  SERVICE")   # spacing/case
    # A carries history on its custom row.
    mt = await lib.add_maintenance_task(a, "", "234", "Kingpin Service", "Grease")

    entry = await lib.create_service_task_library_entry("Kingpin Service")
    assert entry is not None

    # Same rows, upgraded identity — not new rows.
    up_a = await lib.get_service_task(mine_a["id"], a)
    up_b = await lib.get_service_task(mine_b["id"], b)
    assert up_a["canonical_key"] == entry["canonical_key"] == "kingpin_service"
    assert up_b["canonical_key"] == "kingpin_service"
    # No duplicate task appeared in either account.
    for acct in (a, b):
        rows = [t for t in await lib.list_service_tasks(acct, include_archived=True)
                if t["name_key"] == "kingpin service"]
        assert len(rows) == 1
    # History still points at the same task.
    task = await lib.get_maintenance_task(mt, a)
    assert task["service_task_id"] == mine_a["id"]
    # And now it's name-locked like any standard.
    assert await lib.update_service_task(
        mine_a["id"], a, name="Renamed") is False


@pytest.mark.asyncio
async def test_candidates_signal(lib):
    a = (await lib.create_account("Cand A")).id
    b = (await lib.create_account("Cand B")).id
    c = (await lib.create_account("Cand C")).id
    await lib.create_service_task(a, "Reefer Door Seal")
    await lib.create_service_task(b, "reefer door  seal")
    await lib.create_service_task(c, "Only Mine Task")

    cands = await lib.service_task_candidates(min_accounts=2)
    keys = {r["name_key"]: r for r in cands}
    assert "reefer door seal" in keys
    assert keys["reefer door seal"]["account_count"] == 2
    assert "only mine task" not in keys          # single account

    # Promoting removes it from the queue (covered by the library now).
    assert await lib.create_service_task_library_entry("Reefer Door Seal")
    after = {r["name_key"] for r in await lib.service_task_candidates(min_accounts=2)}
    assert "reefer door seal" not in after


# ── The split: identity is the operator's, tuning is the account's ──

@pytest.mark.asyncio
async def test_fanout_never_overwrites_what_the_account_tuned(lib):
    """The bug this rule exists to stop: an operator fixing a typo in
    one entry silently resetting every fleet's labor estimate.  Shop
    rates and crews differ, so a fleet that knows its brake job takes
    2.5h has to be able to say so and keep it."""
    a = (await lib.create_account("Tuner Co")).id
    entry = await lib.create_service_task_library_entry(
        "Brake Job", expected_labor_hours=2.0)
    mine = next(t for t in await lib.list_service_tasks(a)
                if t["canonical_key"] == "brake_job")
    assert await lib.update_service_task(
        mine["id"], a, expected_labor_hours=2.5,
        description="Ours includes the slack adjusters",
        vehicle_type="truck") is True

    # The operator edits the entry — any push runs the fan-out.
    assert await lib.update_service_task_library_entry(
        entry["id"], description="Standard brake service",
        expected_labor_hours=2.0, vehicle_type="trailer") is True

    fresh = await lib.get_service_task(mine["id"], a)
    assert fresh["expected_labor_hours"] == 2.5              # theirs stands
    assert fresh["description"] == "Ours includes the slack adjusters"
    assert fresh["vehicle_type"] == "truck"


@pytest.mark.asyncio
async def test_fanout_still_fills_blanks_and_pushes_identity(lib):
    """Fill-empty-only must not become fill-never: an account that
    never touched a field still gets the operator's value."""
    a = (await lib.create_account("Blank Co")).id
    entry = await lib.create_service_task_library_entry("Fill Me")
    mine = next(t for t in await lib.list_service_tasks(a)
                if t["canonical_key"] == "fill_me")
    assert not mine["description"] and not mine["expected_labor_hours"]

    assert await lib.update_service_task_library_entry(
        entry["id"], name="Fill Me In", description="Now documented",
        expected_labor_hours=1.25, system_key="brakes") is True

    fresh = await lib.get_service_task(mine["id"], a)
    assert fresh["description"] == "Now documented"     # blank → filled
    assert fresh["expected_labor_hours"] == 1.25
    assert fresh["name"] == "Fill Me In"                # identity pushed
    assert fresh["system_key"] == "brakes"              # axis pushed


@pytest.mark.asyncio
async def test_account_cannot_move_a_shared_task_to_another_system(lib):
    """A system that means Brakes in one fleet and Other in another
    makes cross-fleet spend meaningless — so on a standard task the
    axis is the operator's, exactly like the name."""
    a = (await lib.create_account("Drifter Co")).id
    entry = await lib.create_service_task_library_entry(
        "Axle Service", system_key="brakes")
    shared = next(t for t in await lib.list_service_tasks(a)
                  if t["canonical_key"] == "axle_service")
    assert shared["system_key"] == "brakes"

    assert await lib.update_service_task(
        shared["id"], a, system_key="other") is False
    assert (await lib.get_service_task(shared["id"], a))["system_key"] == "brakes"

    # Their OWN task's system is still theirs.
    ours = await lib.create_service_task(a, "Our Own Job")
    assert await lib.update_service_task(
        ours["id"], a, system_key="other") is True

    # And the operator moving it reaches the fleet.
    assert await lib.update_service_task_library_entry(
        entry["id"], system_key="suspension") is True
    assert (await lib.get_service_task(shared["id"], a))["system_key"] == "suspension"


# ── Phase 2: the library carries the task's assembly ────────────────

@pytest.mark.asyncio
async def test_library_assembly_pair_rule_and_hard_push(lib):
    """The entry validates the pair at the SOURCE (an incoherent pair
    must never exist to be fanned out), and the fan-out hard-pushes
    assembly like name and system — identity, not tuning."""
    a = (await lib.create_account("Asm Push Co")).id
    # No system → no assembly; wrong-system assembly refused.
    assert await lib.create_service_task_library_entry(
        "Bad Pair A", assembly_key="water_pump") is None
    assert await lib.create_service_task_library_entry(
        "Bad Pair B", system_key="brakes", assembly_key="water_pump") is None

    entry = await lib.create_service_task_library_entry(
        "Water Pump Replacement", system_key="cooling",
        assembly_key="water_pump")
    assert entry and entry["assembly_key"] == "water_pump"

    mine = next(t for t in await lib.list_service_tasks(a)
                if t["canonical_key"] == "water_pump_replacement")
    assert mine["assembly_key"] == "water_pump"
    assert mine["system_key"] == "cooling"

    # The account cannot move it (same lock as system_key)…
    assert await lib.update_service_task(
        mine["id"], a, assembly_key="thermostat") is False
    # …and a drifted value is corrected by the next fan-out (hard push).
    await lib._db.execute(
        "UPDATE service_tasks SET assembly_key = 'thermostat' WHERE id = ?",
        (mine["id"],))
    await lib._db.commit()
    assert await lib.update_service_task_library_entry(
        entry["id"], assembly_key="water_pump") is True
    fresh = await lib.get_service_task(mine["id"], a)
    assert fresh["assembly_key"] == "water_pump"

    # Update-side pair rule holds too: a wrong-system assembly is
    # refused, clearing the system OUT FROM UNDER a set assembly is
    # refused, and clearing both together is the sanctioned way out.
    assert await lib.update_service_task_library_entry(
        entry["id"], assembly_key="pads_shoes") is False
    assert await lib.update_service_task_library_entry(
        entry["id"], system_key="") is False
    assert await lib.update_service_task_library_entry(
        entry["id"], system_key="", assembly_key="") is True


@pytest.mark.asyncio
async def test_new_account_seeds_carry_system_and_assembly(lib):
    """The seed-rows SELECT once dropped system_key entirely (every row
    fell back to the code map) — operator-added tasks reached new
    accounts systemless.  Pinned here together with the new assembly."""
    entry = await lib.create_service_task_library_entry(
        "Thermostat Replacement", system_key="cooling",
        assembly_key="thermostat")
    assert entry
    b = (await lib.create_account("Late Asm Joiner")).id
    got = next(t for t in await lib.list_service_tasks(b)
               if t["canonical_key"] == "thermostat_replacement")
    assert got["system_key"] == "cooling"
    assert got["assembly_key"] == "thermostat"
