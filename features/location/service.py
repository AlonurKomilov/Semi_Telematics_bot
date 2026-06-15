"""Location service — live map data + vehicle status detection."""

from __future__ import annotations

import asyncio
import time

from features.vehicles.service import get_vehicles_overview
from infra.services import get_client


# Speed threshold for distinguishing moving vs idle (mph)
_MOVING_THRESHOLD = 2


def classify_vehicle_status(vehicle: dict) -> str:
    """Determine vehicle status from speed + (preferably) real engine state.

    Returns one of: 'moving', 'idle', 'stopped'.
    Used by both bot/maps.py (PNG rendering) and api/routes/maps.py (GeoJSON).

    Prefers the authoritative CAN-bus ``engineState`` value when present
    (merged in by ``get_vehicles_for_map`` from /fleet/vehicles/stats?types=
    engineStates).  Falls back to a speed-only heuristic for vehicles whose
    Samsara plan doesn't expose engineStates or for paths that don't merge
    that data (e.g. ``features.location`` consumers other than the map).
    """
    loc = vehicle.get("location", {})
    speed = loc.get("speedMilesPerHour", vehicle.get("speed_mph", 0)) or 0
    engine = vehicle.get("engineState") or loc.get("engineState")

    # Authoritative path: real engineState value present
    if engine in ("On", "Idle", "Off"):
        if engine == "On" and speed > _MOVING_THRESHOLD:
            return "moving"
        if engine in ("On", "Idle"):
            return "idle"
        return "stopped"

    # Heuristic fallback (engineState unavailable)
    if speed > _MOVING_THRESHOLD:
        return "moving"
    if speed > 0:
        return "idle"
    return "stopped"


# Status → marker color for map rendering
STATUS_COLORS = {
    "moving": "#22c55e",   # green
    "idle": "#eab308",     # yellow
    "stopped": "#ef4444",  # red
}


# ── engineStates cache ───────────────────────────────────────────────────────
# Samsara enforces ~100 req/min per token.  /map/vehicles is hit by the
# dashboard every 30s and /map/vehicles/live every 5s.  Caching engineStates
# for 15s gives an upper bound of 4 calls/min while keeping marker accuracy
# within one polling cycle of reality.
_ENGINE_STATES_TTL = 15.0  # seconds
_engine_states_cache: dict[int, tuple[float, dict[str, str]]] = {}


async def _get_engine_states_by_id(account_id: int) -> dict[str, str]:
    """Return {vehicle_id: engineState} for the given account.

    Cached for _ENGINE_STATES_TTL seconds per account.  Returns {} on any
    error (including Samsara plans that don't support engineStates) so the
    caller can degrade gracefully to the speed-based heuristic.
    """
    now = time.monotonic()
    cached = _engine_states_cache.get(account_id)
    if cached and (now - cached[0]) < _ENGINE_STATES_TTL:
        return cached[1]

    try:
        client = await get_client(account_id)
        rows = await client.get_engine_states()
        by_id: dict[str, str] = {}
        for r in rows:
            vid = r.get("id")
            es = r.get("engineStates") or {}
            value = es.get("value") if isinstance(es, dict) else None
            if vid and value in ("On", "Idle", "Off"):
                by_id[str(vid)] = value
    except Exception:
        by_id = {}

    _engine_states_cache[account_id] = (now, by_id)
    return by_id


async def get_vehicles_for_map(
    account_id: int,
    company: str | None = None,
) -> list[dict]:
    """Fetch fleet overview enriched with real engine states for live map.

    Runs the overview fetch and engineStates fetch in parallel so the extra
    accuracy costs no wall-time.  When engineStates are unavailable (older
    Samsara plans, transient API errors) the function still returns the
    overview list and ``classify_vehicle_status`` falls back to speed-only.
    """
    overview, engine_by_id = await asyncio.gather(
        get_vehicles_overview(account_id, company=company),
        _get_engine_states_by_id(account_id),
    )
    if engine_by_id:
        for v in overview:
            es = engine_by_id.get(str(v.get("id")))
            if es:
                v["engineState"] = es
    return overview

