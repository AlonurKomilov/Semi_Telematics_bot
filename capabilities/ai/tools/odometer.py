"""Odometer tools: live ECU mileage readings from Samsara."""

from __future__ import annotations

from capabilities.ai.tools.registry import register_tool


@register_tool({
    "name": "get_vehicle_odometer",
    "description": (
        "Get current odometer readings (miles) directly from vehicle ECU via Samsara. "
        "If vehicle_name is given, returns mileage for that specific vehicle. "
        "Omit vehicle_name to get readings for all vehicles. "
        "NOTE: this is live Samsara OBD data — not the manual maintenance records "
        "stored by your team. Use get_vehicle_maintenance for due-mileage tasks."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "vehicle_name": {
                "type": "string",
                "description": "The vehicle name or number. Omit for all vehicles.",
            },
        },
        "required": [],
    },
})
async def get_vehicle_odometer(tool_args: dict, samsara_client,
                               account_id: int | None = None, db=None) -> dict:
    vehicle = (tool_args.get("vehicle_name") or "").strip()
    readings = await samsara_client.get_current_odometer_readings()

    if vehicle:
        match = [r for r in readings if r.get("name", "").lower() == vehicle.lower()]
        if not match:
            return {
                "error": (
                    f"Vehicle '{vehicle}' not found or odometer data not available. "
                    "Some vehicles without CAN bus gateways may not report odometer."
                ),
            }
        r = match[0]
        return {
            "vehicle": r.get("name"),
            "odometer_miles": r.get("odometer_miles"),
            "time": r.get("time"),
        }

    if not readings:
        return {"result": "No odometer data available. Vehicles may not have CAN bus gateways."}

    readings_sorted = sorted(readings, key=lambda r: r.get("name", ""))
    return {
        "vehicle_count": len(readings_sorted),
        "vehicles": [
            {
                "vehicle": r.get("name"),
                "odometer_miles": r.get("odometer_miles"),
            }
            for r in readings_sorted[:40]
        ],
    }
