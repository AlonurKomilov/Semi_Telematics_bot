"""Telemetry service — Single Source of Truth for health, weather, efficiency data.

Both bot handlers and API routes call these methods instead of
duplicating Samsara client interaction + company-display setup.
"""

from __future__ import annotations

from datetime import datetime, timezone, timedelta

from constants import METERS_PER_MILE
from core.services import get_client
from capabilities.vehicle_catalog.service import prepare_companies


async def get_vehicle_health(
    account_id: str,
    company: str | None = None,
) -> list[dict]:
    """Fetch vehicle health stats (battery, oil, coolant, DEF, etc.)."""
    await prepare_companies(account_id)
    client = await get_client(account_id)
    return await client.get_vehicle_health(company=company)


async def get_fleet_weather(
    account_id: str,
    company: str | None = None,
) -> list[dict]:
    """Fetch ambient temperature readings from vehicle sensors."""
    await prepare_companies(account_id)
    client = await get_client(account_id)
    return await client.get_fleet_weather(company=company)


async def get_fleet_efficiency(
    account_id: str,
    days: int = 7,
    company: str | None = None,
) -> list[dict]:
    """Fetch combined vehicle + driver efficiency data."""
    await prepare_companies(account_id)
    client = await get_client(account_id)
    return await client.get_fleet_efficiency(days=days, company=company)


async def get_vehicles_with_faults(
    account_id: str,
    company: str | None = None,
) -> tuple[list[dict], int, dict]:
    """Fetch vehicles with active faults.

    Returns (faulted_vehicles, total_vehicle_count, company_breakdown).
    """
    await prepare_companies(account_id)
    client = await get_client(account_id)
    return await client.get_vehicles_with_faults(company=company)


async def get_driver_efficiency(
    account_id: str,
    days: int = 7,
    company: str | None = None,
    truck_nums: list[str] | None = None,
) -> list[dict]:
    """Fetch per-driver efficiency/scorecard data.

    When *truck_nums* is provided the results are filtered to drivers whose
    name contains any of the truck identifiers (case-insensitive substring).
    Pass an empty list to get an empty result (used for own-only access when
    no trucks are assigned).
    """
    await prepare_companies(account_id)
    client = await get_client(account_id)
    results = await client.get_driver_efficiency(days=days, company=company)
    if truck_nums is not None:
        if not truck_nums:
            return []
        needles = [t.lower() for t in truck_nums]
        results = [
            d for d in results
            if any(n in d.get("driver_name", "").lower() for n in needles)
        ]
    return results


async def get_vehicle_odometer(
    account_id: int,
    company_code: str,
    vehicle_name: str,
) -> float | None:
    """Fetch current odometer reading for a vehicle (in miles).

    Returns miles as a float, or None if not available.
    Single Source of Truth — used by maintenance scheduler and any
    future API/dashboard endpoint that needs live odometer data.
    """
    try:
        client = await get_client(account_id)
    except Exception:
        return None

    end = datetime.now(timezone.utc)
    start = end - timedelta(hours=24)

    try:
        raw = await client._get_paginated_history(
            "obdOdometerMeters", start, end=end,
        )
    except Exception:
        return None

    fleet = await client.get_fleet_overview(company=company_code)
    vid = None
    for v in fleet:
        if v["name"] == vehicle_name:
            vid = v.get("id", "")
            break
    if not vid or vid not in raw:
        return None

    points = raw[vid].get("obdOdometerMeters", [])
    if not points:
        return None

    last_val = points[-1].get("value", 0)
    return round(last_val / METERS_PER_MILE, 1)
