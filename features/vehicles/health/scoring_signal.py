"""Vehicle-health signal — point-in-time aggregation of active faults
and health alerts (low DEF, low battery, high coolant, etc.) scoped to
the trucks each driver actually drove in the window.

Unlike maintenance/cameras/fuel which are time-series, this signal is
*current-state*: we ask "right now, do this driver's trucks have any
active issues?" — that's how the underlying Samsara endpoints already
behave and matches how operators read the dashboard.

Inputs:
  *health_records* — output of ``capabilities.warehouse.telemetry.service
    .get_vehicle_health`` — vehicles with ``_health_alerts`` list.
  *faulted_vehicles* — first element of
    ``capabilities.warehouse.telemetry.service.get_vehicles_with_faults`` —
    vehicles with active DTCs.
"""

from __future__ import annotations


# Severity of individual health-alert codes. Used to split into
# "critical" vs "minor" buckets so rules can weight them differently.
_CRITICAL_HEALTH_ALERTS = {
    "low_oil_pressure",
    "high_coolant_temp",
    "low_battery",
}
_MINOR_HEALTH_ALERTS = {
    "low_def",
    "seatbelt_unbuckled",
}


def aggregate(
    health_records: list[dict],
    faulted_vehicles: list[dict],
    driver_vehicle_names: list[str],
) -> dict:
    """Return per-driver vehicle-health signal slice.

    Empty truck list ⇒ zero counts (driver had no Samsara-reported
    activity, so we don't credit/penalise them with someone else's
    truck's state).
    """
    if not driver_vehicle_names:
        return {
            "vehicles_assigned":      0,
            "vehicles_with_faults":   0,
            "active_dtcs":          0,
            "critical_alerts":      0,
            "minor_alerts":         0,
            "vehicles_clean":         0,
        }

    needles = {n.lower() for n in driver_vehicle_names if n}

    # ── Active DTCs (Samsara fault codes) ───────────────────────
    vehicles_with_faults: set[str] = set()
    active_dtcs = 0
    for v in faulted_vehicles:
        name = (v.get("name") or "").strip().lower()
        if name not in needles:
            continue
        vehicles_with_faults.add(name)
        active_dtcs += len(v.get("_dtcs", []) or [])

    # ── Health alerts (low DEF, oil pressure, etc.) ─────────────
    critical = minor = 0
    trucks_with_alerts: set[str] = set()
    for v in health_records:
        name = (v.get("name") or "").strip().lower()
        if name not in needles:
            continue
        for code in v.get("_health_alerts") or []:
            if code in _CRITICAL_HEALTH_ALERTS:
                critical += 1
                trucks_with_alerts.add(name)
            elif code in _MINOR_HEALTH_ALERTS:
                minor += 1
                trucks_with_alerts.add(name)

    dirty_trucks = vehicles_with_faults | trucks_with_alerts
    clean = max(0, len(needles) - len(dirty_trucks))

    return {
        "vehicles_assigned":    len(needles),
        "vehicles_with_faults": len(vehicles_with_faults),
        "active_dtcs":        active_dtcs,
        "critical_alerts":    critical,
        "minor_alerts":       minor,
        "vehicles_clean":       clean,
    }
