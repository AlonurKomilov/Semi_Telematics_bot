"""Fuel signal — reads ``fuel_entries`` rows for the trucks each driver
actually drove in the window.

Drivers earn a small "fuel discipline" bonus when entries are logged
for every truck they drove (proxy for the manual-receipt workflow), and
get a penalty when an entry's MPG looks anomalous (theft / leak / data
error).

Driver↔truck mapping reuses the efficiency record's ``_vehicle_names``.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone


# An MPG below this on a single fuel-up is flagged as anomalous. Real
# class-8 trucks rarely sustain <3 MPG; lower than that usually means a
# bad odometer reading, theft, or major leak. Tunable per account later
# via score_rules overrides.
_MIN_REASONABLE_MPG = 3.0


def _entry_in_window(entry: dict, since_date: str) -> bool:
    d = entry.get("date") or ""
    return bool(d) and d >= since_date


def _entry_mpg(entry: dict, prev_odo: float | None) -> float | None:
    """Estimate MPG from this entry vs the previous one's odometer.

    Single-entry MPG is unknowable; we need at least two consecutive
    entries on the same truck. Returns ``None`` when not computable.
    """
    odo     = entry.get("odometer_miles")
    gallons = entry.get("gallons")
    if not gallons or gallons <= 0 or odo is None or prev_odo is None:
        return None
    miles = odo - prev_odo
    if miles <= 0:
        return None
    return miles / gallons


def aggregate(entries: list[dict], driver_vehicle_names: list[str], window_days: int) -> dict:
    """Return per-driver fuel signal slice.

    Inputs:
      *entries*           — raw rows from ``get_fuel_entries`` (any order)
      *driver_vehicle_names* — list of truck names this driver used
      *window_days*       — lookback window

    Output keys consumed by rules:
      entries_logged           — count in window for driver's trucks
      vehicles_with_entries      — distinct trucks that had at least one entry
      vehicles_assigned          — len(driver_vehicle_names)
      anomalous_entries        — entries where computed MPG < _MIN_REASONABLE_MPG
    """
    if not driver_vehicle_names:
        return {
            "entries_logged":      0,
            "vehicles_with_entries": 0,
            "vehicles_assigned":     0,
            "anomalous_entries":   0,
        }

    needles    = {n.lower() for n in driver_vehicle_names if n}
    since_dt   = datetime.now(timezone.utc) - timedelta(days=max(window_days, 1))
    since_date = since_dt.strftime("%Y-%m-%d")

    # Group entries per truck, sorted by date ascending so we can
    # compute consecutive-entry MPG.
    per_truck: dict[str, list[dict]] = {}
    for e in entries:
        truck = (e.get("vehicle_name") or "").strip().lower()
        if truck in needles:
            per_truck.setdefault(truck, []).append(e)

    entries_logged = 0
    vehicles_with_entries: set[str] = set()
    anomalous = 0

    for truck, rows in per_truck.items():
        rows.sort(key=lambda r: r.get("date") or "")
        prev_odo: float | None = None
        for e in rows:
            if _entry_in_window(e, since_date):
                entries_logged += 1
                vehicles_with_entries.add(truck)
                mpg = _entry_mpg(e, prev_odo)
                if mpg is not None and mpg < _MIN_REASONABLE_MPG:
                    anomalous += 1
            # Always advance prev_odo so the next entry's MPG is correct,
            # even if this one was outside the window.
            try:
                prev_odo = float(e.get("odometer_miles")) if e.get("odometer_miles") is not None else prev_odo
            except (TypeError, ValueError):
                pass

    return {
        "entries_logged":      entries_logged,
        "vehicles_with_entries": len(vehicles_with_entries),
        "vehicles_assigned":     len(needles),
        "anomalous_entries":   anomalous,
    }
