"""Fault-related tools: per-vehicle faults and account-wide critical scan."""

from __future__ import annotations

from capabilities.ai.tools.registry import register_tool


@register_tool({
    "name": "get_vehicle_faults",
    "description": (
        "Get fault codes (DTCs) and check engine light status. "
        "If vehicle_name is given, returns faults for that specific vehicle. "
        "If critical_only is true, returns all vehicles with critical warning "
        "lights (STOP, PROTECT, EMISSIONS). "
        "With no arguments, returns an account-wide fault overview."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "vehicle_name": {
                "type": "string",
                "description": "The vehicle name or number, e.g. '101' or 'Truck 205'. Omit for account-wide results.",
            },
            "critical_only": {
                "type": "boolean",
                "description": "If true, return only vehicles with critical warning lights (STOP/PROTECT/EMISSIONS).",
            },
        },
        "required": [],
    },
})
async def get_vehicle_faults(tool_args: dict, samsara_client,
                             account_id: int | None = None, db=None) -> dict:
    vehicle = tool_args.get("vehicle_name", "")
    critical_only = tool_args.get("critical_only", False)

    if vehicle:
        # Per-vehicle fault detail
        detail = await samsara_client.get_vehicle_detail(vehicle)
        if not detail:
            return {"error": f"Vehicle '{vehicle}' not found. Check the name/number and try again."}
        faulted, _ = await samsara_client.get_vehicles_with_faults()
        matches = [v for v in faulted if v.get("name", "").lower() == vehicle.lower()]
        if not matches:
            return {
                "vehicle": vehicle, "fault_count": 0, "faults": [],
                "check_engine_lights": {},
                "status": "No active fault codes detected \u2014 vehicle is clean.",
            }
        v = matches[0]
        return {
            "vehicle": v.get("name"),
            "fault_count": len(v.get("_dtcs", [])),
            "faults": [
                {
                    "spn": d.get("spnId"),
                    "fmi": d.get("fmiId"),
                    "description": d.get("spnDescription", "?"),
                    "severity": d.get("fmiDescription", "?"),
                }
                for d in v.get("_dtcs", [])[:10]
            ],
            "check_engine_lights": v.get("_lights", {}),
        }

    if critical_only:
        # Account-wide critical scan
        critical = await samsara_client.get_critical_faults()
        return {
            "critical_count": len(critical),
            "vehicles": [
                {
                    "vehicle": v.get("name"),
                    "lights": v.get("_lights", {}),
                    "fault_count": len(v.get("_dtcs", [])),
                    "top_faults": [
                        d.get("spnDescription", "?")
                        for d in v.get("_dtcs", [])[:3]
                    ],
                }
                for v in critical[:15]
            ],
        }

    # Account-wide fault overview
    faulted, total = await samsara_client.get_vehicles_with_faults()
    return {
        "total_active_vehicles": total,
        "vehicles_with_faults": len(faulted),
        "vehicles_critical": sum(
            1 for v in faulted
            if v.get("_lights", {}).get("stopIsOn")
            or v.get("_lights", {}).get("protectIsOn")
            or v.get("_lights", {}).get("emissionsIsOn")
        ),
        "fault_summary": [
            {
                "vehicle": v.get("name"),
                "fault_count": len(v.get("_dtcs", [])),
                "critical": bool(
                    v.get("_lights", {}).get("stopIsOn")
                    or v.get("_lights", {}).get("protectIsOn")
                    or v.get("_lights", {}).get("emissionsIsOn")
                ),
            }
            for v in faulted[:20]
        ],
    }
