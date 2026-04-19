"""Vehicle-related tools: detail, location, rolling/stopped status."""

from __future__ import annotations

from ai.tools.registry import register_tool


@register_tool({
    "name": "get_truck_detail",
    "description": (
        "Get detailed info for a specific truck: VIN, make/model/year, "
        "fuel level, DEF level, GPS location, and fault summary."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "truck_name": {
                "type": "string",
                "description": "The truck name or number",
            },
        },
        "required": ["truck_name"],
    },
})
async def get_truck_detail(tool_args: dict, samsara_client,
                           account_id: int | None = None, db=None) -> dict:
    truck = tool_args.get("truck_name", "")
    detail = await samsara_client.get_vehicle_detail(truck)
    if not detail:
        return {"result": f"Truck {truck} not found."}
    v = detail[0] if isinstance(detail, list) else detail
    loc = v.get("location", {})
    return {
        "truck": v.get("name"),
        "vin": v.get("vin"),
        "make": v.get("make"),
        "model": v.get("model"),
        "year": v.get("year"),
        "fuel_pct": v.get("fuel", {}).get("value"),
        "def_pct": v.get("def_level", {}).get("value"),
        "city": loc.get("reverseGeo", {}).get("formattedLocation", ""),
        "speed_mph": round(loc.get("speed", 0) * 0.621371, 1) if loc.get("speed") else 0,
    }


@register_tool({
    "name": "get_truck_location",
    "description": (
        "Get the current GPS location, city, and speed for a specific truck."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "truck_name": {
                "type": "string",
                "description": "The truck name or number",
            },
        },
        "required": ["truck_name"],
    },
})
async def get_truck_location(tool_args: dict, samsara_client,
                             account_id: int | None = None, db=None) -> dict:
    truck = tool_args.get("truck_name", "")
    detail = await samsara_client.get_vehicle_detail(truck)
    if not detail:
        return {"result": f"Truck {truck} not found."}
    v = detail[0] if isinstance(detail, list) else detail
    loc = v.get("location", {})
    geo = loc.get("reverseGeo", {})
    return {
        "truck": v.get("name"),
        "city": geo.get("formattedLocation", "Unknown"),
        "latitude": loc.get("latitude"),
        "longitude": loc.get("longitude"),
        "speed_mph": round(loc.get("speed", 0) * 0.621371, 1) if loc.get("speed") else 0,
        "heading": loc.get("heading"),
        "time": loc.get("time", ""),
    }


@register_tool({
    "name": "get_rolling_stopped",
    "description": (
        "Get current engine state for all trucks: which trucks are "
        "rolling (engine on + moving), idling (engine on + stopped), "
        "or off. Useful for dispatchers and fleet managers to see "
        "real-time fleet activity."
    ),
    "parameters": {
        "type": "object",
        "properties": {},
        "required": [],
    },
})
async def get_rolling_stopped(tool_args: dict, samsara_client,
                              account_id: int | None = None, db=None) -> dict:
    fleet = await samsara_client.get_fleet_overview()
    engine_states = await samsara_client.get_engine_states()
    # Index engine states by vehicle ID
    state_by_id: dict[str, str] = {}
    for es in engine_states:
        vid = es.get("id", "")
        eng = es.get("engineStates", {})
        val = eng.get("value", "Off") if isinstance(eng, dict) else "Off"
        state_by_id[vid] = val
    rolling, idling, off = [], [], []
    for v in fleet:
        vid = v.get("id", "")
        name = v.get("name", "?")
        loc = v.get("location", {})
        speed = loc.get("speed", 0) or 0
        city = loc.get("reverseGeo", {}).get("formattedLocation", "")
        state = state_by_id.get(vid, "Off")
        entry = {"truck": name, "city": city, "speed_mph": round(speed * 0.621371, 1) if speed else 0}
        if state == "On" and speed > 0:
            rolling.append(entry)
        elif state == "On":
            idling.append(entry)
        else:
            off.append(entry)
    return {
        "total": len(fleet),
        "rolling": len(rolling),
        "idling": len(idling),
        "off": len(off),
        "rolling_trucks": rolling[:30],
        "idling_trucks": idling[:30],
        "off_trucks": off[:30],
    }
