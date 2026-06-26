"""Overview AI tool — quick account-wide fleet stats.

Cross-cutting counts (faults / critical / low-fuel / health alerts) that span
multiple domains, so it lives in the overview feature rather than under any
single one.  Scope-aware: filters the fleet + health data to the caller's
Vehicle-Access scope before counting.
"""

from __future__ import annotations

from capabilities.ai.tools.registry import register_tool
from capabilities.ai.tools.scope import filter_to_scope
from capabilities.telemetry.service import get_vehicle_health as _svc_health
from features.vehicles.service import get_vehicles_overview as _svc_fleet


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
    # ``or 100`` would swallow a legitimate 0% (empty tank) as "not low" — use
    # an explicit None check so a truck at 0% is counted, not excluded.
    low_fuel = []
    for v in fleet:
        pct = (v.get("fuel") or {}).get("value")
        if pct is not None and pct <= 20:
            low_fuel.append(v)
    try:
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
