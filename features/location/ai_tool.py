"""Location AI tools — current GPS position (and, once migrated, weather).

Split out of the old central ``vehicle.py`` so location lives with the
``features/location`` feature.  ``get_vehicle_location`` is vehicle-specific
(requires a vehicle_name), so driver/scope isolation is enforced by the gate.
"""

from __future__ import annotations

from capabilities.ai.tools.registry import register_tool
from capabilities.ai.tools.scope import filter_to_scope
from features.vehicles.service import get_vehicle_detail as _svc_detail
from capabilities.telemetry.service import get_fleet_weather as _svc_weather


@register_tool({
    "name": "get_vehicle_location",
    "description": (
        "Get the current GPS location, city, and speed for a specific vehicle."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "vehicle_name": {
                "type": "string",
                "description": "The vehicle name or number",
            },
        },
        "required": ["vehicle_name"],
    },
})
async def get_vehicle_location(tool_args: dict, samsara_client,
                               account_id: int | None = None, db=None) -> dict:
    vehicle = tool_args.get("vehicle_name", "")
    if account_id is None:
        return {"error": "This tool requires account context."}
    detail = await _svc_detail(account_id, vehicle)
    if not detail:
        return {"result": f"Vehicle {vehicle} not found."}
    v = detail[0] if isinstance(detail, list) else detail
    loc = v.get("location", {})
    geo = loc.get("reverseGeo", {})
    return {
        "vehicle": v.get("name"),
        "city": geo.get("formattedLocation", "Unknown"),
        "latitude": loc.get("latitude"),
        "longitude": loc.get("longitude"),
        "speed_mph": round(loc.get("speed", 0) * 0.621371, 1) if loc.get("speed") else 0,
        "heading": loc.get("heading"),
        "time": loc.get("time", ""),
    }


@register_tool({
    "name": "get_weather",
    "description": (
        "Get ambient air temperature (°F) for each truck's current location. "
        "Useful for identifying trucks in extreme cold or heat conditions."
    ),
    "parameters": {
        "type": "object",
        "properties": {},
        "required": [],
    },
})
async def get_weather(tool_args: dict, samsara_client,
                      account_id: int | None = None, db=None) -> dict:
    if account_id is None:
        return {"error": "This tool requires account context."}
    weather = filter_to_scope(await _svc_weather(account_id), tool_args, key="name")
    return {
        "vehicle_count": len(weather),
        "vehicles": [
            {
                "vehicle": v.get("name"),
                "temp_f": v.get("_weather", {}).get("temp_f"),
                "temp_c": v.get("_weather", {}).get("temp_c"),
                "city": v.get("location", {}).get("reverseGeo", {}).get(
                    "formattedLocation", ""),
            }
            for v in weather[:30]
            if v.get("_weather", {}).get("temp_f") is not None
        ],
    }
