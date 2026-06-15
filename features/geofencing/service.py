"""Geofencing service — geofence data fetching and vehicle-in-zone checks."""

from __future__ import annotations

from infra.services import get_client
from features.vehicles.service import prepare_companies


async def get_geofences(
    account_id: int,
    company: str | None = None,
) -> list[dict]:
    """Fetch geofence definitions.

    Warehouse-first: reads ``geofence_definitions`` (hourly-fresh) when
    WAREHOUSE_READS_ENABLED=1, falls back to live Samsara otherwise
    (or on cold-start empty cache).
    """
    await prepare_companies(account_id)
    client = await get_client(account_id)

    async def _live():
        return await client.get_geofences(company=company)

    from capabilities.telemetry import warehouse_reader as _wh
    return await _wh.get_geofences(
        account_id, company=company, samsara_fallback=_live,
    )


async def get_fleet_for_geofence_check(
    account_id: int,
    company: str | None = None,
) -> list[dict]:
    """Fetch fleet overview for geofence proximity checks.

    Warehouse-first via vehicles.service.get_vehicles_overview (60s-fresh) —
    geofence proximity tolerates the small lag and avoids hammering Samsara
    every check cycle.
    """
    from features.vehicles.service import get_vehicles_overview as _svc
    return await _svc(account_id, company=company)


async def get_platform_geofences(account_id: int, db) -> list[dict]:
    """Fetch platform-owned zones from the DB and normalise them so
    ``features.geofencing.geometry.is_inside_geofence`` works unchanged.

    Circle zones become::
        {"id": ..., "name": ..., "geofence_type": ..., "notify_roles": [...],
         "circularGeofence": {"latitude": N, "longitude": N, "radiusMeters": N}}

    Polygon zones become::
        {"id": ..., "name": ..., "geofence_type": ..., "notify_roles": [...],
         "polygonGeofence": {"vertices": [{"latitude": N, "longitude": N}, ...]}}
    """
    raw_zones = await db.get_platform_geofences(account_id, is_active=True)
    result: list[dict] = []
    for z in raw_zones:
        base = {
            "id": z["id"],
            "name": z["name"],
            "geofence_type": z["geofence_type"],
            "notify_roles": z["notify_roles"],
            "zone_role": z.get("zone_role", "all"),
            "_source": "platform",
        }
        if z["shape_type"] == "circle":
            base["circularGeofence"] = {
                "latitude": z.get("latitude") or 0,
                "longitude": z.get("longitude") or 0,
                "radiusMeters": z.get("radius_meters") or 0,
            }
        else:
            # Polygon: vertices stored as [{"lat": N, "lng": N}, ...]
            vertices = [
                {"latitude": v.get("lat", v.get("latitude", 0)),
                 "longitude": v.get("lng", v.get("longitude", 0))}
                for v in (z.get("vertices") or [])
            ]
            base["polygonGeofence"] = {"vertices": vertices}
        result.append(base)
    return result

