"""Geofencing service — geofence data fetching and vehicle-in-zone checks."""

from __future__ import annotations

from core.services import get_client
from capabilities.vehicle_catalog.service import prepare_companies


async def get_geofences(
    account_id: str,
    company: str | None = None,
) -> list[dict]:
    """Fetch geofence definitions from Samsara."""
    await prepare_companies(account_id)
    client = await get_client(account_id)
    return await client.get_geofences(company=company)


async def get_fleet_for_geofence_check(
    account_id: str,
    company: str | None = None,
) -> list[dict]:
    """Fetch fleet overview for geofence proximity checks."""
    await prepare_companies(account_id)
    client = await get_client(account_id)
    return await client.get_fleet_overview(company=company)
