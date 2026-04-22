"""Location service — live map data + vehicle status detection."""

from __future__ import annotations

from capabilities.vehicle_catalog.service import get_fleet_overview


# Speed threshold for distinguishing moving vs idle (mph)
_MOVING_THRESHOLD = 2


def classify_vehicle_status(vehicle: dict) -> str:
    """Determine vehicle status from speed and engine state.

    Returns one of: 'moving', 'idle', 'stopped'.
    Used by both bot/maps.py (PNG rendering) and api/routes/maps.py (GeoJSON).
    """
    loc = vehicle.get("location", {})
    speed = loc.get("speedMilesPerHour", vehicle.get("speed_mph", 0)) or 0
    engine = vehicle.get("engineState", loc.get("engineState", "Off"))

    if speed > _MOVING_THRESHOLD:
        return "moving"
    if engine in ("On", "Idle") or speed > 0:
        return "idle"
    return "stopped"


# Status → marker color for map rendering
STATUS_COLORS = {
    "moving": "#22c55e",   # green
    "idle": "#eab308",     # yellow
    "stopped": "#ef4444",  # red
}


async def get_fleet_for_map(
    account_id: str,
    company: str | None = None,
) -> list[dict]:
    """Fetch fleet overview for live map rendering."""
    return await get_fleet_overview(account_id, company=company)
