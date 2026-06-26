"""Vehicle health AI tool — per-vehicle diagnostics.

The cross-cutting fleet-stats tool (get_account_stats) lives in
``features/overview/ai_tool.py`` — it aggregates faults + fuel + health, so
it's an overview tool, not a health-specific one.
"""

from __future__ import annotations

from capabilities.ai.tools.registry import register_tool
from capabilities.ai.tools.scope import filter_to_scope
from capabilities.warehouse.telemetry.service import get_vehicle_health as _svc_health


@register_tool({
    "name": "get_vehicle_health",
    "description": (
        "Get health diagnostics for all vehicles: battery voltage, "
        "coolant temperature, oil pressure, DEF level, engine RPM, "
        "seatbelt status, and health alerts (low battery, high coolant, etc)."
    ),
    "parameters": {
        "type": "object",
        "properties": {},
        "required": [],
    },
})
async def get_vehicle_health(tool_args: dict, samsara_client,
                             account_id: int | None = None, db=None) -> dict:
    if account_id is None:
        return {"error": "This tool requires account context."}
    health = filter_to_scope(await _svc_health(account_id), tool_args, key="name")
    return {
        "total_vehicles": len(health),
        "vehicles_with_alerts": sum(1 for v in health if v.get("_health_alerts")),
        "vehicles": [
            {
                "vehicle": v.get("name"),
                "battery_v": v.get("_health", {}).get("battery_v"),
                "coolant_c": v.get("_health", {}).get("coolant_c"),
                "oil_psi": v.get("_health", {}).get("oil_psi"),
                "def_pct": v.get("_health", {}).get("def_pct"),
                "rpm": v.get("_health", {}).get("rpm"),
                "engine_on": v.get("_health", {}).get("engine_on"),
                "seatbelt": v.get("_health", {}).get("seatbelt"),
                "alerts": v.get("_health_alerts", []),
            }
            for v in health[:30]
        ],
    }
