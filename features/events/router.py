"""Safety-events API — the events feature's router.

router.py is interface-layer code co-located with its feature
(docs/FEATURES.md): router.py and config.py are the interface-layer pair — those two may
# import interfaces.api.deps; nothing else in the feature may;
service/alert/ai_tool/signal never do.  Paths keep the historical
``/safety`` prefix so URLs (and the frontend) are unchanged.
"""

import json
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, Query, HTTPException

from interfaces.api.deps import (
    require_permission_any, get_tenant_db, get_platform_db,
    get_user_vehicle_nums, get_user_company_codes,
    validate_company_access, filter_by_allowed_companies,
)
from capabilities.permissions.vehicle_scope import VehicleScope, build_vehicle_scope
from infra.services import get_client
from features.events.severity import classify_event_severity as _classify_severity

router = APIRouter(prefix="/safety", tags=["safety"])



# ── Safety Events ─────────────────────────────────────────────


async def _events_vehicle_scope(user: dict, tenant_db):
    """The vehicle wall for a ``can_events_vehicle`` (_own) caller, or None.

    ``None`` means unrestricted — the caller holds the ``_all`` grade.
    Anything else is authoritative, INCLUDING an empty scope, which denies
    every row (a truck-scoped caller with no assignments sees nothing).

    Membership is by identity — registry id -> provider id -> exact name
    (capabilities/permissions/vehicle_scope).  Four walls in this file
    each compared by SUBSTRING, so an assignment of "230" also matched
    "2303" and "100" matched trailer "AK1001".  On /events/{id}/video
    that meant a driver could watch another truck's dashcam footage.
    Substring also happened to absorb provider renames ("229" inside
    "229 Idris Ahmed"); the id rungs now do that on purpose, and survive
    a rename that drops the number entirely.
    """
    if user.get("_matched_perm") != "can_events_vehicle":
        return None
    trucks = await get_user_vehicle_nums(user)
    if not trucks:
        return VehicleScope()
    return await build_vehicle_scope(tenant_db, user["account_id"], trucks)


@router.get("/events")
async def safety_events(
    days: int = Query(7, ge=1, le=90),
    end: str | None = Query(
        None, pattern=r"^\d{4}-\d{2}-\d{2}$",
        description="Window END day (inclusive, UTC). Default: today. "
                    "The window is the `days` ending on this day.",
    ),
    event_type: str | None = Query(None, description="crash, braking, harshTurn, etc."),
    driver: str | None = Query(None, description="Filter by driver name (substring)"),
    company: str | None = Query(None),
    user: dict = Depends(require_permission_any("can_events_all", "can_events_vehicle")),
    tenant_db=Depends(get_tenant_db),
):
    """Safety events — harsh braking, crashes, speeding, etc.

    Explicit-end windows: fetch reaches from the window START through
    today (the storage layer only speaks "last N days"), then the rows
    are filtered PRECISELY to [start, end] below — so the response
    never includes events outside the promised window.
    """
    from datetime import datetime, timedelta, timezone

    allowed = await get_user_company_codes(user)
    validate_company_access(allowed, company)
    client = await get_client(user["account_id"])

    # Resolve the window bounds.  ``end`` in the future clamps to today;
    # an end further back than a year is a client bug → 422.
    now = datetime.now(timezone.utc)
    end_dt: datetime | None = None
    if end:
        try:
            end_dt = datetime.fromisoformat(end).replace(tzinfo=timezone.utc)
        except ValueError:
            raise HTTPException(status_code=422, detail="end must be YYYY-MM-DD")
        if end_dt > now:
            end_dt = None                      # future end = today = default
        elif (now - end_dt).days > 365:
            raise HTTPException(status_code=422, detail="end is too far in the past")

    # Days to fetch so the window START is covered even when the end
    # sits in the past (offset = today − end).
    fetch_days = days + ((now - end_dt).days + 1 if end_dt else 0)

    async def _live():
        return await client.get_events(days=fetch_days, company=company)

    from features.vehicles.warehouse import readers as _wh
    # include_raw=False skips the per-row ``json.loads(raw_json)`` in
    # the warehouse layer — saves hundreds of ms on a 30-day window.
    # The list view doesn't need the full Samsara event shape; the
    # video modal fetches inward-camera info on demand via the
    # /events/{id}/video proxy endpoint.
    wh_rows = await _wh.get_safety_events(
        user["account_id"],
        days=fetch_days,
        event_type=event_type,
        samsara_fallback=_live,
        include_raw=False,
    )

    # Helper to read either a warehouse column or the live-Samsara key,
    # since the cold-start fallback returns live-shape dicts directly.
    def _row_to_event(r: dict) -> dict:
        # ``occurred_at`` is the warehouse column; ``time`` is the
        # live-Samsara field.  Take whichever exists.
        time_val = r.get("occurred_at") or r.get("time") or ""
        # Severity: trust the column the ingestor wrote ("low"/"medium"/
        # "high" / "" / mapped by classify_event_severity).  When that
        # column is empty (cold-start live fallback path) recompute
        # from g-force as a one-time fallback.
        severity = r.get("severity") or ""
        g_force_raw = r.get("g_force", 0) or 0
        if not severity:
            severity = _classify_severity(r.get("event_type", ""), g_force_raw)
        return {
            "event_id":    r.get("samsara_event_id") or r.get("event_id") or "",
            "event_type":  r.get("event_type") or "unknown",
            "severity":    severity,
            "driver_id":   r.get("driver_id") or "",
            "driver_name": r.get("driver_name") or "Unknown",
            "vehicle_id":  r.get("vehicle_id") or "",
            "vehicle_name": r.get("vehicle_name") or "",
            "time":        time_val,
            "g_force":     round(float(g_force_raw), 2),
            "latitude":    r.get("lat") if "lat" in r else r.get("latitude"),
            "longitude":   r.get("lon") if "lon" in r else r.get("longitude"),
            "video_url":   r.get("video_url") or "",
            # company_code is the warehouse column; _org is the live key.
            "company":     r.get("company_code") or r.get("_org") or "",
        }

    # Reshape rows first — ``_row_to_event`` normalises the
    # company under one key ("company") regardless of whether the row
    # came from the warehouse (``company_code`` column) or the live
    # Samsara fallback (``_org`` key).  Then a single
    # ``filter_by_allowed_companies`` call enforces the access rule.
    events = [_row_to_event(r) for r in wh_rows]
    events = filter_by_allowed_companies(events, allowed, key="company")

    # Precise [start, end] filter for explicit-end windows — applied to
    # BOTH the warehouse and the live-fallback rows, so the response
    # matches the promised window regardless of the data path.
    if end_dt is not None:
        upper = end_dt + timedelta(days=1)               # inclusive end day
        lower = upper - timedelta(days=days)
        def _in_window(e: dict) -> bool:
            t = (e.get("time") or "").replace("Z", "+00:00")
            try:
                ts = datetime.fromisoformat(t)
            except ValueError:
                return False
            if ts.tzinfo is None:
                ts = ts.replace(tzinfo=timezone.utc)
            return lower <= ts < upper
        events = [e for e in events if _in_window(e)]

    # If user only has _own, filter to their assigned vehicle
    scope = await _events_vehicle_scope(user, tenant_db)
    if scope is not None:
        events = [e for e in events if scope.allows_row(e, name_key="vehicle_name")]

    # Apply filters
    if event_type:
        events = [e for e in events if e["event_type"] == event_type]
    if driver:
        q = driver.lower()
        events = [e for e in events if q in e["driver_name"].lower()]

    # Summary counts
    by_type: dict[str, int] = {}
    by_severity: dict[str, int] = {}
    for e in events:
        by_type[e["event_type"]] = by_type.get(e["event_type"], 0) + 1
        by_severity[e["severity"]] = by_severity.get(e["severity"], 0) + 1

    return {
        "events": events,
        "count": len(events),
        "days": days,
        "end": end if end_dt is not None else None,
        "summary": {"by_type": by_type, "by_severity": by_severity},
    }


@router.get("/events/summary")
async def safety_events_summary(
    days: int = Query(7, ge=1, le=90),
    company: str | None = Query(None),
    user: dict = Depends(require_permission_any("can_events_all", "can_events_vehicle")),
    tenant_db=Depends(get_tenant_db),
    platform_db=Depends(get_platform_db),
):
    """Lightweight summary counts for the Safety persona's hero strip.

    Returns just the aggregates — no event rows — so the SafetySummaryStrip
    component can land before AlertsResults paints.  Same filter rules as
    /events: company access via ``allowed_companies``, ``_own`` users
    constrained to their assigned vehicles.

    Today vs week is computed in the account's timezone (matches the
    rest of the safety surface).
    """
    allowed = await get_user_company_codes(user)
    validate_company_access(allowed, company)
    client = await get_client(user["account_id"])

    async def _live():
        return await client.get_events(days=days, company=company)

    from features.vehicles.warehouse import readers as _wh
    wh_rows = await _wh.get_safety_events(
        user["account_id"],
        days=days,
        event_type=None,
        samsara_fallback=_live,
        include_raw=False,
    )

    # Project to a minimal shape — we only need event_type, severity,
    # time, company, vehicle for filtering + counting.
    rows: list[dict] = []
    for r in wh_rows:
        time_val = r.get("occurred_at") or r.get("time") or ""
        severity = r.get("severity") or ""
        g_force_raw = r.get("g_force", 0) or 0
        if not severity:
            severity = _classify_severity(r.get("event_type", ""), g_force_raw)
        rows.append({
            "event_type":   r.get("event_type") or "unknown",
            "severity":     severity,
            "vehicle_name": r.get("vehicle_name") or "",
            "time":         time_val,
            "company":      r.get("company_code") or r.get("_org") or "",
        })
    rows = filter_by_allowed_companies(rows, allowed, key="company")

    scope = await _events_vehicle_scope(user, tenant_db)
    if scope is not None:
        rows = [e for e in rows if scope.allows_row(e, name_key="vehicle_name")]

    # "Today" boundary in the account's timezone so the count matches
    # what an operator sees on their wall clock.  Falls back to UTC if
    # the account row is missing a tz (shouldn't happen — every account
    # has a default — but defensive).
    acc = await platform_db.get_account(user["account_id"])
    tz_name = (getattr(acc, "timezone", None) or "America/New_York") if acc else "America/New_York"
    try:
        from zoneinfo import ZoneInfo
        local_today = datetime.now(ZoneInfo(tz_name)).date().isoformat()
    except Exception:
        local_today = datetime.now(timezone.utc).date().isoformat()

    today_count = 0
    week_count = len(rows)
    by_severity: dict[str, int] = {}
    by_type: dict[str, int] = {}
    for e in rows:
        if isinstance(e["time"], str) and e["time"][:10] == local_today:
            today_count += 1
        sev = e["severity"] or "unknown"
        by_severity[sev] = by_severity.get(sev, 0) + 1
        by_type[e["event_type"]] = by_type.get(e["event_type"], 0) + 1

    return {
        "today":       today_count,
        "week":        week_count,
        "days":        days,
        "by_severity": by_severity,
        "by_type":     by_type,
        "timezone":    tz_name,
    }


@router.get("/events/{event_id}/video")
async def safety_event_video(
    event_id: str,
    angle: str = Query("forward", pattern="^(forward|inward)$"),
    user: dict = Depends(require_permission_any("can_events_all", "can_events_vehicle")),
    tenant_db=Depends(get_tenant_db),
):
    """Return a freshly-signed Samsara video URL as JSON.

    Stored URLs in ``safety_event_log`` are S3 pre-signed with an 8-hour
    expiry; anything the user clicks more than 8h after the event was
    ingested gets 403 ``Request has expired`` from S3.  This endpoint
    re-fetches the event from Samsara on every call so the URL is
    always fresh.

    Returns JSON ``{"url": "...", ...}`` rather than a 302 redirect so
    the dashboard can authenticate the request with its Bearer token —
    a plain ``<video src=...>`` GET from the browser would not include
    custom headers and the JWT-required dependency would 422.  S3
    itself doesn't require auth, so the returned URL can be assigned
    directly to ``<video src>`` once the dashboard has it.

    Driver-role users (``can_events_vehicle``) can only fetch videos for
    events on their assigned truck(s).
    """
    client = await get_client(user["account_id"])
    evt = await client.get_safety_event(event_id)
    if not evt:
        raise HTTPException(status_code=404, detail="Event not found")

    # Driver isolation — check the event's vehicle against the caller's
    # assignments.  Permission-by-truck is already enforced for
    # /events listing; we re-check here so a known event_id from
    # another driver's truck can't be opened via direct URL.
    scope = await _events_vehicle_scope(user, tenant_db)
    if scope is not None:
        # The provider's nested shape, mapped onto the ladder: its
        # vehicle id is the same external ref build_vehicle_scope
        # resolved from the registry, so a rename still matches.
        veh = evt.get("vehicle") or {}
        if not scope.allows(external_id=veh.get("id"), name=veh.get("name")):
            raise HTTPException(status_code=403, detail="Event not in your assignments")

    url = evt.get("inward_video_url") if angle == "inward" else evt.get("video_url")
    if not url:
        raise HTTPException(status_code=404, detail=f"No {angle} video for this event")
    return {
        "event_id": event_id,
        "angle": angle,
        "url": url,
        # Echo the other angle when present so the dashboard can switch
        # tabs without a second round-trip.
        "forward_url": evt.get("video_url") or "",
        "inward_url": evt.get("inward_video_url") or "",
    }


@router.get("/events/heatmap")
async def safety_events_heatmap(
    days: int = Query(30, ge=1, le=90),
    user: dict = Depends(require_permission_any("can_events_all", "can_events_vehicle")),
    tenant_db=Depends(get_tenant_db),
):
    """Lat/lon density of safety events over the trailing window
    ( \u2014 LiveMap heat layer).

    Reads directly from the per-tenant ``safety_event_log`` warehouse
    table.  Returns an empty ``points`` list when the warehouse flag
    is off so the heat layer simply renders nothing instead of
    erroring.
    """
    from features.vehicles.warehouse import readers as _wh
    from infra.services import get_tenant_db

    if not getattr(_wh, "_enabled")():
        return {"days": days, "points": []}
    tenant = await get_tenant_db(user["account_id"])
    if tenant is None:
        return {"days": days, "points": []}
    rows = await tenant.get_safety_events_warehouse(
        user["account_id"], days=days, limit=10000,
    )
    # Restrict drivers with _own permission to their own truck(s).
    if user.get("_matched_perm") == "can_events_vehicle":
        scope = await _events_vehicle_scope(user, tenant_db)
        if scope is not None:
            rows = [r for r in rows if scope.allows_row(r, name_key="vehicle_name")]
    points = [
        [r["lat"], r["lon"], 1]
        for r in rows
        if r.get("lat") is not None and r.get("lon") is not None
    ]
    return {"days": days, "count": len(points), "points": points}

