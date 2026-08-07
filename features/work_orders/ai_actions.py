"""Work-order AI write action — the assistant's "Door B".

The user attaches an invoice / issue photos in chat and asks for a work
order; the agent (which already SEES image attachments) fills the
structured args and this tool PROPOSES.  Nothing mutates in the chat
loop — the propose→approve→execute spine (capabilities/ai/actions.py)
re-checks the real JWT role at approve time and the executor runs only
then.

Owner contract (2026-07-24, see memory project-ai-invoice-to-wo):
  * AI drafts only — the created WO is ALWAYS status='open'; the human
    finishes it in the form.  The tool description tells the agent to
    ASK for a missing unit instead of guessing.
  * Access = can_work_orders_all (TOOL_PERMISSIONS), scope =
    vehicle_param (fail-closed gate; VEHICLE_SPECIFIC_TOOLS).
  * The chat files stay transient server-side: ``source_files`` are
    NAMES the client uses post-approve to upload its device-held files
    to the new WO — the approve response's target_id is the only id
    the client may upload to.

Trust posture mirrors extraction.py: every model-supplied value is
re-clamped here AND in the executor (payloads outlive code revisions),
money totals are recomputed server-side, and a mid-way line failure
triggers compensating cleanup (delete the header) so a failed action
never strands a half-built WO without an undo handle.
"""

from __future__ import annotations

import logging

from capabilities.ai.tools.registry import (
    register_action_executor,
    register_tool,
    register_undo_executor,
    tool_error,
    tool_propose,
)
from features.work_orders.task_links import (
    describe_links, invoice_haystack, is_open_task, suggest_task_links,
)

logger = logging.getLogger("bot.work_orders")

_MAX_LINES = 60
_MAX_SOURCE_FILES = 5
_MAX_LINK_TASKS = 10
_MAX_MONEY = 1_000_000.0
_MAX_QTY = 10_000.0
_PRIORITIES = ("", "scheduled", "non_scheduled", "emergency")


def _f(v, cap: float = _MAX_MONEY) -> float:
    """Non-negative, capped, 2-dp float; garbage → 0 (never raises)."""
    try:
        n = float(v)
    except (TypeError, ValueError):
        return 0.0
    if n != n or n in (float("inf"), float("-inf")):
        return 0.0
    return round(min(max(n, 0.0), cap), 2)


def _s(v, cap: int) -> str:
    if not isinstance(v, (str, int, float)):
        return ""
    return str(v).strip()[:cap]


def _clean_lines(raw_parts, raw_labor) -> tuple[list[dict], list[dict]]:
    """Model line items → clamped part/labor dicts (shared by propose
    and execute so a stored payload gets the same rigor on replay)."""
    parts: list[dict] = []
    for p in (raw_parts or [])[:_MAX_LINES]:
        if not isinstance(p, dict):
            continue
        name = _s(p.get("name") or p.get("part_name"), 200)
        if not name:
            continue
        qty = _f(p.get("quantity"), _MAX_QTY) or 1.0
        unit = _f(p.get("unit_cost") or p.get("unit_price"))
        total = _f(p.get("total") or p.get("total_cost"))
        if not total and unit:
            total = round(qty * unit, 2)
        parts.append({
            "part_name": name,
            "part_number": _s(p.get("part_number"), 60),
            "quantity": qty, "unit_cost": unit, "total_cost": total,
        })
    labor: list[dict] = []
    for l in (raw_labor or [])[:_MAX_LINES]:
        if not isinstance(l, dict):
            continue
        desc = _s(l.get("description"), 200)
        if not desc:
            continue
        hours = _f(l.get("hours"), 10_000.0)
        rate = _f(l.get("rate"), 10_000.0)
        total = _f(l.get("total") or l.get("total_cost"))
        if not total and hours and rate:
            total = round(hours * rate, 2)
        labor.append({
            "description": desc, "hours": hours, "rate": rate,
            "total_cost": total,
        })
    return parts[:_MAX_LINES], labor[: max(0, _MAX_LINES - len(parts))]


def _normalize(args: dict) -> dict | None:
    """Args/payload → the one clamped shape both stages agree on.
    Returns None when the required vehicle is missing."""
    vehicle = _s(args.get("vehicle_name"), 80)
    if not vehicle:
        return None
    parts, labor = _clean_lines(args.get("parts"), args.get("labor"))
    priority = _s(args.get("repair_priority"), 20).lower()
    if priority not in _PRIORITIES:
        priority = ""
    files = [
        _s(n, 120) for n in (args.get("source_files") or [])[:_MAX_SOURCE_FILES]
        if _s(n, 120)
    ]
    link_ids: list[int] = []
    for raw in (args.get("link_task_ids") or [])[:_MAX_LINK_TASKS]:
        try:
            tid = int(raw)
        except (TypeError, ValueError):
            continue
        if tid > 0 and tid not in link_ids:
            link_ids.append(tid)
    service_task = _s(args.get("service_task"), 200)
    if service_task:
        for line in (*parts, *labor):
            line["service_task"] = service_task
    return {
        "vehicle_name": vehicle,
        "service_task": service_task,
        "vehicle_type": (
            _s(args.get("vehicle_type"), 10).lower()
            if _s(args.get("vehicle_type"), 10).lower() in ("truck", "trailer")
            else ""
        ),
        "vendor_name": _s(args.get("vendor_name"), 200),
        "vendor_phone": _s(args.get("vendor_phone"), 40),
        "vendor_email": _s(args.get("vendor_email"), 120),
        "vendor_address": _s(args.get("vendor_address"), 300),
        "invoice_number": _s(args.get("invoice_number"), 60),
        "service_date": _s(args.get("service_date"), 10),
        "odometer": _f(args.get("odometer"), 5_000_000.0) or None,
        "repair_priority": priority,
        "fee": _f(args.get("fee")),
        "tax": _f(args.get("tax")),
        "notes": _s(args.get("notes"), 1000),
        "parts": parts,
        "labor": labor,
        "source_files": files,
        # Maintenance tasks this WO closes out.  Filled by the propose
        # step (server-side matching) and/or named by the user; every
        # id is RE-validated against the account + vehicle + still-open
        # state at execute time — a payload id is never trusted.
        "link_task_ids": link_ids,
    }


@register_tool({
    "name": "create_work_order",
    "description": (
        "Propose creating a work order (a shop visit / repair invoice "
        "record) for a vehicle. Use when the user asks to log or create "
        "a work order — especially from an attached invoice or repair "
        "photos: read the attachment(s) first, split parts vs labor "
        "line items, and set repair_priority (emergency for a "
        "breakdown/roadside damage, scheduled for planned service). "
        "This does NOT create it directly — the user approves first, "
        "and the result is an OPEN work order draft they finish in the "
        "Work Orders page. If the documents don't clearly identify the "
        "vehicle unit, ASK the user which vehicle it is — never guess. "
        "List the names of the attached files in source_files so they "
        "can be archived onto the work order after approval."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "vehicle_name": {"type": "string", "description": "Vehicle unit name/number (e.g. '234'). Required — ask the user if the documents don't identify it."},
            "vehicle_type": {"type": "string", "description": "Optional: 'truck' or 'trailer'."},
            "vendor_name": {"type": "string", "description": "Shop / vendor name from the invoice."},
            "vendor_phone": {"type": "string"},
            "vendor_email": {"type": "string"},
            "vendor_address": {"type": "string"},
            "invoice_number": {"type": "string"},
            "service_date": {"type": "string", "description": "YYYY-MM-DD."},
            "odometer": {"type": "number", "description": "Odometer (miles) printed on the invoice, if any."},
            "repair_priority": {"type": "string", "description": "One of: scheduled, non_scheduled, emergency. Omit when unsure."},
            "fee": {"type": "number", "description": "Shop/environmental/misc fees that are not parts or labor lines."},
            "tax": {"type": "number", "description": "Sales tax."},
            "notes": {"type": "string", "description": "Short context — e.g. what the issue photos show."},
            "parts": {
                "type": "array",
                "description": "Part/material line items.",
                "items": {"type": "object", "properties": {
                    "name": {"type": "string"},
                    "part_number": {"type": "string"},
                    "quantity": {"type": "number"},
                    "unit_cost": {"type": "number"},
                    "total": {"type": "number"},
                }, "required": ["name"]},
            },
            "labor": {
                "type": "array",
                "description": "Labor/diagnostic line items.",
                "items": {"type": "object", "properties": {
                    "description": {"type": "string"},
                    "hours": {"type": "number"},
                    "rate": {"type": "number"},
                    "total": {"type": "number"},
                }, "required": ["description"]},
            },
            "source_files": {
                "type": "array", "items": {"type": "string"},
                "description": "Names of the files attached to THIS message the work order is based on.",
            },
            "service_task": {
                "type": "string",
                "description": (
                    "The kind of work this visit covers (e.g. 'Brake "
                    "Service'). Groups the line items and, when the "
                    "invoice itself has no itemization, pulls the "
                    "account's usual parts for that task."
                ),
            },
            "link_task_ids": {
                "type": "array", "items": {"type": "number"},
                "description": (
                    "Optional: ids of open maintenance tasks this shop "
                    "visit covers — only when the user names them. Matching "
                    "tasks are suggested automatically otherwise."
                ),
            },
        },
        "required": ["vehicle_name"],
    },
    "writes": True,
    "risk": "low",
    # Required vehicle_name ⇒ the fail-closed vehicle gate applies (a
    # scoped caller may only target a vehicle they can access).
    "scope": "vehicle_param",
})
async def create_work_order_action(tool_args, samsara_client,
                                   account_id=None, db=None):
    norm = _normalize(tool_args or {})
    if norm is None:
        return tool_error(
            "A vehicle is required to create a work order — ask the user "
            "which unit this is for.",
        )
    # Service-task defaults: when a task is named but the documents
    # carried no itemization, start from the parts that task usually
    # needs.  Fill-empty-only — real invoice lines always win — and
    # SAID OUT LOUD in the summary, because the user would be
    # approving parts that came from their catalog, not their invoice.
    defaults_note = ""
    if db is not None and account_id is not None and norm["service_task"] \
            and not norm["parts"] and not norm["labor"]:
        try:
            defaults = await db.service_task_defaults(
                account_id, norm["service_task"],
            )
        except Exception:      # pragma: no cover — defaults are a bonus
            defaults = None
        if defaults:
            for lp in defaults.get("parts", []):
                norm["parts"].append({
                    "part_name": lp["part_name"],
                    "part_number": lp.get("part_number") or "",
                    "quantity": float(lp.get("quantity") or 1),
                    "unit_cost": 0.0, "total_cost": 0.0,
                    "service_task": norm["service_task"],
                })
            hours = float(defaults.get("expected_labor_hours") or 0)
            if hours > 0:
                norm["labor"].append({
                    "description": defaults["name"], "hours": hours,
                    "rate": 0.0, "total_cost": 0.0,
                    "service_task": norm["service_task"],
                })
            if norm["parts"] or norm["labor"]:
                defaults_note = (
                    f" These are the usual lines for "
                    f"\u201c{defaults['name']}\u201d at $0 \u2014 the invoice "
                    f"had no itemization, so set the amounts."
                )

    lines_total = round(
        sum(p["total_cost"] for p in norm["parts"])
        + sum(l["total_cost"] for l in norm["labor"]), 2,
    )
    grand = round(lines_total + norm["fee"] + norm["tax"], 2)
    bits = [f"{len(norm['parts'])} parts", f"{len(norm['labor'])} labor lines"]
    if norm["vendor_name"]:
        bits.insert(0, norm["vendor_name"])
    summary = (
        f"Create work order for {norm['vehicle_name']}: "
        f"{', '.join(bits)}, total ${grand:,.2f}."
    )

    # Price sanity: does any line cost more than this account usually
    # pays for that part?  Answered from their OWN purchase history, so
    # it works without the three-fleet threshold market ranges need.
    # Named in the summary — the user is approving the amounts, and
    # this is the moment they could still question the shop.
    price_note = ""
    if db is not None and account_id is not None and norm["parts"]:
        # The assistant carries its scope as VEHICLE names (the
        # write-tool gate's axis), so isolation rides that: a caller
        # restricted to one company's trucks can't be shown a band
        # computed from another company's invoices.  ``None`` here
        # means genuinely unrestricted; an empty scope fails closed
        # inside part_price_context.
        _scope = tool_args.get("_scope_vehicles") if tool_args else None
        try:
            ctx = await db.part_price_context(
                account_id, [p["part_name"] for p in norm["parts"]],
                vehicle_names=_scope,
            )
        except Exception:      # pragma: no cover — a bonus, never a blocker
            ctx = {}
        if ctx:
            from adapters.storage.parts_catalog import part_name_key
            over = [
                p for p in norm["parts"]
                if (c := ctx.get(part_name_key(p["part_name"])))
                and p["unit_cost"] > 0 and p["unit_cost"] > c["high"]
            ]
            if over:
                n = len(over)
                price_note = (
                    f" Heads up: {n} part{'s' if n > 1 else ''} priced above "
                    f"what you usually pay ({', '.join(p['part_name'] for p in over[:3])}"
                    f"{'\u2026' if n > 3 else ''})."
                )

    # Maintenance auto-link: does this invoice cover work the vehicle
    # already has an open task for?  Matched server-side (deterministic,
    # no hallucinated ids) and NAMED IN THE SUMMARY — the user approves
    # the links together with the work order, never silently.
    described: list[dict] = []
    if db is not None and account_id is not None:
        open_tasks: list[dict] = []
        suggestions: list[dict] = []
        try:
            open_tasks = await db.get_maintenance_tasks(
                account_id, vehicle_name=norm["vehicle_name"],
            )
            hay = invoice_haystack(
                norm["parts"], norm["labor"], norm["notes"],
            )
            suggestions = suggest_task_links(open_tasks, hay)
        except Exception:      # pragma: no cover — suggestions are a bonus
            logger.exception("Maintenance link suggestion failed")
        # Union with any ids named explicitly ("link task 12").
        merged = list(norm["link_task_ids"])
        for s in suggestions:
            if s["id"] not in merged and len(merged) < _MAX_LINK_TASKS:
                merged.append(s["id"])
        norm["link_task_ids"] = merged

        # EVERY id that could get linked is named in the summary — an
        # explicitly-passed id must not ride along invisibly just
        # because the matcher didn't nominate it.
        by_id = {s["id"]: s for s in suggestions}
        for t in open_tasks:
            tid = int(t.get("id") or 0)
            if tid in merged and tid not in by_id:
                by_id[tid] = {"id": tid,
                              "task_type": str(t.get("task_type") or "")}
        described = [by_id.get(tid, {"id": tid, "task_type": ""})
                     for tid in merged]

    link_phrase = describe_links(described)
    return tool_propose(
        "create_work_order", summary + defaults_note + price_note + link_phrase, norm,
        risk="low",
        consequence=(
            "Creates an OPEN work order draft you can edit or delete on "
            "the Work Orders page — nothing is final until you review it."
            + (" Linked tasks stay open; linking only ties them to this "
               "work order for cost reporting." if link_phrase else "")
        ),
    )


@register_action_executor("create_work_order")
async def _execute_create_work_order(payload, account_id, user_context, db):
    """Create the WO + lines — runs only post-approval.

    Re-clamps everything from the stored payload (never trusts it aged
    well), forces status='open' (AI drafts only), and compensates on a
    mid-way line failure by deleting the header so a failed action
    leaves nothing behind.
    """
    from fastapi import HTTPException

    norm = _normalize(payload or {})
    if norm is None:
        raise HTTPException(status_code=422, detail="Proposal lost its vehicle")

    parts_cost = round(sum(p["total_cost"] for p in norm["parts"]), 2)
    # Header total starts as parts+fee+tax; each labor-line insert
    # below recomputes labor_cost AND total server-side (migration 153
    # contract), so the final row is internally consistent.
    total = round(parts_cost + norm["fee"] + norm["tax"], 2)

    vendor_id = None
    if norm["vendor_name"]:
        _v = await db.resolve_or_create_vendor(
            account_id, norm["vendor_name"],
            address=norm["vendor_address"], phone=norm["vendor_phone"],
            email=norm["vendor_email"],
        )
        if _v:
            vendor_id = _v["id"]

    wo_id = await db.add_work_order(
        account_id, "",                      # company resolved by the human later
        norm["vehicle_name"], norm["vendor_name"],
        vehicle_type=norm["vehicle_type"],
        vendor_address=norm["vendor_address"],
        vendor_phone=norm["vendor_phone"],
        vendor_id=vendor_id,
        service_date=norm["service_date"] or None,
        odometer_at_service=norm["odometer"],
        parts_cost=parts_cost,
        tax_amount=norm["tax"],
        fee_amount=norm["fee"],
        total_cost=total,
        invoice_number=norm["invoice_number"],
        payment_status="unpaid",
        status="open",                        # AI drafts only — never final
        repair_priority=norm["repair_priority"],
        notes=norm["notes"],
        created_by=int((user_context or {}).get("user_id") or 0),
    )
    try:
        for p in norm["parts"]:
            await db.add_work_order_part(wo_id, **p)
        for l in norm["labor"]:
            await db.add_work_order_labor(wo_id, account_id, **l)
    except Exception:
        # Compensating cleanup: a half-built WO with no undo handle is
        # worse than no WO — remove the header + whatever landed.
        try:
            await db.delete_work_order(wo_id, account_id=account_id)
        except Exception:      # pragma: no cover — best-effort cleanup
            logger.exception("WO %s cleanup after failed line insert", wo_id)
        raise

    # Maintenance links: every id is re-checked HERE against the real
    # rows — right account, THIS vehicle, still open, not already on
    # another work order.  The payload proposed them; the database
    # decides.  Non-fatal: a stale id just doesn't link.
    linked = 0
    if norm["link_task_ids"]:
        valid: list[int] = []
        for tid in norm["link_task_ids"]:
            try:
                task = await db.get_maintenance_task(tid, account_id)
            except Exception:      # pragma: no cover
                task = None
            if not task or not is_open_task(task):
                continue
            if str(task.get("vehicle_name") or "").strip().lower() \
                    != norm["vehicle_name"].strip().lower():
                continue
            valid.append(tid)
        if valid:
            try:
                linked = await db.link_maintenance_tasks_to_work_order(
                    account_id, wo_id, valid,
                )
            except Exception:      # pragma: no cover
                logger.exception("WO %s maintenance link failed", wo_id)

    return {
        "created": True,
        "target_type": "work_order",
        "target_id": str(wo_id),
        "vehicle_name": norm["vehicle_name"],
        "vendor_name": norm["vendor_name"],
        "parts": len(norm["parts"]),
        "labor": len(norm["labor"]),
        "linked_tasks": linked,
        # The client uploads its device-held files by these NAMES to the
        # approve response's target_id (never an id from the payload).
        "source_files": norm["source_files"],
        "message": (
            f"Created work order #{wo_id} for {norm['vehicle_name']} as an "
            f"open draft — review and finish it on the Work Orders page."
            + (f" Linked {linked} maintenance task"
               f"{'s' if linked > 1 else ''}." if linked else "")
        ),
        "_wo_id": wo_id,
    }


@register_undo_executor("create_work_order")
async def _undo_create_work_order(result, payload, account_id, user_context, db):
    """Reverse an executed create — delete exactly the WO it made.

    Post-approve client uploads mean attachments may exist by undo
    time, so physical files are cleared first (the DELETE route's own
    recipe); DB rows cascade via delete_work_order.  Tolerant: a WO
    someone already deleted manually is a no-op, not a failure.
    """
    from fastapi import HTTPException

    wo_id = (result or {}).get("_wo_id")
    try:
        wo_id = int(wo_id)
    except (TypeError, ValueError):
        raise HTTPException(
            status_code=409,
            detail="This action predates undo support — delete the work "
                   "order from the Work Orders page instead.",
        )
    wo = await db.get_work_order(wo_id, account_id)
    if not wo:
        return {"undone": 0, "target_type": "work_order",
                "message": "That work order was already deleted."}

    from adapters.storage.object_storage import get_object_storage_for_account
    from features.work_orders.storage import (
        resolve_company_folder, work_order_folder,
    )
    try:
        store = await get_object_storage_for_account(account_id, db)
        company_folder = await resolve_company_folder(
            db, account_id, wo.get("company_code", ""),
        )
        folder = work_order_folder(
            company_folder=company_folder,
            work_order_id=wo_id,
            vehicle_name=wo.get("vehicle_name", ""),
            service_date=wo.get("service_date"),
            vendor_name=wo.get("vendor_name", ""),
        )
        for att in await db.list_work_order_attachments(wo_id):
            try:
                store.delete(folder, att.get("file_name", ""))
            except Exception:
                pass
    except Exception:          # pragma: no cover — files are best-effort
        logger.exception("WO %s undo: object-store cleanup failed", wo_id)

    deleted = await db.delete_work_order(wo_id, account_id=account_id)
    return {
        "undone": deleted,
        "target_type": "work_order",
        "message": f"Deleted work order #{wo_id}.",
    }
