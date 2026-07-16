"""AI import adapter for vehicle inventory — the FIRST ImportTarget.

The universal pipeline (capabilities/ai/attachments.py + propose_import)
handles parsing, mapping, preview, staging, and approval; this module
supplies only the domain pieces (docs/architecture/ai-import-assistant.md
§4 layering law):

  * the ``inventory`` ImportTarget — field vocabulary + ``build_rows``
    (vehicle resolution per §5.5 + row validation → skip report),
  * the ``import_inventory_items`` write action (propose is one
    ``propose_import`` call; the executor writes the staged rows in ONE
    transaction, re-resolving vehicles at execute time).

Resolution rules (fable-advisor §5.5): minimal normalization only
(trim / collapse spaces / casefold) — NEVER strip leading zeros
("022" ≠ "22"); a company-code suffix ("103 OSY") is parsed only
against the registry's enumerated codes; any match must be exactly ONE
vehicle — ambiguity is a skip, never a guess.  Skipped rows are
reported, never a whole-import failure.
"""

from __future__ import annotations

import logging

from adapters.storage.vehicle_inventory import (
    INVENTORY_CATEGORIES,
    INVENTORY_STATUSES,
)
from capabilities.ai.attachments import (
    MAX_RECORDS,
    ImportTarget,
    register_import_target,
)
from capabilities.ai.tools.attachments_tool import propose_import
from capabilities.ai.tools.registry import (
    register_action_executor,
    register_tool,
)

logger = logging.getLogger("bot.ai")


def _norm(s: object) -> str:
    """Trim + collapse internal whitespace + casefold. Nothing more —
    leading zeros are load-bearing ("022" and "22" are different trucks)."""
    return " ".join(str(s or "").split()).casefold()


def _display(v) -> str:
    return f"{v.unit_number} ({v.company_code})" if v.company_code else v.unit_number


def _resolve_vehicles(
    raw_units: set[str], vehicles: list,
) -> tuple[dict[str, tuple[int, str]], dict[str, str]]:
    """Resolve sheet unit strings against the registry.

    Returns ``(resolved, failed)`` keyed by the normalized raw string:
    ``resolved[key] = (vehicle_id, display)``, ``failed[key] = reason``.
    Precedence: exact unit_number match first (a sheet unit may
    legitimately contain spaces), then ``<unit> <company-code>`` suffix
    parse.  Every path demands exactly ONE candidate.
    """
    by_unit: dict[str, list] = {}
    by_pair: dict[tuple[str, str], list] = {}
    codes: set[str] = set()
    for v in vehicles:
        u = _norm(v.unit_number)
        by_unit.setdefault(u, []).append(v)
        c = _norm(v.company_code)
        if c:
            codes.add(c)
            by_pair.setdefault((u, c), []).append(v)

    resolved: dict[str, tuple[int, str]] = {}
    failed: dict[str, str] = {}
    for raw in raw_units:
        key = _norm(raw)
        if not key:
            failed[key] = "blank vehicle"
            continue
        cands = by_unit.get(key, [])
        if len(cands) == 1:
            resolved[key] = (cands[0].id, _display(cands[0]))
            continue
        if len(cands) > 1:
            failed[key] = (
                f"'{raw}' matches {len(cands)} vehicles (different "
                "companies) — add the company code"
            )
            continue
        unit, _, code = key.rpartition(" ")
        if unit and code in codes:
            pair = by_pair.get((unit, code), [])
            if len(pair) == 1:
                resolved[key] = (pair[0].id, _display(pair[0]))
                continue
        failed[key] = f"no vehicle '{raw}' in the registry"
    return resolved, failed


def _clean_category(raw: object) -> str:
    """Unknown category soft-defaults to 'other' — taxonomy, not data."""
    c = _norm(raw).replace(" ", "_")
    return c if c in INVENTORY_CATEGORIES else "other"


def _clean_status(raw: object) -> str | None:
    """Status must be an exact vocabulary value (the mapping's value_map
    is where sheet phrases get translated).  ``None`` = skip the row —
    defaulting a strange cell to 'installed' would CLAIM an item is
    present and healthy when the sheet said something else."""
    s = _norm(raw).replace(" ", "_")
    return s if s in INVENTORY_STATUSES else None


async def _build_rows(
    records: list[dict], account_id: int | None, user_context, db,
) -> tuple[list[dict], list[str]]:
    """Validate mapped records into stageable inventory rows + skip report."""
    if db is None or account_id is None:
        return [], ["Inventory data is not available in this context."]
    vehicles = await db.list_vehicles(int(account_id))
    resolved, failed = _resolve_vehicles(
        {str(r.get("vehicle", "")) for r in records}, vehicles,
    )
    rows: list[dict] = []
    skipped: list[str] = []
    for rec in records:
        where = f"row {rec.get('_source_row', '?')}"
        key = _norm(rec.get("vehicle", ""))
        if key not in resolved:
            skipped.append(f"{where}: {failed.get(key, 'unresolved vehicle')}")
            continue
        vid, display = resolved[key]
        item = " ".join(str(rec.get("item", "")).split())
        if not item:
            skipped.append(f"{where}: no item name")
            continue
        status = _clean_status(rec.get("status", ""))
        if status is None:
            skipped.append(
                f"{where}: status '{rec.get('status', '')}' is not one of "
                f"{', '.join(INVENTORY_STATUSES)} — map it in value_map"
            )
            continue
        rows.append({
            "vehicle": display,                  # the RESOLVED registry vehicle
            "item": item[:120],
            "category": _clean_category(rec.get("category", "")),
            "status": status,
            "identifier": str(rec.get("identifier", "") or "")[:120],
            "note": str(rec.get("note", "") or "")[:1000],
            "_vehicle_id": vid,                  # server-side; hidden in preview
            "_source_row": rec.get("_source_row"),
        })
    return rows, skipped


async def _noop_executor(rows, account_id, user_context, db):  # pragma: no cover
    raise NotImplementedError(
        "inventory imports execute via the import_inventory_items action"
    )


register_import_target(ImportTarget(
    name="inventory",
    description="inventory items",   # doubles as the summary noun
    fields={
        "vehicle": (
            "Unit number as written in the sheet; may carry a company-code "
            "suffix like '103 OSY'. Resolved against the vehicle registry."
        ),
        "item": "Item name (required), e.g. 'Fire extinguisher', 'Dashcam'.",
        "category": (
            "One of: " + ", ".join(INVENTORY_CATEGORIES)
            + ". Anything else becomes 'other'."
        ),
        "status": (
            "One of: " + ", ".join(INVENTORY_STATUSES)
            + ". Translate sheet phrasing via the mapping's value_map — "
            "rows with an unmapped status are skipped."
        ),
        "identifier": "Serial / card number / transponder id (optional).",
        "note": "Free-text note carried onto the item (optional).",
    },
    build_rows=_build_rows,
    executor=_noop_executor,   # real execution = the registered action below
    permission="can_manage_vehicles",
))


# ── The write action ─────────────────────────────────────────────────

@register_tool({
    "name": "import_inventory_items",
    "description": (
        "Import onboard inventory items (cameras, fuel cards, toll "
        "transponders, ELDs, tablets, safety equipment, …) into vehicle "
        "inventory from a spreadsheet attached to THIS message. Call "
        "read_attachment first to see the layout, then pass a mapping from "
        "column INDICES to inventory fields. This does NOT write directly — "
        "it shows a preview of every row (plus what was skipped and why) "
        "and asks the user to approve first."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "attachment": {
                "type": "string",
                "description": (
                    "File name of the attachment to import (omit when only "
                    "one file is attached)."
                ),
            },
            "mapping": {
                "type": "object",
                "description": (
                    "How to read the grid — every column by 0-based INDEX. "
                    "id_columns identify the row (field 'vehicle'); each "
                    "melt_column becomes one record per row with its "
                    "constants (e.g. item + category) plus the cell value "
                    "as value_field (usually 'status'), translated through "
                    "value_map; notes_column rides along as 'note'."
                ),
                "properties": {
                    "has_header": {"type": "boolean", "description": "First row is a header row (default true)."},
                    "id_columns": {
                        "type": "array",
                        "items": {
                            "type": "object",
                            "properties": {
                                "index": {"type": "integer", "description": "0-based column index."},
                                "field": {"type": "string", "description": "Target field, e.g. 'vehicle'."},
                            },
                        },
                    },
                    "melt_columns": {
                        "type": "array",
                        "items": {
                            "type": "object",
                            "properties": {
                                "index": {"type": "integer", "description": "0-based column index."},
                                "constants": {"type": "object", "description": "Fixed fields for every record from this column, e.g. {\"item\": \"Fire extinguisher\", \"category\": \"other\"}."},
                                "value_field": {"type": "string", "description": "Field the cell value lands in, usually 'status'."},
                                "value_map": {"type": "object", "description": "Cell text → vocabulary value, e.g. {\"Good\": \"installed\", \"Not checked\": \"needs_check\"}."},
                                "skip_values": {"type": "array", "items": {"type": "string"}, "description": "Cell values that produce no record (besides empty)."},
                            },
                        },
                    },
                    "notes_column": {
                        "type": "object",
                        "properties": {
                            "index": {"type": "integer"},
                            "field": {"type": "string", "description": "Defaults to 'note'."},
                        },
                    },
                },
            },
        },
        "required": ["mapping"],
    },
    "writes": True,
    "risk": "low",
    # Bulk import across arbitrary vehicles — no single vehicle_name to
    # gate on, so scoped (company/vehicle-restricted) callers are blocked
    # outright (⇒ ACCOUNT_WIDE_TOOLS, NOT SCOPE_AWARE_TOOLS; the guard
    # test enforces the pairing).
    "scope": "account_unscoped",
    # Request grids are injected server-side by execute_tool.
    "uses_attachments": True,
})
async def import_inventory_items(tool_args, samsara_client,
                                 account_id=None, db=None):
    return await propose_import(
        tool="import_inventory_items",
        target_name="inventory",
        tool_args=tool_args,
        account_id=account_id,
        db=db,
        consequence=(
            "Items are added to each truck's inventory with an audit "
            "trail — you can edit or remove them anytime."
        ),
    )


@register_action_executor("import_inventory_items")
async def _execute_import_inventory(payload, account_id, user_context, db):
    """Write the STAGED rows — runs only post-approval, in ONE transaction.

    Vehicles are re-resolved at execute time (the registry may have
    changed since propose): rows whose vehicle vanished are skipped and
    reported, never a whole-import failure (§5.5).  Each insert also
    writes the accountability event with the approving user as actor and
    the truck's currently-assigned driver snapshotted — identical to the
    manual add path.
    """
    from fastapi import HTTPException

    rows = (user_context or {}).get("_staged_rows")
    if not isinstance(rows, list) or not rows:
        raise HTTPException(
            status_code=409,
            detail="This proposal has no staged rows — ask again with the file attached.",
        )
    if len(rows) > MAX_RECORDS:
        raise HTTPException(status_code=409, detail="Staged import exceeds the record cap.")

    actor = int((user_context or {}).get("user_id") or 0) or None
    vehicles = await db.list_vehicles(int(account_id))
    active = {v.id: v for v in vehicles}
    driver_by_vid: dict[int, int | None] = {}

    imported = 0
    skipped: list[str] = []
    async with db.transaction():
        for r in rows:
            try:
                vid = int(r.get("_vehicle_id"))
            except (TypeError, ValueError):
                vid = -1
            v = active.get(vid)
            where = str(r.get("vehicle") or f"row {r.get('_source_row', '?')}")
            if v is None:
                skipped.append(f"{where}: vehicle is no longer in the registry")
                continue
            # Re-validate vocabulary — staged rows are server-written, but
            # a stale/legacy proposal must not smuggle values past the enum.
            status = _clean_status(r.get("status", ""))
            item = " ".join(str(r.get("item", "")).split())
            if status is None or not item:
                skipped.append(f"{where}: invalid staged row")
                continue
            if vid not in driver_by_vid:
                driver_by_vid[vid] = await db.get_assigned_driver_for_truck(
                    int(account_id), v.unit_number,
                )
            await db.add_inventory_item(
                int(account_id), vid,
                category=_clean_category(r.get("category", "")),
                label=item,
                identifier=str(r.get("identifier", "") or ""),
                notes=str(r.get("note", "") or ""),
                status=status,
                actor_user_id=actor,
                driver_user_id=driver_by_vid[vid],
            )
            imported += 1

    msg = f"Imported {imported} inventory items"
    if skipped:
        msg += f" ({len(skipped)} skipped)"
    return {
        "imported": imported,
        "skipped": skipped[:20],
        "skipped_count": len(skipped),
        "target_type": "vehicle_inventory",
        "target_id": "",
        "message": msg + ".",
    }
