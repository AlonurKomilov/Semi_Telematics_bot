"""Geo tools: geofences and fleet weather."""

from __future__ import annotations

from ai.tools.registry import register_tool


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
    fences = await samsara_client.get_geofences()
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
    weather = await samsara_client.get_fleet_weather()
    return {
        "truck_count": len(weather),
        "trucks": [
            {
                "truck": v.get("name"),
                "temp_f": v.get("_weather", {}).get("temp_f"),
                "temp_c": v.get("_weather", {}).get("temp_c"),
                "city": v.get("location", {}).get("reverseGeo", {}).get(
                    "formattedLocation", ""),
            }
            for v in weather[:30]
            if v.get("_weather", {}).get("temp_f") is not None
        ],
    }
