"""Telemetry service — Single Source of Truth for health, weather, efficiency data.

Both bot handlers and API routes call these methods instead of
duplicating Samsara client interaction + company-display setup.
"""

from __future__ import annotations

from datetime import datetime, timezone, timedelta

from constants import METERS_PER_MILE
from infra.services import get_client
from capabilities.vehicles.service import prepare_companies


async def get_vehicle_health(
    account_id: int,
    company: str | None = None,
) -> list[dict]:
    """Fetch vehicle health stats (battery, oil, coolant, DEF, etc.).

    Warehouse-first: reads ``vehicle_health_snapshot`` (5-min-fresh) when
    WAREHOUSE_READS_ENABLED=1, falls back to live Samsara otherwise
    (or on cold-start empty warehouse).
    """
    await prepare_companies(account_id)
    client = await get_client(account_id)

    async def _live():
        return await client.get_vehicle_health(company=company)

    from capabilities.telemetry import warehouse_reader as _wh
    return await _wh.get_vehicle_health(
        account_id, company=company, samsara_fallback=_live,
    )


async def get_fleet_weather(
    account_id: int,
    company: str | None = None,
) -> list[dict]:
    """Fetch ambient temperature readings from vehicle sensors.

    Warehouse-first: reads ``fleet_weather_snapshot`` (5-min-fresh) when
    WAREHOUSE_READS_ENABLED=1, falls back to live Samsara otherwise.
    """
    await prepare_companies(account_id)
    client = await get_client(account_id)

    async def _live():
        return await client.get_fleet_weather(company=company)

    from capabilities.telemetry import warehouse_reader as _wh
    return await _wh.get_fleet_weather(
        account_id, company=company, samsara_fallback=_live,
    )


async def get_fleet_efficiency(
    account_id: int,
    days: int = 7,
    company: str | None = None,
) -> list[dict]:
    """Fetch combined vehicle + driver efficiency data.

    Warehouse-first: reads ``fleet_efficiency_snapshot`` (hourly fresh)
    when WAREHOUSE_READS_ENABLED=1, falls back to live Samsara otherwise.
    """
    await prepare_companies(account_id)
    client = await get_client(account_id)

    async def _live():
        return await client.get_fleet_efficiency(days=days, company=company)

    from capabilities.telemetry import warehouse_reader as _wh
    return await _wh.get_fleet_efficiency(
        account_id, days=days, company=company, samsara_fallback=_live,
    )


async def get_vehicles_with_faults(
    account_id: int,
    company: str | None = None,
) -> tuple[list[dict], int, dict]:
    """Fetch vehicles with active faults.

    Returns (faulted_vehicles, total_vehicle_count, company_breakdown).
    Each vehicle in faulted_vehicles has ``_severity`` stamped as
    'critical' or 'warning' (single source of truth — see
    ``capabilities/alerting/severity.py``).
    Warehouse-first: reads ``vehicle_fault_snapshot`` + ``vehicle_state``
    when WAREHOUSE_READS_ENABLED=1, falls back to live Samsara otherwise.
    """
    from capabilities.vehicles.severity import classify_fault_severity

    await prepare_companies(account_id)
    client = await get_client(account_id)

    async def _live():
        faulted, total, breakdown = await client.get_vehicles_with_faults(company=company)
        for v in faulted:
            if "_severity" not in v:
                v["_severity"] = classify_fault_severity(v)
        return faulted, total, breakdown

    from capabilities.telemetry import warehouse_reader as _wh
    faulted, total, breakdown = await _wh.get_vehicles_with_faults(
        account_id, company=company, samsara_fallback=_live,
    )
    # Warehouse path already stamps _severity from has_critical; stamp any
    # missing entries (e.g. cold-start live fallback result returned directly).
    for v in faulted:
        if "_severity" not in v:
            v["_severity"] = classify_fault_severity(v)
    return faulted, total, breakdown


async def get_low_fuel_vehicles(
    account_id: int,
    threshold: int,
    company: str | None = None,
) -> list[dict]:
    """Fetch vehicles below the given fuel percentage threshold.

    Returns the raw Samsara-shape vehicle dicts with ``_fuel_pct`` populated.
    Warehouse-first: filters from ``vehicle_state`` (60s-fresh) when
    WAREHOUSE_READS_ENABLED=1, falls back to live Samsara otherwise.
    """
    await prepare_companies(account_id)
    client = await get_client(account_id)

    async def _live():
        return await client.get_low_fuel_vehicles(threshold, company=company)

    from capabilities.telemetry import warehouse_reader as _wh
    fleet = await _wh.get_current_vehicles(
        account_id, company=company, samsara_fallback=_live,
    )
    # Warehouse path returns the full fleet; apply the threshold filter
    # locally and synthesize ``_fuel_pct`` to match the Samsara shape that
    # callers (alerting/fuel.py, AI tools) already consume.
    out: list[dict] = []
    for v in fleet:
        pct = (v.get("fuel") or {}).get("value")
        if pct is None or pct >= threshold:
            continue
        v.setdefault("_fuel_pct", pct)
        out.append(v)
    return out


async def get_driver_efficiency(
    account_id: int,
    days: int = 7,
    company: str | None = None,
    vehicle_nums: list[str] | None = None,
) -> list[dict]:
    """Fetch per-driver efficiency/scorecard data.

    When *vehicle_nums* is provided the results are filtered to drivers whose
    ``_vehicle_summaries[].vehicle.name`` matches any of the truck identifiers
    (case-insensitive exact match).  This is structurally correct — earlier
    versions used a substring match against the driver display name, which
    leaked drivers across "own"-scoped users (e.g. truck "T1" matched any
    driver whose name contained "t1": "T10", "Tony", etc.).

    Pass an empty list to get an empty result (used for own-only access when
    no trucks are assigned).
    """
    await prepare_companies(account_id)
    client = await get_client(account_id)
    results = await client.get_driver_efficiency(days=days, company=company)
    if vehicle_nums is not None:
        if not vehicle_nums:
            return []
        allowed = {t.strip().lower() for t in vehicle_nums if t}
        filtered: list[dict] = []
        for d in results:
            for vs in d.get("_vehicle_summaries", []) or []:
                vname = ((vs.get("vehicle") or {}).get("name") or "").strip().lower()
                if vname and vname in allowed:
                    filtered.append(d)
                    break
        results = filtered
    return results


async def get_engine_hours(
    account_id: int,
    days: int = 7,
    company: str | None = None,
) -> list[dict]:
    """Fetch weekly engine hours + driving/idle breakdown.

    Single Source of Truth — wraps the multi-company Samsara client so
    callers (CSV reports, efficiency PDF, AI tools) can switch to the
    warehouse path in a single place once an ``engine_hours_snapshot``
    table is added.  Today this is a thin pass-through.
    """
    await prepare_companies(account_id)
    client = await get_client(account_id)
    return await client.get_engine_hours(days, company=company)


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
