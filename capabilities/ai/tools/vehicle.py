"""Vehicle-related tools: detail, location, rolling/stopped status."""

from __future__ import annotations

from capabilities.ai.tools.registry import register_tool
from capabilities.vehicles.service import (
    get_fleet_overview as _svc_fleet,
    get_vehicle_detail as _svc_detail,
)


@register_tool({
    "name": "get_vehicle_detail",
    "description": (
        "Get detailed info for a specific vehicle: VIN, make/model/year, "
        "fuel level, DEF level, GPS location, and fault summary."
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
async def get_vehicle_detail(tool_args: dict, samsara_client,
                             account_id: int | None = None, db=None) -> dict:
    vehicle = tool_args.get("vehicle_name", "")
    if account_id is not None:
        detail = await _svc_detail(account_id, vehicle)
    else:
        detail = await samsara_client.get_vehicle_detail(vehicle)
    if not detail:
        return {"result": f"Vehicle {vehicle} not found."}
    v = detail[0] if isinstance(detail, list) else detail
    loc = v.get("location", {})
    return {
        "vehicle": v.get("name"),
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
    if account_id is not None:
        detail = await _svc_detail(account_id, vehicle)
    else:
        detail = await samsara_client.get_vehicle_detail(vehicle)
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
    "name": "get_rolling_stopped",
    "description": (
        "Get current engine state for all vehicles: which vehicles are "
        "rolling (engine on + moving), idling (engine on + stopped), "
        "or off. Useful for owners, admins, and dispatchers to see "
        "real-time activity."
    ),
    "parameters": {
        "type": "object",
        "properties": {},
        "required": [],
    },
})
async def get_rolling_stopped(tool_args: dict, samsara_client,
                              account_id: int | None = None, db=None) -> dict:
    if account_id is not None:
        fleet = await _svc_fleet(account_id)
    else:
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
        entry = {"vehicle": name, "city": city, "speed_mph": round(speed * 0.621371, 1) if speed else 0}
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
        "rolling_vehicles": rolling[:30],
        "idling_vehicles": idling[:30],
        "off_vehicles": off[:30],
    }


@register_tool({
    "name": "search_vehicles",
    "description": (
        "Search and filter vehicles by city/location keyword or engine status "
        "(rolling/idling/off). Optionally filter by a name substring (e.g. company prefix). "
        "All filters are optional and combinable. "
        "Useful for 'which trucks are in Dallas?', 'find trucks near Cincinnati', "
        "'show trucks close to Chicago', 'trucks around Houston', or 'show me all rolling PTG vehicles'. "
        "When the user asks for trucks near, close to, around, or in a city, use the city parameter "
        "with just the city name — it does a substring match against each truck's current location."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "name_contains": {
                "type": "string",
                "description": "Filter vehicles whose name contains this string (case-insensitive), e.g. 'PTG' or '2'.",
            },
            "city": {
                "type": "string",
                "description": "City or location keyword to filter by (case-insensitive substring match).",
            },
            "status": {
                "type": "string",
                "description": "Engine status filter: 'rolling', 'idling', or 'off'.",
            },
        },
        "required": [],
    },
})
async def search_vehicles(tool_args: dict, samsara_client,
                          account_id: int | None = None, db=None) -> dict:
    name_filter = (tool_args.get("name_contains") or "").strip().lower()
    city_filter = (tool_args.get("city") or "").strip().lower()
    status_filter = (tool_args.get("status") or "").strip().lower()

    if account_id is not None:
        fleet = await _svc_fleet(account_id)
    else:
        fleet = await samsara_client.get_fleet_overview()

    # Only fetch engine states when status filter is requested
    state_by_id: dict[str, str] = {}
    if status_filter in ("rolling", "idling", "off"):
        engine_states = await samsara_client.get_engine_states()
        for es in engine_states:
            vid = es.get("id", "")
            eng = es.get("engineStates", {})
            val = eng.get("value", "Off") if isinstance(eng, dict) else "Off"
            state_by_id[vid] = val

    results = []
    for v in fleet:
        name = v.get("name", "")
        loc = v.get("location", {})
        city_str = loc.get("reverseGeo", {}).get("formattedLocation", "").lower()
        speed = loc.get("speed", 0) or 0

        if name_filter and name_filter not in name.lower():
            continue
        if city_filter and city_filter not in city_str:
            continue
        if status_filter in ("rolling", "idling", "off"):
            v_state = state_by_id.get(v.get("id", ""), "Off")
            if status_filter == "rolling" and not (v_state == "On" and speed > 0):
                continue
            if status_filter == "idling" and not (v_state == "On" and speed == 0):
                continue
            if status_filter == "off" and v_state != "Off":
                continue

        results.append({
            "vehicle": name,
            "city": loc.get("reverseGeo", {}).get("formattedLocation", ""),
            "fuel_pct": v.get("fuel", {}).get("value"),
            "speed_mph": round(speed * 0.621371, 1) if speed else 0,
        })

    return {
        "matched": len(results),
        "filters": {
            "name_contains": name_filter or None,
            "city": city_filter or None,
            "status": status_filter or None,
        },
        "vehicles": results[:30],
    }
