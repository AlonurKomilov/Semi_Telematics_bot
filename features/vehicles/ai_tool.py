"""Vehicle AI tools — detail, engine state (rolling/stopped), search,
odometer, and state history.

Co-located with the vehicles feature (docs/FEATURES.md): the AI tools that
read vehicle state live next to the feature's service.  The account-wide
tools (``get_rolling_stopped``, ``search_vehicles``) filter to the caller's
Vehicle-Access scope via the shared ``filter_to_scope`` helper.
"""

from __future__ import annotations

from capabilities.ai.tools.registry import register_tool
from capabilities.ai.tools.scope import filter_to_scope
from features.vehicles.service import (
    get_vehicles_overview as _svc_vehicles,
    get_vehicle_detail as _svc_detail,
)
from features.vehicles.warehouse.service import get_engine_states as _svc_engine_states


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
    if account_id is None:
        return {"error": "This tool requires account context."}
    detail = await _svc_detail(account_id, vehicle)
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
        # location.speed is already mph everywhere else (aliased to
        # speedMilesPerHour with no conversion) — the *0.621371 km/h→mph factor
        # was double-converting and under-reporting by ~38%.
        "speed_mph": round(loc.get("speed", 0) or 0, 1),
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
    if account_id is None:
        return {"error": "This tool requires account context."}
    vehicles = filter_to_scope(
        await _svc_vehicles(account_id), tool_args, key="name",
    )
    engine_states = await _svc_engine_states(account_id)
    # Index engine states by vehicle ID
    state_by_id: dict[str, str] = {}
    for es in engine_states:
        vid = es.get("id", "")
        eng = es.get("engineStates", {})
        val = eng.get("value", "Off") if isinstance(eng, dict) else "Off"
        state_by_id[vid] = val
    rolling, idling, off = [], [], []
    for v in vehicles:
        vid = v.get("id", "")
        name = v.get("name", "?")
        loc = v.get("location", {})
        speed = loc.get("speed", 0) or 0  # already mph
        city = loc.get("reverseGeo", {}).get("formattedLocation", "")
        state = state_by_id.get(vid)  # None when the engine-state feed is missing
        entry = {"vehicle": name, "city": city, "speed_mph": round(speed, 1)}
        if state == "On" and speed > 0:
            rolling.append(entry)
        elif state == "On":
            idling.append(entry)
        elif state == "Off":
            off.append(entry)
        elif speed > 0:
            # No engine-state data (Samsara plan without engineStates, or a
            # transient gap) — fall back to speed so a moving truck isn't
            # mislabelled "off".  This was the cause of "the header shows
            # Moving N but the AI says all vehicles are off".
            rolling.append(entry)
        else:
            off.append(entry)
    return {
        "total": len(vehicles),
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

    if account_id is None:
        return {"error": "This tool requires account context."}
    vehicles = filter_to_scope(
        await _svc_vehicles(account_id), tool_args, key="name",
    )

    # Only fetch engine states when status filter is requested
    state_by_id: dict[str, str] = {}
    if status_filter in ("rolling", "idling", "off"):
        engine_states = await _svc_engine_states(account_id)
        for es in engine_states:
            vid = es.get("id", "")
            eng = es.get("engineStates", {})
            val = eng.get("value", "Off") if isinstance(eng, dict) else "Off"
            state_by_id[vid] = val

    results = []
    for v in vehicles:
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
            "speed_mph": round(speed, 1),  # already mph (no km/h conversion)
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


@register_tool({
    "name": "get_vehicle_history",
    "description": (
        "Get historical state snapshots for ONE vehicle over a time "
        "window.  Each row is a 5-minute snapshot of position, speed, "
        "engine state (on/off/idle), fuel %, DEF %, odometer, engine "
        "hours, fault count, driver, battery, oil, coolant, load, RPM.  "
        "Use for questions like 'when did Truck 231 last move', 'show "
        "fuel-level trend for Truck 102 this week', 'how many hours "
        "did Truck 5 drive in the last 3 days', 'what was the longest "
        "idle stretch'.  Returns newest-first; cap at 200 rows so "
        "use a narrow window for finer detail."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "vehicle_name": {
                "type": "string",
                "description": (
                    "Vehicle name or number (e.g. '231').  Required — "
                    "account-wide history isn't supported here because "
                    "the row count explodes; for account-wide questions use "
                    "get_undriven_vehicles, get_parked_vehicles, "
                    "get_account_stats, or get_efficiency_summary."
                ),
            },
            "days": {
                "type": "number",
                "description": (
                    "Lookback window in days (default 7, max 30).  "
                    "Use 1 for 'today', 7 for 'this week'."
                ),
            },
            "max_rows": {
                "type": "number",
                "description": (
                    "Max snapshots to return (default 200, max 500).  "
                    "Newest-first.  Reduce for summarization questions "
                    "where the agent only needs a sample."
                ),
            },
        },
        "required": ["vehicle_name"],
    },
})
async def get_vehicle_history(tool_args: dict, samsara_client,
                              account_id: int | None = None, db=None) -> dict:
    if not db or account_id is None:
        return {"error": "Vehicle history not available in this context"}

    vehicle = (tool_args.get("vehicle_name") or "").strip()
    if not vehicle:
        return {"error": "vehicle_name is required"}

    days = min(int(tool_args.get("days") or 7), 30)
    max_rows = min(int(tool_args.get("max_rows") or 200), 500)

    if not hasattr(db, "query_vehicle_state_history"):
        return {
            "error": (
                "Vehicle history requires the warehouse snapshot table — "
                "this account hasn't been migrated to it yet, or the "
                "snapshot ingestor hasn't run."
            ),
        }

    rows = await db.query_vehicle_state_history(
        account_id,
        vehicle_name=vehicle,
        days=days,
        max_rows=max_rows,
    )

    if not rows:
        return {
            "vehicle": vehicle, "days": days, "count": 0,
            "snapshots": [],
            "note": (
                "No snapshots found in the window.  The vehicle may not "
                "exist on this account, or the snapshot job hasn't run "
                "for this fleet yet."
            ),
        }

    # Derived summary so the agent doesn't have to roll-up itself for
    # the common "did this truck move at all" question.
    odo_vals = [
        r["odometer_mi"] for r in rows
        if r.get("odometer_mi") not in (None, 0)
    ]
    moving = [r for r in rows if (r.get("speed_mph") or 0) > 1]
    last_moved_at = moving[0]["captured_at"] if moving else None

    return {
        "vehicle": vehicle,
        "days": days,
        "count": len(rows),
        "summary": {
            "first_snapshot": rows[-1].get("captured_at"),
            "last_snapshot": rows[0].get("captured_at"),
            "miles_in_window": (
                round(max(odo_vals) - min(odo_vals), 1) if len(odo_vals) >= 2
                else None
            ),
            "moving_snapshots": len(moving),
            "last_moved_at": last_moved_at,
        },
        "snapshots": [
            {
                "captured_at": r.get("captured_at"),
                "lat": r.get("lat"),
                "lon": r.get("lon"),
                "speed_mph": r.get("speed_mph"),
                "engine_state": r.get("engine_state"),
                "fuel_pct": r.get("fuel_pct"),
                "odometer_mi": r.get("odometer_mi"),
                "engine_hours": r.get("engine_hours"),
                "fault_count": r.get("fault_count"),
                "driver_id": r.get("last_driver_id"),
            }
            for r in rows
        ],
    }


@register_tool({
    "name": "get_undriven_vehicles",
    "description": (
        "List vehicles that have NOT been driven / have not moved for at "
        "least the requested number of days, based on actual movement "
        "history (the last time the vehicle's speed was above idle).\n\n"
        "USE THIS for questions like:\n"
        "- 'what vehicle was stopped 3 days without driving'\n"
        "- \"which trucks haven't moved in a week\"\n"
        "- 'vehicles not driven in N days'\n"
        "- 'what truck hasn't been driven recently'\n"
        "Returns vehicle name, company, when it last moved, and how many "
        "days it's been stopped (longest-stopped first).\n\n"
        "This is MOVEMENT-based.  For vehicles parked specifically at an "
        "unsafe/unknown location use get_parked_vehicles; for one named "
        "vehicle's full movement history use get_vehicle_history."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "min_days": {
                "type": "number",
                "description": (
                    "Minimum days without driving (default 1).  "
                    "Use 3 for 'three days', 7 for 'a week'."
                ),
            },
        },
        "required": [],
    },
})
async def get_undriven_vehicles(tool_args: dict, samsara_client,
                                account_id: int | None = None, db=None) -> dict:
    if not db or account_id is None:
        return {"error": "Vehicle movement history not available in this context"}
    if not hasattr(db, "get_undriven_vehicles"):
        return {
            "error": (
                "Movement history requires the warehouse snapshot table — "
                "this account hasn't been migrated to it yet, or the "
                "snapshot ingestor hasn't run."
            ),
        }
    min_days = float(tool_args.get("min_days") or 1)
    rows = await db.get_undriven_vehicles(account_id, min_days=min_days)
    # Account-wide tool → filter to the caller's Vehicle-Access scope.
    rows = filter_to_scope(rows, tool_args, key="vehicle_name")
    return {
        "min_days": min_days,
        "count": len(rows),
        "vehicles": [
            {
                "vehicle": r.get("vehicle_name"),
                "company": r.get("company_code", ""),
                "last_moved": r.get("last_moved"),
                "days_stopped": r.get("days_stopped"),
                "last_seen": r.get("last_seen"),
            }
            for r in rows[:25]
        ],
    }
