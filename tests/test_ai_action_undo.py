"""Copilot-style undo of executed AI actions — the full loop against
real Postgres: approve → execute → undo removes EXACTLY the created
change-set (soft + evented + audited), with the authorization ladder
(approver or owner/admin), the atomic anti-double-undo claim, the 7-day
window, and graceful degradation for legacy/manual-removal cases.
"""

from __future__ import annotations

import os

os.environ.setdefault("ENCRYPTION_KEY", "")

import json

import pytest
from fastapi import HTTPException

import capabilities.ai.tools  # noqa: F401 — hub load registers tools + undo recipes
from capabilities.ai.actions import (
    execute_approved_action,
    undo_approved_action,
    undoable,
)
from capabilities.permissions.roles import Role
from features.vehicles.inventory.ai_actions import _build_rows


async def _seed(pg_db):
    from interfaces.api.auth import _hash_password
    acct = await pg_db.create_account("Undo Fleet Co")
    users = {}
    for key, role in (("owner", Role.OWNER), ("approver", Role.FLEET),
                      ("other", Role.FLEET)):
        u = await pg_db.create_user_with_email(
            email=f"{key}.{acct.id}@example.com",
            password_hash=_hash_password("password12345"),
            account_id=acct.id, role=role, display_name=key.title(),
        )
        users[key] = (u.telegram_id or u.id)
    vids = {}
    for unit in ("110", "111"):
        vids[unit] = await pg_db.add_vehicle(acct.id, unit_number=unit)
    return acct.id, users, vids


def _user(acct: int, uid: int, role: str) -> dict:
    return {"account_id": acct, "sub": str(uid), "role": role}


async def _approve_import(pg_db, acct: int, uid: int, *, role="fleet"):
    """Stage 2 items via the REAL adapter, persist the proposal, execute."""
    records = [
        {"vehicle": "110", "item": "Dashcam", "status": "installed",
         "category": "camera", "_source_row": 2},
        {"vehicle": "111", "item": "Tablet", "status": "installed",
         "category": "tablet", "_source_row": 3},
    ]
    rows, skipped, _notices = await _build_rows(records, acct, None, pg_db)
    assert skipped == []
    pid = await pg_db.create_action_proposal(
        acct, uid, "import_inventory_items", "Import 2 inventory items",
        json.dumps({"target": "inventory", "count": 2}),
        staged_payload_json=json.dumps(rows),
    )
    res = await execute_approved_action(
        pid, user=_user(acct, uid, role), user_context={},
        platform_db=pg_db, tenant_db=pg_db,
    )
    assert res["status"] == "consumed"
    # Client-facing result never leaks the server-side undo manifest.
    assert "_item_ids" not in res["result"]
    return pid


async def _active_items(pg_db, acct, vids):
    out = []
    for vid in vids.values():
        out += await pg_db.list_vehicle_inventory(acct, vid)
    return out


async def test_full_undo_loop_removes_exactly_the_change_set(pg_db):
    acct, users, vids = await _seed(pg_db)
    uid = users["approver"]
    pid = await _approve_import(pg_db, acct, uid)
    assert len(await _active_items(pg_db, acct, vids)) == 2

    res = await undo_approved_action(
        pid, user=_user(acct, uid, "fleet"), user_context={},
        platform_db=pg_db, tenant_db=pg_db,
    )
    assert res["status"] == "undone"
    assert res["result"]["undone"] == 2 and res["result"]["already_gone"] == 0

    # Soft + evented: rows gone from the active list, trail preserved.
    assert await _active_items(pg_db, acct, vids) == []
    prop = await pg_db.get_action_proposal_for_account(pid, acct)
    assert prop["status"] == "undone"
    assert prop["undone_by"] == uid and prop["undone_at"]
    # Both audit rows, in order, with the right actors.
    log = await pg_db.get_audit_log(acct, limit=10)
    actions = [(r["action"], r["user_id"]) for r in log]
    assert ("ai_write:import_inventory_items", uid) in actions
    assert ("ai_undo:import_inventory_items", uid) in actions


async def test_double_undo_is_idempotent(pg_db):
    acct, users, _vids = await _seed(pg_db)
    uid = users["approver"]
    pid = await _approve_import(pg_db, acct, uid)
    first = await undo_approved_action(
        pid, user=_user(acct, uid, "fleet"), user_context={},
        platform_db=pg_db, tenant_db=pg_db)
    again = await undo_approved_action(
        pid, user=_user(acct, uid, "fleet"), user_context={},
        platform_db=pg_db, tenant_db=pg_db)
    assert first["status"] == again["status"] == "undone"
    assert again["result"]["undone"] == 2   # stored outcome, no second write


async def test_authorization_ladder(pg_db):
    """Approver ✓ · other employee ✗ (even with the tool permission) ·
    owner ✓ — the 'find who did it and fix it' path."""
    acct, users, vids = await _seed(pg_db)
    pid = await _approve_import(pg_db, acct, users["approver"])

    with pytest.raises(HTTPException) as e:
        await undo_approved_action(
            pid, user=_user(acct, users["other"], "fleet"), user_context={},
            platform_db=pg_db, tenant_db=pg_db)
    assert e.value.status_code == 403

    res = await undo_approved_action(
        pid, user=_user(acct, users["owner"], "owner"), user_context={},
        platform_db=pg_db, tenant_db=pg_db)
    assert res["status"] == "undone"
    prop = await pg_db.get_action_proposal_for_account(pid, acct)
    assert prop["undone_by"] == users["owner"]     # the owner, not the approver


async def test_window_expiry_refuses(pg_db):
    acct, users, _vids = await _seed(pg_db)
    uid = users["approver"]
    pid = await _approve_import(pg_db, acct, uid)
    await pg_db._db.execute(
        "UPDATE ai_action_proposals SET created_at = ? WHERE id = ?",
        ("2020-01-01T00:00:00+00:00", pid))
    prop = await pg_db.get_action_proposal_for_account(pid, acct)
    assert undoable(prop) is False
    with pytest.raises(HTTPException) as e:
        await undo_approved_action(
            pid, user=_user(acct, uid, "fleet"), user_context={},
            platform_db=pg_db, tenant_db=pg_db)
    assert e.value.status_code == 409


async def test_action_without_recipe_refuses(pg_db):
    acct, users, _vids = await _seed(pg_db)
    uid = users["approver"]
    pid = await pg_db.create_action_proposal(
        acct, uid, "create_maintenance_task", "Create task", "{}")
    await pg_db._db.execute(
        "UPDATE ai_action_proposals SET status = 'consumed' WHERE id = ?", (pid,))
    with pytest.raises(HTTPException) as e:
        await undo_approved_action(
            pid, user=_user(acct, uid, "fleet"), user_context={},
            platform_db=pg_db, tenant_db=pg_db)
    assert e.value.status_code == 409


async def test_legacy_result_without_manifest_fails_closed_and_stays_undoable(pg_db):
    """A pre-undo-era import has no _item_ids: the recipe 409s and the
    claim rolls back to consumed (nothing half-done)."""
    acct, users, _vids = await _seed(pg_db)
    uid = users["approver"]
    pid = await _approve_import(pg_db, acct, uid)
    from infra.crypto import encrypt
    await pg_db._db.execute(
        "UPDATE ai_action_proposals SET result = ? WHERE id = ?",
        (encrypt(json.dumps({"imported": 2, "message": "legacy"})), pid))
    with pytest.raises(HTTPException) as e:
        await undo_approved_action(
            pid, user=_user(acct, uid, "fleet"), user_context={},
            platform_db=pg_db, tenant_db=pg_db)
    assert e.value.status_code == 409
    prop = await pg_db.get_action_proposal_for_account(pid, acct)
    assert prop["status"] == "consumed"            # claim rolled back


async def test_manually_removed_item_is_skipped_not_fatal(pg_db):
    acct, users, vids = await _seed(pg_db)
    uid = users["approver"]
    pid = await _approve_import(pg_db, acct, uid)
    victim = (await pg_db.list_vehicle_inventory(acct, vids["110"]))[0]
    await pg_db.remove_inventory_item(acct, victim["id"], note="manual")
    res = await undo_approved_action(
        pid, user=_user(acct, uid, "fleet"), user_context={},
        platform_db=pg_db, tenant_db=pg_db)
    assert res["result"]["undone"] == 1
    assert res["result"]["already_gone"] == 1
