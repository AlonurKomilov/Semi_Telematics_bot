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

from features.vehicles.scope import company_allows
from interfaces.api.deps import (
    require_permission_any,
    get_user_company_codes,
    validate_company_access,
    filter_by_company_map,
    filter_by_allowed_companies,
    filter_by_assigned_trucks,
    require_permission,
    paginate,
    resolve_user_id,
)
from capabilities import source as reconciliation
from features.vehicles.service import (
    get_vehicles_overview as _svc_vehicles_overview,
    get_vehicle_detail as _svc_vehicle_detail,
)
from features.vehicles.warehouse.service import (
    get_vehicle_health as _svc_vehicle_health,
    get_fleet_weather as _svc_fleet_weather,
)
import aiohttp

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


def _derive_engine_state(status: str, reported: str | None = None) -> str:
    """The engine state to SHOW.

    ``reported`` is what the truck actually told us — the value the
    ingest resolved and stored, surfaced by the warehouse reader as
    ``location.engineStates.value``.  When we have it, it WINS:
    ``status`` is only a speed inference here, because
    ``classify_vehicle_status`` looks for an ``engineState`` key while
    the warehouse supplies ``engineStates``, so its authoritative
    branch never matches.  Speed and engine are different facts, and
    inferring one from the other was wrong in both directions — 28
    trucks idling with wheels still read "Off", and a truck parked six
    days with its engine off read "Idle" off 1 mph of GPS jitter.

    ``""`` (present but empty) means the ingest looked and found
    nothing — a device that cannot read the engine bus.  That renders
    as UNKNOWN, never "Off": ``resolve_engine_state`` refuses to guess
    there precisely so the roll-ups never count silence as parked, and
    restating the guess here would undo that.

    ``None`` (absent) is different from empty: the payload carries no
    engine field at all, which is the live-Samsara fallback used on a
    cold cache.  There the old speed heuristic still applies — showing
    every truck as "unknown" because our own cache is cold would be a
    worse lie than the one this fixes.
    """
    if reported is not None:
        return {"moving": "On", "idle": "Idle", "off": "Off"}.get(
            reported.strip().lower(), "",
        )
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
    # Absent key vs empty value are different answers — see
    # ``_derive_engine_state``.  Only the warehouse reader emits this
    # field; the live fallback has none, and keeps the old heuristic.
    _eng = loc.get("engineStates")
    _reported = _eng.get("value") or "" if isinstance(_eng, dict) else None
    engine_state = "" if no_telemetry else _derive_engine_state(
        status, _reported,
    )
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
    # Absent key vs empty value are different answers — see
    # ``_derive_engine_state``.  Only the warehouse reader emits this
    # field; the live fallback has none, and keeps the old heuristic.
    _eng = loc.get("engineStates")
    _reported = _eng.get("value") or "" if isinstance(_eng, dict) else None
    engine_state = "" if no_telemetry else _derive_engine_state(
        status, _reported,
    )
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
    # Scoped to the PAGE, not the account: the list renders 50 rows, so
    # asking for the other 137 vehicles' callouts would be work nobody
    # can see.
    callouts = await _vehicle_callouts(
        user["account_id"], tenant_reg,
        [v.get("id") for v in paged["items"]],
    )
    return {
        "vehicles": paged["items"],
        "count": paged["total"],
        "page": paged["page"],
        "page_size": paged["page_size"],
        "total_pages": paged["total_pages"],
        "callouts": callouts,
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
    # UTC date, never the server's local day (Europe here, hours ahead
    # of every account zone) — a 730-day floor doesn't need account-tz
    # precision, but it must not move with the host's clock.
    _floor = (datetime.now(ZoneInfo("UTC")).date()
              - timedelta(days=MILEAGE_RETENTION_DAYS))
    if date.fromisoformat(s_day) < _floor:
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
    from datetime import date, datetime, timedelta, timezone as _tzu
    try:
        s, e = date.fromisoformat(start[:10]), date.fromisoformat(end[:10])
    except ValueError:
        raise HTTPException(422, "start/end must be YYYY-MM-DD")
    if s > e:
        raise HTTPException(422, "start must be on or before end")
    # UTC date, not the host's local day — same rule as _mileage_bounds.
    if s < datetime.now(_tzu.utc).date() - timedelta(days=MILEAGE_RETENTION_DAYS):
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
    # Conditions belong on THIS surface most of all: a truck whose
    # device stopped reading the engine reports 0 miles here while it
    # is genuinely driving, and a zero in a mileage report is read as
    # an idle asset rather than a blind one.  The vehicle page can
    # explain it all it likes — this is where the number misleads.
    callouts = await _vehicle_callouts(
        user["account_id"], tenant_db,
        [v.get("vehicle_id") or v.get("id") for v in vehicles],
    )
    return {
        "start": start, "end": end,
        "vehicles": vehicles,
        "total_miles": round(sum(v["miles"] for v in vehicles), 1),
        "no_data": no_data,
        "data_through": data_through,
        "time_requested": bool(s_ts or e_ts),
        "imprecise_time_for": imprecise,
        "callouts": callouts,
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


async def _resolve_vehicle(
    vehicle_name: str, company: str | None, user: dict, allowed: list[str],
) -> list[dict]:
    """Resolve one truck for a detail-style endpoint, permission-filtered.

    The registry is the SSOT and the provider ENRICHES it, so a truck the
    registry knows must still resolve when the provider cannot answer.
    Two ways it fails to answer, and both land here:

    * It answers with nothing — trailers and manual vehicles exist only
      in the registry, so a row click on them must not read "Vehicle not
      found".  This path already existed on the detail endpoint.
    * It RAISES — a 5s timeout, an HTTP error, or SamsaraUnavailable
      once the breaker opens.  That escaped as a 500 and took the page
      with it, which is what GET /vehicles/224/usage did on 07-29: the
      truck vanished from the dashboard because a third party was slow.

    Caught narrowly on purpose.  ``SamsaraUnavailable`` subclasses
    ``aiohttp.ClientError``, so those two plus ``TimeoutError`` cover
    every provider failure without a bare ``except`` swallowing real
    bugs behind a silently degraded page.
    """
    # Archived trucks never reach the provider.  The detail fetch fans
    # out one round-trip per COMPANY (~3-5s each), and for a retired
    # truck the answer is either nothing (unlinked) or months-old state
    # rendered beside a freshness dot — so the page sat through the
    # whole fan-out to end at what the registry knew instantly, and
    # then the active-only fallback below couldn't see the truck at
    # all, leaving the cards loading forever.  A LIVE truck sharing the
    # name still wins: only when EVERY row answering to it is retired
    # does the short-circuit fire (door numbers are reused, and the
    # truck that inherited one deserves live data).
    tenant_pre = await _get_tenant_db(user["account_id"])
    if tenant_pre is not None:
        try:
            all_rows = await tenant_pre.list_vehicles(
                user["account_id"], company_code=company,
                include_inactive=True,
            )
            needle_pre = vehicle_name.lower()
            named = [v for v in all_rows
                     if v.unit_number.lower() == needle_pre]
            if named and not any(v.is_active for v in named):
                reg_only = _wh_reader.merge_registry_with_live(named, [])
                reg_only = filter_by_allowed_companies(reg_only, allowed)
                return await filter_by_assigned_trucks(reg_only, user)
        except Exception:
            logger.debug("archived pre-check failed acct=%s",
                         user["account_id"], exc_info=True)

    try:
        matches = await _svc_vehicle_detail(
            user["account_id"], vehicle_name, company=company,
        )
    except (aiohttp.ClientError, TimeoutError):
        # WARNING, not debug: a provider outage must be visible in the
        # logs rather than hiding behind a page that still renders.
        logger.warning(
            "telematics lookup failed for acct=%s vehicle=%s — "
            "serving the registry record without live telemetry",
            user["account_id"], vehicle_name, exc_info=True,
        )
        matches = []
    else:
        matches = filter_by_allowed_companies(matches, allowed)
        matches = await filter_by_assigned_trucks(matches, user)
        if matches:
            return matches

    # merge_registry_with_live([v], []) synthesizes the same
    # no-telemetry overview row the list builds for a registry-only
    # vehicle, so every consumer sees one shape.
    tenant_reg = await _get_tenant_db(user["account_id"])
    if tenant_reg is None:
        return []
    try:
        # include_inactive: an archived truck's page must resolve from
        # the registry — its record is the reason archiving keeps the
        # row.  Active-only here meant provider-miss + archived = a
        # page that never finished loading.
        registry = await tenant_reg.list_vehicles(
            user["account_id"], company_code=company,
            include_inactive=True,
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
    return await filter_by_assigned_trucks(reg_matches, user)


@router.get("/{vehicle_name}/trips")
async def vehicle_period_trips(
    vehicle_name: str,
    start: str = Query(..., description="YYYY-MM-DD, inclusive"),
    end: str = Query(..., description="YYYY-MM-DD, inclusive"),
    company: str | None = Query(None),
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
    # Company narrows the name match: unit numbers repeat ACROSS
    # companies ("103" is a real truck in both G1 and OSY), and the
    # first-name-match this used to do could open the OTHER company's
    # trips behind a Mileage row.  The drawer always sends its row's
    # company; a bare call (no duplicate) behaves as before.
    matches = [
        v for v in visible
        if (v.get("name") or "").lower() == vehicle_name.lower()
        and (company is None
             or (v.get("_org") or v.get("company_code") or "") == company)
    ]
    meta = matches[0] if matches else None
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


# ── Device-identity events — the watch's resolution flow ──────────
# The ingest's identity watch records anchor changes (VIN / gateway /
# odometer scale) in warehouse.device_event_log and notifies admins.
# These endpoints close the loop: an admin answers the question the
# event asks.  A vin_change offers the real decision — same truck
# (accept) or a different truck behind the gateway (split the unit);
# other kinds just acknowledge.
#
# Registered ABOVE /{vehicle_name} on purpose: FastAPI matches in
# registration order, and the catch-all would swallow /device-events.

# Shared by this block and the registry-admin section below.
_manage_vehicles = require_permission("can_manage_vehicles")


class DeviceEventResolve(BaseModel):
    # ``dismissed`` replaced ``acknowledge``: acknowledging is the
    # ALERTS workspace verb (accepting a work item routed to you), and
    # these rows route nothing.  The old value stays accepted so a
    # browser tab left open across the deploy still resolves instead of
    # 422-ing, and so historical rows keep meaning what they said.
    action: str = Field(
        ...,
        pattern="^(same_truck|different_truck|dismissed|acknowledge)$",
    )
    # different_truck only: identity of the NEW unit to create.
    company_code: str = Field("", max_length=64)
    unit_number: str = Field("", max_length=64)
    archive_old: bool = False


async def _registry_company_map(tenant, account_id: int) -> dict:
    """``registry_id -> company_code`` for this account's vehicles.

    Rows with no company are omitted rather than mapped to '': absent
    from the map means "unresolved", which `filter_by_company_map` keeps
    — and an unscoped vehicle should be visible to anyone holding the
    permission, which is the same answer.
    """
    try:
        rows = await tenant.list_vehicles(account_id)
    except Exception:
        return {}                      # cold source -> helper fails open
    return {v.id: v.company_code for v in rows
            if getattr(v, "company_code", "")}


@router.get("/device-events")
async def list_device_events(
    user: dict = Depends(_manage_vehicles),
):
    """Identity events, open first — the Vehicles page's notice card."""
    account_id = int(user["account_id"])
    tenant = await _get_tenant_db(account_id)
    if tenant is None:
        raise HTTPException(503, "tenant DB unavailable")
    events = await tenant.get_device_events(account_id)
    # These rows carry no company column, so the company comes from the
    # registry via `filter_by_company_map`.  Keyed on registry_id, not
    # vehicle_name: unit numbers are reused across companies once a truck
    # retires, so a name key would resolve some rows to the wrong wall.
    # The helper's documented FAIL-OPEN posture applies — an unplaced row
    # (registry_id NULL) is kept rather than hidden, because a device
    # nobody has placed yet is exactly what this card exists to surface.
    allowed = await get_user_company_codes(user)
    if allowed:
        company_by_id = await _registry_company_map(tenant, account_id)
        events = filter_by_company_map(
            events, allowed, company_by_id, key="registry_id")
    # Devices reporting telemetry that resolves to no vehicle.  The
    # quarantine was written every tick and read by nothing, so two of
    # them accumulated 20,263 ingest ticks over eleven and four days
    # while being invisible everywhere: not pickable for a trigger, not
    # alertable, not listed.  Same card, because it is the same question
    # a person is already here to answer — "is my fleet's identity
    # straight?" — and a second card would be a second place to forget.
    try:
        orphans = await tenant.list_ingest_orphans(account_id)
    except Exception:
        orphans = []                    # never fail the card for this
    if allowed:
        orphans = [o for o in orphans
                   if company_allows(o.get("company_code") or "", allowed)]
    return {
        "events": events,
        "open_count": sum(1 for e in events if e.get("status") == "open"),
        "orphans": orphans,
        "orphan_count": len(orphans),
    }


@router.post("/device-events/{event_id}/resolve")
async def resolve_device_event(
    event_id: int,
    body: DeviceEventResolve,
    user: dict = Depends(_manage_vehicles),
):
    """Answer an open identity event.

    ``different_truck`` (vin_change only) performs the unit split in
    the registry — new unit created with the new VIN, telematics link
    moved onto it, the old unit's true VIN restored, optional retire —
    then closes the event.  ``same_truck`` / ``acknowledge`` just
    close it.  Resolving an already-resolved event is a 409, so two
    admins clicking at once can't split twice.
    """
    account_id = int(user["account_id"])
    tenant = await _get_tenant_db(account_id)
    if tenant is None:
        raise HTTPException(503, "tenant DB unavailable")
    event = await tenant.get_device_event(account_id, event_id)
    if event is None:
        raise HTTPException(404, "event not found")
    # An id-referencing WRITE, so it takes the same wall as restore/PUT/
    # DELETE — and 404 for the same reason.  The listing beside it was
    # walled first; leaving this one open would have meant a restricted
    # caller could not SEE an event but could still resolve it, which is
    # the exact read/write split this contract exists to end.  An event
    # with no registry_id yet is unplaced, not foreign: it stays
    # resolvable, matching the listing's fail-open.
    if event.get("registry_id"):
        await _wall_registry_vehicle(
            tenant, account_id, int(event["registry_id"]), user)
    if event.get("status") != "open":
        raise HTTPException(409, "event is already resolved")

    actor = await resolve_user_id(user)
    new_vehicle_id = None
    resolution = body.action
    if body.action == "different_truck":
        if event.get("kind") != "vin_change":
            raise HTTPException(
                400, "only a vin_change can be resolved as a different truck")
        resolution = (
            f"different_truck:new_unit={body.company_code}/"
            f"{body.unit_number}"
            + (":old_archived" if body.archive_old else "")
        )

    # Claim FIRST: the status flip open→resolved is the lock, so two
    # admins clicking at once can't both perform the registry split —
    # the loser gets a 409 before any surgery happens.
    ok = await tenant.resolve_device_event(
        account_id, event_id, resolution=resolution, resolved_by=actor)
    if not ok:
        raise HTTPException(409, "event was resolved concurrently")

    if body.action == "different_truck":
        old_id = event.get("registry_id")
        if not old_id:
            # The event predates registry stamping or the row is gone —
            # fall back to the live link the event is about.
            for v in await tenant.list_vehicles(account_id):
                if v.telematics_ref == event.get("vehicle_id"):
                    old_id = v.id
                    break
        try:
            if not old_id:
                raise ValueError(
                    "the event's vehicle is no longer in the registry")
            new_vehicle_id = await tenant.split_vehicle_identity(
                account_id,
                old_vehicle_id=int(old_id),
                new_company_code=body.company_code,
                new_unit_number=body.unit_number,
                new_vin=str(event.get("new_value") or ""),
                restore_vin=str(event.get("old_value") or ""),
                archive_old=body.archive_old,
                actor_user_id=actor,
            )
        except ValueError as e:
            # Split refused (collision, missing row) — hand the claim
            # back so the admin can retry with a different unit number.
            await tenant.reopen_device_event(account_id, event_id)
            raise HTTPException(400, str(e))

    # ── The record ──────────────────────────────────────────────
    # Answering an identity question is an ACCOUNT-WIDE act: the row
    # goes inactive for everyone, the question leaves every admin's
    # screen, and "same truck" quietly welds two identities' history
    # together for good.  The warehouse row already carried who and
    # when, but nothing reached the activity trail — the place an owner
    # actually browses — so the most consequential answer on the
    # Vehicles page left no trace where anyone would look for it.
    #
    # Written LAST, deliberately.  The registry surgery is already
    # done and cannot be rolled back to satisfy a logger, so a trail
    # failure is shouted into the log rather than turned into a 5xx
    # that would tell the caller nothing happened when it did.
    await _record_device_event_answer(
        tenant, account_id, actor, event, resolution, new_vehicle_id,
    )

    return {
        "resolved": True,
        "event_id": event_id,
        "resolution": resolution,
        "new_vehicle_id": new_vehicle_id,
    }


async def _record_device_event_answer(
    tenant, account_id: int, actor: int | None, event: dict,
    resolution: str, new_vehicle_id: int | None,
) -> None:
    """File the answer on the truck's own timeline.

    Keyed by ``callout_id`` in the context so a later audit can ask
    "what happened to THIS question" and get the answer, the person and
    the moment — the id is the same string the dashboard rendered, so
    the two halves of the story join up.
    """
    from capabilities.activity_trail import record_simple
    from capabilities.callouts import callout_id
    from features.vehicles.callouts import EVENT_CALLOUT_KEYS

    key = EVENT_CALLOUT_KEYS.get(str(event.get("kind") or ""), "")
    if not key:
        return
    ident = callout_id(
        key,
        f"vehicle:{event.get('vehicle_id') or ''}",
        str(event.get("observed_at") or ""),
    )
    # The note carries the words the PERSON saw, not the words the
    # column stores.  `dismissed` is a wire value the button stopped
    # saying when it became "Confirm"; a trail that reports the storage
    # vocabulary makes a reader translate their own action back.  The
    # stored `resolution` is untouched and rides in the context, so
    # nothing is lost.
    choice = resolution.split(":")[0]
    answer = {
        "same_truck": "Same truck",
        "different_truck": "Different truck",
        "dismissed": "Confirmed",
        "acknowledge": "Confirmed",
    }.get(choice, choice)
    # Values, not prose: the trail's contract is that a reader can see
    # WHAT the state was, not just be told a change occurred.
    note = (
        f"{answer} — {event.get('vehicle_name') or event.get('vehicle_id')}: "
        f"{event.get('old_value') or ''} → {event.get('new_value') or ''}"
    )
    try:
        await record_simple(
            tenant, account_id, actor,
            f"device_event.{choice}",
            "vehicle", event.get("registry_id") or event.get("vehicle_id"),
            note=note,
            context={
                "callout_id": ident,
                "callout_key": key,
                "event_id": event.get("id"),
                "old_value": str(event.get("old_value") or ""),
                "new_value": str(event.get("new_value") or ""),
                "resolution": resolution,
                **({"new_vehicle_id": new_vehicle_id} if new_vehicle_id else {}),
            },
        )
    except Exception:
        logger.exception(
            "device event answered but not recorded acct=%d event=%s id=%s",
            account_id, event.get("id"), ident,
        )


async def _vehicle_callouts(account_id: int, tenant, vehicle_ids) -> list[dict]:
    """Live callouts for the vehicles a response is about.

    Returned TOP-LEVEL and keyed by entity rather than stamped on each
    row: a 500-row list where one truck is blind should carry one
    entry, not 499 empty arrays, and a page-scoped callout (one that
    belongs to the screen rather than to a row) has nowhere to live in
    a per-row field.

    Never raises — a callout is a courtesy on top of the data; failing
    to fetch one must not take the vehicle list down with it.
    """
    from capabilities.callouts import Callout, callout_wire

    if tenant is None:
        return []
    try:
        rows = await tenant.get_open_conditions(
            account_id, vehicle_ids=[str(v) for v in vehicle_ids if v],
        )
    except Exception:
        logger.debug("callout read failed acct=%d", account_id, exc_info=True)
        return []
    out = [
        Callout(
            key=str(r.get("key") or ""),
            entity=f"vehicle:{r.get('vehicle_id')}",
            since=str(r.get("opened_at") or ""),
            params=r.get("params") or {},
        )
        for r in rows if r.get("key")
    ]
    out.extend(await _archived_callouts(account_id, tenant, vehicle_ids))
    return callout_wire(out)


async def _archived_callouts(account_id: int, tenant, vehicle_ids) -> list:
    """One statement per truck on this page that has left the fleet.

    Not a stored condition like the others — it is read straight off
    the registry, because being archived IS the registry's own state
    and duplicating it into `vehicle_conditions` would give one fact
    two homes that can disagree.

    It matters most on the detail page, which reads the PROVIDER
    directly rather than our warehouse: the ingest gate cannot reach
    it, so Samsara happily returns a retired truck's last-known fuel,
    DEF and coordinates and the page renders them beside a freshness
    dot as though they were readings from this morning.
    """
    from capabilities.callouts import Callout

    wanted = {str(v) for v in vehicle_ids if v}
    if not wanted:
        return []
    try:
        rows = await tenant.list_archived_vehicles(account_id)
    except Exception:
        logger.debug("archived callouts unavailable acct=%d", account_id,
                     exc_info=True)
        return []
    out = []
    for v in rows:
        if v.telematics_ref not in wanted:
            continue
        # Two keys, not one with a branch: "someone retired this" and
        # "its gateway went silent" are different facts, and only the
        # second has an action attached — go and check the device.
        out.append(Callout(
            key=("vehicle.stopped_reporting"
                 if v.archived_reason == "sweep" else "vehicle.archived"),
            entity=f"vehicle:{v.telematics_ref}",
            since=str(v.updated_at or ""),
            params={"unit": v.unit_number},
        ))
    return out


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
    matches = await _resolve_vehicle(vehicle_name, company, user, allowed)
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
    # Engine state travels with them.  This endpoint reads LIVE Samsara,
    # whose payload carries no engine state at all — so without this
    # overlay ``_derive_engine_state`` sees an absent field, falls back
    # to its speed heuristic, and a truck standing still reads "Off".
    # On 548640 that put a confident "Off" directly beneath a banner
    # saying the device cannot read the engine.  The warehouse already
    # holds the resolved value; the list has been using it all along.
    engine_state_by_id: dict[str, str] = {}
    engine_state_by_name: dict[str, str] = {}
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
        state = row.get("engine_state")
        # "" is a real answer here — the ingest looked and found no
        # engine feed — so it must reach the overlay rather than being
        # skipped as missing.
        if state is not None:
            if rid:
                engine_state_by_id[rid] = state
            if rname:
                engine_state_by_name[rname] = state
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
        if match_id in engine_state_by_id or match_name in engine_state_by_name:
            s_hit = engine_state_by_id.get(match_id)
            if s_hit is None:
                s_hit = engine_state_by_name.get(match_name, "")
            # Same shape the warehouse reader emits, so ONE derivation
            # path serves both the list and this page.
            loc = match.setdefault("location", {})
            if isinstance(loc, dict):
                loc["engineStates"] = {"value": s_hit}

    normalized = [_normalize_detail(m) for m in matches]
    callouts = await _vehicle_callouts(
        user["account_id"], tenant,
        [m.get("id") for m in matches],
    )
    return {
        "vehicles": normalized,
        "count": len(normalized),
        "callouts": callouts,
    }


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
        snap = await _wh_reader.get_vehicle_fault_live(
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
    matches = await _resolve_vehicle(vehicle_name, company, user, allowed)
    if not matches:
        return {"error": "Vehicle not found", "points": []}
    vehicle_id = str(matches[0].get("id") or "")
    if not vehicle_id:
        return {"name": matches[0].get("name"), "points": []}
    points = await _wh_reader.get_vehicle_state_hour(
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
    matches = await _resolve_vehicle(vehicle_name, company, user, allowed)
    if not matches:
        return {"error": "Vehicle not found", "summary": None, "series": []}
    vehicle_id = str(matches[0].get("id") or "")
    if not vehicle_id:
        return {"name": matches[0].get("name"), "summary": None, "series": []}
    summary = await _wh_reader.get_vehicle_usage_summary(
        user["account_id"], vehicle_id, days=days,
    )
    series = await _wh_reader.get_vehicle_state_day(
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
        # ``source`` = who created the row; ``sources`` = everyone who
        # contributed a value (derived from provenance — one truck can
        # be created by one integration and enriched by another).
        "source": v.source, "sources": list(v.sources), "notes": v.notes,
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
    # 403 here, not 404: the caller supplied the company themselves, so
    # there is no resource whose existence a 404 would be hiding.
    allowed = await get_user_company_codes(user)
    validate_company_access(allowed, body.company_code)
    if allowed and not body.company_code:
        # Otherwise a restricted user could mint UNSCOPED vehicles, which
        # pass every company wall in the product by design — creating a
        # blind spot rather than crossing one.
        raise HTTPException(
            400, "Pick a company for this vehicle — your access is "
                 "limited to specific companies")
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
    except Exception:
        # Almost always the UNIQUE(account_id, company_code, unit_number)
        # collision.  Name the collision instead of forwarding the
        # driver's error text: the raw string tells the operator nothing
        # they can act on, and it hands schema detail to the client.
        # The archived case misled worst — the truck EXISTS, the unit is
        # spoken for, and the answer is Restore, not a second row.
        logger.warning(
            "manual add failed acct=%d %s/%s",
            account_id, body.company_code, body.unit_number, exc_info=True,
        )
        clash = None
        try:
            rows = await tenant.list_vehicles(
                account_id, company_code=body.company_code or None,
                include_inactive=True,
            )
            needle = body.unit_number.strip().lower()
            clash = next(
                (v for v in rows
                 if v.unit_number.strip().lower() == needle), None)
        except Exception:
            logger.debug("collision lookup failed", exc_info=True)
        if clash is not None and not clash.is_active:
            raise HTTPException(
                409,
                f"Unit {clash.unit_number} is archived — restore it from "
                f"the Archived tab instead of adding a second row.")
        if clash is not None:
            where = f" in {clash.company_code}" if clash.company_code else ""
            raise HTTPException(
                409, f"Unit {clash.unit_number} already exists{where}.")
        raise HTTPException(409, "Could not add this vehicle.")
    v = await tenant.get_vehicle(account_id, vid)
    return _vehicle_to_dict(v)


@router.get("/registry/archived")
async def list_archived_vehicles(user: dict = Depends(_manage_vehicles)):
    """Trucks that have left the fleet.

    Registry-only by nature: an archived truck has no live telematics
    row — the ingest gate stops writing them and archiving deletes the
    last one — so there is nothing to overlay and this does not go
    through the vehicles list's live merge.

    ``archived_reason`` travels with each row because the two ways a
    truck leaves are different facts a person must be able to tell
    apart: ``operator`` is someone deciding it is gone, ``sweep`` is
    its gateway having stopped reporting.  One is a decision, the
    other might be a broken device.
    """
    account_id = int(user["account_id"])
    tenant = await _get_tenant_db(account_id)
    if tenant is None:
        raise HTTPException(503, "tenant DB unavailable")
    rows = await tenant.list_archived_vehicles(account_id)
    # A list route walls by FILTERING, like the other reads in this file
    # — never by 404, which has nothing to hide behind on a collection.
    # A null company_code is unscoped and stays visible.
    allowed = await get_user_company_codes(user)
    if allowed:
        rows = [v for v in rows
                if not (getattr(v, "company_code", "") or "")
                or (v.company_code in allowed)]
    return {
        # Shaped like a row from the vehicles LIST, not like the manage
        # dialog's `_vehicle_to_dict`.  These rows land in the same grid
        # as live trucks, so they must speak its column names: `name`
        # and `company`, not `unit_number` and `company_code`.  And
        # `registry_id` is what the row menu keys Restore off — without
        # it the action never appears and the tab is a dead end.
        "vehicles": [
            {
                "id": v.id,
                "name": v.unit_number,
                "company": v.company_code,
                "registry_id": v.id,
                "vehicle_type": v.vehicle_type,
                "source": v.source,
                "sources": list(v.sources),
                "status": v.status,
                "vin": v.vin,
                "plate_number": v.plate_number,
                "make": v.make,
                "model": v.model,
                "year": v.year,
                "archived_reason": v.archived_reason,
                "status_before_archive": v.status_before_archive,
                "archived_at": v.updated_at,
            }
            for v in rows
        ],
        "count": len(rows),
    }


async def _wall_registry_vehicle(tenant, account_id: int, vehicle_id: int,
                                 user: dict):
    """The registry row this caller may act on, or 404.

    THE CONTRACT, written down because its absence is what let six
    endpoints drift apart: company restriction binds EVERY VERB, not just
    viewing.  Owners and unrestricted users pass; a null ``company_code``
    is unscoped and passes for anyone holding the permission.

    404 rather than 403, matching ``inventory/router.py::_resolve_vehicle``
    — a 403 on a foreign id confirms that the id exists, which is the
    disclosure the wall is there to prevent.

    Managing was account-wide while VIEWING the same rows was walled, so
    a user restricted to company A could rename, archive or read the VIN
    and plate of company B's trucks — writes wider than reads, the same
    defect shape as the alerting leak fixed in e7e5bb07.  Multi-company
    accounts here are often separate legal entities sharing one login.
    """
    v = await tenant.get_vehicle(account_id, vehicle_id)
    if v is None:
        raise HTTPException(404, "no vehicle with that id")
    allowed = await get_user_company_codes(user)
    if not company_allows(getattr(v, "company_code", "") or "", allowed):
        raise HTTPException(404, "no vehicle with that id")
    return v


@router.post("/registry/{vehicle_id}/restore")
async def restore_registry_vehicle(
    vehicle_id: int,
    user: dict = Depends(_manage_vehicles),
):
    """Bring a retired truck back, with the status it had before.

    One act, because archiving destroyed nothing: the telematics link
    was never cleared, so the ingest gate stops dropping this truck's
    rows and telemetry resumes on the next tick.
    """
    account_id = int(user["account_id"])
    tenant = await _get_tenant_db(account_id)
    if tenant is None:
        raise HTTPException(503, "tenant DB unavailable")
    await _wall_registry_vehicle(tenant, account_id, vehicle_id, user)
    ok = await tenant.restore_vehicle(
        account_id, vehicle_id,
        actor_user_id=await resolve_user_id(user),
    )
    if not ok:
        raise HTTPException(404, "no archived vehicle with that id")
    # The truck's paperwork comes home from the archive tree.  Best-
    # effort: the restore already happened, and a folder that failed to
    # move is a misfiling, not a loss — rows still point where the
    # files are, so downloads keep working either way.
    try:
        from features.vehicles.documents import move_documents_on_restore
        await move_documents_on_restore(tenant, account_id, vehicle_id)
    except Exception:
        logger.warning("restore: documents not moved v=%d acct=%d",
                       vehicle_id, account_id, exc_info=True)
    v = await tenant.get_vehicle(account_id, vehicle_id)
    return {"restored": True, "id": vehicle_id,
            "status": v.status if v else "active"}


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
    await _wall_registry_vehicle(tenant, account_id, vehicle_id, user)
    fields = {k: v for k, v in body.model_dump().items() if v is not None}
    # A restricted caller may not MOVE a vehicle out of their own
    # companies either — the wall above proves they may touch this row,
    # not that they may hand it to a company they cannot see.
    if fields.get("company_code"):
        validate_company_access(await get_user_company_codes(user),
                                fields["company_code"])
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
    await _wall_registry_vehicle(tenant, account_id, vehicle_id, user)
    ok = await tenant.deactivate_vehicle(
        account_id, vehicle_id,
        actor_user_id=await resolve_user_id(user),
    )
    if not ok:
        raise HTTPException(404, "vehicle not found")
    # Its paperwork moves to vehicles/_archive/{date}/{unit}/ — the
    # driver-archive recipe: the live folder frees up for a future
    # truck reusing the number, the carrier keeps a dated audit trail,
    # and restore brings it back.  Best-effort, after the archive.
    try:
        from features.vehicles.documents import move_documents_on_archive
        await move_documents_on_archive(tenant, account_id, vehicle_id)
    except Exception:
        logger.warning("archive: documents not moved v=%d acct=%d",
                       vehicle_id, account_id, exc_info=True)
    return {"deactivated": True, "id": vehicle_id}
