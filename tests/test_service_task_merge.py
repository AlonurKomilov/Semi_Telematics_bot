"""Merge duplicates + vehicle-type scoping.

Merge is the destructive one, so the pins are strict: ALL history
repoints (all three reference columns, legacy strings included),
standard tasks can never be merged away, one-level nesting survives,
and linked-part collisions don't explode the UNIQUE constraint.
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
from interfaces.api.auth import create_jwt


@pytest_asyncio.fixture
async def acct(db):
    return (await db.create_account("Merge Co")).id


async def _dupe_with_history(db, acct, name="Brake Job"):
    """A custom task with one row in each of the three consumers."""
    task = await db.create_service_task(acct, name)
    mt = await db.add_maintenance_task(acct, "", "234", name, "Squealing")
    wo = await db.add_work_order(acct, "", "234", "Shop A")
    pid = await db.add_work_order_part(wo, part_name="Pad", service_task=name)
    lid = await db.add_work_order_labor(
        wo, acct, description="Bleed", service_task=name)
    return {"task": task, "mt": mt, "wo": wo, "pid": pid, "lid": lid}


@pytest.mark.asyncio
async def test_merge_repoints_all_three_consumers(db, acct):
    h = await _dupe_with_history(db, acct)
    winner = next(t for t in await db.list_service_tasks(acct)
                  if t["canonical_key"] == "brakes")

    ok, reason = await db.merge_service_tasks(acct, h["task"]["id"], winner["id"])
    assert ok is True, reason

    mt = await db.get_maintenance_task(h["mt"], acct)
    assert mt["service_task_id"] == winner["id"]
    assert mt["task_type"] == "brakes"          # legacy string follows too

    part = next(p for p in await db.list_work_order_parts(h["wo"])
                if p["id"] == h["pid"])
    line = next(l for l in await db.list_work_order_labor(h["wo"], acct)
                if l["id"] == h["lid"])
    assert part["service_task_id"] == winner["id"] and part["service_task"] == "brakes"
    assert line["service_task_id"] == winner["id"] and line["service_task"] == "brakes"

    # The duplicate is gone.
    assert await db.get_service_task(h["task"]["id"], acct) is None


@pytest.mark.asyncio
async def test_reports_stop_splitting_after_merge(db, acct):
    """The actual point of merging: one row in the per-task report
    instead of two."""
    h = await _dupe_with_history(db, acct)
    await db.update_work_order(h["wo"], account_id=acct, service_date="2026-07-01")
    wo2 = await db.add_work_order(acct, "", "234", "Shop B",
                                  service_date="2026-07-02")
    await db.add_work_order_part(wo2, part_name="Pad", service_task="brakes")

    before = {r["service_task"] for r in await db.cost_by_service_task(acct)}
    assert {"Brake Job", "Brake Service"} <= before   # split two ways

    winner = next(t for t in await db.list_service_tasks(acct)
                  if t["canonical_key"] == "brakes")
    assert (await db.merge_service_tasks(acct, h["task"]["id"], winner["id"]))[0]

    after = {r["service_task"] for r in await db.cost_by_service_task(acct)}
    assert "Brake Job" not in after and "Brake Service" in after


@pytest.mark.asyncio
async def test_standard_task_can_never_be_merged_away(db, acct):
    std = next(t for t in await db.list_service_tasks(acct)
               if t["canonical_key"] == "oil")
    mine = await db.create_service_task(acct, "My Oil Thing")
    ok, reason = await db.merge_service_tasks(acct, std["id"], mine["id"])
    assert ok is False and "archive" in reason.lower()
    assert await db.get_service_task(std["id"], acct) is not None


@pytest.mark.asyncio
async def test_merge_into_self_and_missing_rejected(db, acct):
    mine = await db.create_service_task(acct, "Solo Task")
    assert (await db.merge_service_tasks(acct, mine["id"], mine["id"]))[0] is False
    assert (await db.merge_service_tasks(acct, mine["id"], 999_999))[0] is False


@pytest.mark.asyncio
async def test_merge_moves_subtasks_and_guards_nesting(db, acct):
    parent = await db.create_service_task(acct, "Big Job")
    child = await db.create_service_task(acct, "Small Step", parent_id=parent["id"])
    other_parent = await db.create_service_task(acct, "Other Big Job")
    other_child = await db.create_service_task(
        acct, "Other Step", parent_id=other_parent["id"])

    # Folding a parent into a SUBTASK would create two levels — refused.
    ok, reason = await db.merge_service_tasks(
        acct, parent["id"], other_child["id"])
    assert ok is False and "nesting" in reason.lower()

    # Into a top-level task: the subtask follows its new parent.
    ok, _ = await db.merge_service_tasks(acct, parent["id"], other_parent["id"])
    assert ok is True
    moved = await db.get_service_task(child["id"], acct)
    assert moved["parent_id"] == other_parent["id"]


@pytest.mark.asyncio
async def test_merge_folds_linked_parts_without_unique_violation(db, acct):
    loser = await db.create_service_task(acct, "Dup Brakes")
    winner = await db.create_service_task(acct, "Real Brakes")
    shared, _ = await db.create_catalog_part(acct, "Brake Pad")
    only_loser, _ = await db.create_catalog_part(acct, "Clip Kit")
    # BOTH tasks link the same part — the naive UPDATE would violate
    # UNIQUE(service_task_id, part_id).
    await db.add_service_task_part(acct, loser["id"], shared["id"])
    await db.add_service_task_part(acct, winner["id"], shared["id"])
    await db.add_service_task_part(acct, loser["id"], only_loser["id"])

    ok, reason = await db.merge_service_tasks(acct, loser["id"], winner["id"])
    assert ok is True, reason
    names = {p["part_name"] for p in
             await db.list_service_task_parts(winner["id"], acct)}
    assert names == {"Brake Pad", "Clip Kit"}


@pytest.mark.asyncio
async def test_merge_is_account_scoped(db, acct):
    other = (await db.create_account("Other Merge Co")).id
    mine = await db.create_service_task(acct, "Mine")
    theirs = await db.create_service_task(other, "Theirs")
    assert (await db.merge_service_tasks(acct, mine["id"], theirs["id"]))[0] is False
    assert await db.get_service_task(mine["id"], acct) is not None


# ── Vehicle-type scoping ────────────────────────────────────────────

@pytest.mark.asyncio
async def test_vehicle_type_narrows_without_hiding_shared_tasks(db, acct):
    truck_only = await db.create_service_task(
        acct, "Fifth Wheel Service", vehicle_type="truck")
    trailer_only = await db.create_service_task(
        acct, "Landing Gear", vehicle_type="trailer")
    anyone = await db.create_service_task(acct, "Lighting Check")

    for_truck = {t["id"] for t in await db.list_service_tasks(
        acct, vehicle_type="truck")}
    assert truck_only["id"] in for_truck
    assert trailer_only["id"] not in for_truck
    assert anyone["id"] in for_truck          # '' shows everywhere

    for_trailer = {t["id"] for t in await db.list_service_tasks(
        acct, vehicle_type="trailer")}
    assert trailer_only["id"] in for_trailer
    assert truck_only["id"] not in for_trailer

    # Unfiltered still returns everything.
    all_ids = {t["id"] for t in await db.list_service_tasks(acct)}
    assert {truck_only["id"], trailer_only["id"], anyone["id"]} <= all_ids


@pytest.mark.asyncio
async def test_vehicle_type_validated_and_retaggable(db, acct):
    t = await db.create_service_task(acct, "Tagged Task", vehicle_type="truck")
    assert t["vehicle_type"] == "truck"
    assert await db.update_service_task(t["id"], acct, vehicle_type="nonsense") is False
    assert await db.update_service_task(t["id"], acct, vehicle_type="trailer") is True
    assert (await db.get_service_task(t["id"], acct))["vehicle_type"] == "trailer"
    # Standard tasks are scopeable too — narrowing isn't identity.
    std = next(x for x in await db.list_service_tasks(acct)
               if x["canonical_key"] == "trailer_service")
    assert await db.update_service_task(std["id"], acct, vehicle_type="trailer") is True


# ── API ─────────────────────────────────────────────────────────────

@pytest_asyncio.fixture
async def api(db):
    import infra.platform as cp
    cp._db = db
    a = await db.create_account("Merge API Co")
    owner = await db.create_user(950001, a.id, role=Role.OWNER)
    driver = await db.create_user(950002, a.id, role=Role.DRIVER)
    from interfaces.api.app import create_api
    return {"app": create_api(), "acct": a.id,
            "owner": create_jwt(owner.telegram_id, a.id, "owner"),
            "driver": create_jwt(driver.telegram_id, a.id, "driver")}


def _h(tok):
    return {"Authorization": f"Bearer {tok}"}


@pytest.mark.asyncio
async def test_api_merge_route(api, db):
    a = api["acct"]
    loser = await db.create_service_task(a, "Api Dup")
    winner = next(t for t in await db.list_service_tasks(a)
                  if t["canonical_key"] == "brakes")
    async with AsyncClient(transport=ASGITransport(app=api["app"]),
                           base_url="http://t") as c:
        # /merge must not be swallowed by the /{task_id} converter.
        r = await c.post("/api/service-tasks/merge", headers=_h(api["owner"]),
                         json={"loser_id": loser["id"], "winner_id": winner["id"]})
        assert r.status_code == 200, r.text
        assert r.json() == {"merged": True}

        r = await c.post("/api/service-tasks/merge", headers=_h(api["owner"]),
                         json={"loser_id": winner["id"], "winner_id": winner["id"]})
        assert r.status_code == 422

        r = await c.post("/api/service-tasks/merge", headers=_h(api["driver"]),
                         json={"loser_id": 1, "winner_id": 2})
        assert r.status_code == 403


@pytest.mark.asyncio
async def test_api_vehicle_type_filter(api, db):
    a = api["acct"]
    await db.create_service_task(a, "Trailer Only Job", vehicle_type="trailer")
    async with AsyncClient(transport=ASGITransport(app=api["app"]),
                           base_url="http://t") as c:
        r = await c.get("/api/service-tasks?vehicle_type=truck",
                        headers=_h(api["owner"]))
        names = {t["name"] for t in r.json()["service_tasks"]}
        assert "Trailer Only Job" not in names
        r = await c.get("/api/service-tasks?vehicle_type=trailer",
                        headers=_h(api["owner"]))
        assert "Trailer Only Job" in {t["name"] for t in r.json()["service_tasks"]}
