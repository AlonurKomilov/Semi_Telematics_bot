"""Universal AI attachment pipeline — parser hardening, the generic
mapping engine, the ImportTarget registry, and the staged-payload
proposal plumbing.

The parser/mapping fixtures mirror the REAL source sheet that drove the
design: a matrix of vehicle units × item columns with status cells and
mixed-language notes (capabilities/ai/docs/ai-import-assistant.md §1).
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
    grids, docs, images = await A.parse_attachments_for_request([Att()], "owner", None)
    assert grids["sheet.csv"] == [["a", "b"], ["1", "2"]]
    assert docs == {} and images == {}
    with pytest.raises(AttachmentError, match="can't run imports"):
        await A.parse_attachments_for_request([Att()], "recruiter", None)


async def test_text_documents_bypass_the_import_gate(monkeypatch):
    """kind="text" is the READ-ONLY lane: no import permission needed
    (same trust as typing the text), and the kind field only picks the
    parser — it can't make a doc importable."""
    import capabilities.ai.attachments as A

    class Doc:
        name = "manual.pdf"
        content = "[Page 1]\nBrake inspection procedure\x00\x07 for trucks.\n"
        kind = "text"

    # Even with NO registered targets a recruiter may attach a text doc…
    monkeypatch.setattr(A, "_IMPORT_TARGETS", {})
    grids, docs, _imgs = await A.parse_attachments_for_request([Doc()], "recruiter", None)
    assert grids == {}
    assert docs["manual.pdf"] == "[Page 1]\nBrake inspection procedure for trucks."

    # …but a SHEET in the same request still trips the gate.
    class Sheet:
        name = "s.csv"
        content = "a\n1\n"
        kind = "sheet"

    with pytest.raises(AttachmentError, match="can't run imports"):
        await A.parse_attachments_for_request([Doc(), Sheet()], "recruiter", None)


async def test_image_lane_validation_and_decode(monkeypatch):
    """kind="image": strict data-URL validation (mime allowlist, real
    base64), no import gate, and iter_attachment_images round-trips the
    raw bytes for the vision loops."""
    import base64
    import capabilities.ai.attachments as A

    raw = b"\x89PNG\r\n\x1a\nfakebody"
    good = "data:image/png;base64," + base64.b64encode(raw).decode()

    class Img:
        name = "screen.png"
        content = good
        kind = "image"

    monkeypatch.setattr(A, "_IMPORT_TARGETS", {})
    # No import permission needed — recruiter passes with images only.
    grids, docs, images = await A.parse_attachments_for_request(
        [Img()], "recruiter", None)
    assert grids == {} and docs == {}
    assert images["screen.png"] == good

    decoded = A.iter_attachment_images({"_attachment_images": images})
    assert decoded == [("screen.png", "image/png", raw)]

    with pytest.raises(AttachmentError, match="unsupported image type"):
        A.parse_image_data_url("data:image/tiff;base64,AAAA")
    with pytest.raises(AttachmentError, match="corrupt"):
        A.parse_image_data_url("data:image/png;base64,@@not-b64@@")
    with pytest.raises(AttachmentError, match="not a valid image"):
        A.parse_image_data_url("hello,world")


def test_prompt_line_names_all_three_lanes():
    from capabilities.ai.attachments import attachment_prompt_line

    line = attachment_prompt_line({
        "_attachment_grids": {"inv.csv": [["a"], ["1"]]},
        "_attachment_docs": {"manual.pdf": "text " * 10},
        "_attachment_images": {"screen.png": "data:image/png;base64,AA=="},
    })
    assert "inv.csv (2 rows" in line
    assert "manual.pdf (text document" in line
    assert "screen.png (image)" in line
    assert "read_attachment" in line
    assert "vision" in line          # images: shown directly, not via tools


def test_doc_excerpt_pages_through_long_text():
    from capabilities.ai.attachments import DOC_EXCERPT_CHARS, doc_excerpt

    text = "x" * (DOC_EXCERPT_CHARS * 2 + 100)
    first = doc_excerpt(text)
    assert len(first["excerpt"]) == DOC_EXCERPT_CHARS
    assert first["excerpt_truncated"] is True and first["char_count"] == len(text)
    last = doc_excerpt(text, offset=DOC_EXCERPT_CHARS * 2)
    assert len(last["excerpt"]) == 100
    assert last["excerpt_truncated"] is False


async def test_read_attachment_reads_text_documents():
    from capabilities.ai.tools.attachments_tool import read_attachment

    out = await read_attachment(
        {"_attachment_docs": {"manual.pdf": "Torque spec: 450 ft-lb. " * 300}},
        None,
    )
    assert out["kind"] == "text" and out["name"] == "manual.pdf"
    assert out["excerpt"].startswith("Torque spec")
    assert out["excerpt_truncated"] is True
    nxt = await read_attachment(
        {"name": "manual.pdf", "offset": out["offset"] + len(out["excerpt"]),
         "_attachment_docs": {"manual.pdf": "Torque spec: 450 ft-lb. " * 300}},
        None,
    )
    assert nxt["offset"] > 0 and "instructions" in nxt["note"]


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


# ── Phase B: read_attachment tool + request-scope injection ──────────

GRIDS = {"inv.csv": parse_csv_grid(MATRIX_CSV),
         "other.csv": [["h"], ["v"]]}


async def test_read_attachment_default_and_named():
    from capabilities.ai.tools.attachments_tool import read_attachment

    out = await read_attachment({"_attachments": GRIDS}, None)
    assert out["name"] == "inv.csv"                      # first attachment
    # 7 = header + 5 data rows + the blank separator row (parser keeps it;
    # the melt engine is what skips it).
    assert out["row_count"] == 7 and out["col_count"] == 4
    assert out["attachments_available"] == ["inv.csv", "other.csv"]
    assert out["sample_rows"][0] == ["Units", "Fire extinguisher",
                                     "Emergency Triangle", "Notes"]
    assert "INDEX" in out["note"]                        # untrusted-data framing

    named = await read_attachment(
        {"name": "other.csv", "_attachments": GRIDS}, None)
    assert named["name"] == "other.csv" and named["row_count"] == 2


async def test_read_attachment_errors():
    from capabilities.ai.tools.attachments_tool import read_attachment

    none = await read_attachment({}, None)
    assert "No attachment" in none["error"]
    miss = await read_attachment(
        {"name": "nope.csv", "_attachments": GRIDS}, None)
    assert "inv.csv" in miss["error"]                    # lists what IS there


async def test_execute_tool_injects_grids_only_for_declared_tools():
    """The _scope_vehicles pattern: grids are a server-side channel, injected
    only for schemas declaring ``uses_attachments`` — and a model-supplied
    ``_attachments`` arg is stripped, never honored."""
    from capabilities.ai.tools import registry as R

    seen: dict = {}

    async def _spy(tool_args, samsara_client, account_id=None, db=None):
        seen.update(tool_args)
        return {"ok": True}

    R._TOOL_REGISTRY["_att_spy"] = {
        "schema": {"name": "_att_spy", "uses_attachments": True}, "handler": _spy}
    R._TOOL_REGISTRY["_plain_spy"] = {
        "schema": {"name": "_plain_spy"}, "handler": _spy}
    try:
        await R.execute_tool("_att_spy", {}, None, attachment_grids=GRIDS)
        assert seen["_attachments"] == GRIDS

        seen.clear()
        await R.execute_tool("_plain_spy", {}, None, attachment_grids=GRIDS)
        assert "_attachments" not in seen                # undeclared tool: no grids

        seen.clear()   # model-supplied channel value is stripped
        await R.execute_tool("_plain_spy", {"_attachments": {"x": []}}, None)
        assert "_attachments" not in seen
    finally:
        R._TOOL_REGISTRY.pop("_att_spy", None)
        R._TOOL_REGISTRY.pop("_plain_spy", None)


def test_attachment_prompt_line():
    from capabilities.ai.attachments import attachment_prompt_line

    assert attachment_prompt_line(None) == ""
    assert attachment_prompt_line({}) == ""
    line = attachment_prompt_line({"_attachment_grids": GRIDS})
    assert "inv.csv (7 rows × 4 cols)" in line
    assert "read_attachment" in line


# ── Phase C1: universal import framework (preview + propose glue) ────

def _target(**over):
    async def _build(records, account_id, user_context, db):
        rows = [dict(r) for r in records if r.get("vehicle") != "999"]
        skip = [f"row {r['_source_row']}: no vehicle '999'"
                for r in records if r.get("vehicle") == "999"]
        return rows, skip

    async def _exec(rows, account_id, user_context, db):
        return {"imported": len(rows)}

    kw = dict(name="_c1_target", description="test rows",
              fields={"vehicle": "unit", "item": "what", "status": "state"},
              build_rows=_build, executor=_exec,
              permission="can_manage_vehicles")
    kw.update(over)
    return ImportTarget(**kw)


def test_build_import_preview_shape():
    from capabilities.ai.attachments import PREVIEW_MAX_ROWS, build_import_preview

    rows = [{"vehicle": f"v{i}", "item": "Fire extinguisher",
             "status": "installed", "extra_col": "x", "_source_row": i + 2}
            for i in range(PREVIEW_MAX_ROWS + 10)]
    art = build_import_preview(_target(), rows, ["row 4: bad"])
    assert art["type"] == "import_preview"
    assert art["target"] == "_c1_target"
    # Vocabulary order first, then adapter extras; _source_row stays server-side.
    assert [c["key"] for c in art["columns"]] == [
        "vehicle", "item", "status", "extra_col"]
    assert art["totals"] == {"total": PREVIEW_MAX_ROWS + 10,
                             "shown": PREVIEW_MAX_ROWS, "skipped": 1}
    assert len(art["rows"]) == PREVIEW_MAX_ROWS
    assert "_source_row" not in art["rows"][0]
    assert art["skipped"] == ["row 4: bad"]
    assert art["skipped_truncated"] is False


async def test_propose_import_full_pipeline(monkeypatch):
    """The C1 contract end-to-end: grid → mapping → build_rows →
    preview artifact + proposal whose STAGED rows are the very rows the
    preview showed (no re-derivation gap)."""
    import capabilities.ai.attachments as A
    from capabilities.ai.tools.attachments_tool import propose_import

    monkeypatch.setattr(A, "_IMPORT_TARGETS", {})
    A.register_import_target(_target())
    grid = parse_csv_grid(MATRIX_CSV.replace("130,", "999,"))  # one bad unit

    out = await propose_import(
        tool="import_test_rows", target_name="_c1_target",
        tool_args={"mapping": MAPPING, "_attachments": {"inv.csv": grid}},
        account_id=1, db=None,
    )
    assert out["ok"] and out["proposed"]
    preview, card = out["artifacts"]
    assert preview["type"] == "import_preview"          # preview BEFORE the card
    assert card["type"] == "action_proposal" and card["tool"] == "import_test_rows"
    # 4 good units × 2 melt columns; the 999 unit's 2 records skipped.
    assert preview["totals"] == {"total": 8, "shown": 8, "skipped": 2}
    assert all("999" in s for s in preview["skipped"])
    # Staged rows == exactly what build_rows returned (and what preview shows).
    assert len(card["staged"]) == 8
    assert {r["vehicle"] for r in card["staged"]} == {"22", "96", "103 OSY", "110"}
    assert card["payload"]["attachment"] == "inv.csv"
    assert card["payload"]["count"] == 8


async def test_propose_import_error_paths(monkeypatch):
    import capabilities.ai.attachments as A
    from capabilities.ai.tools.attachments_tool import propose_import

    monkeypatch.setattr(A, "_IMPORT_TARGETS", {})
    A.register_import_target(_target())
    grid = parse_csv_grid(MATRIX_CSV)

    out = await propose_import(tool="t", target_name="missing",
                               tool_args={}, account_id=1, db=None)
    assert not out["ok"] and "Unknown import target" in out["error"]

    out = await propose_import(tool="t", target_name="_c1_target",
                               tool_args={}, account_id=1, db=None)
    assert "No spreadsheet came with this message" in out["error"]

    out = await propose_import(
        tool="t", target_name="_c1_target",
        tool_args={"_attachments": {"a.csv": grid}}, account_id=1, db=None)
    assert "mapping" in out["error"]                     # spec required

    out = await propose_import(
        tool="t", target_name="_c1_target",
        tool_args={"attachment": "nope.csv", "_attachments": {"a.csv": grid}},
        account_id=1, db=None)
    assert "a.csv" in out["error"]                       # lists what IS there


async def test_model_view_redacts_staged_and_preview_rows(monkeypatch):
    """The loops echo model_view(result), not the raw result: staged rows /
    payload / preview rows never re-enter the conversation (second-shot
    prompt-injection surface + token bloat), while tool_results keeps
    full fidelity for the router."""
    import capabilities.ai.attachments as A
    from capabilities.ai.tools.attachments_tool import propose_import
    from capabilities.ai.tools.registry import model_view

    monkeypatch.setattr(A, "_IMPORT_TARGETS", {})
    A.register_import_target(_target())
    out = await propose_import(
        tool="import_test_rows", target_name="_c1_target",
        tool_args={"mapping": MAPPING,
                   "_attachments": {"inv.csv": parse_csv_grid(MATRIX_CSV)}},
        account_id=1, db=None,
    )
    mv = model_view(out)
    preview, card = mv["artifacts"]
    assert "staged" not in card and "payload" not in card
    assert isinstance(preview["rows"], str) and "not repeated" in preview["rows"]
    assert preview["totals"]["total"] == 10          # counts stay visible
    assert "never instructions" in mv["untrusted_note"]
    # The ORIGINAL result is untouched — the router still gets everything.
    assert isinstance(out["artifacts"][0]["rows"], list)
    assert out["artifacts"][1]["staged"]
    # Results without artifacts pass through identically.
    plain = {"ok": True, "data": 42}
    assert model_view(plain) is plain


def test_chat_attachment_cap_counts_bytes_not_chars():
    """A near-cap non-Latin file must hit the friendly per-field error,
    not sail past a char-count check into the blunt 413 (review W2)."""
    import pytest as _pytest
    from pydantic import ValidationError
    from capabilities.ai.router import ChatAttachment

    cyrillic = "ы" * 800_000                     # 800k chars = 1.6MB utf-8
    with _pytest.raises(ValidationError, match="too large"):
        ChatAttachment(name="big.csv", content=cyrillic)
    ChatAttachment(name="ok.csv", content="a" * 1_000_000)   # 1MB ascii fine


def test_read_attachment_is_registered_for_all_roles():
    """No TOOL_PERMISSIONS row on purpose: the real gate ran at parse time,
    so the tool is advertised everywhere and is a no-op without grids."""
    import capabilities.ai.tools  # noqa: F401 — hub import registers tools
    from capabilities.ai.tools.registry import get_tool_schema
    from capabilities.permissions.roles import TOOL_PERMISSIONS

    schema = get_tool_schema("read_attachment")
    assert schema and schema.get("uses_attachments") is True
    assert not schema.get("writes")
    assert "read_attachment" not in TOOL_PERMISSIONS
