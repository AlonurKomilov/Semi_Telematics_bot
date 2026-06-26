"""Telemetry service — Single Source of Truth for health, weather, efficiency data.

Both bot handlers and API routes call these methods instead of
duplicating Samsara client interaction + company-display setup.
"""

from __future__ import annotations


from infra.services import get_client
from features.vehicles.service import prepare_companies


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

    from capabilities.warehouse.telemetry import warehouse_reader as _wh
    return await _wh.get_vehicle_health(
        account_id, company=company, samsara_fallback=_live,
    )


async def get_fleet_weather(
    account_id: int,
    company: str | None = None,
) -> list[dict]:
    """Fetch ambient temperature readings from vehicle sensors.

    Warehouse-first: reads ``aggregate_weather_snapshot`` (5-min-fresh) when
    WAREHOUSE_READS_ENABLED=1, falls back to live Samsara otherwise.
    """
    await prepare_companies(account_id)
    client = await get_client(account_id)

    async def _live():
        return await client.get_fleet_weather(company=company)

    from capabilities.warehouse.telemetry import warehouse_reader as _wh
    return await _wh.get_fleet_weather(
        account_id, company=company, samsara_fallback=_live,
    )


async def get_fleet_efficiency(
    account_id: int,
    days: int = 7,
    company: str | None = None,
) -> list[dict]:
    """Fetch combined vehicle + driver efficiency data.

    Warehouse-first: reads ``aggregate_efficiency_snapshot`` (hourly fresh)
    when WAREHOUSE_READS_ENABLED=1, falls back to live Samsara otherwise.
    """
    await prepare_companies(account_id)
    client = await get_client(account_id)

    async def _live():
        return await client.get_fleet_efficiency(days=days, company=company)

    from capabilities.warehouse.telemetry import warehouse_reader as _wh
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
    from features.vehicles.severity import classify_fault_severity

    await prepare_companies(account_id)
    client = await get_client(account_id)

    async def _live():
        faulted, total, breakdown = await client.get_vehicles_with_faults(company=company)
        for v in faulted:
            if "_severity" not in v:
                v["_severity"] = classify_fault_severity(v)
        return faulted, total, breakdown

    from capabilities.warehouse.telemetry import warehouse_reader as _wh
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

    from capabilities.warehouse.telemetry import warehouse_reader as _wh
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


def _ensure_eff_keys(d: dict) -> dict:
    """Bridge the two driver-efficiency record shapes.

    The live Samsara reader emits ``_``-prefixed keys (``_miles``, ``_mpg``,
    ``_drive_h`` …) that the AI tools and scorecard signals read.  The
    warehouse path (the default) returns the bare column names (``miles``,
    ``mpg`` …), so those consumers were reading ``None`` for every metric and
    the AI reported "0 miles / no MPG" for drivers who actually had data.
    Add the ``_``-prefixed keys (deriving ``_idle_pct`` from idle/drive hours)
    when only the bare ones are present — additive, so live records and the
    warehouse function's own callers/tests are unaffected.
    """
    if "_miles" in d or "miles" not in d:
        return d  # already live-shaped, or not an efficiency row
    idle_h = d.get("idle_h") or 0
    drive_h = d.get("drive_h") or 0
    idle_pct = None
    try:
        total = float(idle_h) + float(drive_h)
        if total > 0:
            idle_pct = round(float(idle_h) / total * 100, 1)
    except (TypeError, ValueError):
        idle_pct = None
    return {
        **d,
        "_miles": d.get("miles"),
        "_mpg": d.get("mpg"),
        "_drive_h": d.get("drive_h"),
        "_idle_h": d.get("idle_h"),
        "_idle_pct": idle_pct,
        "_green_pct": d.get("green_pct"),
        "_antic_pct": d.get("antic_pct"),
        "_overspeed_min": d.get("overspeed_min"),
        "_harsh_brake": d.get("harsh_brake"),
        "_harsh_turn": d.get("harsh_turn"),
        "_harsh_accel": d.get("harsh_accel"),
    }


async def get_driver_efficiency(
    account_id: int,
    days: int = 7,
    company: str | None = None,
    vehicle_nums: list[str] | None = None,
) -> list[dict]:
    """Fetch per-driver efficiency/scorecard data.

    Warehouse-first: reads ``driver_efficiency_daily`` (snapshotted hourly)
    when WAREHOUSE_READS_ENABLED=1, falls back to live Samsara otherwise
    (or on cold-start empty warehouse).

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

    async def _live():
        return await client.get_driver_efficiency(days=days, company=company)

    from capabilities.warehouse.telemetry import warehouse_reader as _wh
    # The truck filter (below) matches ``_vehicle_summaries[].vehicle.name``,
    # which ONLY the live reader provides — the warehouse efficiency table has
    # no vehicle join, so reading it for a vehicle filter would silently return
    # empty for every driver.  A vehicle filter therefore reads live; everything
    # else stays warehouse-first.
    if vehicle_nums is not None:
        results = await _live()
    else:
        results = await _wh.get_driver_efficiency_window(
            account_id, days=days, samsara_fallback=_live,
        )
    # Bridge warehouse (bare keys) → the _-prefixed shape consumers expect.
    results = [_ensure_eff_keys(r) for r in results]
    # Warehouse path is account-scoped but not company-scoped — filter
    # locally so the warehouse hit still respects the caller's company
    # parameter when present.
    if company:
        results = [
            r for r in results
            if (r.get("_org") or r.get("company") or "") == company
        ]
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

    Reads ``vehicle_state.odometer_mi`` directly from the warehouse
    table — DB is the single source of truth, populated every 60s by
    ``ingest_vehicle_state``.  Bypasses the WAREHOUSE_READS_ENABLED
    cutover flag because odometer is *only* in the warehouse.
    Returns ``None`` when the vehicle has never reported odometer (no
    CAN bus gateway, plan limitation, or warehouse cold-start).
    """
    from infra.platform import get_tenant_db

    tenant = await get_tenant_db(account_id)
    rows = await tenant.get_vehicle_state(
        account_id, company=company_code or None, vehicle_nums=[vehicle_name],
    )
    name_lower = vehicle_name.strip().lower()
    for row in rows:
        if (row.get("vehicle_name") or "").strip().lower() != name_lower:
            continue
        miles = row.get("odometer_mi")
        if isinstance(miles, (int, float)):
            return float(miles)
        return None
    return None


async def get_engine_states(account_id: int) -> list[dict]:
    """Latest engine state (On / Off / Idle) for every vehicle.

    SSOT accessor for the rolling/idling/stopped classification — tools
    and hubs call this instead of ``samsara_client.get_engine_states()``
    directly (service-contract rule, docs/FEATURES.md).
    """
    await prepare_companies(account_id)
    client = await get_client(account_id)
    return await client.get_engine_states()
