"""Efficiency signal — derived from existing driver-efficiency data.

Reuses ``capabilities.telemetry.service.get_driver_efficiency`` so the
scorecard never makes a second Samsara call.  Returns a dict keyed by
driver_id with normalized facts.
"""

from __future__ import annotations


def from_driver_records(drivers: list[dict]) -> dict[str, dict]:
    """Convert raw efficiency records into per-driver signal dicts.

    Preserves the source records under ``_raw`` for the API layer that
    still wants to expose Samsara's columns (miles, MPG, …) alongside
    the composite score.
    """
    out: dict[str, dict] = {}
    for d in drivers:
        did = str(d.get("driver_id") or d.get("driver_name") or "")
        if not did:
            continue
        out[did] = {
            "driver_id":   d.get("driver_id", ""),
            "driver_name": d.get("driver_name", "Unknown"),
            "company":     d.get("_org", ""),
            # facts the rules read
            "overspeed_min": float(d.get("_overspeed_min", 0) or 0),
            "green_pct":     float(d.get("_green_pct", 0) or 0),
            "antic_pct":     float(d.get("_antic_pct", 0) or 0),
            # ── Exposure metrics — promoted to the top-level signals
            # dict so curve evaluators (per-mile / per-hour) can read
            # them without digging into ``_raw``.  Audit Phase 0.
            "miles":         float(d.get("_miles", 0) or 0),
            "drive_hours":   float(d.get("_drive_h", 0) or 0),
            "idle_hours":    float(d.get("_idle_h", 0) or 0),
            "idle_pct":      float(d.get("_idle_pct", 0) or 0),
            # truck list (used by maintenance signal to scope tasks per driver)
            "_vehicle_names": [
                (vs.get("vehicle", {}).get("name") or "").strip().lower()
                for vs in d.get("_vehicle_summaries", [])
                if vs.get("vehicle", {}).get("name")
            ],
            "_raw": d,
        }
    return out
