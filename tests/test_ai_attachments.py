"""Universal AI attachment pipeline — parser hardening, the generic
mapping engine, the ImportTarget registry, and the staged-payload
proposal plumbing.

The parser/mapping fixtures mirror the REAL source sheet that drove the
design: a matrix of vehicle units × item columns with status cells and
mixed-language notes (docs/architecture/ai-import-assistant.md §1).
"""

from __future__ import annotations

import os

os.environ.setdefault("ENCRYPTION_KEY", "")

import json

import pytest

from capabilities.ai.attachments import (
    MAX_CELL_CHARS,
    MAX_COLS,
    MAX_ROWS,
    AttachmentError,
    ImportTarget,
    apply_mapping,
    get_import_target,
    grid_sample,
    parse_csv_grid,
    register_import_target,
)
from capabilities.permissions.roles import Role

# The real sheet's shape: units × item columns + free-text notes.
MATRIX_CSV = (
    "Units,Fire extinguisher,Emergency Triangle,Notes\n"
    "22,Good,3 of 3,\n"
    "96,Not checked,Not checked,remontda bolshi kere\n"
    "103 OSY,Not checked,Not checked,\n"
    "110,Good,3 of 3,\n"
    ",,,\n"
    "130,Not checked,Not checked,\"Driver kasal ekan, kamen v pochkah\"\n"
)

MAPPING = {
    "has_header": True,
    "id_columns": [{"index": 0, "field": "vehicle"}],
    "melt_columns": [
        {"index": 1, "constants": {"item": "Fire extinguisher", "category": "safety"},
         "value_field": "status",
         "value_map": {"good": "installed", "not checked": "unverified"}},
        {"index": 2, "constants": {"item": "Emergency Triangle", "category": "safety"},
         "value_field": "status",
         "value_map": {"3 of 3": "installed", "not checked": "unverified"}},
    ],
    "notes_column": {"index": 3, "field": "note"},
}


# ── Parser hardening ─────────────────────────────────────────────────

def test_parser_handles_bom_and_quoted_newlines():
    csv_text = "﻿a,b\n\"line1\nline2\",x\n"
    grid = parse_csv_grid(csv_text)
    assert grid[0] == ["a", "b"]
    assert "line1" in grid[1][0] and "line2" in grid[1][0]
    assert len(grid) == 2  # the quoted newline did NOT create a third row


def test_parser_strips_control_chars():
    grid = parse_csv_grid("a\x00b\x07c,d\n")
    assert grid[0][0] == "abc"


def test_parser_caps_rows_cols_cells():
    too_many_rows = "\n".join("x" for _ in range(MAX_ROWS + 1))
    with pytest.raises(AttachmentError, match="too many rows"):
        parse_csv_grid(too_many_rows)
    with pytest.raises(AttachmentError, match="too many columns"):
        parse_csv_grid(",".join("c" for _ in range(MAX_COLS + 1)))
    with pytest.raises(AttachmentError, match="exceeds"):
        parse_csv_grid("x" * (MAX_CELL_CHARS + 1))


def test_parser_rejects_empty():
    with pytest.raises(AttachmentError, match="empty"):
        parse_csv_grid(",,\n,,\n")


def test_grid_sample_is_bounded():
    grid = [[f"r{r}c{c}" * 30 for c in range(3)] for r in range(50)]
    s = grid_sample(grid)
    assert s["row_count"] == 50 and s["sample_truncated"] is True
    assert len(s["sample_rows"]) == 20
    assert all(len(c) <= 80 for row in s["sample_rows"] for c in row)


# ── Mapping engine (wide → long melt) ────────────────────────────────

def test_apply_mapping_melts_the_real_matrix():
    grid = parse_csv_grid(MATRIX_CSV)
    records, problems = apply_mapping(grid, MAPPING)
    assert problems == []
    # 5 data rows × 2 melt columns = 10 records (blank row skipped)
    assert len(records) == 10
    r22 = [r for r in records if r["vehicle"] == "22"]
    assert {r["item"] for r in r22} == {"Fire extinguisher", "Emergency Triangle"}
    assert all(r["status"] == "installed" for r in r22)   # value_map applied
    r96 = [r for r in records if r["vehicle"] == "96"]
    assert all(r["status"] == "unverified" for r in r96)
    assert all(r["note"] == "remontda bolshi kere" for r in r96)  # note rides along
    r130 = [r for r in records if r["vehicle"] == "130"][0]
    assert "kamen v pochkah" in r130["note"]              # quoted comma survived
    assert all(r["category"] == "safety" for r in records)
    assert all(isinstance(r["_source_row"], int) for r in records)


def test_apply_mapping_skip_values_and_blank_cells():
    grid = [["u", "col"], ["22", "n/a"], ["23", ""], ["24", "Good"]]
    spec = {
        "has_header": True,
        "id_columns": [{"index": 0, "field": "vehicle"}],
        "melt_columns": [{"index": 1, "constants": {"item": "X"},
                          "value_field": "status", "skip_values": ["n/a"]}],
    }
    records, problems = apply_mapping(grid, spec)
    assert problems == []
    assert [r["vehicle"] for r in records] == ["24"]


def test_apply_mapping_rejects_bad_spec():
    records, problems = apply_mapping([["a"]], {"id_columns": [], "melt_columns": []})
    assert records == [] and problems


def test_apply_mapping_record_cap():
    grid = [["u", "a"]] + [[f"v{i}", "x"] for i in range(1200)]
    spec = {
        "has_header": True,
        "id_columns": [{"index": 0, "field": "vehicle"}],
        "melt_columns": [{"index": 1, "constants": {}, "value_field": "s"}],
    }
    records, problems = apply_mapping(grid, spec)
    assert records == []
    assert any("cap" in p for p in problems)


# ── ImportTarget registry ────────────────────────────────────────────

async def test_import_target_registry_roundtrip():
    async def _build(records, account_id, user_context, db):
        return records, []

    async def _exec(rows, account_id, user_context, db):
        return {"imported": len(rows)}

    t = register_import_target(ImportTarget(
        name="_test_target", description="t", fields={"vehicle": "unit"},
        build_rows=_build, executor=_exec,
    ))
    assert get_import_target("_test_target") is t
    assert get_import_target("nope") is None


# ── Attachment gate (a registered ImportTarget's permission) ─────────

async def test_parse_gate_by_import_target_permission(monkeypatch):
    """Parsing is gated on holding a registered ImportTarget's permission
    (fail-closed with none registered — attachments only exist to feed
    imports).  "Any write-tool permission" was rejected as vacuous:
    always-on derived flags give every role SOME write tool."""
    import capabilities.ai.attachments as A

    class Att:
        name = "sheet.csv"
        content = "a,b\n1,2\n"

    async def _noop(*a, **k):
        return [], []

    # No registered targets → even the owner is refused (fail-closed).
    monkeypatch.setattr(A, "_IMPORT_TARGETS", {})
    with pytest.raises(AttachmentError, match="can't run imports"):
        await A.parse_attachments_for_request([Att()], "owner", None)

    # With a target gated on can_manage_vehicles: owner passes,
    # recruiter (no vehicle management) is refused.
    A.register_import_target(A.ImportTarget(
        name="inv", description="", fields={},
        build_rows=_noop, executor=_noop, permission="can_manage_vehicles",
    ))
    grids = await A.parse_attachments_for_request([Att()], "owner", None)
    assert grids["sheet.csv"] == [["a", "b"], ["1", "2"]]
    with pytest.raises(AttachmentError, match="can't run imports"):
        await A.parse_attachments_for_request([Att()], "recruiter", None)


# ── Staged payload on proposals (real Postgres) ──────────────────────

@pytest.fixture()
def _encryption_on(monkeypatch):
    from infra import crypto
    monkeypatch.setenv("ENCRYPTION_KEY", "test-passphrase-attachments")
    crypto.init_encryption()
    yield
    monkeypatch.setenv("ENCRYPTION_KEY", "")
    crypto.init_encryption()


async def _seed(pg_db, name="Import Co"):
    from interfaces.api.auth import _hash_password
    acct = await pg_db.create_account(name)
    owner = await pg_db.create_user_with_email(
        email=f"owner.{acct.id}@example.com",
        password_hash=_hash_password("ownerpass123"),
        account_id=acct.id, role=Role.OWNER, display_name="Owner",
    )
    return acct.id, (owner.telegram_id or owner.id)


async def test_staged_payload_roundtrip_untruncated(pg_db, _encryption_on):
    acct, uid = await _seed(pg_db)
    # Bigger than payload's 8k truncation — staged must survive intact.
    rows = [{"vehicle": f"v{i}", "item": "Fire extinguisher", "note": "x" * 40}
            for i in range(300)]
    staged_json = json.dumps(rows)
    assert len(staged_json) > 8000
    pid = await pg_db.create_action_proposal(
        acct, uid, "import_inventory_items", "Import 300 items",
        json.dumps({"mapping": "…"}), staged_payload_json=staged_json,
    )
    # Ciphertext at rest.
    cur = await pg_db._db.execute(
        "SELECT staged_payload FROM ai_action_proposals WHERE id = ?", (pid,))
    raw = (await cur.fetchone())[0]
    assert raw.startswith("enc::") and "Fire extinguisher" not in raw
    # Full roundtrip, no truncation.
    got = await pg_db.get_action_proposal(pid, acct, uid)
    assert json.loads(got["staged_payload"]) == rows


async def test_staged_payload_defaults_empty(pg_db):
    acct, uid = await _seed(pg_db)
    pid = await pg_db.create_action_proposal(acct, uid, "t", "s", "{}")
    got = await pg_db.get_action_proposal(pid, acct, uid)
    assert got["staged_payload"] == ""
