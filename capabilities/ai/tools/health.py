"""Health-related tools: vehicle health diagnostics and fleet stats."""

from __future__ import annotations

from capabilities.ai.tools.registry import register_tool


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
    health = await samsara_client.get_vehicle_health()
    return {
        "total_vehicles": len(health),
        "vehicles_with_alerts": sum(1 for v in health if v.get("_health_alerts")),
        "vehicles": [
            {
                "truck": v.get("name"),
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
    "name": "count_stats",
    "description": (
        "Get quick account-wide counts: total active trucks, trucks with "
        "faults, trucks with critical faults, low fuel trucks, and "
        "trucks with health alerts. Fast overview without full details."
    ),
    "parameters": {
        "type": "object",
        "properties": {},
        "required": [],
    },
})
async def count_stats(tool_args: dict, samsara_client,
                            account_id: int | None = None, db=None) -> dict:
    fleet = await samsara_client.get_fleet_overview()
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
        health = await samsara_client.get_vehicle_health()
        alerts = sum(1 for v in health if v.get("_health_alerts"))
    except Exception:
        alerts = 0
    return {
        "total_active_trucks": len(fleet),
        "trucks_with_faults": len(faulted),
        "trucks_critical": len(critical),
        "trucks_low_fuel": len(low_fuel),
        "trucks_with_health_alerts": alerts,
    }
