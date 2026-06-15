"""Health-related tools: vehicle health diagnostics and fleet stats."""

from __future__ import annotations

from capabilities.ai.tools.registry import register_tool
from capabilities.ai.tools.scope import filter_to_scope
from capabilities.telemetry.service import get_vehicle_health as _svc_health
from features.vehicles.service import get_vehicles_overview as _svc_fleet


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


@register_tool({
    "name": "get_account_stats",
    "description": (
        "Get quick account-wide counts: total active vehicles, vehicles with "
        "faults, vehicles with critical faults, low fuel vehicles, and "
        "vehicles with health alerts. Fast overview without full details."
    ),
    "parameters": {
        "type": "object",
        "properties": {},
        "required": [],
    },
})
async def get_account_stats(tool_args: dict, samsara_client,
                            account_id: int | None = None, db=None) -> dict:
    if account_id is None:
        return {"error": "This tool requires account context."}
    fleet = filter_to_scope(await _svc_fleet(account_id), tool_args, key="name")
    faulted = []
    critical = []
    for v in fleet:
        fc = v.get("fault_codes", {})
        j1939 = fc.get("j1939", {})
        dtcs = j1939.get("diagnosticTroubleCodes", [])
        cel = j1939.get("checkEngineLights", {})
        if dtcs:
            faulted.append(v)
        if (cel.get("stopIsOn") or cel.get("protectIsOn")
                or cel.get("emissionsIsOn")):
            critical.append(v)
    low_fuel = [v for v in fleet
                if (v.get("fuel", {}).get("value") or 100) <= 20]
    try:
        if account_id is None:
            return {"error": "This tool requires account context."}
        health = filter_to_scope(await _svc_health(account_id), tool_args, key="name")
        alerts = sum(1 for v in health if v.get("_health_alerts"))
    except Exception:
        alerts = 0
    return {
        "total_active_vehicles": len(fleet),
        "vehicles_with_faults": len(faulted),
        "vehicles_critical": len(critical),
        "vehicles_low_fuel": len(low_fuel),
        "vehicles_with_health_alerts": alerts,
    }
