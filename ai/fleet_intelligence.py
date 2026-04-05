"""Fleet intelligence: diagnose, summarise, ask, and agent mode."""

from __future__ import annotations

import json
import logging

from ai.registry import MODEL_REGISTRY, _is_openai_compat
from ai.cache import _cache_key, _cache_get, _cache_put, _snapshot_hash
from ai.chat import _chat_histories, _store_history
from ai.models import (
    _account_models,
    get_current_model_name,
    get_model_for_account,
)
from ai.generation import (
    generate,
    _capture_usage,
    FLEET_ASSISTANT_SYSTEM,
    FAULT_DIAGNOSIS_SYSTEM,
    FLEET_SUMMARY_SYSTEM,
)

logger = logging.getLogger("bot.ai")


async def diagnose_faults(vehicle_name: str,
                          dtcs: list[dict],
                          lights: dict | None = None,
                          account_id: int | None = None,
                          language: str = "en") -> str:
    """AI-powered fault code diagnosis for a specific vehicle."""
    context = {
        "truck": vehicle_name,
        "check_engine_lights": lights or {},
        "active_faults": [
            {
                "spn": dtc.get("spnId"),
                "fmi": dtc.get("fmiId"),
                "description": dtc.get("spnDescription", "Unknown"),
                "severity": dtc.get("fmiDescription", "Unknown"),
                "source": dtc.get("sourceAddressName", "Unknown"),
                "count": dtc.get("count", 1),
            }
            for dtc in dtcs[:10]
        ],
    }
    prompt = (
        f"Diagnose the active faults on Truck #{vehicle_name}. "
        f"There are {len(dtcs)} active fault code(s)."
    )
    return await generate(prompt, system=FAULT_DIAGNOSIS_SYSTEM,
                          context_data=context, account_id=account_id,
                          language=language)


async def fleet_summary(fleet_data: dict,
                        account_id: int | None = None,
                        language: str = "en") -> str:
    """Generate an AI executive summary of fleet status."""
    prompt = "Generate a morning fleet status briefing from this data."
    return await generate(prompt, system=FLEET_SUMMARY_SYSTEM,
                          context_data=fleet_data, account_id=account_id,
                          language=language)


async def ask_fleet(question: str, fleet_context: dict,
                    user_id: int | None = None,
                    account_id: int | None = None,
                    language: str = "en") -> str:
    """Answer a natural-language question about the fleet."""
    return await generate(question, system=FLEET_ASSISTANT_SYSTEM,
                          context_data=fleet_context, user_id=user_id,
                          account_id=account_id, language=language)


# ── Function-Calling Agent ───────────────────────────────────────

FLEET_TOOLS = [
    {
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
    },
    {
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
    },
    {
        "name": "get_driver_efficiency",
        "description": (
            "Get driver efficiency stats for the last N days: MPG, idle %, "
            "miles driven, eco-driving score, overspeed minutes."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "days": {
                    "type": "integer",
                    "description": "Number of days to look back (default 7)",
                },
            },
            "required": [],
        },
    },
    {
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
    },
    {
        "name": "get_low_fuel_vehicles",
        "description": (
            "Get trucks with fuel level at or below a threshold percentage."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "threshold": {
                    "type": "integer",
                    "description": "Fuel percentage threshold (default 20)",
                },
            },
            "required": [],
        },
    },
    {
        "name": "get_vehicle_health",
        "description": (
            "Get health diagnostics for all vehicles: battery voltage, "
            "coolant temperature, oil pressure, DEF level, engine RPM, "
            "seatbelt status, and health alerts (low battery, high coolant, etc)."
        ),
        "parameters": {
            "type": "object",
            "properties": {},
            "required": [],
        },
    },
    {
        "name": "get_fleet_weather",
        "description": (
            "Get ambient air temperature (°F) for each truck's current location. "
            "Useful for identifying trucks in extreme cold or heat conditions."
        ),
        "parameters": {
            "type": "object",
            "properties": {},
            "required": [],
        },
    },
    {
        "name": "get_fleet_efficiency",
        "description": (
            "Get engine hours, driving hours, idle hours, idle percentage, "
            "and miles driven per truck over the last N days. Also includes "
            "driver fuel efficiency (MPG) and eco-driving score when available."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "days": {
                    "type": "integer",
                    "description": "Number of days to look back (default 7)",
                },
            },
            "required": [],
        },
    },
    {
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
    },
    {
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
    },
    {
        "name": "count_fleet_stats",
        "description": (
            "Get quick fleet-wide counts: total active trucks, trucks with "
            "faults, trucks with critical faults, low fuel trucks, and "
            "trucks with health alerts. Fast overview without full details."
        ),
        "parameters": {
            "type": "object",
            "properties": {},
            "required": [],
        },
    },
    # ── Safety Events ────────────────────────────────────────────
    {
        "name": "get_truck_events",
        "description": (
            "Get safety events (harsh braking, harsh acceleration, crash, "
            "speeding, rolling stop, distracted driving, etc.) for a specific "
            "truck over a given number of days. Always state the time period "
            "you checked in your answer."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "truck_name": {
                    "type": "string",
                    "description": "The truck name or number",
                },
                "days": {
                    "type": "integer",
                    "description": "Number of days to look back (1-30, default 7)",
                },
            },
            "required": ["truck_name"],
        },
    },
    {
        "name": "get_fleet_events_summary",
        "description": (
            "Get a fleet-wide safety event summary: total counts by event type "
            "(harsh brake, crash, speeding, etc.), top drivers by event count, "
            "and the 10 most severe events by g-force. Always state the time "
            "period you checked in your answer."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "days": {
                    "type": "integer",
                    "description": "Number of days to look back (1-30, default 7)",
                },
            },
            "required": [],
        },
    },
    # ── Maintenance ──────────────────────────────────────────────
    {
        "name": "get_truck_maintenance",
        "description": (
            "Get pending and overdue maintenance tasks for a specific truck. "
            "Includes task type (oil change, tires, brakes, DOT inspection, "
            "DPF regen, DEF refill, etc.), due date, due mileage, and status."
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
    },
    {
        "name": "get_maintenance_summary",
        "description": (
            "Get account-wide maintenance summary: total pending tasks, "
            "overdue tasks, breakdown by task type, and the most urgent "
            "overdue items."
        ),
        "parameters": {
            "type": "object",
            "properties": {},
            "required": [],
        },
    },
    # ── Fuel Costs ───────────────────────────────────────────────
    {
        "name": "get_truck_fuel_costs",
        "description": (
            "Get fuel fill-up history and cost data for a specific truck: "
            "recent fill-ups (gallons, price/gallon, total cost, odometer), "
            "totals and average cost per gallon."
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
    },
    {
        "name": "get_fuel_cost_summary",
        "description": (
            "Get account-wide fuel cost summary: per-vehicle totals "
            "(gallons, total cost, average price per gallon, cost per mile)."
        ),
        "parameters": {
            "type": "object",
            "properties": {},
            "required": [],
        },
    },
    # ── Camera Check ─────────────────────────────────────────────
    {
        "name": "check_truck_camera",
        "description": (
            "Check the dashcam status for a specific truck: captures the "
            "latest camera image and analyzes it for obstruction, alignment, "
            "and image quality. This is per-truck only — for a full fleet "
            "camera check, direct the user to the Camera Check feature "
            "in the main menu."
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
    },
]


async def _execute_tool_call(tool_name: str, tool_args: dict,
                             samsara_client,
                             account_id: int | None = None,
                             db=None) -> dict:
    """Execute a single tool call and return the result as a dict."""
    try:
        if tool_name == "get_truck_faults":
            truck = tool_args.get("truck_name", "")
            faulted, _ = await samsara_client.get_vehicles_with_faults()
            matches = [
                v for v in faulted
                if v.get("name", "").lower() == truck.lower()
            ]
            if not matches:
                return {"result": f"No active faults found for truck {truck}."}
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

        elif tool_name == "get_truck_detail":
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

        elif tool_name == "get_driver_efficiency":
            days = tool_args.get("days", 7)
            drivers = await samsara_client.get_driver_efficiency(days=days)
            return {
                "period_days": days,
                "drivers": [
                    {
                        "name": d["driver_name"],
                        "miles": d.get("_miles"),
                        "mpg": d.get("_mpg"),
                        "idle_pct": d.get("_idle_pct"),
                        "drive_hours": d.get("_drive_h"),
                        "green_pct": d.get("_green_pct"),
                        "overspeed_min": d.get("_overspeed_min"),
                    }
                    for d in drivers[:20]
                ],
            }

        elif tool_name == "get_critical_faults":
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

        elif tool_name == "get_low_fuel_vehicles":
            threshold = tool_args.get("threshold", 20)
            low = await samsara_client.get_low_fuel_vehicles(threshold=threshold)
            return {
                "threshold_pct": threshold,
                "count": len(low),
                "vehicles": [
                    {
                        "truck": v.get("name"),
                        "fuel_pct": v.get("_fuel_pct"),
                    }
                    for v in low[:20]
                ],
            }

        elif tool_name == "get_vehicle_health":
            health = await samsara_client.get_vehicle_health()
            return {
                "total_vehicles": len(health),
                "vehicles_with_alerts": sum(1 for v in health if v.get("_health_alerts")),
                "vehicles": [
                    {
                        "truck": v.get("name"),
                        "battery_v": v.get("_health", {}).get("battery_v"),
                        "coolant_c": v.get("_health", {}).get("coolant_c"),
                        "oil_psi": v.get("_health", {}).get("oil_psi"),
                        "def_pct": v.get("_health", {}).get("def_pct"),
                        "rpm": v.get("_health", {}).get("rpm"),
                        "engine_on": v.get("_health", {}).get("engine_on"),
                        "seatbelt": v.get("_health", {}).get("seatbelt"),
                        "alerts": v.get("_health_alerts", []),
                    }
                    for v in health[:30]
                ],
            }

        elif tool_name == "get_fleet_weather":
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

        elif tool_name == "get_fleet_efficiency":
            days = tool_args.get("days", 7)
            eff = await samsara_client.get_fleet_efficiency(days=days)
            return {
                "period_days": days,
                "truck_count": len(eff),
                "trucks": [
                    {
                        "truck": v.get("name"),
                        "engine_hours": v.get("_engine_hours"),
                        "driving_hours": v.get("_driving_hours"),
                        "idle_hours": v.get("_idle_hours"),
                        "idle_pct": v.get("_idle_pct"),
                        "miles": v.get("_miles"),
                        "driver": v.get("_driver_name"),
                        "mpg": v.get("_mpg"),
                        "green_pct": v.get("_green_pct"),
                    }
                    for v in eff[:30]
                ],
            }

        elif tool_name == "get_truck_location":
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

        elif tool_name == "get_geofences":
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

        elif tool_name == "count_fleet_stats":
            fleet = await samsara_client.get_fleet_overview()
            faulted = []
            critical = []
            for v in fleet:
                fc = v.get("fault_codes", {})
                j1939 = fc.get("j1939", {})
                dtcs = j1939.get("diagnosticTroubleCodes", [])
                cel = j1939.get("checkEngineLights", {})
                if dtcs:
                    faulted.append(v)
                if (cel.get("stopIsOn") or cel.get("protectIsOn")
                        or cel.get("emissionsIsOn")):
                    critical.append(v)
            low_fuel = [v for v in fleet
                        if (v.get("fuel", {}).get("value") or 100) <= 20]
            try:
                health = await samsara_client.get_vehicle_health()
                alerts = sum(1 for v in health if v.get("_health_alerts"))
            except Exception:
                alerts = 0
            return {
                "total_active_trucks": len(fleet),
                "trucks_with_faults": len(faulted),
                "trucks_critical": len(critical),
                "trucks_low_fuel": len(low_fuel),
                "trucks_with_health_alerts": alerts,
            }

        # ── Safety Events ────────────────────────────────────────
        elif tool_name == "get_truck_events":
            truck = tool_args.get("truck_name", "")
            days = min(max(tool_args.get("days", 7), 1), 30)
            events = await samsara_client.get_events(days=days)
            truck_events = [
                e for e in events
                if e.get("vehicle_name", "").lower() == truck.lower()
            ]
            return {
                "truck": truck,
                "period_days": days,
                "total_events": len(truck_events),
                "events": [
                    {
                        "type": e.get("event_name", "Unknown"),
                        "driver": e.get("driver_name", "Unassigned"),
                        "time": e.get("time", ""),
                        "g_force": e.get("g_force", 0),
                        "coaching_state": e.get("coaching_state", ""),
                    }
                    for e in truck_events[:20]
                ],
            }

        elif tool_name == "get_fleet_events_summary":
            days = min(max(tool_args.get("days", 7), 1), 30)
            events = await samsara_client.get_events(days=days)
            # Counts by type
            by_type: dict[str, int] = {}
            by_driver: dict[str, int] = {}
            for e in events:
                etype = e.get("event_name", "Unknown")
                by_type[etype] = by_type.get(etype, 0) + 1
                dname = e.get("driver_name", "Unassigned")
                by_driver[dname] = by_driver.get(dname, 0) + 1
            # Top drivers sorted by count
            top_drivers = sorted(by_driver.items(), key=lambda x: x[1], reverse=True)[:10]
            # Most severe by g-force
            severe = sorted(events, key=lambda e: e.get("g_force", 0), reverse=True)[:10]
            return {
                "period_days": days,
                "total_events": len(events),
                "events_by_type": by_type,
                "top_drivers_by_events": [
                    {"driver": d, "count": c} for d, c in top_drivers
                ],
                "most_severe": [
                    {
                        "type": e.get("event_name", "Unknown"),
                        "truck": e.get("vehicle_name", "?"),
                        "driver": e.get("driver_name", "Unassigned"),
                        "g_force": e.get("g_force", 0),
                        "time": e.get("time", ""),
                    }
                    for e in severe
                ],
            }

        # ── Maintenance ──────────────────────────────────────────
        elif tool_name == "get_truck_maintenance":
            truck = tool_args.get("truck_name", "")
            if not db or account_id is None:
                return {"error": "Maintenance data not available in this context"}
            tasks = await db.get_maintenance_tasks(account_id, vehicle_name=truck)
            active = [t for t in tasks if t.get("status") in ("pending", "overdue")]
            return {
                "truck": truck,
                "total_tasks": len(active),
                "tasks": [
                    {
                        "type": t.get("task_type", "custom"),
                        "description": t.get("description", ""),
                        "status": t.get("status", ""),
                        "due_date": t.get("due_date"),
                        "due_miles": t.get("due_miles"),
                        "created_at": t.get("created_at", ""),
                    }
                    for t in active[:15]
                ],
            }

        elif tool_name == "get_maintenance_summary":
            if not db or account_id is None:
                return {"error": "Maintenance data not available in this context"}
            tasks = await db.get_maintenance_tasks(account_id)
            pending = [t for t in tasks if t.get("status") == "pending"]
            overdue = [t for t in tasks if t.get("status") == "overdue"]
            # By type
            maint_by_type: dict[str, int] = {}
            for t in pending + overdue:
                tt = t.get("task_type", "custom")
                maint_by_type[tt] = maint_by_type.get(tt, 0) + 1
            return {
                "total_pending": len(pending),
                "total_overdue": len(overdue),
                "tasks_by_type": maint_by_type,
                "overdue_tasks": [
                    {
                        "truck": t.get("vehicle_name", "?"),
                        "type": t.get("task_type", "custom"),
                        "description": t.get("description", ""),
                        "due_date": t.get("due_date"),
                        "due_miles": t.get("due_miles"),
                    }
                    for t in overdue[:10]
                ],
            }

        # ── Fuel Costs ───────────────────────────────────────────
        elif tool_name == "get_truck_fuel_costs":
            truck = tool_args.get("truck_name", "")
            if not db or account_id is None:
                return {"error": "Fuel cost data not available in this context"}
            entries = await db.get_fuel_entries(account_id, vehicle_name=truck, limit=20)
            if not entries:
                return {"truck": truck, "result": "No fuel entries recorded for this truck."}
            total_gal = sum(e.get("gallons", 0) for e in entries)
            total_cost = sum(e.get("total_cost", 0) for e in entries)
            return {
                "truck": truck,
                "entry_count": len(entries),
                "total_gallons": round(total_gal, 1),
                "total_cost": round(total_cost, 2),
                "avg_price_per_gallon": round(total_cost / total_gal, 3) if total_gal else 0,
                "recent_fills": [
                    {
                        "date": e.get("date", ""),
                        "gallons": e.get("gallons"),
                        "price_per_gallon": e.get("price_per_gallon"),
                        "total_cost": e.get("total_cost"),
                        "odometer": e.get("odometer_miles"),
                    }
                    for e in entries[:10]
                ],
            }

        elif tool_name == "get_fuel_cost_summary":
            if not db or account_id is None:
                return {"error": "Fuel cost data not available in this context"}
            summary = await db.get_fuel_summary(account_id)
            if not summary:
                return {"result": "No fuel entries recorded for this account."}
            result_items = []
            for s in summary[:20]:
                first_odo = s.get("first_odo") or 0
                last_odo = s.get("last_odo") or 0
                miles = last_odo - first_odo if last_odo > first_odo else 0
                total_cost = s.get("total_cost") or 0
                cost_per_mile = round(total_cost / miles, 3) if miles > 0 else None
                result_items.append({
                    "truck": s.get("vehicle_name", "?"),
                    "entries": s.get("entries", 0),
                    "total_gallons": round(s.get("total_gallons") or 0, 1),
                    "total_cost": round(total_cost, 2),
                    "avg_price_per_gallon": round(s.get("avg_price") or 0, 3),
                    "cost_per_mile": cost_per_mile,
                })
            return {"vehicles": result_items}

        # ── Camera Check ─────────────────────────────────────────
        elif tool_name == "check_truck_camera":
            truck = tool_args.get("truck_name", "")
            try:
                from samsara_client import SamsaraClient
                from ai.vision import analyze_camera_image
                # Get snapshots for the specific truck
                from bot.config import db as _bot_db
                companies = await _bot_db.get_account_companies(account_id) if account_id else []
                snap = None
                for co in companies:
                    client = SamsaraClient(
                        api_key=co.samsara_api_key,
                        active_days=co.active_days,
                    )
                    try:
                        snaps = await client.get_dashcam_snapshots(days=3)
                        match = [
                            s for s in snaps
                            if s["vehicle_name"].lower() == truck.lower()
                        ]
                        if match:
                            snap = match[0]
                            break
                    finally:
                        await client.close()
                if not snap or not snap.get("image_bytes"):
                    return {"truck": truck, "result": "No recent camera image found for this truck."}
                analysis = await analyze_camera_image(
                    snap["image_bytes"],
                    vehicle_name=truck,
                    account_id=account_id,
                )
                return {
                    "truck": truck,
                    "camera_type": snap.get("camera_type", "unknown"),
                    "event_time": snap.get("event_time", ""),
                    "analysis": analysis,
                }
            except Exception as e:
                logger.error(f"Camera check tool failed for {truck}: {e}")
                return {"truck": truck, "error": f"Camera check failed: {e}"}

        else:
            return {"error": f"Unknown tool: {tool_name}"}

    except Exception as e:
        logger.error(f"Tool {tool_name} failed: {e}")
        return {"error": str(e)}


_cached_tools = None


def _invalidate_tool_cache():
    """Clear cached tool objects — call after modifying FLEET_TOOLS."""
    global _cached_tools
    _cached_tools = None


def _get_cached_tools():
    """Return cached Vertex AI Tool objects, building them once on first call."""
    global _cached_tools
    if _cached_tools is not None:
        return _cached_tools
    from vertexai.generative_models import Tool, FunctionDeclaration
    func_decls = [FunctionDeclaration(**td) for td in FLEET_TOOLS]
    _cached_tools = [Tool(function_declarations=func_decls)]
    return _cached_tools


async def ask_fleet_agent(question: str, fleet_context: dict,
                          samsara_client,
                          user_id: int | None = None,
                          account_id: int | None = None,
                          db=None) -> dict:
    """Agent-mode: AI can call Samsara tools to answer questions."""
    import asyncio

    try:
        from vertexai.generative_models import (
            GenerativeModel, GenerationConfig, Tool, FunctionDeclaration,
            Part, Content,
        )
    except ImportError:
        text = await ask_fleet(question, fleet_context, user_id=user_id,
                               account_id=account_id)
        return {"text": text, "tool_results": []}

    cur_model_name = get_current_model_name()
    if account_id is not None and account_id in _account_models:
        cur_model_name = _account_models[account_id][0]
    if _is_openai_compat(cur_model_name):
        text = await ask_fleet(question, fleet_context, user_id=user_id,
                               account_id=account_id)
        return {"text": text, "tool_results": []}

    model, cur_model_name, _ = get_model_for_account(account_id)

    has_history = bool(user_id and user_id in _chat_histories)
    snap_h = _snapshot_hash(fleet_context) if not has_history else ""
    ck = _cache_key(question, snap_h, cur_model_name) if not has_history else ""
    if not has_history and ck:
        cached = _cache_get(ck)
        if cached is not None:
            logger.debug("Cache hit for ask_fleet_agent()")
            if user_id is not None:
                _store_history(user_id, question, cached)
            return {"text": cached, "tool_results": []}

    tools = _get_cached_tools()

    parts = [FLEET_ASSISTANT_SYSTEM, "\n\n"]
    if fleet_context:
        data_str = json.dumps(fleet_context, separators=(',', ':'), default=str)
        if len(data_str) > 30000:
            data_str = data_str[:30000] + "\n... (truncated)"
        parts.append(f"Fleet snapshot:\n```\n{data_str}\n```\n\n")

    parts.append(
        "You have access to tools that can fetch live data from Samsara. "
        "Use them when the user asks about specific trucks, faults, fuel, "
        "driver efficiency, safety events, maintenance, fuel costs, or cameras. "
        "For safety events, always state the time period checked. "
        "For camera checks, only check one truck at a time — for fleet-wide "
        "camera checks, direct the user to the Camera Check menu. "
        "If the snapshot already has enough info, answer directly without "
        "calling tools.\n\n"
    )

    if user_id and user_id in _chat_histories:
        history = _chat_histories[user_id]
        parts.append("Previous conversation:\n")
        for entry in history:
            parts.append(f"{entry['role']}: {entry['text']}\n")
        parts.append("\n")

    parts.append(f"User question: {question}")
    full_prompt = "".join(parts)
    tool_results: list[dict] = []

    max_retries = 2
    last_exc = None

    for attempt in range(max_retries + 1):
        try:
            response = await asyncio.to_thread(
                model.generate_content, full_prompt, tools=tools,
            )

            for _round in range(3):
                if not response.candidates:
                    return {
                        "text": (
                            "I couldn't generate a response for that question. "
                            "Please try rephrasing it."
                        ),
                        "tool_results": tool_results,
                    }

                candidate = response.candidates[0]
                part = candidate.content.parts[0]

                if part.text:
                    text = part.text.strip()
                    text = text.replace("**", "").replace("##", "").replace("# ", "")
                    _capture_usage(response)
                    if user_id is not None:
                        _store_history(user_id, question, text)
                    if ck and not has_history:
                        _cache_put(ck, text)
                    return {"text": text, "tool_results": tool_results}

                if part.function_call:
                    fc = part.function_call
                    tool_name = fc.name
                    tool_args = dict(fc.args) if fc.args else {}

                    logger.info(f"AI agent calling tool: {tool_name}({tool_args})")
                    result = await _execute_tool_call(
                        tool_name, tool_args, samsara_client,
                        account_id=account_id, db=db,
                    )
                    tool_results.append({"tool": tool_name, "args": tool_args, "data": result})

                    fn_response = Part.from_function_response(
                        name=tool_name,
                        response={"result": result},
                    )
                    response = await asyncio.to_thread(
                        model.generate_content,
                        [
                            Content(parts=[Part.from_text(full_prompt)], role="user"),
                            candidate.content,
                            Content(parts=[fn_response], role="function"),
                        ],
                        tools=tools,
                    )
                else:
                    break

            if response.candidates and response.candidates[0].content.parts:
                try:
                    text = response.text.strip()
                except ValueError:
                    parts_list = response.candidates[0].content.parts
                    text = ''.join(getattr(p, 'text', '') for p in parts_list).strip()
                text = text.replace("**", "").replace("##", "").replace("# ", "")
                _capture_usage(response)
                if user_id is not None:
                    _store_history(user_id, question, text)
                if ck and not has_history:
                    _cache_put(ck, text)
                return {"text": text, "tool_results": tool_results}

            text = await ask_fleet(question, fleet_context, user_id=user_id,
                                   account_id=account_id)
            return {"text": text, "tool_results": tool_results}

        except Exception as e:
            last_exc = e
            err_str = str(e).lower()
            if ('429' in err_str or 'resource exhausted' in err_str) and attempt < max_retries:
                wait = 2 ** attempt
                logger.warning(
                    f"Agent rate limited (attempt {attempt+1}/{max_retries+1}), "
                    f"retrying in {wait}s"
                )
                await asyncio.sleep(wait)
                continue
            logger.warning(f"Agent mode failed, falling back: {e}")
            text = await ask_fleet(question, fleet_context, user_id=user_id,
                                   account_id=account_id)
            return {"text": text, "tool_results": tool_results}

    logger.warning(f"Agent exhausted retries, falling back: {last_exc}")
    text = await ask_fleet(question, fleet_context, user_id=user_id,
                           account_id=account_id)
    return {"text": text, "tool_results": tool_results}
