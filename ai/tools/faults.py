"""Fault-related tools: truck faults and critical faults."""

from __future__ import annotations

from ai.tools.registry import register_tool


@register_tool({
    "name": "get_truck_faults",
    "description": (
        "Get active J1939/OBD fault codes (DTCs) and check engine light "
        "status for a specific truck by its name/number."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "truck_name": {
                "type": "string",
                "description": "The truck name or number, e.g. '101' or 'Truck 205'",
            },
        },
        "required": ["truck_name"],
    },
})
async def get_truck_faults(tool_args: dict, samsara_client,
                           account_id: int | None = None, db=None) -> dict:
    truck = tool_args.get("truck_name", "")
    # First verify the truck exists
    detail = await samsara_client.get_vehicle_detail(truck)
    if not detail:
        return {"error": f"Truck '{truck}' not found in the fleet. Check the truck name/number and try again."}
    faulted, _ = await samsara_client.get_vehicles_with_faults()
    matches = [
        v for v in faulted
        if v.get("name", "").lower() == truck.lower()
    ]
    if not matches:
        return {
            "truck": truck, "fault_count": 0, "faults": [],
            "check_engine_lights": {},
            "status": "No active fault codes detected \u2014 truck is clean.",
        }
    v = matches[0]
    return {
        "truck": v.get("name"),
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


@register_tool({
    "name": "get_critical_faults",
    "description": (
        "Get all trucks with critical faults: STOP light, PROTECT light, "
        "EMISSIONS light, or severe FMI codes. Returns only critical vehicles."
    ),
    "parameters": {
        "type": "object",
        "properties": {},
        "required": [],
    },
})
async def get_critical_faults(tool_args: dict, samsara_client,
                              account_id: int | None = None, db=None) -> dict:
    critical = await samsara_client.get_critical_faults()
    return {
        "critical_count": len(critical),
        "vehicles": [
            {
                "truck": v.get("name"),
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
