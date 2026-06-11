"""Geo tools: geofences and fleet weather."""

from __future__ import annotations

from capabilities.ai.tools.registry import register_tool
from capabilities.telemetry.service import get_fleet_weather as _svc_weather


@register_tool({
    "name": "get_geofences",
    "description": (
        "Get all geofence zones defined in Samsara: name, address, "
        "type (circle/polygon), and coordinates."
    ),
    "parameters": {
        "type": "object",
        "properties": {},
        "required": [],
    },
})
async def get_geofences(tool_args: dict, samsara_client,
                        account_id: int | None = None, db=None) -> dict:
    if account_id is None:
        return {"error": "This tool requires account context."}
    from features.geofencing.service import get_geofences as _svc_geofences
    fences = await _svc_geofences(account_id)
    return {
        "count": len(fences),
        "geofences": [
            {
                "name": f.get("name"),
                "address": (f.get("formattedAddress")
                            or f.get("address", {}).get("formattedAddress", "")),
            }
            for f in fences[:30]
        ],
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
    weather = await _svc_weather(account_id)
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
