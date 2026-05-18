"""Vehicle-keyed efficiency signal (vehicle-primary scorecards).

The driver-keyed equivalent in ``efficiency.py`` consumes the
``driverSummaries`` payload (one row per driver, drive-time aggregated
across that driver's trucks).  This module consumes ``get_fleet_efficiency``
which is already keyed per-vehicle and reuses the same
``_summarize_efficiency`` shape under the hood — no second Samsara call,
no schema drift.

Output dict matches ``efficiency.from_driver_records`` so the scoring
engine and downstream service code can read either feed identically.
"""

from __future__ import annotations


def from_vehicle_records(vehicles: list[dict]) -> dict[str, dict]:
    """Convert raw fleet-efficiency records into per-vehicle signal dicts.

    Each input record is one truck (``get_fleet_efficiency`` already
    pre-joins engine-hours + driver-efficiency at the per-vehicle level
    via ``vehicleSummaries[].vehicle.name``).

    Returns ``{vehicle_id: {…signals…}}`` ready to feed ``score()``.
    Trucks with zero engine activity are filtered out — same dead-row
    suppression the driver path uses.
    """
    out: dict[str, dict] = {}
    for v in vehicles:
        # Vehicle id is the canonical key.  Fall back to name when the
        # API omits id (rare, defensive).
        vid = str(v.get("id") or v.get("name") or "")
        if not vid:
            continue
        # Drop trucks with no recorded driving — can't produce a
        # meaningful score and would just clutter the list.
        miles    = float(v.get("_miles", 0) or 0)
        drive_h  = float(v.get("_driving_hours", 0) or 0)
        if miles <= 0 and drive_h <= 0:
            continue
        name = (v.get("name") or "").strip() or "?"
        out[vid] = {
            "driver_id":   vid,                           # legacy-shape alias
            "driver_name": name,                          # legacy-shape alias
            "company":     v.get("_org", ""),
            # facts the rules read — same keys as efficiency.py
            "overspeed_min": float(v.get("_overspeed_min", 0) or 0),
            "green_pct":     float(v.get("_green_pct", 0) or 0),
            "antic_pct":     float(v.get("_antic_pct", 0) or 0),
            # exposure
            "miles":         miles,
            "drive_hours":   drive_h,
            "idle_hours":    float(v.get("_idle_hours", 0) or 0),
            "idle_pct":      float(v.get("_idle_pct", 0) or 0),
            # truck list — for vehicle subject this is just the one
            # vehicle.  Maintenance / camera / fuel signals already
            # bucket by truck name, so the existing aggregators work
            # unchanged with a single-element list.
            "_vehicle_names":  [name.lower()] if name and name != "?" else [],
            "_raw":          v,
        }
    return out
