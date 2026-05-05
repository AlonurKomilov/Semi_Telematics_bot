"""Safety-events signal — counts events per driver from the Events feature."""

from __future__ import annotations


def from_events(events: list[dict]) -> dict[str, dict]:
    """Aggregate raw safety events into per-driver count buckets.

    Returns ``{driver_id_or_name: {"count": int, "by_type": {...}}}``.
    Falls back to driver_name when driver_id is empty (Samsara payloads
    sometimes omit it).
    """
    by_driver: dict[str, dict] = {}
    for e in events:
        key = str(e.get("driver_id") or e.get("driver_name") or "").strip()
        if not key:
            continue
        slot = by_driver.setdefault(key, {"count": 0, "by_type": {}})
        slot["count"] += 1
        et = e.get("event_type", "unknown")
        slot["by_type"][et] = slot["by_type"].get(et, 0) + 1
    return by_driver


def from_events_by_vehicle(events: list[dict]) -> dict[str, dict]:
    """Phase B: same shape as ``from_events`` but keyed by vehicle.

    Indexed by both vehicle_id (primary) and vehicle_name (fallback) so
    the caller can match either way, mirroring the driver path's
    id-or-name fallback.
    """
    by_vehicle: dict[str, dict] = {}
    for e in events:
        vid = str(e.get("vehicle_id") or "").strip()
        vname = str(e.get("vehicle_name") or "").strip()
        et = e.get("event_type", "unknown")
        for key in {vid, vname}:
            if not key:
                continue
            slot = by_vehicle.setdefault(key, {"count": 0, "by_type": {}})
            slot["count"] += 1
            slot["by_type"][et] = slot["by_type"].get(et, 0) + 1
    return by_vehicle
