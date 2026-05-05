"""Efficiency-related tools: driver efficiency, fleet efficiency, scorecards."""

from __future__ import annotations

from capabilities.ai.tools.registry import register_tool
from capabilities.telemetry.service import (
    get_driver_efficiency as _svc_drv_eff,
    get_fleet_efficiency as _svc_fleet_eff,
)


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
    if account_id is not None:
        drivers = await _svc_drv_eff(account_id, days=days)
    else:
        drivers = await samsara_client.get_driver_efficiency(days=days)
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
    "name": "get_efficiency_summary",
    "description": (
        "Get engine hours, driving hours, idle hours, idle percentage, "
        "and miles driven per truck over the last N days. Also includes "
        "driver fuel efficiency (MPG) and eco-driving score when available."
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
async def get_efficiency_summary(tool_args: dict, samsara_client,
                               account_id: int | None = None, db=None) -> dict:
    days = tool_args.get("days", 7)
    if account_id is not None:
        eff = await _svc_fleet_eff(account_id, days=days)
    else:
        eff = await samsara_client.get_fleet_efficiency(days=days)
    return {
        "period_days": days,
        "vehicle_count": len(eff),
        "vehicles": [
            {
                "vehicle": v.get("name"),
                "engine_hours": v.get("_engine_hours"),
                "driving_hours": v.get("_driving_hours"),
                "idle_hours": v.get("_idle_hours"),
                "idle_pct": v.get("_idle_pct"),
                "miles": v.get("_miles"),
                "driver": v.get("_driver_name"),
                "mpg": v.get("_mpg"),
                "green_pct": v.get("_green_pct"),
            }
            for v in eff[:30]
        ],
    }


@register_tool({
    "name": "get_driver_scorecard",
    "description": (
        "Get driver scorecards: miles driven, MPG, idle %, drive hours, "
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
    if account_id is not None:
        drivers = await _svc_drv_eff(account_id, days=days)
    else:
        drivers = await samsara_client.get_driver_efficiency(days=days)
    if driver_filter:
        drivers = [
            d for d in drivers
            if driver_filter in d.get("driver_name", "").lower()
        ]
    if not drivers:
        return {
            "period_days": days, "drivers": [],
            "status": "No driver scorecard data found for the requested period.",
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
