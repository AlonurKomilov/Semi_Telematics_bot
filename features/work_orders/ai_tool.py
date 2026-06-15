"""Work-orders AI tool — recent shop visits / work orders.

Vehicle-specific (optional vehicle_name), so driver/scope isolation is
enforced by the gate, which requires a scoped caller to name an allowed
vehicle before this runs.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from capabilities.ai.tools.registry import register_tool


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
                "description": (
                    "Optional: 'open', 'in_progress', 'closed', "
                    "'cancelled'.  Omit for all statuses."
                ),
            },
            "payment_status": {
                "type": "string",
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

    vehicle = (tool_args.get("vehicle_name") or "").strip() or None
    status = (tool_args.get("status") or "").strip() or None
    pay = (tool_args.get("payment_status") or "").strip() or None
    days = int(tool_args.get("days") or 90)

    rows = await db.list_work_orders(
        account_id, status=status, payment_status=pay,
        vehicle_name=vehicle,
    )

    # Narrow by service-date window in Python; the storage method
    # doesn't filter dates and a 1-year mechanic-shop dataset is small.
    rows = [r for r in rows if _within_days(r.get("service_date") or "", days)]

    total_cost_cents = sum(int(r.get("total_cost_cents") or 0) for r in rows)

    return {
        "count": len(rows),
        "filters": {
            "vehicle_name": vehicle, "status": status,
            "payment_status": pay, "days": days,
        },
        "total_cost_dollars": round(total_cost_cents / 100.0, 2),
        "work_orders": [
            {
                "id": r.get("id"),
                "vehicle": r.get("vehicle_name") or "",
                "vendor": r.get("vendor_name") or "",
                "service_date": r.get("service_date") or "",
                "status": r.get("status") or "",
                "payment_status": r.get("payment_status") or "",
                "total_cost": round(
                    int(r.get("total_cost_cents") or 0) / 100.0, 2,
                ),
                "summary": (r.get("notes") or r.get("description") or "")[:200],
            }
            for r in rows[:30]
        ],
    }
