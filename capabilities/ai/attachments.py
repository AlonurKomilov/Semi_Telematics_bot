"""Universal AI attachment pipeline — transient, feature-agnostic.

Design (docs/architecture/ai-import-assistant.md): the FILE lives on the
user's device; its text rides inline on the chat request and is parsed
HERE, in memory, per turn.  Nothing in this module persists anything —
no disk, no DB.  The parsed grid travels the request scope
(``user_context["_attachment_grids"]``) and dies with the turn.

Three parts, all feature-blind:

  • ``parse_csv_grid``        — hardened CSV → grid (list of rows)
  • ``apply_mapping``         — grid + mapping_spec → normalized records
                                (generic wide→long "melt"; columns are
                                addressed by INDEX, never header name —
                                duplicate/blank headers break names)
  • ``ImportTarget`` registry — a feature registers its import adapter
                                (field vocabulary, row builder, executor);
                                the pipeline stays ignorant of domains.

Security posture (fable-advisor consult, §5 of the design doc): strict
utf-8-sig decode (no charset guessing), real CSV reader (quoted embedded
newlines), streaming caps with early abort, NUL/control stripping.
Spreadsheet cells are UNTRUSTED — samples shown to the model are
truncated and framed as data, and row data never round-trips through the
model (it proposes a mapping; the server owns the data).
"""

from __future__ import annotations

import csv
import io
import logging
from dataclasses import dataclass, field
from typing import Any, Awaitable, Callable

logger = logging.getLogger("bot.ai")

# ── Caps (advisor-set; enforced streaming, early abort) ─────────────
MAX_ROWS = 2000
MAX_COLS = 64
MAX_CELL_CHARS = 500
MAX_RECORDS = 1000          # normalized records per import
MAX_ATTACHMENTS = 3         # per request
# Per-attachment cap in UTF-8 BYTES — the outer boundary (the 2MB
# request-body middleware) counts bytes, and non-Latin sheets are
# 2+ bytes/char, so a char-count cap here would let a request sail
# past this check only to hit the blunt 413.  The dashboard's
# attachmentStore enforces the same byte caps client-side first.
MAX_CONTENT_BYTES = 1_400_000

# The bounded sample the MODEL may see (prompt-injection blast-radius
# control: a poisoned cell can at worst suggest a bad mapping, which the
# human-reviewed preview catches).
SAMPLE_ROWS = 20
SAMPLE_CELL_CHARS = 80

# Text documents (PDF text extracted on the device, plain .txt): the
# model reads them through read_attachment in bounded windows.
DOC_EXCERPT_CHARS = 4000

# Images arrive as device-compressed data-URLs (downscaled + re-encoded
# in the browser) and are shown DIRECTLY to vision-capable models.
IMAGE_MIMES = frozenset({"image/png", "image/jpeg", "image/webp", "image/gif"})

# The bounded preview the USER sees on the import_preview artifact (the
# full staged rows still travel to the executor — this only caps display).
PREVIEW_MAX_ROWS = 50
PREVIEW_MAX_SKIPPED = 20


class AttachmentError(ValueError):
    """User-facing attachment problem (bad encoding, over caps, …)."""


def parse_csv_grid(content: str, *, name: str = "attachment") -> list[list[str]]:
    """Parse CSV text into a rectangular-ish grid with hard caps.

    ``content`` arrives as str (FastAPI/pydantic already decoded the JSON
    request as UTF-8); a stray BOM from Excel/Sheets exports is stripped.
    Control characters (except tab) are removed per cell; cells are
    length-capped.  Raises ``AttachmentError`` with a human message on
    any cap breach — never truncates silently (a silently-shortened
    import is worse than a rejected one).
    """
    if content.startswith("﻿"):
        content = content[1:]
    if len(content.encode("utf-8")) > MAX_CONTENT_BYTES:
        raise AttachmentError(
            f"{name}: file too large (max {MAX_CONTENT_BYTES / 1_000_000:.1f} MB)."
        )
    # Quoted fields may legally contain newlines — a real CSV reader is
    # mandatory (naive splitlines inflates row counts).
    csv.field_size_limit(MAX_CELL_CHARS * 4)
    grid: list[list[str]] = []
    try:
        for row in csv.reader(io.StringIO(content)):
            if len(grid) >= MAX_ROWS:
                raise AttachmentError(f"{name}: too many rows (max {MAX_ROWS}).")
            if len(row) > MAX_COLS:
                raise AttachmentError(f"{name}: too many columns (max {MAX_COLS}).")
            cleaned = []
            for cell in row:
                cell = "".join(
                    ch for ch in cell if ch == "\t" or ch == "\n" or ord(ch) >= 32
                ).strip()
                if len(cell) > MAX_CELL_CHARS:
                    raise AttachmentError(
                        f"{name}: a cell exceeds {MAX_CELL_CHARS} characters."
                    )
                cleaned.append(cell)
            grid.append(cleaned)
    except csv.Error as e:
        raise AttachmentError(f"{name}: not valid CSV ({e}).") from e
    if not grid or not any(any(c for c in row) for row in grid):
        raise AttachmentError(f"{name}: the file is empty.")
    return grid


def grid_sample(grid: list[list[str]]) -> dict:
    """The bounded, truncated view the MODEL is allowed to see."""
    rows = [
        [c[:SAMPLE_CELL_CHARS] for c in row[:MAX_COLS]]
        for row in grid[:SAMPLE_ROWS]
    ]
    return {
        "row_count": len(grid),
        "col_count": max((len(r) for r in grid), default=0),
        "sample_rows": rows,
        "sample_truncated": len(grid) > SAMPLE_ROWS,
    }


# ── Generic mapping engine (wide → long melt) ────────────────────────
#
# mapping_spec (all columns by INDEX):
#   {
#     "id_columns":   [{"index": 0, "field": "vehicle"}],
#     "melt_columns": [{"index": 1,
#                       "constants": {"item": "Fire extinguisher",
#                                     "category": "safety"},
#                       "value_field": "status",
#                       "value_map": {"good": "installed", …},   # optional
#                       "skip_values": ["", "n/a"]}],            # optional
#     "notes_column": {"index": 3, "field": "note"},             # optional
#     "has_header": true
#   }
#
# Each data row × each melt column → ONE record:
#   {**id_fields, **constants, value_field: mapped(cell), note?: …}
# Empty melt cells (or skip_values) produce no record.

def apply_mapping(grid: list[list[str]], spec: dict) -> tuple[list[dict], list[str]]:
    """Apply a mapping spec to the FULL grid → (records, problems).

    Deterministic and server-side only — this same function feeds both
    the preview artifact and (via the staged rows) the executor, so what
    the user approves is exactly what gets written.
    """
    problems: list[str] = []

    def _col(entry: Any, key: str = "index") -> int | None:
        try:
            idx = int(entry[key])
        except (TypeError, KeyError, ValueError):
            return None
        return idx if 0 <= idx < MAX_COLS else None

    id_cols = []
    for e in spec.get("id_columns") or []:
        idx = _col(e)
        fld = str(e.get("field") or "").strip()
        if idx is None or not fld:
            problems.append(f"Bad id_column entry: {e!r}")
            continue
        id_cols.append((idx, fld))
    melts = []
    for e in spec.get("melt_columns") or []:
        idx = _col(e)
        vfield = str(e.get("value_field") or "").strip()
        if idx is None or not vfield:
            problems.append(f"Bad melt_column entry: {e!r}")
            continue
        constants = {
            str(k): str(v) for k, v in (e.get("constants") or {}).items()
        }
        vmap = {
            str(k).strip().lower(): str(v)
            for k, v in (e.get("value_map") or {}).items()
        }
        skip = {str(s).strip().lower() for s in (e.get("skip_values") or [])}
        skip.add("")   # empty cells never produce records
        melts.append((idx, vfield, constants, vmap, skip))
    if not id_cols or not melts:
        problems.append("Mapping needs at least one id_column and one melt_column.")
        return [], problems

    notes = spec.get("notes_column") or None
    notes_idx = _col(notes) if notes else None
    notes_field = str((notes or {}).get("field") or "note").strip() or "note"

    start = 1 if spec.get("has_header", True) else 0
    records: list[dict] = []
    for r, row in enumerate(grid[start:], start=start):
        def _cell(i: int) -> str:
            return row[i] if i < len(row) else ""
        ids = {fld: _cell(idx) for idx, fld in id_cols}
        if not any(v.strip() for v in ids.values()):
            continue   # blank id row — separator/footer noise
        note_val = _cell(notes_idx) if notes_idx is not None else ""
        for idx, vfield, constants, vmap, skip in melts:
            raw = _cell(idx)
            key = raw.strip().lower()
            if key in skip:
                continue
            value = vmap.get(key, raw.strip())
            rec = {**ids, **constants, vfield: value, "_source_row": r + 1}
            if note_val:
                rec[notes_field] = note_val
            records.append(rec)
            if len(records) > MAX_RECORDS:
                problems.append(
                    f"Import exceeds the {MAX_RECORDS}-record cap — split the file."
                )
                return [], problems
    if not records:
        problems.append("The mapping produced zero records — check the columns.")
    return records, problems


# ── ImportTarget registry (features register; pipeline stays blind) ──

@dataclass
class ImportTarget:
    """A feature's import adapter.

    ``fields``     — the target's vocabulary: {field_name: description}.
                     The mapping engine's output records use these names.
    ``build_rows`` — async (records, account_id, user_context, db) →
                     (rows, skipped) : validated domain rows ready to
                     stage + human-readable skip reasons.
    ``executor``   — async (rows, account_id, user_context, db) → result
                     dict.  MUST be transactional (all-or-nothing).
    ``permission`` — echoed into the write action's TOOL_PERMISSIONS
                     entry by the feature itself; kept here for docs.
    """
    name: str
    description: str
    fields: dict[str, str]
    build_rows: Callable[..., Awaitable[tuple[list[dict], list[str]]]]
    executor: Callable[..., Awaitable[dict]]
    permission: str = ""
    extra: dict = field(default_factory=dict)


_IMPORT_TARGETS: dict[str, ImportTarget] = {}


def register_import_target(target: ImportTarget) -> ImportTarget:
    _IMPORT_TARGETS[target.name] = target
    return target


def get_import_target(name: str) -> ImportTarget | None:
    return _IMPORT_TARGETS.get(name)


def list_import_targets() -> list[ImportTarget]:
    return list(_IMPORT_TARGETS.values())


def build_import_preview(
    target: ImportTarget, rows: list[dict], skipped: list[str],
    *, title: str = "", notices: list[str] | None = None,
) -> dict:
    """Build the shared ``import_preview`` artifact for a staged import.

    One shape for EVERY import target: bounded rows (display cap only —
    the un-truncated staged rows still reach the executor), totals, and
    the skip report.  Column order follows the target's field vocabulary
    first, then any extra keys the adapter added; ``_``-prefixed keys
    (``_source_row``) stay server-side.
    """
    cols = [k for k in target.fields if any(k in r for r in rows)]
    for r in rows:
        for k in r:
            if not k.startswith("_") and k not in cols:
                cols.append(k)
    shown = rows[:PREVIEW_MAX_ROWS]
    return {
        "type": "import_preview",
        "title": title or f"Import preview — {target.name}",
        "target": target.name,
        "columns": [
            {"key": k, "label": k.replace("_", " ").title()} for k in cols
        ],
        "rows": [{k: r.get(k, "") for k in cols} for r in shown],
        "totals": {
            "total": len(rows),
            "shown": len(shown),
            "skipped": len(skipped),
        },
        "skipped": [str(s)[:200] for s in skipped[:PREVIEW_MAX_SKIPPED]],
        "skipped_truncated": len(skipped) > PREVIEW_MAX_SKIPPED,
        # Non-fatal adjustments (e.g. a value coerced to a vocabulary
        # default).  Distinct from skips: the rows DID stage, but not
        # exactly as mapped — silence here once let the model claim a
        # change succeeded when the adapter had quietly rejected it.
        "notices": [str(n)[:300] for n in (notices or [])[:PREVIEW_MAX_SKIPPED]],
    }


# ── Request-scope helpers ─────────────────────────────────────────────

def clean_doc_text(content: str, *, name: str = "attachment") -> str:
    """Sanitize an extracted text document (PDF text, .txt).

    Same posture as the grid parser: strip control characters (keep
    newline/tab), enforce the byte cap by refusal — never by silent
    truncation.
    """
    if content.startswith("﻿"):
        content = content[1:]
    if len(content.encode("utf-8")) > MAX_CONTENT_BYTES:
        raise AttachmentError(
            f"{name}: file too large (max {MAX_CONTENT_BYTES / 1_000_000:.1f} MB)."
        )
    cleaned = "".join(
        ch for ch in content if ch in ("\n", "\t") or ord(ch) >= 32
    ).strip()
    if not cleaned:
        raise AttachmentError(f"{name}: no readable text in the file.")
    return cleaned


def parse_image_data_url(content: str, *, name: str = "attachment") -> str:
    """Validate an image attachment's data-URL; returns it unchanged.

    Strict shape check (``data:<allowed mime>;base64,<valid b64>``) with
    the same refuse-never-truncate byte law — a malformed or oversized
    image is rejected with a human message, never silently mangled.
    """
    if len(content.encode("utf-8")) > MAX_CONTENT_BYTES:
        raise AttachmentError(
            f"{name}: image too large (max {MAX_CONTENT_BYTES / 1_000_000:.1f} MB)."
        )
    header, sep, b64 = content.partition(",")
    if not sep or not header.startswith("data:") or ";base64" not in header:
        raise AttachmentError(f"{name}: not a valid image attachment.")
    mime = header[5:].split(";", 1)[0].strip().lower()
    if mime not in IMAGE_MIMES:
        raise AttachmentError(
            f"{name}: unsupported image type '{mime}' "
            f"(use {', '.join(sorted(IMAGE_MIMES))})."
        )
    import base64 as _b64
    try:
        _b64.b64decode(b64, validate=True)
    except Exception:
        raise AttachmentError(f"{name}: the image data is corrupt.")
    return content


def iter_attachment_images(user_context: dict | None) -> list[tuple[str, str, bytes]]:
    """Decode the request's image attachments → [(name, mime, raw bytes)].

    Used by the model loops to attach the images to the request itself
    (vision input) — images never flow through tools.  Entries that fail
    to decode are skipped (they were validated at parse time; this is
    belt-and-braces).
    """
    import base64 as _b64
    out: list[tuple[str, str, bytes]] = []
    for name, dataurl in ((user_context or {}).get("_attachment_images") or {}).items():
        try:
            header, _, b64 = str(dataurl).partition(",")
            mime = header[5:].split(";", 1)[0].strip().lower()
            if mime in IMAGE_MIMES:
                out.append((name, mime, _b64.b64decode(b64)))
        except Exception:
            continue
    return out


def doc_excerpt(text: str, offset: int = 0) -> dict:
    """A bounded window of a text document for the MODEL.

    ``offset`` pages through long documents (the model asks for the next
    window instead of ever receiving the whole file at once).
    """
    offset = max(0, int(offset))
    window = text[offset:offset + DOC_EXCERPT_CHARS]
    return {
        "char_count": len(text),
        "offset": offset,
        "excerpt": window,
        "excerpt_truncated": offset + len(window) < len(text),
    }


def attachment_prompt_line(user_context: dict | None) -> str:
    """Profile-line hint that THIS message carries attachments.

    Injected next to the datetime anchor in the agent prompt builders so
    the model reaches for ``read_attachment`` instead of guessing at (or
    hallucinating) the file's contents.  Empty string when the request
    has no parsed attachments — callers append conditionally.
    """
    uc = user_context or {}
    grids = uc.get("_attachment_grids") or {}
    docs = uc.get("_attachment_docs") or {}
    images = uc.get("_attachment_images") or {}
    if not grids and not docs and not images:
        return ""
    shapes = [
        f"{name} ({len(g)} rows × {max((len(r) for r in g), default=0)} cols)"
        for name, g in grids.items()
    ] + [
        f"{name} (text document, {len(text):,} chars)"
        for name, text in docs.items()
    ] + [
        f"{name} (image)"
        for name in images
    ]
    line = (
        f"- Attached files on THIS message: {', '.join(shapes)}."
    )
    if grids or docs:
        line += (
            " Call read_attachment FIRST to inspect spreadsheet/document "
            "contents before answering about them or proposing any action."
        )
    if images:
        line += (
            " Images are provided with this request directly if your model "
            "supports vision — do not call read_attachment for them."
        )
    return line


async def parse_attachments_for_request(
    attachments: list, role: str | None, account_id: int | None,
) -> tuple[dict[str, list[list[str]]], dict[str, str], dict[str, str]]:
    """Validate + transiently parse a request's attachments.

    Returns ``(grids, docs, images)`` — lanes with different trust rules:

    * **grids** (``kind="sheet"``, the default): spreadsheet text that
      can feed IMPORTS.  Gated: parsed only for callers holding a
      permission of at least one registered ``ImportTarget`` — a role
      that could never execute an import gets no free parse.  ("Any
      write-tool permission" was considered and rejected: derived
      always-on flags like ``can_alerts_vehicle`` give every role SOME
      write tool, making that gate vacuous.)  Fail-closed when no
      targets are registered.
    * **docs** (``kind="text"``): extracted text documents (PDF/TXT) the
      model can only READ through bounded windows.  No import gate —
      reading a document is the same trust level as the user typing its
      contents, and every tool call stays behind the normal permission
      gate.  The ``kind`` field only picks the parser: a mislabeled
      attachment yields a failed parse or an inert text doc, never a
      privilege change.
    * **images** (``kind="image"``): device-compressed data-URLs, shown
      DIRECTLY to vision-capable models (never through tools).  Same
      read-only trust rules as text docs — no import gate.

    Raises ``AttachmentError`` with a user-facing message on any refusal.
    """
    grids: dict[str, list[list[str]]] = {}
    docs: dict[str, str] = {}
    images: dict[str, str] = {}
    if not attachments:
        return grids, docs, images
    if len(attachments) > MAX_ATTACHMENTS:
        raise AttachmentError(f"At most {MAX_ATTACHMENTS} attachments per message.")
    sheet_atts = [
        a for a in attachments
        if str(getattr(a, "kind", "sheet")) not in ("text", "image")
    ]
    if sheet_atts:
        targets = [t for t in list_import_targets() if t.permission]
        allowed = False
        if role and targets:
            try:
                from adapters.storage import Role
                from capabilities.permissions.roles import (
                    get_account_permissions, get_permissions,
                )
                r = Role(role)
                perms = (await get_account_permissions(r, int(account_id))
                         if account_id is not None else get_permissions(r))
                allowed = any(getattr(perms, t.permission, False) for t in targets)
            except (ValueError, KeyError, ImportError):
                allowed = False
        if not allowed:
            raise AttachmentError(
                "Your role can't run imports, so attachments aren't processed."
            )
    for att in attachments:
        name = str(getattr(att, "name", "") or "attachment")[:120]
        content = str(getattr(att, "content", "") or "")
        kind = str(getattr(att, "kind", "sheet"))
        if kind == "text":
            docs[name] = clean_doc_text(content, name=name)
        elif kind == "image":
            images[name] = parse_image_data_url(content, name=name)
        else:
            grids[name] = parse_csv_grid(content, name=name)
    return grids, docs, images
