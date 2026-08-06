"""Vehicle resource endpoints.

Resource-first URL convention: /vehicles is the canonical home for all
per-vehicle data regardless of which role is calling.  Permission guards
+ ``filter_by_assigned_trucks`` enforce what each role can actually see.

URL structure:
    GET /api/vehicles/                    list (all roles that can see vehicles)
    GET /api/vehicles/{name}              detail
    GET /api/vehicles/{name}/health       subsystem — battery, oil, DEF, …
    GET /api/vehicles/{name}/faults       active DTCs
    GET /api/vehicles/{name}/timeline     hourly telemetry roll-up (warehouse)
    GET /api/vehicles/overview            fleet snapshot (was /fleet/overview)
    GET /api/vehicles/weather             cabin/ambient sensors (was /fleet/weather)
    GET /api/vehicles/utilization-summary per-vehicle utilization
"""
# router.py is interface-layer code co-located with its feature
# (docs/FEATURES.md): router.py and config.py are the interface-layer pair — those two may
# import interfaces.api.deps; nothing else in the feature may;
# service/alert/ai_tool/signal modules never do.


from __future__ import annotations

import logging

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field

from interfaces.api.deps import (
    require_permission_any,
    get_user_company_codes,
    validate_company_access,
    filter_by_allowed_companies,
    filter_by_assigned_trucks,
    require_permission,
    paginate,
    resolve_user_id,
)
from capabilities.integrations import reconciliation
from features.vehicles.service import (
    get_vehicles_overview as _svc_vehicles_overview,
    get_vehicle_detail as _svc_vehicle_detail,
)
from features.vehicles.warehouse.service import (
    get_vehicle_health as _svc_vehicle_health,
    get_fleet_weather as _svc_fleet_weather,
)
from features.vehicles.warehouse import readers as _wh_reader
from features.location.service import classify_vehicle_status
from infra.platform import get_tenant_db as _get_tenant_db
import infra.cache as _redis

# Short TTL for the full Samsara snapshot backing the vehicle list.
# Collapses burst polls from concurrent driver sessions without making
# GPS positions feel stale.
_FLEET_CACHE_TTL = 30

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/vehicles", tags=["vehicles"])


# ── Raw-field extractors (Samsara nested → flat) ─────────────────

def _extract_fuel(v: dict) -> float | None:
    """Extract fuel percent from raw Samsara vehicle dict."""
    fuel = v.get("fuel", {})
    if isinstance(fuel, dict):
        return fuel.get("value")
    if isinstance(fuel, (int, float)):
        return float(fuel)
    return None


def _extract_def(v: dict) -> float | None:
    """Extract DEF level percent from raw Samsara vehicle dict."""
    def_lvl = v.get("def_level", {})
    if isinstance(def_lvl, dict):
        return def_lvl.get("value")
    if isinstance(def_lvl, (int, float)):
        return float(def_lvl)
    return None


def _extract_fault_count(v: dict) -> int:
    """Count active DTCs from raw Samsara fault_codes dict."""
    fc = v.get("fault_codes", {})
    if isinstance(fc, dict):
        return len(fc.get("j1939", {}).get("diagnosticTroubleCodes", []))
    if isinstance(fc, list):
        return len(fc)
    return 0


def _extract_dtcs(v: dict) -> list:
    """Return raw DTC list from fault_codes."""
    fc = v.get("fault_codes", {})
    if isinstance(fc, dict):
        return fc.get("j1939", {}).get("diagnosticTroubleCodes", [])
    return []


def _extract_speed(v: dict) -> float:
    """Extract speed (mph) from nested location dict."""
    loc = v.get("location", {})
    speed = loc.get("speedMilesPerHour") or loc.get("speed") or 0
    return float(speed or 0)


def _derive_engine_state(status: str) -> str:
    """Derive a human-readable engine state from classified status."""
    if status == "moving":
        return "On"
    if status == "idle":
        return "Idle"
    return "Off"


def _extract_odometer(v: dict) -> tuple[float | None, str | None]:
    """Pull odometer (miles, ISO timestamp) from a merged vehicle dict.

    The value comes from the warehouse ``vehicle_state.odometer_mi``
    column populated by ``ingest_vehicle_state``; the fallback live
    path (cold cache) shapes the same key.  Vehicles without a CAN
    bus gateway return None for both fields.
    """
    odometer = v.get("odometer")
    if isinstance(odometer, dict):
        miles = odometer.get("miles")
        timestamp = odometer.get("time")
        if isinstance(miles, (int, float)):
            return float(miles), timestamp if isinstance(timestamp, str) else None
    return None, None


def _extract_engine_hours(v: dict) -> tuple[float | None, str | None]:
    """Pull cumulative engine hours (hours, ISO timestamp) from a merged
    vehicle dict.  Mirrors ``_extract_odometer`` — same warehouse-or-
    live source distinction.  Vehicles without an engine-hours OBD
    signal return None for both fields.
    """
    eng = v.get("engine_hours_reading")
    if isinstance(eng, dict):
        hours = eng.get("hours")
        timestamp = eng.get("time")
        if isinstance(hours, (int, float)):
            return float(hours), timestamp if isinstance(timestamp, str) else None
    return None, None


def _simplify(v: dict) -> dict:
    """Flatten a fleet overview vehicle into the consistent API shape."""
    loc = v.get("location", {})
    speed = _extract_speed(v)
    # Registry vehicles with no telematics match (trailers, manual
    # trucks) carry a marker so we don't mis-classify a GPS-less row
    # as "stopped" — they get an explicit "no_telemetry" status.
    no_telemetry = bool(v.get("_no_telemetry"))
    status = "no_telemetry" if no_telemetry else classify_vehicle_status(v)
    engine_state = "" if no_telemetry else _derive_engine_state(status)
    address = (
        loc.get("reverseGeo", {}).get("formattedLocation")
        or loc.get("address")
        or ""
    )
    odometer_miles, odometer_time = _extract_odometer(v)
    engine_hours, engine_hours_time = _extract_engine_hours(v)
    return {
        "id": v.get("id"),
        "name": v.get("name", ""),
        "company": v.get("_org", ""),
        # Registry fields — drive the Type column + source hint on the
        # Vehicles page.  Default to truck/samsara for any live row the
        # registry overlay didn't tag (steady state: none).
        "vehicle_type": v.get("vehicle_type", "truck"),
        "source": v.get("source", "samsara"),
        # Registry row id for the manage UI's edit/delete; null for a
        # live-only vehicle the registry hasn't caught yet (then the
        # edit affordance is disabled until the next ingest registers it).
        "registry_id": v.get("_registry_id"),
        "latitude": loc.get("latitude"),
        "longitude": loc.get("longitude"),
        "speed_mph": speed,
        "address": address,
        "engine_state": engine_state,
        "fuel_percent": _extract_fuel(v),
        "def_percent": _extract_def(v),
        "fault_count": _extract_fault_count(v),
        "odometer_miles": odometer_miles,
        "odometer_time": odometer_time,
        "engine_hours": engine_hours,
        "engine_hours_time": engine_hours_time,
        "status": status,
        "time": (
            loc.get("time")
            or (v.get("fuel") or {}).get("time")
            or (v.get("def_level") or {}).get("time")
            or v.get("time")
        ),
    }


def _normalize_detail(v: dict) -> dict:
    """Produce a normalized vehicle dict for the detail endpoint."""
    loc = v.get("location", {})
    speed = _extract_speed(v)
    fuel_pct = _extract_fuel(v)
    def_pct = _extract_def(v)
    dtcs = _extract_dtcs(v)
    # Same marker handling as _simplify: a registry vehicle with no
    # telematics is "no_telemetry", never mis-read as "stopped".
    no_telemetry = bool(v.get("_no_telemetry"))
    status = "no_telemetry" if no_telemetry else classify_vehicle_status(v)
    engine_state = "" if no_telemetry else _derive_engine_state(status)
    address = (
        loc.get("reverseGeo", {}).get("formattedLocation")
        or loc.get("address")
        or ""
    )
    odometer_miles, odometer_time = _extract_odometer(v)
    engine_hours, engine_hours_time = _extract_engine_hours(v)
    return {
        **v,
        "fuel_percent": fuel_pct,
        "fuelPercent": fuel_pct,
        "def_percent": def_pct,
        "defPercent": def_pct,
        "speed_mph": speed,
        "engine_state": engine_state,
        "engineState": engine_state,
        "status": status,
        "fault_count": len(dtcs),
        "odometer_miles": odometer_miles,
        "odometer_time": odometer_time,
        "engine_hours": engine_hours,
        "engine_hours_time": engine_hours_time,
        "formattedAddress": address,
        "address": address,
        "latitude": loc.get("latitude"),
        "longitude": loc.get("longitude"),
        "licensePlate": v.get("licensePlate") or v.get("license_plate") or "N/A",
    }


# ── Routes ───────────────────────────────────────────────────────

@router.get("/")
async def vehicles_list(
    company: str | None = Query(None),
    search: str | None = Query(None, description="Search by vehicle name"),
    status: str | None = Query(None, description="Filter: moving, idle, stopped, no_telemetry"),
    sort: str | None = Query(None, description="Sort field: name, fuel_percent, fault_count, status"),
    order: str = Query("asc", description="Sort order: asc or desc"),
    page: int = Query(1, ge=1, description="Page number"),
    # le=500: the registry overlay folds trailers + manual vehicles into
    # this list, so a ~100-truck carrier already sits near the old 200
    # cap.  The dashboard fetches one page; past 500 real vehicles the
    # page must switch to walking total_pages (the useFleetList pattern).
    page_size: int = Query(50, ge=1, le=500, description="Items per page"),
    user: dict = Depends(require_permission_any("can_faults", "can_vehicle_vehicle")),
):
    """Vehicle list with location and engine state — supports filtering, sorting, pagination."""
    allowed = await get_user_company_codes(user)
    validate_company_access(allowed, company)

    async def _live_cached() -> list:
        """Hit Samsara, serve Redis cache when warm.

        Caches the full unfiltered snapshot per account+company slice.
        Python-side search/status/sort filters below still apply on the
        cached list so a cache hit never returns stale partial data.
        """
        cache_key = f"fleet:raw:{user['account_id']}:{company or '_all'}"
        cached = await _redis.get(cache_key)
        if cached is not None:
            return cached
        data = await _svc_vehicles_overview(user["account_id"], company=company)
        await _redis.cache_set(cache_key, data, ttl=_FLEET_CACHE_TTL)
        return data

    vehicles = await _wh_reader.get_current_vehicles(
        user["account_id"], company=company, samsara_fallback=_live_cached,
    )
    # Registry overlay (SSOT) — the same merge the service-level read
    # applies: every active registry vehicle appears, trailers and
    # no-telematics trucks as explicit no_telemetry rows instead of
    # silently missing from the list.  The merge is idempotent, so the
    # cold-start fallback path (whose cached list is already merged)
    # stays correct.
    tenant_reg = await _get_tenant_db(user["account_id"])
    if tenant_reg is not None:
        try:
            registry = await tenant_reg.list_vehicles(
                user["account_id"], company_code=company,
            )
        except Exception:
            # Degrade to live-only rather than 500 — but LOUDLY, or ops
            # can't tell "no trailers registered" from "overlay broken".
            logger.warning(
                "registry overlay failed for acct=%s — vehicle list is live-only",
                user["account_id"], exc_info=True,
            )
            registry = []
        if registry:
            vehicles = _wh_reader.merge_registry_with_live(registry, vehicles)
    vehicles = filter_by_allowed_companies(vehicles, allowed)
    vehicles = await filter_by_assigned_trucks(vehicles, user)

    # Enrich with warehouse-sourced odometer + engine hours regardless
    # of which path produced ``vehicles``.  When WAREHOUSE_READS_ENABLED
    # is off the rows came from live Samsara via _live_cached and don't
    # include either (Samsara overview never does); when on the rows
    # already have them but a fresh re-read is cheap and keeps the
    # contract uniform.  Read directly from vehicle_state mixin to
    # bypass the cutover flag.
    if vehicles:
        tenant_db = await _get_tenant_db(user["account_id"])
        warehouse_rows = await tenant_db.get_vehicle_state(
            user["account_id"], company=company,
        )
        odometer_by_id: dict[str, dict] = {}
        odometer_by_name: dict[str, dict] = {}
        engine_hours_by_id: dict[str, dict] = {}
        engine_hours_by_name: dict[str, dict] = {}
        for row in warehouse_rows:
            rid = str(row.get("vehicle_id") or "")
            rname = (row.get("vehicle_name") or "").lower()
            miles = row.get("odometer_mi")
            if miles is not None:
                odometer = {"miles": miles, "time": row.get("odometer_time")}
                if rid:
                    odometer_by_id[rid] = odometer
                if rname:
                    odometer_by_name[rname] = odometer
            hours = row.get("engine_hours")
            if hours is not None:
                eng = {"hours": hours, "time": row.get("engine_hours_time")}
                if rid:
                    engine_hours_by_id[rid] = eng
                if rname:
                    engine_hours_by_name[rname] = eng
        for v in vehicles:
            vid = str(v.get("id") or "")
            vname = (v.get("name") or "").lower()
            if not v.get("odometer"):
                o_hit = odometer_by_id.get(vid) or odometer_by_name.get(vname)
                if o_hit:
                    v["odometer"] = o_hit
            if not v.get("engine_hours_reading"):
                e_hit = engine_hours_by_id.get(vid) or engine_hours_by_name.get(vname)
                if e_hit:
                    v["engine_hours_reading"] = e_hit

    result = [_simplify(v) for v in vehicles]

    if search:
        q = search.lower()
        result = [v for v in result if q in v["name"].lower()]

    if status and status in ("moving", "idle", "stopped", "no_telemetry"):
        result = [v for v in result if v["status"] == status]

    if sort and sort in ("name", "fuel_percent", "fault_count", "status", "company"):
        reverse = order.lower() == "desc"
        result.sort(key=lambda v: (v.get(sort) is None, v.get(sort, "")), reverse=reverse)

    paged = paginate(result, page, page_size)
    return {
        "vehicles": paged["items"],
        "count": paged["total"],
        "page": paged["page"],
        "page_size": paged["page_size"],
        "total_pages": paged["total_pages"],
    }


@router.get("/overview")
async def fleet_overview(
    company: str | None = Query(None, description="Filter by company code"),
    user: dict = Depends(require_permission("can_faults")),
):
    """Fleet snapshot — vehicles with status, location, faults.

    URL history: was GET /fleet/overview before 2026-06-11."""
    allowed = await get_user_company_codes(user)
    validate_company_access(allowed, company)
    async def _live():
        return await _svc_vehicles_overview(user["account_id"], company=company)
    vehicles = await _wh_reader.get_current_vehicles(
        user["account_id"], company=company, samsara_fallback=_live,
    )
    vehicles = filter_by_allowed_companies(vehicles, allowed)
    vehicles = await filter_by_assigned_trucks(vehicles, user)
    return {"vehicles": vehicles, "count": len(vehicles)}


@router.get("/weather")
async def fleet_weather(
    user: dict = Depends(require_permission("can_faults")),
):
    """Ambient temperature readings from vehicle sensors.

    URL history: was GET /fleet/weather before 2026-06-11."""
    vehicles = await _svc_fleet_weather(user["account_id"])
    vehicles = await filter_by_assigned_trucks(vehicles, user)

    items = []
    temps: list[float] = []
    for v in vehicles:
        w = v.get("_weather", {})
        temp_f = w.get("temp_f")
        entry = {
            "name": v.get("name", "?"),
            "company": v.get("_org", ""),
            "temp_f": round(temp_f, 1) if temp_f is not None else None,
            "temp_c": round(w["temp_c"], 1) if w.get("temp_c") is not None else None,
            "baro_inhg": round(w["baro_inhg"], 2) if w.get("baro_inhg") is not None else None,
            "temp_time": w.get("temp_time"),
            "baro_time": w.get("baro_time"),
            "location": v.get("location", {}).get("reverseGeo", {}).get("formattedLocation", ""),
        }
        items.append(entry)
        if temp_f is not None:
            temps.append(temp_f)

    summary = {}
    if temps:
        summary = {
            "avg_f": round(sum(temps) / len(temps), 1),
            "min_f": round(min(temps), 1),
            "max_f": round(max(temps), 1),
            "freezing_count": sum(1 for t in temps if t <= 32),
            "hot_count": sum(1 for t in temps if t >= 95),
            "reporting_count": len(temps),
        }

    return {"vehicles": items, "count": len(items), "summary": summary}


@router.get("/utilization-summary")
async def fleet_utilization_summary(
    days: int = Query(30, ge=7, le=365),
    user: dict = Depends(require_permission_any("can_faults", "can_vehicle_all", "can_vehicle_vehicle")),
):
    """Per-vehicle utilization across the entire visible fleet.

    Backs the Vehicles page utilization card.  Filters down to the
    caller's visible vehicles so drivers see only their own row and
    operators see everything in their company allow-list.
    """
    allowed = await get_user_company_codes(user)
    rows = await _wh_reader.get_account_utilization_summary(
        user["account_id"], days=days,
    )
    if not rows:
        return {"days": days, "vehicles": []}

    # Cross-reference against the visible-vehicles list so callers
    # don't see utilization for trucks outside their allow-list / not
    # in their assigned-trucks set.  Hot path is the dashboard so the
    # extra fetch is acceptable; the filter is in-memory.
    visible = await _wh_reader.get_current_vehicles(user["account_id"])
    visible = filter_by_allowed_companies(visible, allowed)
    visible = await filter_by_assigned_trucks(visible, user)
    name_by_vid: dict[str, dict] = {
        str(v.get("id") or ""): v for v in visible if v.get("id")
    }

    enriched: list[dict] = []
    for r in rows:
        vid = str(r.get("vehicle_id") or "")
        meta = name_by_vid.get(vid)
        if not meta:
            continue
        enriched.append({
            **r,
            "vehicle_name":   meta.get("name") or "",
            "company_code":   meta.get("_org") or meta.get("company_code") or "",
        })
    enriched.sort(key=lambda r: float(r.get("utilization_pct") or 0), reverse=True)
    return {"days": days, "vehicles": enriched}


# ── Period mileage (odometer-delta engine, warehouse) ──────────────
#
# "How many miles did each vehicle drive between these dates?" — the
# Samsara Trip-History-style question, answered from OUR stored
# end-of-day odometer history (730 days), zero live API calls.  Literal
# route: placed before ``/{vehicle_name}`` so the path isn't swallowed.

# The daily table keeps 730 days — ranges past it can only return
# silent zeros, so they're rejected honestly instead.
MILEAGE_RETENTION_DAYS = 730


def _parse_boundary(value: str, *, is_end: bool) -> tuple[str, str | None]:
    """``"YYYY-MM-DD"`` or ``"YYYY-MM-DD[T ]HH:MM[:SS]"`` →
    ``(day, "HH:MM:SS" | None)``.  A bare date means the whole day —
    which for an END boundary is 23:59:59, not midnight."""
    v = (value or "").strip().replace(" ", "T")
    day, _, clock = v.partition("T")
    from datetime import date, time as _time
    try:
        date.fromisoformat(day)
    except ValueError:
        raise HTTPException(422, "start/end must be YYYY-MM-DD or YYYY-MM-DDTHH:MM")
    if not clock:
        return day, None
    try:
        t = _time.fromisoformat(clock)
    except ValueError:
        raise HTTPException(422, f"bad time of day: {clock!r}")
    del is_end
    return day, t.strftime("%H:%M:%S")


async def _mileage_bounds(account_id: int, start: str, end: str):
    """Parse + validate both boundaries; returns
    ``(start_day, end_day, start_ts_utc, end_ts_utc, tz)`` where the ts
    values are naive-UTC ISO strings (or None when no time was given).
    Times are interpreted in the ACCOUNT timezone — the same clock the
    rest of the Mileage tab speaks."""
    from datetime import date, datetime, timedelta
    from zoneinfo import ZoneInfo
    from infra.services import get_platform_db as _get_pdb

    s_day, s_clock = _parse_boundary(start, is_end=False)
    e_day, e_clock = _parse_boundary(end, is_end=True)
    if (s_day, s_clock or "00:00:00") > (e_day, e_clock or "23:59:59"):
        raise HTTPException(422, "start must be on or before end")
    if date.fromisoformat(s_day) < date.today() - timedelta(days=MILEAGE_RETENTION_DAYS):
        raise HTTPException(
            422,
            f"odometer history is kept {MILEAGE_RETENTION_DAYS} days — "
            "choose a more recent start date",
        )
    tz = None
    s_ts = e_ts = None
    if s_clock or e_clock:
        account = await _get_pdb().get_account(account_id)
        try:
            tz = ZoneInfo((getattr(account, "timezone", None) or "")
                          or "America/New_York")
        except Exception:
            tz = ZoneInfo("America/New_York")

        def _to_utc(day: str, clock: str) -> str:
            local = datetime.fromisoformat(f"{day}T{clock}").replace(tzinfo=tz)
            return local.astimezone(ZoneInfo("UTC")).strftime("%Y-%m-%dT%H:%M:%S")
        if s_clock:
            s_ts = _to_utc(s_day, s_clock)
        if e_clock:
            e_ts = _to_utc(e_day, e_clock)
    return s_day, e_day, s_ts, e_ts, tz


def _validate_mileage_range(start: str, end: str) -> None:
    """Kept for callers that only need day validation."""
    from datetime import date, timedelta
    try:
        s, e = date.fromisoformat(start[:10]), date.fromisoformat(end[:10])
    except ValueError:
        raise HTTPException(422, "start/end must be YYYY-MM-DD")
    if s > e:
        raise HTTPException(422, "start must be on or before end")
    if s < date.today() - timedelta(days=MILEAGE_RETENTION_DAYS):
        raise HTTPException(
            422,
            f"odometer history is kept {MILEAGE_RETENTION_DAYS} days — "
            "choose a more recent start date",
        )


def _merge_unit_rows(rows: list[dict]) -> list[dict]:
    """Collapse mileage rows to ONE per unit, keyed by (name, COMPANY).

    A truck whose gateway was swapped can keep the retired telematics id
    in ``vehicle_state`` (production: PTG's "6729" carries two ids), and
    each id produced its own row.  Miles sum across those devices — that
    IS the unit's driving — while the odometer span stays the device
    that drove most of them, because two devices' odometers aren't on
    one scale; the ``device_change`` flag says so.

    Company is part of the key on purpose: unit numbers repeat ACROSS
    companies (production: "103" exists in both G1 and OSY), and those
    are different trucks.  Merging on the bare name would fuse two real
    vehicles into one wrong row.
    """
    groups: dict[tuple[str, str], list[dict]] = {}
    for r in rows:
        groups.setdefault(
            (r["vehicle_name"], r.get("company") or ""), []).append(r)
    out: list[dict] = []
    for group in groups.values():
        group.sort(key=lambda g: g["miles"], reverse=True)
        primary = dict(group[0])
        if len(group) > 1:
            primary["miles"] = round(sum(g["miles"] for g in group), 1)
            primary["days_covered"] = max(g["days_covered"] for g in group)
            if sum(1 for g in group if g["miles"] > 0) > 1:
                primary["flag"] = "device_change"
        out.append(primary)
    out.sort(key=lambda v: v["miles"], reverse=True)
    return out


@router.get("/mileage")
async def account_period_mileage(
    start: str = Query(..., description="YYYY-MM-DD, inclusive"),
    end: str = Query(..., description="YYYY-MM-DD, inclusive"),
    user: dict = Depends(require_permission_any(
        "can_vehicle_all", "can_vehicle_vehicle")),
):
    """Miles driven per vehicle in the range, account-wide.

    Same visibility rules as the rest of the vehicles API: company
    allow-list narrows operators, assigned-trucks narrows own-vehicle
    callers.  Vehicles WITHOUT usable odometer history are returned
    separately by name (``no_data``) so the UI can say "no odometer
    data" instead of silently omitting them (omitted ≠ zero).
    """
    s_day, e_day, s_ts, e_ts, _tz = await _mileage_bounds(
        user["account_id"], start, end)
    tenant_db = await _get_tenant_db(user["account_id"])
    rows = await tenant_db.get_period_mileage(
        user["account_id"], s_day, e_day, start_ts=s_ts, end_ts=e_ts)

    # Visibility cross-reference — mirrors /utilization-summary.
    allowed = await get_user_company_codes(user)
    visible = await _wh_reader.get_current_vehicles(user["account_id"])
    visible = filter_by_allowed_companies(visible, allowed)
    visible = await filter_by_assigned_trucks(visible, user)
    meta_by_vid = {str(v.get("id") or ""): v for v in visible if v.get("id")}

    covered: set[str] = set()
    seen: list[dict] = []
    for r in rows:
        meta = meta_by_vid.get(r["vehicle_id"])
        if not meta:
            continue
        covered.add(r["vehicle_id"])
        seen.append({
            **r,
            "vehicle_name": meta.get("name") or r["vehicle_name"],
            "company": meta.get("_org") or meta.get("company_code") or "",
        })

    vehicles = _merge_unit_rows(seen)
    named = {(v["vehicle_name"], v.get("company") or "") for v in vehicles}

    no_data = sorted(
        (v.get("name") or "") for vid, v in meta_by_vid.items()
        if vid not in covered
    )
    _named_only = {n for n, _co in named}
    no_data = [n for n in no_data if n and n not in _named_only]
    # How current the stored odometer history actually is.  When the
    # ingest stalls (or simply hasn't rolled up today yet) the newest
    # reading trails the requested end, and the totals are short by
    # exactly that much — the UI says so instead of letting the number
    # quietly disagree with Samsara.
    data_through = max((v.get("end_read_on") or "" for v in vehicles),
                       default="")
    # Time-of-day honesty: when exact times were requested, name the
    # vehicles the tiers couldn't answer at that precision (their rows
    # fell back to whole-day boundaries).
    imprecise: list[str] = []
    if s_ts or e_ts:
        # Same unit number in two companies = two real trucks — the
        # bare name would print as a baffling duplicate ("103, 103"),
        # so repeated names carry their company.
        hits = [(v["vehicle_name"], v.get("company") or "")
                for v in vehicles
                if (s_ts and not v.get("start_precise"))
                or (e_ts and not v.get("end_precise"))]
        name_counts: dict[str, int] = {}
        for nm, _co in hits:
            name_counts[nm] = name_counts.get(nm, 0) + 1
        imprecise = sorted(
            f"{nm} ({co})" if name_counts[nm] > 1 and co else nm
            for nm, co in hits
        )
    return {
        "start": start, "end": end,
        "vehicles": vehicles,
        "total_miles": round(sum(v["miles"] for v in vehicles), 1),
        "no_data": no_data,
        "data_through": data_through,
        "time_requested": bool(s_ts or e_ts),
        "imprecise_time_for": imprecise,
    }


@router.get("/{vehicle_name}/mileage")
async def vehicle_period_mileage(
    vehicle_name: str,
    start: str = Query(..., description="YYYY-MM-DD, inclusive"),
    end: str = Query(..., description="YYYY-MM-DD, inclusive"),
    user: dict = Depends(require_permission_any(
        "can_vehicle_all", "can_vehicle_vehicle")),
):
    """One vehicle's period mileage + per-day breakdown (detail page).

    Own-vehicle callers can only ask about their assigned truck —
    enforced against the same visible-vehicles set as the list route,
    so this can't become a side door around Team-Management scoping.
    """
    s_day, e_day, s_ts, e_ts, _tz = await _mileage_bounds(
        user["account_id"], start, end)
    allowed = await get_user_company_codes(user)
    visible = await _wh_reader.get_current_vehicles(user["account_id"])
    visible = filter_by_allowed_companies(visible, allowed)
    visible = await filter_by_assigned_trucks(visible, user)
    if not any((v.get("name") or "").lower() == vehicle_name.lower()
               for v in visible):
        raise HTTPException(404, "Vehicle not found")
    tenant_db = await _get_tenant_db(user["account_id"])
    out = await tenant_db.get_vehicle_period_mileage(
        user["account_id"], vehicle_name, s_day, e_day,
        start_ts=s_ts, end_ts=e_ts)
    if out is None:
        return {"start": start, "end": end, "vehicle_name": vehicle_name,
                "no_data": True}
    return {"start": start, "end": end, "no_data": False, **out}


@router.get("/{vehicle_name}/trips")
async def vehicle_period_trips(
    vehicle_name: str,
    start: str = Query(..., description="YYYY-MM-DD, inclusive"),
    end: str = Query(..., description="YYYY-MM-DD, inclusive"),
    user: dict = Depends(require_permission_any(
        "can_vehicle_all", "can_vehicle_vehicle")),
):
    """Trip segments (start→stop) for one vehicle in the range — the
    drill-in behind a Mileage row.

    Unlike the mileage numbers (our stored odometer history), trips
    need a LIVE Samsara call — on-demand for one vehicle, one range,
    riding the client's circuit breaker.  Day boundaries follow the
    ACCOUNT timezone so "Jul 26" here means the same day the Mileage
    row labeled.  Same visibility wall as the mileage detail: a truck
    outside the caller's visible set is a 404, not a fetch.
    """
    s_day, e_day, s_clock_ts, e_clock_ts, _tz0 = await _mileage_bounds(
        user["account_id"], start, end)
    allowed = await get_user_company_codes(user)
    visible = await _wh_reader.get_current_vehicles(user["account_id"])
    visible = filter_by_allowed_companies(visible, allowed)
    visible = await filter_by_assigned_trucks(visible, user)
    meta = next(
        (v for v in visible
         if (v.get("name") or "").lower() == vehicle_name.lower()),
        None,
    )
    if meta is None:
        raise HTTPException(404, "Vehicle not found")
    samsara_id = str(meta.get("id") or "")
    company = meta.get("_org") or meta.get("company_code") or ""
    if not samsara_id:
        return {"start": start, "end": end, "vehicle_name": vehicle_name,
                "no_data": True, "reason": "not_linked", "trips": []}

    # Range → epoch-ms bounds in the ACCOUNT's timezone, so the day
    # labels match the Mileage tab's day math.
    from datetime import datetime, timedelta
    from zoneinfo import ZoneInfo
    from infra.services import get_platform_db as _get_pdb
    account = await _get_pdb().get_account(user["account_id"])
    try:
        tz = ZoneInfo((account.timezone if account else "") or "America/New_York")
    except Exception:
        tz = ZoneInfo("America/New_York")
    # Exact times win when given (parsed to UTC already); bare dates
    # keep whole-day semantics in the account timezone.
    if s_clock_ts:
        start_dt = datetime.fromisoformat(s_clock_ts).replace(tzinfo=ZoneInfo("UTC"))
    else:
        start_dt = datetime.fromisoformat(s_day).replace(tzinfo=tz)
    if e_clock_ts:
        end_dt = datetime.fromisoformat(e_clock_ts).replace(tzinfo=ZoneInfo("UTC"))
    else:
        end_dt = (datetime.fromisoformat(e_day) + timedelta(days=1)).replace(tzinfo=tz)

    from infra.services import get_client
    from adapters.telematics.samsara.circuit_breaker import SamsaraUnavailable
    mc = await get_client(user["account_id"])
    sc = (mc.clients.get(company) if company else None) \
        or next(iter(mc.clients.values()), None)
    if sc is None:
        raise HTTPException(503, "Telematics is not connected for this account")
    try:
        raw = await sc.get_vehicle_trips(
            samsara_id,
            int(start_dt.timestamp() * 1000),
            int(end_dt.timestamp() * 1000),
        )
    except SamsaraUnavailable:
        raise HTTPException(
            503,
            "Samsara is unreachable right now — mileage totals still "
            "work (stored history); trips need the live API.",
        )
    except Exception as e:
        logger.warning("trips fetch failed for %s/%s: %s",
                       user["account_id"], vehicle_name, e)
        raise HTTPException(502, "Trip history fetch failed")

    def _loc(t: dict, key: str) -> str:
        v = t.get(f"{key}Location")
        if isinstance(v, str) and v:
            return v
        addr = t.get(f"{key}Address")
        if isinstance(addr, dict) and addr.get("name"):
            return str(addr["name"])
        coords = t.get(f"{key}Coordinates")
        if isinstance(coords, dict) and coords.get("latitude") is not None:
            return f"{coords['latitude']:.4f}, {coords['longitude']:.4f}"
        return ""

    # Samsara marks a trip STILL IN PROGRESS with endMs = int64 max —
    # trusting it printed a 2.5-trillion-hour duration.  Anything ending
    # visibly in the future is in progress: duration runs start → now,
    # and the row says so instead of faking an end time.
    import time as _time
    _now_ms = int(_time.time() * 1000)
    _future_cutoff = _now_ms + 60 * 60 * 1000

    trips = []
    total_m = 0.0
    driving_min = 0.0
    for t in sorted(raw, key=lambda t: t.get("startMs") or 0, reverse=True):
        s_ms, e_ms = int(t.get("startMs") or 0), int(t.get("endMs") or 0)
        in_progress = e_ms <= 0 or e_ms > _future_cutoff
        eff_end = _now_ms if in_progress else e_ms
        miles = float(t.get("distanceMeters") or 0) / 1609.344
        dur = max(0.0, (eff_end - s_ms) / 60_000)
        total_m += miles
        driving_min += dur
        trips.append({
            "start_ms": s_ms,
            "end_ms": 0 if in_progress else e_ms,
            "in_progress": in_progress,
            "duration_min": round(dur, 1),
            "start_location": _loc(t, "start"),
            "end_location": _loc(t, "end"),
            "miles": round(miles, 1),
            "driver_id": t.get("driverId"),
        })
    return {
        "start": start, "end": end,
        "vehicle_name": meta.get("name") or vehicle_name,
        "no_data": False,
        "trips": trips,
        "trip_count": len(trips),
        "total_trip_miles": round(total_m, 1),
        "driving_min": round(driving_min, 1),
    }

# Source precedence moved to features/vehicles/config.py — config is a
# separate action from view, so it is a separate file (/vehicles/config).
# That module MUST be mounted before this router: /{vehicle_name} below
# would otherwise swallow /vehicles/config.


@router.get("/{vehicle_name}")
async def vehicle_detail(
    vehicle_name: str,
    company: str | None = Query(None),
    user: dict = Depends(require_permission_any("can_faults", "can_vehicle_vehicle")),
):
    """Single vehicle detail by name.

    Live Samsara is the source for static metadata (VIN, make, model,
    license plate) which the warehouse intentionally doesn't track.
    Dynamic telemetry (odometer_miles + odometer_time) is merged in
    from ``vehicle_state`` so the value is consistent with everywhere
    else in the app — DB stays the single source of truth for state.
    """
    allowed = await get_user_company_codes(user)
    validate_company_access(allowed, company)
    matches = await _svc_vehicle_detail(user["account_id"], vehicle_name, company=company)
    matches = filter_by_allowed_companies(matches, allowed)
    matches = await filter_by_assigned_trucks(matches, user)
    if not matches:
        # Registry fallback — trailers and manual vehicles exist only
        # in the registry (SSOT), so a row click on them must not land
        # on "Vehicle not found".  merge_registry_with_live([v], [])
        # synthesizes the same no-telemetry overview row the list uses.
        tenant_reg = await _get_tenant_db(user["account_id"])
        if tenant_reg is not None:
            try:
                registry = await tenant_reg.list_vehicles(
                    user["account_id"], company_code=company,
                )
            except Exception:
                logger.warning(
                    "registry fallback failed for acct=%s vehicle=%s",
                    user["account_id"], vehicle_name, exc_info=True,
                )
                registry = []
            needle = vehicle_name.lower()
            reg_matches = _wh_reader.merge_registry_with_live(
                [v for v in registry if v.unit_number.lower() == needle], [],
            )
            reg_matches = filter_by_allowed_companies(reg_matches, allowed)
            matches = await filter_by_assigned_trucks(reg_matches, user)
    if not matches:
        return {"error": "Vehicle not found", "vehicles": []}

    # Enrich with warehouse-sourced odometer + engine hours.  Read the
    # raw vehicle_state table directly via the mixin — bypasses the
    # WAREHOUSE_READS_ENABLED cutover flag because both fields are
    # *only* in the warehouse, never in the live overview.
    tenant = await _get_tenant_db(user["account_id"])
    warehouse_rows = await tenant.get_vehicle_state(
        user["account_id"], company=company, vehicle_nums=[vehicle_name],
    )
    odometer_by_id: dict[str, dict] = {}
    odometer_by_name: dict[str, dict] = {}
    engine_hours_by_id: dict[str, dict] = {}
    engine_hours_by_name: dict[str, dict] = {}
    for row in warehouse_rows:
        rid = str(row.get("vehicle_id") or "")
        rname = (row.get("vehicle_name") or "").lower()
        miles = row.get("odometer_mi")
        if miles is not None:
            odometer = {"miles": miles, "time": row.get("odometer_time")}
            if rid:
                odometer_by_id[rid] = odometer
            if rname:
                odometer_by_name[rname] = odometer
        hours = row.get("engine_hours")
        if hours is not None:
            eng = {"hours": hours, "time": row.get("engine_hours_time")}
            if rid:
                engine_hours_by_id[rid] = eng
            if rname:
                engine_hours_by_name[rname] = eng
    for match in matches:
        match_id = str(match.get("id") or "")
        match_name = (match.get("name") or "").lower()
        if not match.get("odometer"):
            o_hit = odometer_by_id.get(match_id) or odometer_by_name.get(match_name)
            if o_hit:
                match["odometer"] = o_hit
        if not match.get("engine_hours_reading"):
            e_hit = engine_hours_by_id.get(match_id) or engine_hours_by_name.get(match_name)
            if e_hit:
                match["engine_hours_reading"] = e_hit

    normalized = [_normalize_detail(m) for m in matches]
    return {"vehicles": normalized, "count": len(normalized)}


@router.get("/{vehicle_name}/health")
async def vehicle_health(
    vehicle_name: str,
    company: str | None = Query(None),
    user: dict = Depends(require_permission_any("can_health", "can_vehicle_vehicle")),
):
    """Vehicle health stats: battery, oil, coolant, DEF, seatbelt, engine load."""
    allowed = await get_user_company_codes(user)
    validate_company_access(allowed, company)
    all_health = await _svc_vehicle_health(user["account_id"], company=company)
    all_health = filter_by_allowed_companies(all_health, allowed)
    all_health = await filter_by_assigned_trucks(all_health, user)
    name_lower = vehicle_name.lower()
    match = [v for v in all_health if v.get("name", "").lower() == name_lower]
    if not match:
        return {"error": "Vehicle not found or no health data", "health": None}
    v = match[0]
    return {
        "name": v.get("name"),
        "company": v.get("_org", ""),
        "health": v.get("_health", {}),
        "alerts": v.get("_health_alerts", []),
    }


@router.get("/{vehicle_name}/faults")
async def vehicle_faults(
    vehicle_name: str,
    company: str | None = Query(None),
    user: dict = Depends(require_permission_any("can_faults", "can_vehicle_all", "can_vehicle_vehicle")),
):
    """Active fault codes for a specific vehicle."""
    allowed = await get_user_company_codes(user)
    validate_company_access(allowed, company)
    overview = await _svc_vehicles_overview(user["account_id"], company=company)
    overview = filter_by_allowed_companies(overview, allowed)
    overview = await filter_by_assigned_trucks(overview, user)
    name_lower = vehicle_name.lower()
    match = [v for v in overview if v.get("name", "").lower() == name_lower]
    if not match:
        return {"error": "Vehicle not found", "faults": []}
    v = match[0]

    # Prefer the fault snapshot — it carries the live Samsara shape
    # (spnDescription / fmiDescription / sourceAddressName) so the UI
    # can render real fault names.  ``_extract_dtcs`` on the warehouse
    # vehicle row only sees ``[{}, {}, …]`` placeholders because
    # vehicle_state stores fault_count, not per-DTC detail — that's
    # why the dashboard was rendering 4 generic "DTC" rows.
    try:
        snap = await _wh_reader.get_vehicle_fault_snapshot(
            user["account_id"], v.get("name", ""),
        )
    except Exception:
        snap = None
    if snap:
        snap_dtcs = (
            (snap.get("fault_codes") or {})
            .get("j1939", {})
            .get("diagnosticTroubleCodes")
        ) or snap.get("dtcs") or []
        if snap_dtcs:
            return {
                "name": v.get("name"),
                "company": v.get("_org", ""),
                "faults": snap_dtcs,
                "fault_count": len(snap_dtcs),
            }

    # Fallback — placeholder DTCs from vehicle_state (count only).
    dtcs = _extract_dtcs(v)
    return {
        "name": v.get("name"),
        "company": v.get("_org", ""),
        "faults": dtcs,
        "fault_count": len(dtcs),
    }


@router.get("/{vehicle_name}/reading-as-of")
async def vehicle_reading_as_of(
    vehicle_name: str,
    date: str = Query(..., description="Service date (YYYY-MM-DD)"),
    user: dict = Depends(require_permission_any(
        "can_work_orders_all", "can_faults", "can_vehicle_vehicle",
    )),
):
    """Odometer + engine-hours for a vehicle AS OF a date — backs the
    work-order form's back-dated auto-fill.

    When a snapshot exists, returns it.  When none does, returns nulls plus
    the vehicle's snapshot *coverage window* (``telematics_linked`` +
    ``coverage_start``/``coverage_end``) so the caller can explain WHY —
    no telematics link vs. history not reaching that far back."""
    tenant_db = await _get_tenant_db(user["account_id"])
    r = await tenant_db.get_reading_as_of(user["account_id"], vehicle_name, date)
    if r:
        return {**r, "telematics_linked": True}
    cov = await tenant_db.get_snapshot_coverage(user["account_id"], vehicle_name)
    return {
        "odometer_miles": None, "engine_hours": None, "as_of": None,
        "telematics_linked": cov["telematics_linked"],
        "coverage_start": cov["coverage_start"],
        "coverage_end": cov["coverage_end"],
    }


@router.get("/{vehicle_name}/timeline")
async def vehicle_timeline(
    vehicle_name: str,
    days: int = Query(7, ge=1, le=30),
    company: str | None = Query(None),
    user: dict = Depends(require_permission_any("can_faults", "can_vehicle_all", "can_vehicle_vehicle")),
):
    """Hourly telemetry roll-up for a single vehicle (warehouse).

    Returns oldest-first ``points`` from the hourly tier of ``vehicle_telemetry``.
    Returns empty list when the warehouse flag is off or the tier is cold.
    """
    allowed = await get_user_company_codes(user)
    validate_company_access(allowed, company)
    matches = await _svc_vehicle_detail(user["account_id"], vehicle_name, company=company)
    matches = filter_by_allowed_companies(matches, allowed)
    matches = await filter_by_assigned_trucks(matches, user)
    if not matches:
        return {"error": "Vehicle not found", "points": []}
    vehicle_id = str(matches[0].get("id") or "")
    if not vehicle_id:
        return {"name": matches[0].get("name"), "points": []}
    points = await _wh_reader.get_vehicle_telemetry_hourly(
        user["account_id"], vehicle_id=vehicle_id, hours=days * 24,
    )
    points = list(reversed(points))  # reader returns DESC; chart wants oldest-first
    return {
        "name": matches[0].get("name"),
        "vehicle_id": vehicle_id,
        "days": days,
        "points": points,
    }


@router.get("/{vehicle_name}/usage")
async def vehicle_usage(
    vehicle_name: str,
    days: int = Query(30, ge=7, le=365),
    company: str | None = Query(None),
    user: dict = Depends(require_permission_any("can_faults", "can_vehicle_all", "can_vehicle_vehicle")),
):
    """Per-vehicle usage summary + daily series over the window.

    Combines the daily roll-up (miles, drive/idle hours, harsh events)
    with the work-orders cost aggregate to produce a single payload
    the dashboard renders as the "Usage trends" card on the vehicle
    detail page.  Fleet ops uses utilization; accounting uses
    cost-per-mile; safety uses harsh-event totals — same query.
    """
    allowed = await get_user_company_codes(user)
    validate_company_access(allowed, company)
    matches = await _svc_vehicle_detail(user["account_id"], vehicle_name, company=company)
    matches = filter_by_allowed_companies(matches, allowed)
    matches = await filter_by_assigned_trucks(matches, user)
    if not matches:
        return {"error": "Vehicle not found", "summary": None, "series": []}
    vehicle_id = str(matches[0].get("id") or "")
    if not vehicle_id:
        return {"name": matches[0].get("name"), "summary": None, "series": []}
    summary = await _wh_reader.get_vehicle_usage_summary(
        user["account_id"], vehicle_id, days=days,
    )
    series = await _wh_reader.get_vehicle_metrics_daily(
        user["account_id"], vehicle_id=vehicle_id, days=days,
    )
    return {
        "name":       matches[0].get("name"),
        "vehicle_id": vehicle_id,
        "days":       days,
        "summary":    summary,
        "series":     series,
    }


# ── Registry management (add / edit / remove vehicles) ───────────
#
# The vehicle registry in our DB is the single source of truth.  These
# write endpoints let an operator add a truck or trailer by hand —
# including equipment Samsara doesn't report — and are gated by the
# delegatable ``can_manage_vehicles`` permission (Owner/Admin/Fleet by
# default; grantable to any role via the Permissions matrix).  Reads
# come through the merged ``GET /vehicles/`` list, which already carries
# ``vehicle_type`` / ``source`` / ``registry_id`` per row.

_manage_vehicles = require_permission("can_manage_vehicles")


class VehicleCreate(BaseModel):
    unit_number: str = Field(..., min_length=1, max_length=64)
    vehicle_type: str = Field("truck")
    company_code: str = Field("", max_length=64)
    vin: str = Field("", max_length=64)
    plate_number: str = Field("", max_length=32)
    make: str = Field("", max_length=64)
    model: str = Field("", max_length=64)
    year: int | None = Field(None, ge=1900, le=2100)
    notes: str = Field("", max_length=500)


class VehicleUpdate(BaseModel):
    unit_number: str | None = Field(None, min_length=1, max_length=64)
    vehicle_type: str | None = None
    company_code: str | None = Field(None, max_length=64)
    vin: str | None = Field(None, max_length=64)
    plate_number: str | None = Field(None, max_length=32)
    make: str | None = Field(None, max_length=64)
    model: str | None = Field(None, max_length=64)
    year: int | None = Field(None, ge=1900, le=2100)
    status: str | None = None
    notes: str | None = Field(None, max_length=500)


def _vehicle_to_dict(v) -> dict:
    return {
        "id": v.id, "company_code": v.company_code,
        "unit_number": v.unit_number, "vehicle_type": v.vehicle_type,
        "vin": v.vin, "plate_number": v.plate_number, "make": v.make,
        "model": v.model, "year": v.year, "status": v.status,
        "source": v.source, "notes": v.notes,
    }


@router.post("/")
async def create_vehicle(
    body: VehicleCreate,
    user: dict = Depends(_manage_vehicles),
):
    """Add a vehicle to the registry by hand (truck, trailer, or other).
    Works with no telematics — the row stands on its own and is enriched
    later if an integration matches it."""
    account_id = int(user["account_id"])
    tenant = await _get_tenant_db(account_id)
    if tenant is None:
        raise HTTPException(503, "tenant DB unavailable")
    try:
        vid = await tenant.add_vehicle(
            account_id,
            unit_number=body.unit_number,
            vehicle_type=body.vehicle_type,
            company_code=body.company_code,
            vin=body.vin, plate_number=body.plate_number,
            make=body.make, model=body.model, year=body.year,
            notes=body.notes, source="manual",
            actor_user_id=await resolve_user_id(user),
        )
    except ValueError as e:
        raise HTTPException(400, str(e))
    except Exception as e:
        # UNIQUE(account_id, company_code, unit_number) collision, etc.
        raise HTTPException(409, f"could not add vehicle: {e}")
    v = await tenant.get_vehicle(account_id, vid)
    return _vehicle_to_dict(v)


@router.put("/registry/{vehicle_id}")
async def update_registry_vehicle(
    vehicle_id: int,
    body: VehicleUpdate,
    user: dict = Depends(_manage_vehicles),
):
    """Edit a registry vehicle's spec / type / status."""
    account_id = int(user["account_id"])
    tenant = await _get_tenant_db(account_id)
    if tenant is None:
        raise HTTPException(503, "tenant DB unavailable")
    fields = {k: v for k, v in body.model_dump().items() if v is not None}
    try:
        ok = await tenant.update_vehicle(
            account_id, vehicle_id,
            actor_user_id=await resolve_user_id(user), **fields,
        )
    except ValueError as e:
        raise HTTPException(400, str(e))
    if not ok:
        raise HTTPException(404, "vehicle not found")
    return _vehicle_to_dict(await tenant.get_vehicle(account_id, vehicle_id))


@router.delete("/registry/{vehicle_id}")
async def delete_registry_vehicle(
    vehicle_id: int,
    user: dict = Depends(_manage_vehicles),
):
    """Soft-delete a registry vehicle.  History (maintenance, fuel,
    inspections referencing the unit by name) is untouched."""
    account_id = int(user["account_id"])
    tenant = await _get_tenant_db(account_id)
    if tenant is None:
        raise HTTPException(503, "tenant DB unavailable")
    ok = await tenant.deactivate_vehicle(
        account_id, vehicle_id,
        actor_user_id=await resolve_user_id(user),
    )
    if not ok:
        raise HTTPException(404, "vehicle not found")
    return {"deactivated": True, "id": vehicle_id}
