"""Overview AI tool — quick account-wide fleet stats.

Cross-cutting counts (faults / critical / low-fuel / health alerts) that span
multiple domains, so it lives in the overview feature rather than under any
single one.  Scope-aware: filters the fleet + health data to the caller's
Vehicle-Access scope before counting.
"""

from __future__ import annotations

import logging

from capabilities.ai.tools.registry import register_tool
from capabilities.ai.tools.scope import filter_to_scope
from features.vehicles.warehouse.service import get_vehicle_health as _svc_health
from features.vehicles.service import get_vehicles_overview as _svc_fleet


logger = logging.getLogger("bot.ai.tools")


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
    fleet = filter_to_scope(await _svc_fleet(account_id), tool_args, key="name",
                           external_key="id")
    faulted = []
    # Severity comes from the single source of truth, not from a
    # hand-rolled lamp check.  This loop used to read stopIsOn /
    # protectIsOn / emissionsIsOn directly — keys the WAREHOUSE read
    # path never produces (it synthesises {"red": True}) — so
    # `vehicles_critical` was structurally 0 on the default path and
    # the assistant answered "5 trucks with faults, none critical" for
    # a fleet that had them.
    from features.vehicles.severity import classify_is_critical

    critical = []
    for v in fleet:
        fc = v.get("fault_codes", {})
        j1939 = fc.get("j1939", {})
        dtcs = j1939.get("diagnosticTroubleCodes", [])
        cel = j1939.get("checkEngineLights", {})
        # A lamp on with no DTC still means the truck is reporting a
        # fault; counting only DTC-bearing rows hid exactly the case a
        # STOP lamp exists to announce.
        if dtcs or cel:
            faulted.append(v)
        if classify_is_critical(v):
            critical.append(v)
    # ``or 100`` would swallow a legitimate 0% (empty tank) as "not low" — use
    # an explicit None check so a truck at 0% is counted, not excluded.
    low_fuel = []
    for v in fleet:
        pct = (v.get("fuel") or {}).get("value")
        if pct is not None and pct <= 20:
            low_fuel.append(v)
    # A health-service failure is NOT zero health alerts.  Counting it
    # as 0 handed the model a fabricated all-clear with ok:true, and the
    # bare except hid the cause from the logs as well.  Omit the key and
    # say the source was unavailable, so the model reports what it does
    # not know instead of inventing a number.
    alerts: int | None = None
    health_error = ""
    try:
        health = filter_to_scope(await _svc_health(account_id), tool_args, key="name",
                                external_key="id")
        alerts = sum(1 for v in health if v.get("_health_alerts"))
    except Exception as e:  # noqa: BLE001 — one source failing must not fail the rollup
        logger.warning("account stats: health source unavailable: %s", e)
        health_error = (
            "health alerts could not be read — this is not a count of zero"
        )
    out = {
        "total_active_vehicles": len(fleet),
        "vehicles_with_faults": len(faulted),
        "vehicles_critical": len(critical),
        "vehicles_low_fuel": len(low_fuel),
    }
    if alerts is None:
        out["health_unavailable"] = health_error
    else:
        out["vehicles_with_health_alerts"] = alerts
    return out
