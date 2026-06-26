"""Driver AI tools — roster list and hours-of-service status.

``get_driver_hos_status`` carries each driver's assigned truck, so it filters
to the caller's Vehicle-Access scope (by ``truck_num``).  ``get_drivers_list``
has no vehicle dimension on the roster record, so it is not scope-filtered and
remains blocked for scoped users by the gate.
"""

from __future__ import annotations

from capabilities.ai.tools.registry import register_tool
from capabilities.ai.tools.scope import filter_to_scope
from capabilities.telemetry.service import get_driver_efficiency as _svc_drv_eff


@register_tool({
    "name": "get_drivers_list",
    "description": (
        "Get the list of all active drivers in the fleet: name, ID, "
        "and contact info. Useful for answering 'who are our drivers?' "
        "or finding which driver is assigned to a vehicle."
    ),
    "parameters": {
        "type": "object",
        "properties": {},
        "required": [],
    },
})
async def get_drivers_list(tool_args: dict, samsara_client,
                           account_id: int | None = None, db=None) -> dict:
    if account_id is None:
        return {"error": "This tool requires account context."}
    from features.drivers.service import get_drivers as _svc_drivers
    drivers = await _svc_drivers(account_id)
    # Filter to active drivers only
    active = [d for d in drivers if not d.get("deactivatedAtMs")]
    return {
        "driver_count": len(active),
        "drivers": [
            {
                "name": d.get("name"),
                "id": d.get("id"),
                "username": d.get("username", ""),
                "phone": d.get("phone", ""),
            }
            for d in active[:50]
        ],
    }


def _fmt_seconds(secs: int | None) -> str:
    """Format ``11h 30m`` style — readable in chat, parseable for the AI."""
    if secs is None or secs < 0:
        return "unknown"
    h, rem = divmod(int(secs), 3600)
    m = rem // 60
    if h and m:
        return f"{h}h {m}m"
    if h:
        return f"{h}h"
    return f"{m}m"


@register_tool({
    "name": "get_driver_hos_status",
    "description": (
        "Get hours-of-service status for one driver (by name) or for "
        "every driver on the account.  Returns duty status (driving / "
        "on_duty / off_duty / sleeper), drive hours used today, on-duty "
        "hours used today, cycle hours remaining (typically 70-hour "
        "cycle), shift hours remaining, when the status last changed, "
        "and the assigned truck.  Use for questions like 'how many "
        "hours does John have left?', 'who's out of hours?', 'is the "
        "truck 102 driver still on shift?'."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "driver_name": {
                "type": "string",
                "description": (
                    "Optional case-insensitive substring match against "
                    "driver display name.  Omit for the full account roster."
                ),
            },
            "status_filter": {
                "type": "string",
                "description": (
                    "Optional: 'driving', 'on_duty', 'off_duty', "
                    "'sleeper' — restrict to drivers in that state."
                ),
            },
        },
        "required": [],
    },
})
async def get_driver_hos_status(tool_args: dict, samsara_client,
                                account_id: int | None = None, db=None) -> dict:
    if not db or account_id is None:
        return {"error": "HOS data not available in this context"}

    name_q = (tool_args.get("driver_name") or "").strip().lower()
    status_q = (tool_args.get("status_filter") or "").strip().lower()

    rows = await db.get_driver_hos_status(account_id)
    # Scope to the caller's vehicles (by the driver's assigned truck).
    rows = filter_to_scope(rows, tool_args, key="truck_num")

    filtered: list[dict] = []
    for r in rows:
        if name_q and name_q not in (r.get("display_name") or "").lower():
            continue
        if status_q and (r.get("duty_status") or "").lower() != status_q:
            continue
        filtered.append(r)

    if not filtered:
        return {
            "count": 0,
            "drivers": [],
            "note": (
                "No HOS data found for the requested filter.  Either "
                "no drivers match, or the HOS sync job hasn't populated "
                "this account yet — manual ELD entries in Samsara only "
                "appear here after the next sync run."
            ),
        }

    return {
        "count": len(filtered),
        "name_filter": name_q or None,
        "status_filter": status_q or None,
        "drivers": [
            {
                "name": r.get("display_name") or "?",
                "truck": r.get("truck_num") or "",
                "duty_status": r.get("duty_status") or "unknown",
                "drive_today": _fmt_seconds(r.get("drive_seconds_today")),
                "on_duty_today": _fmt_seconds(r.get("on_duty_seconds_today")),
                "cycle_remaining": _fmt_seconds(r.get("cycle_seconds_remaining")),
                "shift_remaining": _fmt_seconds(r.get("shift_seconds_remaining")),
                "last_status_change": r.get("last_status_change") or "",
                "updated_at": r.get("updated_at") or "",
            }
            for r in filtered[:50]
        ],
    }


@register_tool({
    "name": "get_driver_efficiency",
    "description": (
        "Get driver efficiency stats for the last N days: MPG, idle %, "
        "miles driven, eco-driving score, overspeed minutes."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "days": {
                "type": "integer",
                "description": "Number of days to look back (default 7)",
            },
        },
        "required": [],
    },
})
async def get_driver_efficiency(tool_args: dict, samsara_client,
                                account_id: int | None = None, db=None) -> dict:
    days = tool_args.get("days", 7)
    if account_id is None:
        return {"error": "This tool requires account context."}
    drivers = await _svc_drv_eff(account_id, days=days)
    return {
        "period_days": days,
        "drivers": [
            {
                "name": d.get("driver_name", "Unknown"),
                "miles": d.get("_miles"),
                "mpg": d.get("_mpg"),
                "idle_pct": d.get("_idle_pct"),
                "drive_hours": d.get("_drive_h"),
                "green_pct": d.get("_green_pct"),
                "overspeed_min": d.get("_overspeed_min"),
            }
            for d in drivers[:20]
        ],
    }


@register_tool({
    "name": "get_driver_scorecard",
    "description": (
        "Get scorecards: miles driven, MPG, idle %, drive hours, "
        "eco-driving score (green %), overspeed minutes, anticipation %. "
        "Returns top drivers ranked by miles. Optionally filter by a "
        "specific driver name."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "driver_name": {
                "type": "string",
                "description": "Optional driver name to filter. Omit for all drivers.",
            },
            "days": {
                "type": "integer",
                "description": "Number of days to look back (default 7)",
            },
        },
        "required": [],
    },
})
async def get_driver_scorecard(tool_args: dict, samsara_client,
                               account_id: int | None = None, db=None) -> dict:
    days = tool_args.get("days", 7)
    driver_filter = tool_args.get("driver_name", "").strip().lower()
    if account_id is None:
        return {"error": "This tool requires account context."}
    drivers = await _svc_drv_eff(account_id, days=days)
    if driver_filter:
        drivers = [
            d for d in drivers
            if driver_filter in d.get("driver_name", "").lower()
        ]
    if not drivers:
        return {
            "period_days": days, "drivers": [],
            "status": "No scorecard data found for the requested period.",
        }
    return {
        "period_days": days,
        "driver_count": len(drivers),
        "drivers": [
            {
                "name": d.get("driver_name", "?"),
                "miles": d.get("_miles"),
                "mpg": d.get("_mpg"),
                "idle_pct": d.get("_idle_pct"),
                "drive_hours": d.get("_drive_h"),
                "green_pct": d.get("_green_pct"),
                "overspeed_min": d.get("_overspeed_min"),
                "anticipation_pct": d.get("_antic_pct"),
            }
            for d in drivers[:25]
        ],
    }
