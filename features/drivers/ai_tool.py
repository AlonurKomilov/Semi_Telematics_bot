"""Driver AI tools — the roster and per-driver efficiency.

Hours of service used to live here too.  It moved to
``features/eld/ai_tool.py`` when the ELD feature landed, keeping its
tool id: a driver is who the hours belong to, but an ELD is where they
come from, and the answer now needs a service that knows how old the
reading is.

``get_drivers_list`` has no vehicle dimension on the roster record, so
it is not scope-filtered and remains blocked for scoped users by the
gate.
"""

from __future__ import annotations

from capabilities.ai.tools.registry import register_tool
from capabilities.ai.tools.scope import (
    filter_to_scope, scope_from_args, scope_vehicle_set,
)
from features.vehicles.warehouse.service import get_driver_efficiency as _svc_drv_eff


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


def _scope_trucks(tool_args: dict) -> list[str] | None:
    """The caller's allowed trucks for the efficiency service, or ``None``.

    ``None`` = unrestricted (the service reads the warehouse, account-wide).
    A list — even empty — makes the service filter to drivers whose
    ``_vehicle_summaries`` name one of these trucks, reading live because
    the warehouse rollup carries no vehicle join; ``[]`` returns nobody.
    Same contract as every other scope-aware tool: the orchestrator
    injects ``_scope_vehicles`` from Team Management's Vehicle Access,
    and the model can never supply it.
    """
    allowed = scope_vehicle_set(tool_args)
    return None if allowed is None else sorted(allowed)


def _drivers_in_scope(rows: list[dict], tool_args: dict) -> list[dict]:
    """Narrow driver rows to the caller's own trucks, by identity.

    The service filters by NAME (``vehicle_nums`` above), which cannot
    tell two companies' same-numbered trucks apart — so a driver who
    only ever drove the OTHER company's "103" came back inside a scope
    that names "103". This runs after and removes them.

    A driver row is not a vehicle row: it carries a LIST of trucks under
    ``_vehicle_summaries``, and a driver belongs to the caller when ANY
    of those trucks does. The scope is built ONCE for the whole list.

    Deliberately a NARROWING pass over the service's own result rather
    than a change to what the service is asked for: it can only remove
    rows, never add them, which is the safe half of this fix. Pushing
    identity down into the KPI service is the other half and waits for
    the scorecard work that will reshape these rows anyway.
    """
    scope = scope_from_args(tool_args)
    if scope is None:
        return rows
    kept = []
    for row in rows:
        for vs in row.get("_vehicle_summaries") or []:
            veh = (vs or {}).get("vehicle") or {}
            if scope.allows(external_id=veh.get("id"), name=veh.get("name")):
                kept.append(row)
                break
    return kept


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
    drivers = await _svc_drv_eff(
        account_id, days=days, vehicle_nums=_scope_trucks(tool_args))
    drivers = _drivers_in_scope(drivers, tool_args)
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
    # Scope FIRST, name second.  "Omit for all drivers" was literally the
    # whole account: a driver scoped to one truck who left the name out
    # got every colleague's scorecard.  With the scope applied, "all"
    # means all drivers on the trucks this caller can see.
    drivers = await _svc_drv_eff(
        account_id, days=days, vehicle_nums=_scope_trucks(tool_args))
    drivers = _drivers_in_scope(drivers, tool_args)
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
