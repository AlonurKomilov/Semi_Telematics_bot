"""Reporting transformers — pure functions that reshape raw Samsara data
into the canonical API / bot response shapes.

These are the Single Source of Truth for all report data-shaping so both
the API routes and the bot handlers produce identical output.
"""

from __future__ import annotations


def check_light_severity(lights: dict) -> str:
    """Map J1939 check-engine lights to a severity string."""
    if lights.get("stopIsOn"):
        return "critical"
    if lights.get("protectIsOn") or lights.get("emissionsIsOn"):
        return "warning"
    if lights.get("warningIsOn"):
        return "caution"
    return "ok"


def simplify_fault(v: dict) -> dict:
    """Flatten a vehicle's faults into a summary dict."""
    codes = v.get("fault_codes", {})
    j1939 = codes.get("j1939", {})
    dtcs = j1939.get("diagnosticTroubleCodes", [])
    lights = j1939.get("checkEngineLights", {})
    return {
        "vehicle_name": v.get("name", ""),
        "company": v.get("_org", ""),
        "dtc_count": len(dtcs),
        "dtcs": [
            {
                "spn": d.get("spnId"),
                "spn_desc": d.get("spnDescription", ""),
                "fmi": d.get("fmiCode"),
                "fmi_desc": d.get("fmiDescription", ""),
                "occurrences": d.get("occurrences", 0),
            }
            for d in dtcs[:20]  # cap per vehicle
        ],
        "lights": lights,
        "severity": check_light_severity(lights),
        "fault_time": codes.get("time", ""),
    }


def simplify_fuel(v: dict) -> dict:
    """Flatten a vehicle's fuel & DEF levels into a summary dict."""
    fuel = v.get("fuel", {})
    de = v.get("def_level", {})
    fuel_pct = fuel.get("value")
    def_pct = de.get("value")
    return {
        "vehicle_name": v.get("name", ""),
        "company": v.get("_org", ""),
        "fuel_pct": round(fuel_pct, 1) if fuel_pct is not None else None,
        "def_pct": round(def_pct, 1) if def_pct is not None else None,
        "fuel_time": fuel.get("time", ""),
        "def_time": de.get("time", ""),
    }


def simplify_health(v: dict) -> dict:
    """Flatten a vehicle's live health telemetry into a summary dict."""
    h = v.get("_health", {})
    return {
        "vehicle_name": v.get("name", ""),
        "company": v.get("_org", ""),
        "battery_v": h.get("battery_v"),
        "oil_psi": h.get("oil_psi"),
        "coolant_c": h.get("coolant_c"),
        "def_pct": h.get("def_pct"),
        "load_pct": h.get("load_pct"),
        "seatbelt": h.get("seatbelt"),
        "rpm": h.get("rpm"),
        "engine_on": h.get("engine_on"),
        "alerts": v.get("_health_alerts", []),
    }


def simplify_efficiency(v: dict) -> dict:
    """Flatten a driver/vehicle efficiency record into a summary dict."""
    return {
        "vehicle_name": v.get("name", v.get("vehicle_name", "")),
        "company": v.get("_org", ""),
        "driver_name": v.get("driver_name", ""),
        "miles": round(v.get("_miles", 0), 1),
        "mpg": round(v.get("_mpg", 0), 1),
        "drive_hours": round(v.get("_drive_h", 0), 1),
        "idle_hours": round(v.get("_idle_h", 0), 1),
        "drive_pct": round(v.get("_drive_pct", 0), 1),
        "idle_pct": round(v.get("_idle_pct", 0), 1),
        "eco_pct": round(v.get("_green_pct", 0), 1),
        "overspeed_min": round(v.get("_overspeed_min", 0), 1),
    }
