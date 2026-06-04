"""Reporting transformers — pure functions that reshape raw Samsara data
into the canonical API / bot response shapes.

These are the Single Source of Truth for all report data-shaping so both
the API routes and the bot handlers produce identical output.
"""

from __future__ import annotations

from typing import Optional


def _round_or_none(value, ndigits: int = 1) -> Optional[float]:
    """``round`` that survives ``None`` cleanly.

    ``dict.get(key, default)`` only returns *default* when the key is
    *missing* — a key that's present-but-``None`` (which the Samsara
    client deliberately produces for vehicles without a paired driver,
    see ``adapters/samsara/client.py:1258``) propagates through and
    blows up ``round(None, 1)``.  This helper coerces None to ``None``
    in the output dict so the API serialises it as JSON ``null`` rather
    than crashing the request.
    """
    if value is None:
        return None
    try:
        return round(value, ndigits)
    except (TypeError, ValueError):
        return None


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
    """Flatten a driver/vehicle efficiency record into a summary dict.

    Vehicles without a paired driver have ``_miles``/``_mpg``/etc.
    explicitly set to ``None`` by the Samsara client (see
    ``adapters/samsara/client.py:1258``).  Use ``_round_or_none`` so
    those nulls round-trip as JSON ``null`` instead of crashing the
    endpoint with ``round(None, 1)`` → ``TypeError`` (the 500 on
    ``/api/reports/efficiency`` from 2026-06).
    """
    return {
        "vehicle_name": v.get("name", v.get("vehicle_name", "")),
        "company": v.get("_org", ""),
        "driver_name": v.get("driver_name", ""),
        "miles":        _round_or_none(v.get("_miles")),
        "mpg":          _round_or_none(v.get("_mpg")),
        "drive_hours":  _round_or_none(v.get("_drive_h")),
        "idle_hours":   _round_or_none(v.get("_idle_h")),
        "drive_pct":    _round_or_none(v.get("_drive_pct")),
        "idle_pct":     _round_or_none(v.get("_idle_pct")),
        "eco_pct":      _round_or_none(v.get("_green_pct")),
        "overspeed_min": _round_or_none(v.get("_overspeed_min")),
    }
