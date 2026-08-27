"""Maintenance auto-link (3B): the pure matcher + the propose/execute
contract that a link is APPROVED, never silent, and never trusted from
the payload alone.
"""
from __future__ import annotations

import os
import sys

os.environ.setdefault("ENCRYPTION_KEY", "test-key-32-chars-min-aaaaaaaaaaaaaaaaaaaa")
os.environ.setdefault("JWT_SECRET", "test-secret-32-chars-or-more-please-aaaaaaaaaaaa")

import pytest
import pytest_asyncio

import capabilities.ai.tools  # noqa: F401 — preload registrations
from capabilities.ai.tools.registry import get_action_executor, get_undo_executor
from features.work_orders.ai_actions import create_work_order_action
from features.work_orders.task_links import (
    describe_links, invoice_haystack, is_open_task, suggest_task_links,
)


# maintenance_tasks carries an account_id FK, so these need REAL
# accounts (unlike work_orders, which doesn't).
@pytest_asyncio.fixture
async def acct(db):
    a = await db.create_account("Task Link Co")
    return a.id


@pytest_asyncio.fixture
async def other_acct(db):
    a = await db.create_account("Other Tenant Co")
    return a.id


def _task(tid, task_type, desc="", status="pending", wo=None):
    return {"id": tid, "task_type": task_type, "description": desc,
            "status": status, "work_order_id": wo}


# ── The matcher (pure) ──────────────────────────────────────────────

class TestSuggestTaskLinks:
    def test_type_vocabulary_match(self):
        hay = invoice_haystack(
            [{"part_name": "Brake pads front"}, {"part_name": "Rotor"}],
            [{"description": "Labor - brake job"}],
        )
        out = suggest_task_links([_task(1, "brakes"), _task(2, "tires")], hay)
        assert [s["id"] for s in out] == [1]
        assert out[0]["matched_on"] == "brake"

    def test_custom_task_matches_on_its_own_description(self):
        hay = invoice_haystack([{"part_name": "Air line"}], [])
        out = suggest_task_links([_task(9, "custom", "Air line leak at cab")], hay)
        assert [s["id"] for s in out] == [9]
        assert out[0]["matched_on"] == "air"

    def test_generic_component_words_do_not_bind(self):
        """An AIR-filter task must not latch onto an OIL-filter line —
        'filter' and 'replace' carry no signal."""
        hay = invoice_haystack([{"part_name": "Oil filter"}],
                               [{"description": "Replace filter"}])
        out = suggest_task_links([_task(3, "custom", "Replace air filter")], hay)
        assert out == []

    def test_word_boundaries(self):
        # 'def' (DEF refill) must not match 'definitely'/'defrost'.
        hay = invoice_haystack([], [{"description": "Defrost switch definitely bad"}])
        assert suggest_task_links([_task(4, "def_refill")], hay) == []
        hay2 = invoice_haystack([{"part_name": "DEF fluid 2.5 gal"}], [])
        assert [s["id"] for s in suggest_task_links([_task(4, "def_refill")], hay2)] == [4]

    def test_closed_and_already_linked_tasks_are_skipped(self):
        hay = invoice_haystack([{"part_name": "Brake pads"}], [])
        tasks = [
            _task(1, "brakes", status="completed"),
            _task(2, "brakes", status="done"),          # bot vocabulary
            _task(3, "brakes", status="cancelled"),
            _task(4, "brakes", wo=555),                 # on another WO
            _task(5, "brakes"),                         # the only open one
        ]
        assert [s["id"] for s in suggest_task_links(tasks, hay)] == [5]

    def test_unrelated_invoice_suggests_nothing(self):
        hay = invoice_haystack([{"part_name": "Windshield glass"}], [])
        assert suggest_task_links([_task(1, "brakes"), _task(2, "oil")], hay) == []

    def test_limit_caps_suggestions(self):
        hay = invoice_haystack([{"part_name": "Brake pads"}], [])
        tasks = [_task(i, "brakes") for i in range(1, 9)]
        assert len(suggest_task_links(tasks, hay, limit=3)) == 3

    def test_is_open_task(self):
        assert is_open_task(_task(1, "oil")) is True
        assert is_open_task(_task(1, "oil", status="COMPLETED")) is False
        assert is_open_task(_task(1, "oil", wo=3)) is False

    def test_describe_links_is_explicit(self):
        assert describe_links([]) == ""
        phrase = describe_links([{"id": 7, "task_type": "dot_inspection"}])
        assert "#7 dot inspection" in phrase and "1 open maintenance task" in phrase


# ── Propose: suggestions reach the summary the user approves ────────

async def _seed(db, acct, *, vehicle="234"):
    oil = await db.add_maintenance_task(acct, "", vehicle, "oil", "Oil change due")
    brakes = await db.add_maintenance_task(acct, "", vehicle, "brakes", "Front brakes")
    other = await db.add_maintenance_task(acct, "", "999", "brakes", "Other truck")
    return {"oil": oil, "brakes": brakes, "other": other}


def _args(**over):
    base = {
        "vehicle_name": "234",
        "vendor_name": "Road Repair LLC",
        "parts": [{"name": "Brake pads", "quantity": 2, "unit_cost": 40, "total": 80}],
        "labor": [{"description": "Brake job", "hours": 1, "rate": 100}],
    }
    base.update(over)
    return base


def _artifact(out):
    return out["artifacts"][0]


@pytest.mark.asyncio
async def test_propose_suggests_matching_task_and_names_it(db, acct):
    ids = await _seed(db, acct)
    out = await create_work_order_action(_args(), None, acct, db)
    art = _artifact(out)
    # The brakes task matches; the oil task and the other truck's don't.
    assert art["payload"]["link_task_ids"] == [ids["brakes"]]
    assert f"#{ids['brakes']} brakes" in art["summary"]
    assert "open maintenance task" in art["summary"]
    assert "Linked tasks stay open" in art["consequence"]


@pytest.mark.asyncio
async def test_propose_without_match_says_nothing_about_links(db, acct):
    await _seed(db, acct)
    out = await create_work_order_action(
        _args(parts=[{"name": "Windshield glass", "total": 300}], labor=[]),
        None, acct, db,
    )
    art = _artifact(out)
    assert art["payload"]["link_task_ids"] == []
    assert "maintenance task" not in art["summary"]


@pytest.mark.asyncio
async def test_explicit_id_is_also_named_in_the_summary(db, acct):
    """An id passed in (user-named or model-supplied) must appear in the
    approval text too — nothing links invisibly."""
    ids = await _seed(db, acct)
    out = await create_work_order_action(
        # Windshield invoice matches nothing; the oil task is passed in.
        _args(parts=[{"name": "Windshield glass", "total": 300}], labor=[],
              link_task_ids=[ids["oil"]]),
        None, acct, db,
    )
    art = _artifact(out)
    assert art["payload"]["link_task_ids"] == [ids["oil"]]
    assert f"#{ids['oil']} oil" in art["summary"]


@pytest.mark.asyncio
async def test_propose_keeps_user_named_ids_and_survives_no_db():
    # No db (unit context) → no suggestions, but explicit ids stay.
    out = await create_work_order_action(_args(link_task_ids=[12, 12, "x", -3]), None)
    assert _artifact(out)["payload"]["link_task_ids"] == [12]


# ── Execute: the database decides, not the payload ──────────────────

@pytest.mark.asyncio
async def test_executor_links_only_valid_tasks(db, acct):
    ids = await _seed(db, acct)
    closed = await db.add_maintenance_task(acct, "", "234", "tires", "Old")
    await db.update_maintenance_status(closed, "completed", acct)

    ex = get_action_executor("create_work_order")
    res = await ex(
        _args(link_task_ids=[
            ids["brakes"],      # valid
            ids["other"],       # different vehicle → rejected
            closed,             # completed → rejected
            999_999,            # nonexistent → rejected
        ]),
        acct, {"user_id": 1}, db,
    )
    wo_id = int(res["target_id"])
    assert res["linked_tasks"] == 1
    assert "Linked 1 maintenance task" in res["message"]

    linked = await db.list_tasks_for_work_order(wo_id, acct)
    assert [t["id"] for t in linked] == [ids["brakes"]]
    # The rejected ones stay unlinked and untouched.
    assert (await db.get_maintenance_task(ids["other"], acct))["work_order_id"] is None
    assert (await db.get_maintenance_task(closed, acct))["work_order_id"] is None


@pytest.mark.asyncio
async def test_linked_task_stays_open(db, acct):
    """Linking is an association, not a completion — the task's own
    status is the maintenance module's business."""
    ids = await _seed(db, acct)
    ex = get_action_executor("create_work_order")
    await ex(_args(link_task_ids=[ids["brakes"]]), acct, {"user_id": 1}, db)
    task = await db.get_maintenance_task(ids["brakes"], acct)
    assert task["status"] not in ("completed", "done", "cancelled")


@pytest.mark.asyncio
async def test_undo_unlinks_the_tasks(db, acct):
    ids = await _seed(db, acct)
    ex = get_action_executor("create_work_order")
    undo = get_undo_executor("create_work_order")
    res = await ex(_args(link_task_ids=[ids["brakes"]]), acct, {"user_id": 1}, db)
    assert res["linked_tasks"] == 1

    await undo(res, _args(), acct, {"user_id": 1}, db)
    task = await db.get_maintenance_task(ids["brakes"], acct)
    assert task["work_order_id"] is None      # back-reference cleared
    assert task["status"] not in ("completed", "done")


@pytest.mark.asyncio
async def test_cross_account_task_never_links(db, acct, other_acct):
    """A payload id from another tenant is rejected by the account-scoped
    lookup, not merely by the UPDATE's WHERE clause."""
    foreign = await db.add_maintenance_task(other_acct, "", "234", "brakes", "Theirs")
    ex = get_action_executor("create_work_order")
    res = await ex(_args(link_task_ids=[foreign]), acct, {"user_id": 1}, db)
    assert res["linked_tasks"] == 0
    assert (await db.get_maintenance_task(foreign, other_acct))["work_order_id"] is None
