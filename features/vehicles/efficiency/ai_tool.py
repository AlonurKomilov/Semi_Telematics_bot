"""Vehicle efficiency AI tool — per-vehicle rolling-window efficiency.

Driver-centric efficiency tools (get_driver_efficiency, get_driver_scorecard)
live in ``features/drivers/ai_tool.py`` — they're driver metrics, not vehicle
metrics, so they belong with the other driver tools.
"""

from __future__ import annotations

from capabilities.ai.tools.registry import register_tool
from capabilities.ai.tools.scope import filter_to_scope
from capabilities.warehouse.service import get_fleet_efficiency as _svc_fleet_eff


@register_tool({
    "name": "get_efficiency_summary",
    "description": (
        "Efficiency report across the user's accessible vehicles.  "
        "Per-vehicle rolling-window stats: engine hours, driving "
        "hours, miles driven, MPG, eco-driving score (also exposes "
        "idle hours / idle percentage as a secondary metric).  "
        "Role-agnostic — the underlying scope is the set of vehicles "
        "the caller has permission to see, so the same tool serves "
        "an owner asking about all trucks, a driver asking about "
        "their own assigned truck, or a dispatcher asking about "
        "their company's trucks.\n\n"
        "USE THIS for questions like:\n"
        "- 'efficiency over the last week'\n"
        "- 'how are my vehicles performing'\n"
        "- 'how was my driving this week' (driver scope)\n"
        "- 'MPG report'\n"
        "- 'eco-driving score by vehicle'\n\n"
        "DO NOT use this for 'which trucks are stopped/parked N days' "
        "or 'what vehicle hasn't moved' — those questions are about "
        "long-idle vehicles parked at unsafe locations, which "
        "``get_parked_vehicles`` answers directly.  If the user's "
        "previous turn asked about stopped/parked/idle vehicles and "
        "they then say 'N days', keep calling get_parked_vehicles with "
        "min_days=N — don't switch to this tool just because it "
        "accepts a ``days`` parameter."
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
    if account_id is None:
        return {"error": "This tool requires account context."}
    eff = filter_to_scope(
        await _svc_fleet_eff(account_id, days=days), tool_args, key="name",
    )
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
