"""Fuel-related tools: low fuel, truck fuel costs, fuel cost summary."""

from __future__ import annotations

from capabilities.ai.tools.registry import register_tool
from capabilities.ai.tools.scope import filter_to_scope
from features.vehicles.warehouse.service import get_low_fuel_vehicles as _svc_low_fuel


@register_tool({
    "name": "get_low_fuel_vehicles",
    "description": (
        "Get trucks with fuel level at or below a threshold percentage."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "threshold": {
                "type": "integer",
                "description": "Fuel percentage threshold (default 20)",
            },
        },
        "required": [],
    },
})
async def get_low_fuel_vehicles(tool_args: dict, samsara_client,
                                account_id: int | None = None, db=None) -> dict:
    threshold = tool_args.get("threshold", 20)
    if account_id is None:
        return {"error": "This tool requires account context."}
    low = filter_to_scope(
        await _svc_low_fuel(account_id, threshold=threshold), tool_args, key="name",
    )
    return {
        "threshold_pct": threshold,
        "count": len(low),
        "vehicles": [
            {
                "vehicle": v.get("name"),
                "fuel_pct": v.get("_fuel_pct"),
            }
            for v in low[:20]
        ],
    }


@register_tool({
    "name": "get_vehicle_fuel_costs",
    "description": (
        "Get fuel fill-up history and cost data for a specific vehicle: "
        "recent fill-ups (gallons, price/gallon, total cost, odometer), "
        "totals and average cost per gallon. Optionally filter by number of days."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "vehicle_name": {
                "type": "string",
                "description": "The vehicle name or number",
            },
            "days": {
                "type": "integer",
                "description": "Limit to fill-ups in the last N days (e.g. 30 for this month). Omit for all-time.",
            },
        },
        "required": ["vehicle_name"],
    },
})
async def get_vehicle_fuel_costs(tool_args: dict, samsara_client,
                                 account_id: int | None = None, db=None) -> dict:
    vehicle = tool_args.get("vehicle_name", "")
    days = tool_args.get("days")
    if not db or account_id is None:
        return {"error": "Fuel cost data not available in this context"}
    entries = await db.get_fuel_entries(account_id, vehicle_name=vehicle, limit=200)
    if days and isinstance(days, int) and days > 0:
        # Stored fuel dates are ACCOUNT-local day strings (the bot
        # writes them in the account timezone) — the cutoff must speak
        # the same clock, never the server's local day.
        from datetime import datetime, timedelta
        from zoneinfo import ZoneInfo

        from capabilities.localization.tz import effective_tz_for_account
        try:
            _tz = ZoneInfo(await effective_tz_for_account(account_id))
        except Exception:
            _tz = ZoneInfo("America/New_York")
        cutoff = (datetime.now(_tz) - timedelta(days=days)).strftime("%Y-%m-%d")
        entries = [e for e in entries if (e.get("date") or "") >= cutoff]
    if not entries:
        period = f" in the last {days} days" if days else ""
        stats = await db.get_fuel_entry_stats(account_id)
        if stats["count"] == 0:
            return {
                "vehicle": vehicle,
                "result": (
                    "This account has NEVER recorded any fuel entries (for any "
                    "vehicle) — fuel costs are manual entries (Costs → Fuel "
                    "Costs → Add entry). Do not suggest other time periods; "
                    "tell the user how entries get recorded."
                ),
                "entries_total": 0,
            }
        return {
            "vehicle": vehicle,
            "result": (
                f"No fuel entries recorded for this vehicle{period}. The "
                f"account has {stats['count']} entries for other vehicles/"
                f"periods, spanning {stats['first_date']} to {stats['last_date']}."
            ),
            "entries_total": stats["count"],
        }
    total_gal = sum(e.get("gallons", 0) for e in entries)
    total_cost = sum(e.get("total_cost", 0) for e in entries)
    return {
        "vehicle": vehicle,
        "period_days": days,
        "entry_count": len(entries),
        "total_gallons": round(total_gal, 1),
        "total_cost": round(total_cost, 2),
        "avg_price_per_gallon": round(total_cost / total_gal, 3) if total_gal else 0,
        "recent_fills": [
            {
                "date": e.get("date", ""),
                "gallons": e.get("gallons"),
                "price_per_gallon": e.get("price_per_gallon"),
                "total_cost": e.get("total_cost"),
                "odometer": e.get("odometer_miles"),
            }
            for e in entries[:10]
        ],
    }


@register_tool({
    "name": "get_fuel_cost_summary",
    "description": (
        "Get account-wide fuel cost summary: per-vehicle totals "
        "(gallons, total cost, average price per gallon, cost per mile). "
        "Optionally filter by start_date and/or end_date (YYYY-MM-DD). "
        "Omit both dates to get all recorded history."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "start_date": {
                "type": "string",
                "description": "Filter entries on or after this date (YYYY-MM-DD). Optional.",
            },
            "end_date": {
                "type": "string",
                "description": "Filter entries on or before this date (YYYY-MM-DD). Optional.",
            },
        },
        "required": [],
    },
})
async def get_fuel_cost_summary(tool_args: dict, samsara_client,
                                account_id: int | None = None, db=None) -> dict:
    if not db or account_id is None:
        return {"error": "Fuel cost data not available in this context"}
    start_date = tool_args.get("start_date")
    end_date = tool_args.get("end_date")
    summary = await db.get_fuel_summary(account_id, start_date=start_date, end_date=end_date)
    summary = filter_to_scope(summary, tool_args, key="vehicle_name")
    if not summary:
        # Empty answers need to say WHICH kind of empty (the model can't
        # guess, and "try another period" is wrong advice when the account
        # has never recorded anything):
        #   • zero entries EVER → say so + how data gets recorded, and
        #     tell the model not to suggest other periods.
        #   • entries exist but none in the requested window → echo the
        #     window and the real data range so the model can point at it.
        stats = await db.get_fuel_entry_stats(account_id)
        window = (
            f" between {start_date or 'the beginning'} and {end_date or 'now'}"
            if (start_date or end_date) else ""
        )
        if stats["count"] == 0:
            return {
                "result": (
                    "This account has NEVER recorded any fuel entries — the "
                    "fuel-costs feature is fed by manual entries (Costs → "
                    "Fuel Costs → Add entry, or ask an admin). There is no "
                    "data for ANY time period, so do not suggest querying "
                    "other dates; instead tell the user how entries get "
                    "recorded."
                ),
                "entries_total": 0,
            }
        return {
            "result": (
                f"No fuel entries{window}. The account does have "
                f"{stats['count']} entries recorded overall, spanning "
                f"{stats['first_date']} to {stats['last_date']} — the user "
                f"may want that range instead."
            ),
            "entries_total": stats["count"],
            "data_range": {"first": stats["first_date"], "last": stats["last_date"]},
        }
    result_items = []
    for s in summary[:20]:
        first_odo = s.get("first_odo") or 0
        last_odo = s.get("last_odo") or 0
        miles = last_odo - first_odo if last_odo > first_odo else 0
        total_cost = s.get("total_cost") or 0
        cost_per_mile = round(total_cost / miles, 3) if miles > 0 else None
        result_items.append({
            "vehicle": s.get("vehicle_name", "?"),
            "entries": s.get("entries", 0),
            "total_gallons": round(s.get("total_gallons") or 0, 1),
            "total_cost": round(total_cost, 2),
            "avg_price_per_gallon": round(s.get("avg_price") or 0, 3),
            "cost_per_mile": cost_per_mile,
        })
    return {"vehicles": result_items, "start_date": start_date, "end_date": end_date}
