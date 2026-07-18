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
    normalize_inventory_category,
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
    register_undo_executor,
)

logger = logging.getLogger("bot.ai")


def _norm(s: object) -> str:
    """Trim + collapse internal whitespace + casefold. Nothing more —
    leading zeros are load-bearing ("022" and "22" are different trucks)."""
    return " ".join(str(s or "").split()).casefold()


def _build_lookup(vehicles: list) -> tuple[dict, dict, set]:
    """Registry lookup maps: by unit, by (unit, company), known codes."""
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
    return by_unit, by_pair, codes


def _resolve_one(
    unit_raw: object, comp_raw: object,
    by_unit: dict, by_pair: dict, codes: set,
):
    """One record's vehicle → ``(Vehicle, None)`` or ``(None, reason)``.

    A mapped COMPANY column wins outright: (unit, company) is unique in
    the registry, so a sheet that carries both disambiguates units like
    '22' that exist in several companies.  Without it: exact unit match
    first (a sheet unit may legitimately contain spaces), then the
    '<unit> <code>' suffix parse.  Every path demands exactly ONE
    candidate — ambiguity is a reported skip, never a guess.
    """
    key = _norm(unit_raw)
    if not key:
        return None, "blank vehicle"
    comp = _norm(comp_raw)
    if comp:
        pair = by_pair.get((key, comp), [])
        if len(pair) == 1:
            return pair[0], None
        return None, f"no vehicle '{unit_raw}' in company '{comp_raw}'"
    cands = by_unit.get(key, [])
    if len(cands) == 1:
        return cands[0], None
    if len(cands) > 1:
        return None, (
            f"'{unit_raw}' matches {len(cands)} vehicles (different "
            "companies) — map the sheet's company column too"
        )
    unit, _, code = key.rpartition(" ")
    if unit and code in codes:
        pair = by_pair.get((unit, code), [])
        if len(pair) == 1:
            return pair[0], None
    return None, f"no vehicle '{unit_raw}' in the registry"


def _clean_category(raw: object) -> str:
    """Category is an OPEN vocabulary (owner decision): a value outside
    the built-ins becomes a normalized CUSTOM category ('Safety
    Equipment' -> safety_equipment), never a silent 'other'.  Empty ->
    'other'."""
    return normalize_inventory_category(raw)


def _clean_status(raw: object) -> str | None:
    """Status must be an exact vocabulary value (the mapping's value_map
    is where sheet phrases get translated).  ``None`` = skip the row —
    defaulting a strange cell to 'installed' would CLAIM an item is
    present and healthy when the sheet said something else."""
    s = _norm(raw).replace(" ", "_")
    return s if s in INVENTORY_STATUSES else None


async def _build_rows(
    records: list[dict], account_id: int | None, user_context, db,
) -> tuple[list[dict], list[str], list[str]]:
    """Validate mapped records into stageable inventory rows.

    Returns ``(rows, skipped, notices)``.  Notices are non-fatal
    ADJUSTMENTS the user must see: above all NEW categories — category
    is an open vocabulary, so an unknown value creates a custom category
    (normalized snake_case) rather than silently becoming 'other' (the
    old coercion once let the model claim a mapping change had applied
    when the adapter had quietly rejected it).  STATUS stays fixed.
    """
    if db is None or account_id is None:
        return [], ["Inventory data is not available in this context."], []
    vehicles = await db.list_vehicles(int(account_id))
    by_unit, by_pair, codes = _build_lookup(vehicles)
    rows: list[dict] = []
    skipped: list[str] = []
    new_categories: dict[str, int] = {}
    for rec in records:
        where = f"row {rec.get('_source_row', '?')}"
        v, reason = _resolve_one(
            rec.get("vehicle", ""), rec.get("company", ""),
            by_unit, by_pair, codes,
        )
        if v is None:
            skipped.append(f"{where}: {reason}")
            continue
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
        category = _clean_category(rec.get("category", ""))
        if category not in INVENTORY_CATEGORIES:
            new_categories[category] = new_categories.get(category, 0) + 1
        rows.append({
            # The RESOLVED registry vehicle, split exactly like the
            # Inventory page's own columns (Vehicle | Company) — the
            # preview must mirror the destination's shape.
            "vehicle": v.unit_number,
            "company": v.company_code,
            "item": item[:120],
            "category": category,
            "status": status,
            "identifier": str(rec.get("identifier", "") or "")[:120],
            "note": str(rec.get("note", "") or "")[:1000],
            "_vehicle_id": v.id,                 # server-side; hidden in preview
            "_source_row": rec.get("_source_row"),
        })
    notices = [
        (
            f"new category '{cat}' will be created ({count} row(s)) — "
            "it joins the built-ins "
            f"({', '.join(INVENTORY_CATEGORIES)}) in the category picker"
        )
        for cat, count in new_categories.items()
    ]
    return rows, skipped, notices


async def _edit_row(row: dict, changes: dict, account_id: int, db):
    """Per-cell corrections on a staged import row (the editable
    preview).  Same rules as build_rows: STATUS is fixed vocabulary and
    an invalid value is refused with the reason; CATEGORY is open and
    normalizes; vehicle/company re-resolve against the registry
    (exactly one or refused).  Returns (new_row, None) or (None, why).
    """
    new_row = dict(row)
    for field_name, raw in changes.items():
        value = str(raw or "").strip()
        if field_name == "status":
            status = _clean_status(value)
            if status is None:
                return None, (
                    f"status must be one of: {', '.join(INVENTORY_STATUSES)}"
                )
            new_row["status"] = status
        elif field_name == "category":
            new_row["category"] = _clean_category(value)
        elif field_name == "item":
            item = " ".join(value.split())
            if not item:
                return None, "item can't be empty"
            new_row["item"] = item[:120]
        elif field_name == "identifier":
            new_row["identifier"] = value[:120]
        elif field_name == "note":
            new_row["note"] = value[:1000]
        elif field_name in ("vehicle", "company"):
            unit = value if field_name == "vehicle" else str(new_row.get("vehicle", ""))
            comp = value if field_name == "company" else str(new_row.get("company", ""))
            vehicles = await db.list_vehicles(int(account_id))
            by_unit, by_pair, codes = _build_lookup(vehicles)
            v, reason = _resolve_one(unit, comp, by_unit, by_pair, codes)
            if v is None:
                return None, reason or "vehicle not found"
            new_row["vehicle"] = v.unit_number
            new_row["company"] = v.company_code
            new_row["_vehicle_id"] = v.id
        else:
            return None, f"'{field_name}' can't be edited"
    return new_row, None


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
        "company": (
            "Company code (e.g. OSY, G1). Map the sheet's company column "
            "here when it has one — it disambiguates unit numbers that "
            "exist in several companies; otherwise it's filled from the "
            "registry after resolution."
        ),
        "item": "Item name (required), e.g. 'Fire extinguisher', 'Dashcam'.",
        "category": (
            "OPEN vocabulary. Built-ins: " + ", ".join(INVENTORY_CATEGORIES)
            + "; any other value creates a NEW custom category "
            "(normalized like safety_equipment) — tell the user when "
            "that happens."
        ),
        "status": (
            "FIXED vocabulary (drives alert logic, cannot take new "
            "values): " + ", ".join(INVENTORY_STATUSES)
            + ". Translate sheet phrasing via the mapping's value_map — "
            "rows with an unmapped status are skipped. If the user asks "
            "for a status outside this list, explain the column is "
            "fixed instead of attempting it."
        ),
        "identifier": "Serial / card number / transponder id (optional).",
        "note": "Free-text note carried onto the item (optional).",
    },
    build_rows=_build_rows,
    executor=_noop_executor,   # real execution = the registered action below
    permission="can_manage_vehicles",
    edit_row=_edit_row,
    edit_options={"status": list(INVENTORY_STATUSES)},
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
                                "field": {"type": "string", "description": "Target field: 'vehicle', and 'company' when the sheet has its own company-code column (map BOTH — it disambiguates units that exist in several companies)."},
                            },
                        },
                    },
                    "melt_columns": {
                        "type": "array",
                        "items": {
                            "type": "object",
                            "properties": {
                                "index": {"type": "integer", "description": "0-based column index."},
                                "constants": {"type": "object", "description": (
                                    "Fixed fields for every record from this column, e.g. "
                                    "{\"item\": \"Fire extinguisher\", \"category\": \"other\"}. "
                                    "CATEGORY is open (built-ins: " + ", ".join(INVENTORY_CATEGORIES)
                                    + "; other values create a new custom category). "
                                    "STATUS is FIXED to: " + ", ".join(INVENTORY_STATUSES)
                                    + " — new status values are impossible; explain that instead of trying."
                                )},
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

    def _vid_of(r: dict) -> int:
        try:
            return int(r.get("_vehicle_id"))  # type: ignore[arg-type]
        except (TypeError, ValueError):
            return -1

    # Prefetch the per-vehicle driver snapshots BEFORE opening the
    # transaction — reads go through their own pool connections, and
    # holding the transaction's pinned connection while acquiring more
    # is avoidable pool pressure on a large import.
    driver_by_vid: dict[int, int | None] = {}
    for vid in {_vid_of(r) for r in rows}:
        v = active.get(vid)
        if v is not None:
            driver_by_vid[vid] = await db.get_assigned_driver_for_truck(
                int(account_id), v.unit_number,
            )

    imported = 0
    item_ids: list[int] = []
    skipped: list[str] = []
    async with db.transaction():
        for r in rows:
            vid = _vid_of(r)
            v = active.get(vid)
            _veh = str(r.get("vehicle") or "")
            _comp = str(r.get("company") or "")
            where = (f"{_veh} ({_comp})" if _veh and _comp else _veh) \
                or f"row {r.get('_source_row', '?')}"
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
            item_id = await db.add_inventory_item(
                int(account_id), vid,
                category=_clean_category(r.get("category", "")),
                label=item,
                identifier=str(r.get("identifier", "") or ""),
                notes=str(r.get("note", "") or ""),
                status=status,
                actor_user_id=actor,
                driver_user_id=driver_by_vid[vid],
            )
            item_ids.append(int(item_id))
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
        # The undo manifest — exactly what this execution created.
        # Underscore-prefixed = server-side: stripped from every client
        # response, stored (un-truncated, encrypted) with the proposal.
        "_item_ids": item_ids,
    }


@register_undo_executor("import_inventory_items")
async def _undo_import_inventory(result, payload, account_id, user_context, db):
    """Reverse an executed import — remove EXACTLY the items it created.

    Copilot-style change-set undo: soft (is_active=0 + a 'removed' event
    noting the AI-import undo — the trail survives, and even the undo is
    recoverable), account-scoped in SQL, and tolerant of items someone
    already removed manually (skip + count, never a failure).
    """
    from fastapi import HTTPException

    ids = result.get("_item_ids") if isinstance(result, dict) else None
    if not isinstance(ids, list) or not ids:
        raise HTTPException(
            status_code=409,
            detail="This import predates undo support — remove the items "
                   "from the Inventory page instead.",
        )
    actor = int((user_context or {}).get("user_id") or 0) or None
    undone = 0
    already_gone = 0
    async with db.transaction():
        for raw_id in ids[:MAX_RECORDS]:
            try:
                iid = int(raw_id)
            except (TypeError, ValueError):
                continue
            ok = await db.remove_inventory_item(
                int(account_id), iid,
                note="AI import undone",
                actor_user_id=actor,
            )
            if ok:
                undone += 1
            else:
                already_gone += 1
    msg = f"Removed {undone} imported items"
    if already_gone:
        msg += f" ({already_gone} were already removed)"
    return {
        "undone": undone,
        "already_gone": already_gone,
        "target_type": "vehicle_inventory",
        "message": msg + ".",
    }
