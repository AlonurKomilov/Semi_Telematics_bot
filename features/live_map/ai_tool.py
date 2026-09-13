"""Location AI tools — current GPS position (and, once migrated, weather).

Split out of the old central ``vehicle.py`` so location lives with the
``features/live_map`` feature.  ``get_vehicle_location`` is vehicle-specific
(requires a vehicle_name), so driver/scope isolation is enforced by the gate.
"""

from __future__ import annotations

from capabilities.ai.tools.registry import register_tool
from capabilities.ai.tools.scope import filter_to_scope
from features.vehicles.service import get_vehicle_detail as _svc_detail
from features.vehicles.resolve import resolve_for_tool, company_for
from features.vehicles.warehouse.service import get_fleet_weather as _svc_weather


@register_tool({
    # The current GPS fix.
    # `vehicle_scope: "live"` makes the DISPATCHER refuse this for a
    # truck that has left the fleet, rather than answering with a
    # months-old position as though it were current — see
    # capabilities/ai/tools/registry.py.
    "vehicle_scope": "live",
    "vehicle_arg": "vehicle_name",
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
            "company": {
                "type": "string",
                "description": (
                    "Optional company code (e.g. 'OSY', 'G1') — needed only "
                    "when more than one truck shares the number."
                ),
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
    resolved, err = await resolve_for_tool(db, account_id, tool_args)
    if err:
        return err
    detail = await _svc_detail(account_id, vehicle, company=company_for(resolved, tool_args))
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
        "speed_mph": round(loc.get("speed", 0) or 0, 1),  # already mph (no km/h conversion)
        "heading": loc.get("heading"),
        "time": loc.get("time", ""),
    }


# How many vehicles to name at EACH end. A token budget only — the
# summary above the lists is computed over every reading, so the cap
# cannot change what the extremes are.
HALF = 15


@register_tool({
    "name": "get_weather",
    "description": (
        "Ambient air temperature (°F) at each truck's current location. "
        "Returns a summary over every reporting vehicle (min, max, "
        "average, how many are at or below freezing, how many at or "
        "above 95°F) plus the coldest and hottest ends of the fleet. "
        "Use for 'which trucks are in extreme cold', 'which trucks are "
        "running hot', or 'how cold is it out there'."
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
    weather = filter_to_scope(await _svc_weather(account_id), tool_args, key="name",
                              external_key="id")

    def _row(v: dict) -> dict:
        return {
            "vehicle": v.get("name"),
            "temp_f": v.get("_weather", {}).get("temp_f"),
            "temp_c": v.get("_weather", {}).get("temp_c"),
            "city": v.get("location", {}).get("reverseGeo", {}).get(
                "formattedLocation", ""),
        }

    # Both ends, because the tool promises both.
    #
    # The source list is sorted COLDEST FIRST and the cap took the head
    # of it, so on any account with more than thirty reporting trucks
    # the warm end never reached the model at all — while the
    # description advertises "extreme cold or heat". Asked which trucks
    # were running hot during a heat wave, the assistant answered from
    # the thirty coldest and reported the warmest it could see, which is
    # a wrong answer indistinguishable from a right one.
    readings = [v for v in weather
                if v.get("_weather", {}).get("temp_f") is not None]
    temps = [v["_weather"]["temp_f"] for v in readings]
    coldest = [_row(v) for v in readings[:HALF]]
    hottest = [_row(v) for v in reversed(readings[-HALF:])]
    return {
        "vehicle_count": len(weather),
        "reporting_count": len(readings),
        # Computed over every reading, so the extremes are right even
        # when the two lists below are cut.
        "summary": {
            "min_f": min(temps), "max_f": max(temps),
            "avg_f": round(sum(temps) / len(temps), 1),
            "freezing_count": sum(1 for t in temps if t <= 32),
            "hot_count": sum(1 for t in temps if t >= 95),
        } if temps else {},
        "coldest": coldest,
        "hottest": hottest,
        "truncated": len(readings) > len(coldest) + len(hottest),
        "note": (
            f"The {len(coldest)} coldest and {len(hottest)} hottest of "
            f"{len(readings)} reporting vehicles"
            + (f" ({len(weather)} in scope)." if len(weather) != len(readings)
               else ".")
        ),
    }
