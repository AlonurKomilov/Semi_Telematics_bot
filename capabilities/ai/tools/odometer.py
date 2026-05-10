"""Odometer tool — current mileage readings sourced from the warehouse.

The OBD values flow Samsara → ``ingest_vehicle_state`` → ``vehicle_state``
table; this tool reads the table directly so the AI agent and every
other consumer share the same single source of truth.  Live Samsara is
never queried on the request path.  Bypasses the WAREHOUSE_READS_ENABLED
cutover flag — odometer is only in the warehouse.
"""

from __future__ import annotations

from capabilities.ai.tools.registry import register_tool


@register_tool({
    "name": "get_vehicle_odometer",
    "description": (
        "Get current odometer readings (miles) for fleet vehicles, sourced from "
        "the warehouse (refreshed every 60s from Samsara OBD telemetry). "
        "If vehicle_name is given, returns mileage for that specific vehicle. "
        "Omit vehicle_name to get readings for all vehicles. "
        "NOTE: this is OBD-derived odometer — not the manual maintenance "
        "records. Use get_vehicle_maintenance for due-mileage tasks."
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
    if account_id is None:
        return {"error": "account_id is required to read the warehouse"}

    from infra.platform import get_tenant_db
    tenant = await get_tenant_db(account_id)

    vehicle = (tool_args.get("vehicle_name") or "").strip()

    if vehicle:
        rows = await tenant.get_vehicle_state(account_id, vehicle_nums=[vehicle])
        match = next(
            (r for r in rows if (r.get("vehicle_name") or "").lower() == vehicle.lower()),
            None,
        )
        if not match or match.get("odometer_mi") is None:
            return {
                "error": (
                    f"Vehicle '{vehicle}' not found or odometer data not available. "
                    "Some vehicles without CAN bus gateways may not report odometer."
                ),
            }
        return {
            "vehicle": match.get("vehicle_name"),
            "odometer_miles": match.get("odometer_mi"),
            "time": match.get("odometer_time"),
        }

    rows = await tenant.get_vehicle_state(account_id)
    rows_with_odometer = [r for r in rows if r.get("odometer_mi") is not None]
    if not rows_with_odometer:
        return {"result": "No odometer data available. Vehicles may not have CAN bus gateways."}

    rows_sorted = sorted(rows_with_odometer, key=lambda r: r.get("vehicle_name", ""))
    return {
        "vehicle_count": len(rows_sorted),
        "vehicles": [
            {
                "vehicle": r.get("vehicle_name"),
                "odometer_miles": r.get("odometer_mi"),
            }
            for r in rows_sorted[:40]
        ],
    }
