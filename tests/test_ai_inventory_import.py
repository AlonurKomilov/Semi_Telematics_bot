"""Inventory import adapter (phase C2) — vehicle resolution per the
advisor's §5.5 rules, staged-row execution in ONE transaction, and the
propose-side flow through the real registered ImportTarget.

Resolution law under test: minimal normalization only (NEVER strip
leading zeros), company-suffix parse against enumerated codes, and
"exactly one candidate or skip" — ambiguity is reported, not guessed.
"""

from __future__ import annotations

import os

os.environ.setdefault("ENCRYPTION_KEY", "")

import pytest
from fastapi import HTTPException

import capabilities.ai.tools  # noqa: F401 — load the hub FIRST (registers everything fully)
from capabilities.permissions.roles import Role
from features.vehicles.inventory.ai_actions import (
    _build_rows,
    _execute_import_inventory,
    import_inventory_items,
)


async def _seed(pg_db):
    from interfaces.api.auth import _hash_password
    acct = await pg_db.create_account("Import Fleet Co")
    owner = await pg_db.create_user_with_email(
        email=f"owner.{acct.id}@example.com",
        password_hash=_hash_password("ownerpass123"),
        account_id=acct.id, role=Role.OWNER, display_name="Owner",
    )
    vids = {}
    for unit, code in (
        ("22", "OSY"), ("22", "TRK"),      # bare "22" is ambiguous
        ("022", "OSY"),                     # leading zero ≠ "22"
        ("103", "OSY"),                     # reachable as "103 OSY"
        ("110", ""),                        # bare unique
    ):
        vids[(unit, code)] = await pg_db.add_vehicle(
            acct.id, unit_number=unit, company_code=code,
        )
    return acct.id, (owner.telegram_id or owner.id), vids


def _rec(vehicle, item="Dashcam", status="installed", row=2, **extra):
    return {"vehicle": vehicle, "item": item, "status": status,
            "category": "camera", "_source_row": row, **extra}


# ── Resolution rules (§5.5) ──────────────────────────────────────────

async def test_resolution_rules(pg_db):
    acct, _uid, vids = await _seed(pg_db)
    records = [
        _rec("110", row=2),                 # bare unique → resolved
        _rec("22", row=3),                  # two companies → ambiguous skip
        _rec("103 OSY", row=4),             # suffix parse → resolved
        _rec("022", row=5),                 # leading zero: its OWN vehicle
        _rec("999", row=6),                 # unknown → skip
    ]
    rows, skipped = await _build_rows(records, acct, None, pg_db)
    assert [r["vehicle"] for r in rows] == ["110", "103 (OSY)", "022 (OSY)"]
    assert rows[0]["_vehicle_id"] == vids[("110", "")]
    assert rows[1]["_vehicle_id"] == vids[("103", "OSY")]
    assert rows[2]["_vehicle_id"] == vids[("022", "OSY")]   # NOT ("22","OSY")
    assert len(skipped) == 2
    assert any("matches 2 vehicles" in s and "row 3" in s for s in skipped)
    assert any("no vehicle '999'" in s and "row 6" in s for s in skipped)


async def test_row_validation(pg_db):
    acct, _uid, _vids = await _seed(pg_db)
    records = [
        _rec("110", item="  ", row=2),                     # blank item
        _rec("110", status="Good", row=3),                 # unmapped status
        _rec("110", status="needs check", row=4),          # normalizes to vocab
        _rec("110", category="Safety Equipment", row=5),   # unknown cat → other
    ]
    rows, skipped = await _build_rows(records, acct, None, pg_db)
    assert len(rows) == 2
    assert rows[0]["status"] == "needs_check"
    assert rows[1]["category"] == "other"
    assert any("no item name" in s and "row 2" in s for s in skipped)
    assert any("value_map" in s and "row 3" in s for s in skipped)


# ── Executor: one transaction, re-resolution, accountability ─────────

async def _stage(pg_db, acct):
    records = [
        _rec("110", item="Fire extinguisher", row=2),
        _rec("103 OSY", item="Tablet", status="needs_check", row=3,
             identifier="SN-42", note="screen cracked"),
    ]
    rows, skipped = await _build_rows(records, acct, None, pg_db)
    assert skipped == []
    return rows


async def test_execute_imports_staged_rows_with_audit_trail(pg_db):
    acct, uid, vids = await _seed(pg_db)
    rows = await _stage(pg_db, acct)
    out = await _execute_import_inventory(
        {}, acct, {"_staged_rows": rows, "user_id": uid}, pg_db)
    assert out["imported"] == 2 and out["skipped_count"] == 0
    items = await pg_db.list_vehicle_inventory(acct, vids[("103", "OSY")])
    assert len(items) == 1
    it = items[0]
    assert (it["label"], it["status"], it["identifier"]) == (
        "Tablet", "needs_check", "SN-42")
    events = await pg_db.list_inventory_events(acct, it["id"])
    assert events and events[0]["actor_user_id"] == uid


async def test_execute_skips_vanished_vehicle_never_fails_whole_import(pg_db):
    acct, uid, vids = await _seed(pg_db)
    rows = await _stage(pg_db, acct)
    await pg_db.deactivate_vehicle(acct, vids[("110", "")])
    out = await _execute_import_inventory(
        {}, acct, {"_staged_rows": rows, "user_id": uid}, pg_db)
    assert out["imported"] == 1 and out["skipped_count"] == 1
    assert "no longer in the registry" in out["skipped"][0]


async def test_execute_is_all_or_nothing(pg_db, monkeypatch):
    acct, uid, vids = await _seed(pg_db)
    rows = await _stage(pg_db, acct)
    real = pg_db.add_inventory_item
    calls = {"n": 0}

    async def _boom(*a, **k):
        calls["n"] += 1
        if calls["n"] == 2:
            raise RuntimeError("disk full")
        return await real(*a, **k)

    monkeypatch.setattr(pg_db, "add_inventory_item", _boom)
    with pytest.raises(RuntimeError):
        await _execute_import_inventory(
            {}, acct, {"_staged_rows": rows, "user_id": uid}, pg_db)
    # The FIRST insert succeeded in-transaction — the rollback must erase it.
    for vid in vids.values():
        assert await pg_db.list_vehicle_inventory(acct, vid) == []


async def test_execute_refuses_missing_staged_rows(pg_db):
    acct, uid, _vids = await _seed(pg_db)
    with pytest.raises(HTTPException) as e:
        await _execute_import_inventory({}, acct, {"user_id": uid}, pg_db)
    assert e.value.status_code == 409


# ── Propose side through the REAL registered target ──────────────────

SHEET = (
    "Units,Fire extinguisher,Emergency Triangle,Notes\n"
    "110,Good,3 of 3,\n"
    "103 OSY,Not checked,Not checked,remontda bolshi kere\n"
    "999,Good,3 of 3,\n"
)

MAPPING = {
    "has_header": True,
    "id_columns": [{"index": 0, "field": "vehicle"}],
    "melt_columns": [
        {"index": 1, "constants": {"item": "Fire extinguisher", "category": "other"},
         "value_field": "status",
         "value_map": {"good": "installed", "not checked": "needs_check"}},
        {"index": 2, "constants": {"item": "Emergency Triangle", "category": "other"},
         "value_field": "status",
         "value_map": {"3 of 3": "installed", "not checked": "needs_check"}},
    ],
    "notes_column": {"index": 3, "field": "note"},
}


async def test_propose_flow_shows_resolved_vehicles(pg_db):
    from capabilities.ai.attachments import parse_csv_grid
    acct, _uid, _vids = await _seed(pg_db)

    out = await import_inventory_items(
        {"mapping": MAPPING, "_attachments": {"inv.csv": parse_csv_grid(SHEET)}},
        None, account_id=acct, db=pg_db)
    assert out["ok"] and out["proposed"]
    preview, card = out["artifacts"]
    # 2 units × 2 items staged; unit 999 skipped twice (2 melt records).
    assert preview["totals"] == {"total": 4, "shown": 4, "skipped": 2}
    # Approval is only meaningful if resolution is VISIBLE (§5.5): the
    # preview shows the resolved registry vehicle, not the sheet string.
    assert {r["vehicle"] for r in preview["rows"]} == {"110", "103 (OSY)"}
    assert {r["status"] for r in preview["rows"]} == {"installed", "needs_check"}
    assert card["summary"] == "Import 4 inventory items — 2 skipped"
    assert len(card["staged"]) == 4
    assert all(r.get("_vehicle_id") for r in card["staged"])


def test_scope_and_permission_contract():
    """The registered schema honors the write-tool scope law and the
    parse gate finds a registered target (no longer fail-closed-empty)."""
    from capabilities.ai.attachments import list_import_targets
    from capabilities.ai.tools.registry import get_tool_schema
    from capabilities.permissions.roles import (
        ACCOUNT_WIDE_TOOLS, SCOPE_AWARE_TOOLS, TOOL_PERMISSIONS,
    )

    s = get_tool_schema("import_inventory_items")
    assert s and s["writes"] and s["scope"] == "account_unscoped"
    assert s["uses_attachments"] is True
    assert TOOL_PERMISSIONS["import_inventory_items"] == ["can_manage_vehicles"]
    assert "import_inventory_items" in ACCOUNT_WIDE_TOOLS
    assert "import_inventory_items" not in SCOPE_AWARE_TOOLS
    assert any(t.name == "inventory" and t.permission == "can_manage_vehicles"
               for t in list_import_targets())
