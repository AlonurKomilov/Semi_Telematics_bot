"""Work-orders AI tool — recent shop visits / work orders.

Vehicle-specific (optional vehicle_name), so driver/scope isolation is
enforced by the gate, which requires a scoped caller to name an allowed
vehicle before this runs.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from capabilities.ai.tools.registry import register_tool
from capabilities.ai.tools.scope import filter_to_scope


def _parses(service_date: str) -> bool:
    """True when the stored date is readable at all.

    Separates "this work order is older than the window" from "this
    work order's date is garbage" — the second is a data problem worth
    naming, and lumping it into the first hides it forever.
    """
    if not service_date:
        return False
    try:
        datetime.fromisoformat(service_date.replace("Z", "+00:00"))
        return True
    except Exception:
        return False


def _within_days(service_date: str, days: int) -> bool:
    """True when ``service_date`` (ISO-ish) is within the last ``days``."""
    if not service_date:
        return False
    try:
        # Accept '2026-05-12' or full ISO timestamp.
        sd = datetime.fromisoformat(service_date.replace("Z", "+00:00"))
        if sd.tzinfo is None:
            sd = sd.replace(tzinfo=timezone.utc)
    except Exception:
        return False
    cutoff = datetime.now(timezone.utc) - timedelta(days=max(days, 1))
    return sd >= cutoff


@register_tool({
    "name": "get_recent_work_orders",
    "description": (
        "List recent shop visits / work orders.  Returns vehicle, "
        "vendor, service date, work performed, total cost, payment "
        "status, and current status (open / in progress / closed).  "
        "Use for questions like 'what repairs did truck 231 get last "
        "month', 'how much have we spent at shop X', 'show open work "
        "orders', 'what was the last service on truck 102'."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "vehicle_name": {
                "type": "string",
                "description": "Optional vehicle filter (exact match).",
            },
            "status": {
                "type": "string",
                "enum": ["open", "in_progress", "completed"],
                "description": (
                    "Optional.  'completed' is a FINISHED repair — the "
                    "answer to 'what work did we get done'.  Omit for "
                    "all statuses."
                ),
            },
            "payment_status": {
                "type": "string",
                "enum": ["unpaid", "partial", "paid"],
                "description": "Optional: 'unpaid', 'partial', 'paid'.",
            },
            "days": {
                "type": "number",
                "description": (
                    "Restrict to work orders with service_date within "
                    "the last N days.  Default 90.  Use 30 for 'this "
                    "month', 365 for 'this year'."
                ),
            },
        },
        "required": [],
    },
})
async def get_recent_work_orders(tool_args: dict, samsara_client,
                                 account_id: int | None = None, db=None) -> dict:
    if not db or account_id is None:
        return {"error": "Work order data not available in this context"}

    from adapters.storage.work_orders import normalize_wo_status
    from capabilities.ai.tools import tool_error

    vehicle = (tool_args.get("vehicle_name") or "").strip() or None
    raw_status = (tool_args.get("status") or "").strip() or None
    pay = (tool_args.get("payment_status") or "").strip().lower() or None
    days = int(tool_args.get("days") or 90)

    # The schema used to advertise 'closed' and 'void' — the brief
    # interim vocabulary, translated only at the WRITE boundary — and
    # omitted 'completed' entirely.  So "what repairs finished this
    # month" filtered on a value no row holds and came back empty,
    # which reads as "none".  Normalize the legacy spellings the same
    # way a write does, and refuse anything else rather than answering
    # zero.
    status = normalize_wo_status(raw_status) if raw_status else None
    if raw_status and status is None:
        return tool_error(
            "status must be one of open, in_progress, completed."
        )
    if pay and pay not in ("unpaid", "partial", "paid"):
        return tool_error("payment_status must be unpaid, partial or paid.")

    # Ask the store for the window instead of the history.  The date
    # narrowing below still runs — the store's filter is deliberately
    # generous (it keeps undated and unparseable dates so the counters
    # below stay honest) — but a three-year-old shop history no longer
    # crosses the wire to be thrown away in Python.  The cutoff is the
    # DATE floor of the same instant, so the store can only ever hand
    # back a superset of what the check below keeps.
    cutoff_day = (
        datetime.now(timezone.utc) - timedelta(days=max(days, 1))
    ).date().isoformat()
    rows = await db.list_work_orders(
        account_id, status=status, payment_status=pay,
        vehicle_name=vehicle, since=cutoff_day,
    )

    # The caller's own trucks, by the strongest rung the rows carry.
    #
    # This tool is in VEHICLE_SPECIFIC_TOOLS, so the dispatcher hands it
    # `_scope_vehicles` and `_scope_identities` — and the handler read
    # neither. The only narrowing was SQL `vehicle_name = ?`, which
    # cannot split same-numbered trucks across companies, so a caller
    # scoped to one company's unit 234 was shown the other company's
    # 234 work orders too: their shop, their parts, their money. Filter
    # BEFORE the totals below, so count and cost describe the caller's
    # own rows rather than the account's.
    #
    # work_orders rows carry vehicle_id, so the default external key is
    # right here and rung 2 splits the twins even without a registry id.
    rows = filter_to_scope(rows, tool_args, key="vehicle_name")

    # Narrow by service-date window in Python; the storage method
    # doesn't filter dates and a 1-year mechanic-shop dataset is small.
    #
    # A row with no service date is NOT "outside the window" — it is a
    # work order nobody has dated, which is a different fact and often
    # the interesting one (an open job, or a synced invoice that came
    # through without a date).  Silently dropping them made the count a
    # statement the data does not support.
    undated = [r for r in rows if not (r.get("service_date") or "").strip()]
    dated = [r for r in rows if (r.get("service_date") or "").strip()]
    unreadable = [r for r in dated if not _parses(r.get("service_date") or "")]
    rows = [r for r in dated if _within_days(r.get("service_date") or "", days)]

    # ``total_cost`` is stored as REAL dollars (work_orders table), NOT cents —
    # the old ``total_cost_cents`` key never existed, so every cost came back $0.
    total_cost = sum(float(r.get("total_cost") or 0) for r in rows)

    return {
        "count": len(rows),
        # Named, not dropped: an undated work order is invisible to a
        # date window but still exists, and a count that quietly omits
        # it is a number the model will state as complete.
        "undated_count": len(undated),
        "unreadable_date_count": len(unreadable),
        "filters": {
            "vehicle_name": vehicle, "status": status,
            "payment_status": pay, "days": days,
        },
        "total_cost_dollars": round(total_cost, 2),
        "work_orders": [
            {
                "id": r.get("id"),
                "vehicle": r.get("vehicle_name") or "",
                "vendor": r.get("vendor_name") or "",
                "service_date": r.get("service_date") or "",
                "status": r.get("status") or "",
                "payment_status": r.get("payment_status") or "",
                "total_cost": round(float(r.get("total_cost") or 0), 2),
                "summary": (r.get("notes") or r.get("description") or "")[:200],
            }
            for r in rows[:30]
        ],
    }
