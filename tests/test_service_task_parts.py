"""Linked parts + labor estimates — the payoff of tasks being objects.

Pins: cross-account ids are rejected (never trust a caller id), the
defaults lookup is READ-ONLY (asking a question must not create an
archived task the way the writers' fail-open resolver does), and the
AI proposal pre-fills only when the invoice gave us nothing.
"""
from __future__ import annotations

import os
import sys

os.environ.setdefault("ENCRYPTION_KEY", "test-key-32-chars-min-aaaaaaaaaaaaaaaaaaaa")
os.environ.setdefault("JWT_SECRET", "test-secret-32-chars-or-more-please-aaaaaaaaaaaa")

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient

import capabilities.ai.tools  # noqa: F401 — preload registrations
from adapters.storage import Role
from features.work_orders.ai_actions import create_work_order_action
from interfaces.api.auth import create_jwt


@pytest_asyncio.fixture
async def seeded(db):
    acct = (await db.create_account("Linked Parts Co")).id
    other = (await db.create_account("Other Tenant")).id
    brakes = next(t for t in await db.list_service_tasks(acct)
                  if t["canonical_key"] == "brakes")
    pad, _ = await db.create_catalog_part(acct, "Brake Pad Set")
    rotor, _ = await db.create_catalog_part(acct, "Rotor")
    foreign, _ = await db.create_catalog_part(other, "Their Part")
    return {"acct": acct, "other": other, "task": brakes,
            "pad": pad, "rotor": rotor, "foreign": foreign}


# ── Linking ─────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_link_list_unlink(db, seeded):
    a, task = seeded["acct"], seeded["task"]
    link = await db.add_service_task_part(
        a, task["id"], seeded["pad"]["id"], quantity=2,
    )
    assert link and link["part_name"] == "Brake Pad Set" and link["quantity"] == 2
    await db.add_service_task_part(a, task["id"], seeded["rotor"]["id"])

    rows = await db.list_service_task_parts(task["id"], a)
    assert {r["part_name"] for r in rows} == {"Brake Pad Set", "Rotor"}

    assert await db.remove_service_task_part(a, link["id"]) is True
    rows = await db.list_service_task_parts(task["id"], a)
    assert [r["part_name"] for r in rows] == ["Rotor"]


@pytest.mark.asyncio
async def test_duplicate_link_rejected(db, seeded):
    a, task = seeded["acct"], seeded["task"]
    assert await db.add_service_task_part(a, task["id"], seeded["pad"]["id"])
    assert await db.add_service_task_part(a, task["id"], seeded["pad"]["id"]) is None


@pytest.mark.asyncio
async def test_cross_account_ids_rejected(db, seeded):
    """Both sides of the link are re-checked against the account — a
    caller-supplied id is never trusted."""
    a, task = seeded["acct"], seeded["task"]
    assert await db.add_service_task_part(
        a, task["id"], seeded["foreign"]["id"],
    ) is None
    foreign_task = next(t for t in await db.list_service_tasks(seeded["other"])
                        if t["canonical_key"] == "brakes")
    assert await db.add_service_task_part(
        a, foreign_task["id"], seeded["pad"]["id"],
    ) is None


@pytest.mark.asyncio
async def test_unlink_is_account_scoped(db, seeded):
    a, task = seeded["acct"], seeded["task"]
    link = await db.add_service_task_part(a, task["id"], seeded["pad"]["id"])
    assert await db.remove_service_task_part(seeded["other"], link["id"]) is False
    assert len(await db.list_service_task_parts(task["id"], a)) == 1


# ── Defaults lookup ─────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_defaults_returns_parts_and_labor_estimate(db, seeded):
    a, task = seeded["acct"], seeded["task"]
    await db.add_service_task_part(a, task["id"], seeded["pad"]["id"], quantity=4)
    await db.update_service_task(task["id"], a, expected_labor_hours=2.5)

    by_key = await db.service_task_defaults(a, "brakes")
    assert by_key["expected_labor_hours"] == 2.5
    assert by_key["parts"][0]["quantity"] == 4
    # The task's NAME resolves to the same defaults as its key.
    by_name = await db.service_task_defaults(a, task["name"])
    assert by_name["id"] == by_key["id"]


@pytest.mark.asyncio
async def test_defaults_is_read_only(db, seeded):
    """The writers' resolver is fail-open (unknown ⇒ archived custom
    task).  A DEFAULTS lookup must never write — asking a question
    about an unknown task creates nothing."""
    a = seeded["acct"]
    before = len(await db.list_service_tasks(a, include_archived=True))
    assert await db.service_task_defaults(a, "never heard of it") is None
    assert await db.service_task_defaults(a, "") is None
    after = len(await db.list_service_tasks(a, include_archived=True))
    assert after == before


# ── API ─────────────────────────────────────────────────────────────

@pytest_asyncio.fixture
async def api(db):
    import infra.platform as cp
    cp._db = db
    a = (await db.create_account("STP API Co"))
    owner = await db.create_user(940001, a.id, role=Role.OWNER)
    driver = await db.create_user(940002, a.id, role=Role.DRIVER)
    task = next(t for t in await db.list_service_tasks(a.id)
                if t["canonical_key"] == "oil")
    part, _ = await db.create_catalog_part(a.id, "Oil Filter")
    from interfaces.api.app import create_api
    return {"app": create_api(), "acct": a.id, "task": task, "part": part,
            "owner": create_jwt(owner.telegram_id, a.id, "owner"),
            "driver": create_jwt(driver.telegram_id, a.id, "driver")}


def _h(tok):
    return {"Authorization": f"Bearer {tok}"}


@pytest.mark.asyncio
async def test_api_link_flow_and_defaults_route(api):
    tid, pid = api["task"]["id"], api["part"]["id"]
    async with AsyncClient(transport=ASGITransport(app=api["app"]),
                           base_url="http://t") as c:
        r = await c.post(f"/api/service-tasks/{tid}/parts", headers=_h(api["owner"]),
                         json={"part_id": pid, "quantity": 2})
        assert r.status_code == 200, r.text
        link_id = r.json()["id"]

        r = await c.get(f"/api/service-tasks/{tid}/parts", headers=_h(api["owner"]))
        assert [p["part_name"] for p in r.json()["parts"]] == ["Oil Filter"]

        # /defaults must NOT be shadowed by the /{task_id} converter.
        r = await c.get("/api/service-tasks/defaults?task=oil",
                        headers=_h(api["owner"]))
        assert r.status_code == 200, r.text
        assert r.json()["found"] is True
        assert r.json()["parts"][0]["quantity"] == 2

        r = await c.get("/api/service-tasks/defaults?task=nope",
                        headers=_h(api["owner"]))
        assert r.json() == {"found": False, "expected_labor_hours": 0, "parts": []}

        r = await c.delete(f"/api/service-tasks/{tid}/parts/{link_id}",
                           headers=_h(api["owner"]))
        assert r.status_code == 200

        # Writes stay behind can_service_tasks.
        r = await c.post(f"/api/service-tasks/{tid}/parts", headers=_h(api["driver"]),
                         json={"part_id": pid})
        assert r.status_code == 403


# ── AI proposal pre-fill ────────────────────────────────────────────

def _artifact(out):
    return out["artifacts"][0]


@pytest.mark.asyncio
async def test_ai_prefills_usual_parts_when_invoice_has_none(db, seeded):
    a, task = seeded["acct"], seeded["task"]
    await db.add_service_task_part(a, task["id"], seeded["pad"]["id"], quantity=2)
    await db.update_service_task(task["id"], a, expected_labor_hours=1.5)

    out = await create_work_order_action(
        {"vehicle_name": "234", "service_task": "brakes"}, None, a, db,
    )
    art = _artifact(out)
    payload = art["payload"]
    assert [p["part_name"] for p in payload["parts"]] == ["Brake Pad Set"]
    assert payload["parts"][0]["quantity"] == 2
    assert payload["parts"][0]["unit_cost"] == 0      # amounts come from the invoice
    assert payload["labor"][0]["hours"] == 1.5
    # Every pre-filled line is tagged so it groups under the task.
    assert payload["parts"][0]["service_task"] == "brakes"
    # And the user is TOLD these came from the catalog, not the invoice.
    assert "usual lines" in art["summary"]
    assert "1 parts" in art["summary"]                # summary counts them


@pytest.mark.asyncio
async def test_ai_never_overrides_real_invoice_lines(db, seeded):
    a, task = seeded["acct"], seeded["task"]
    await db.add_service_task_part(a, task["id"], seeded["pad"]["id"], quantity=9)

    out = await create_work_order_action({
        "vehicle_name": "234", "service_task": "brakes",
        "parts": [{"name": "Actual Pad From Invoice", "quantity": 1,
                   "unit_cost": 55, "total": 55}],
    }, None, a, db)
    payload = _artifact(out)["payload"]
    assert [p["part_name"] for p in payload["parts"]] == ["Actual Pad From Invoice"]
    assert "usual lines" not in _artifact(out)["summary"]


@pytest.mark.asyncio
async def test_executor_tags_lines_with_the_task(db, seeded):
    from capabilities.ai.tools.registry import get_action_executor
    a, task = seeded["acct"], seeded["task"]
    await db.add_service_task_part(a, task["id"], seeded["pad"]["id"])

    ex = get_action_executor("create_work_order")
    res = await ex({
        "vehicle_name": "234", "service_task": "brakes",
        "parts": [{"name": "Pad", "quantity": 2, "unit_cost": 30, "total": 60}],
    }, a, {"user_id": 1}, db)
    wo_id = int(res["target_id"])
    parts = await db.list_work_order_parts(wo_id)
    assert parts[0]["service_task"] == "brakes"
    # …and the dual-write resolved it to the real task row.
    assert parts[0]["service_task_id"] == task["id"]
