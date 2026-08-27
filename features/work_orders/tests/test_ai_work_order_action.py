"""create_work_order AI write action — propose clamps, executor
contract (open draft, compensating cleanup), undo, and the
delete_work_order labor-orphan regression the advisor caught.

Everything runs against the real storage layer; no model calls.
"""
from __future__ import annotations

import os
import sys

os.environ.setdefault("ENCRYPTION_KEY", "test-key-32-chars-min-aaaaaaaaaaaaaaaaaaaa")
os.environ.setdefault("JWT_SECRET", "test-secret-32-chars-or-more-please-aaaaaaaaaaaa")

import pytest

import capabilities.ai.tools  # noqa: F401 — preload registrations
from capabilities.ai.tools.registry import (
    get_action_executor, get_undo_executor,
)
from features.work_orders.ai_actions import create_work_order_action

_A = 77


def _args(**over):
    base = {
        "vehicle_name": "234",
        "vehicle_type": "truck",
        "vendor_name": "Road Repair LLC",
        "service_date": "2026-07-20",
        "invoice_number": "INV-55",
        "fee": 15, "tax": 8.5,
        "repair_priority": "emergency",
        "parts": [
            {"name": "Air line", "quantity": 2, "unit_cost": 40, "total": 80},
        ],
        "labor": [
            {"description": "Replace air line", "hours": 1.5, "rate": 100},
        ],
        "source_files": ["invoice.jpg", "damage.jpg"],
    }
    base.update(over)
    return base


# ── Propose ─────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_propose_requires_vehicle_and_clamps():
    out = await create_work_order_action(_args(vehicle_name=""), None)
    assert "error" in out and "vehicle" in out["error"].lower()

    out = await create_work_order_action(_args(), None)
    art = out["artifacts"][0]
    assert art["type"] == "action_proposal"
    assert "234" in art["summary"]
    # labor total derived from hours×rate: 150; +80 parts +15 +8.5
    assert "$253.50" in art["summary"]
    assert "OPEN work order draft" in art["consequence"]


@pytest.mark.asyncio
async def test_propose_clamps_garbage():
    out = await create_work_order_action(_args(
        parts=[{"name": "X", "quantity": "NaN", "unit_cost": -4, "total": "junk"},
               "not a dict"],
        labor=[{"description": "", "hours": 1}],       # blank desc dropped
        fee="free", tax=None,
        repair_priority="URGENT!!",                     # not in vocab → ''
        source_files=[f"f{i}.jpg" for i in range(9)],   # capped at 5
    ), None)
    payload = _payload_of(out)
    assert payload["parts"][0]["quantity"] == 1.0
    assert payload["parts"][0]["unit_cost"] == 0.0
    assert payload["labor"] == []
    assert payload["fee"] == 0 and payload["tax"] == 0
    assert payload["repair_priority"] == ""
    assert len(payload["source_files"]) == 5


def _payload_of(out: dict) -> dict:
    """The propose payload rides the artifact for the approve spine."""
    art = out["artifacts"][0]
    return art.get("payload") or art.get("proposal", {}).get("payload") or {}


# ── Execute ─────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_executor_creates_open_draft_with_lines(db):
    ex = get_action_executor("create_work_order")
    assert ex is not None
    res = await ex(_args(), _A, {"user_id": 42}, db)
    assert res["created"] is True and res["target_type"] == "work_order"
    wo_id = int(res["target_id"])
    assert res["source_files"] == ["invoice.jpg", "damage.jpg"]
    assert res["_wo_id"] == wo_id

    wo = await db.get_work_order(wo_id, _A)
    assert wo["status"] == "open"                 # AI drafts only
    assert wo["payment_status"] == "unpaid"
    assert wo["repair_priority"] == "emergency"
    assert wo["vendor_id"]                        # registry auto-link
    assert wo["parts_cost"] == 80
    # labor recompute: 1.5h × $100 = 150 → total 80+150+15+8.5
    assert wo["labor_cost"] == 150
    assert wo["total_cost"] == 253.5
    parts = await db.list_work_order_parts(wo_id)
    labor = await db.list_work_order_labor(wo_id, _A)
    assert len(parts) == 1 and len(labor) == 1


@pytest.mark.asyncio
async def test_executor_compensates_on_line_failure(db, monkeypatch):
    ex = get_action_executor("create_work_order")
    before = {r["id"] for r in await db.list_work_orders(_A)}

    async def boom(*a, **kw):
        raise RuntimeError("insert failed")
    monkeypatch.setattr(db, "add_work_order_labor", boom)

    with pytest.raises(RuntimeError):
        await ex(_args(), _A, {"user_id": 42}, db)
    after = {r["id"] for r in await db.list_work_orders(_A)}
    assert after == before        # header cleaned up — nothing stranded


# ── Undo (and the labor-orphan regression) ──────────────────────────

@pytest.mark.asyncio
async def test_undo_deletes_wo_and_labor_rows(db):
    ex = get_action_executor("create_work_order")
    undo = get_undo_executor("create_work_order")
    assert undo is not None
    res = await ex(_args(), _A, {"user_id": 42}, db)
    wo_id = int(res["target_id"])

    out = await undo(res, _args(), _A, {"user_id": 42}, db)
    assert out["undone"] == 1
    assert await db.get_work_order(wo_id, _A) is None
    # THE regression: labor lines must not survive as orphans.
    assert await db.list_work_order_labor(wo_id, _A) == []
    assert await db.list_work_order_parts(wo_id) == []

    # Tolerant re-undo: already gone is a no-op, not a failure.
    out2 = await undo(res, _args(), _A, {"user_id": 42}, db)
    assert out2["undone"] == 0


@pytest.mark.asyncio
async def test_delete_work_order_cascades_labor(db):
    """Direct storage regression for the pre-existing DELETE-route bug:
    labor rows used to survive a WO delete and kept counting in the
    per-task cost rollups."""
    wo_id = await db.add_work_order(_A, "", "T900", "Shop Z")
    await db.add_work_order_labor(wo_id, _A, description="Diag", hours=1, rate=90)
    assert len(await db.list_work_order_labor(wo_id, _A)) == 1
    assert await db.delete_work_order(wo_id, account_id=_A) == 1
    assert await db.list_work_order_labor(wo_id, _A) == []
