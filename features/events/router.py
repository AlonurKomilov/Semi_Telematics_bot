"""Safety-events API — the events feature's router.

router.py is interface-layer code co-located with its feature
(docs/FEATURES.md): ONLY router.py may import interfaces.api.deps;
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
from infra.services import get_client
from features.events.severity import classify_event_severity as _classify_severity

router = APIRouter(prefix="/safety", tags=["safety"])



# ── Safety Events ─────────────────────────────────────────────


@router.get("/events")
async def safety_events(
    days: int = Query(7, ge=1, le=90),
    event_type: str | None = Query(None, description="crash, braking, harshTurn, etc."),
    driver: str | None = Query(None, description="Filter by driver name (substring)"),
    company: str | None = Query(None),
    user: dict = Depends(require_permission_any("can_events_all", "can_events_vehicle")),
):
    """Safety events — harsh braking, crashes, speeding, etc."""
    allowed = await get_user_company_codes(user)
    validate_company_access(allowed, company)
    client = await get_client(user["account_id"])

    async def _live():
        return await client.get_events(days=days, company=company)

    from capabilities.warehouse.telemetry import warehouse_reader as _wh
    # include_raw=False skips the per-row ``json.loads(raw_json)`` in
    # the warehouse layer — saves hundreds of ms on a 30-day window.
    # The list view doesn't need the full Samsara event shape; the
    # video modal fetches inward-camera info on demand via the
    # /events/{id}/video proxy endpoint.
    wh_rows = await _wh.get_safety_events(
        user["account_id"],
        days=days,
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

    # If user only has _own, filter to their assigned vehicle
    if user.get("_matched_perm") == "can_events_vehicle":
        trucks = await get_user_vehicle_nums(user)
        if trucks:
            needles = [t.lower() for t in trucks]
            events = [e for e in events if any(n in e["vehicle_name"].lower() for n in needles)]
        else:
            events = []

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
        "summary": {"by_type": by_type, "by_severity": by_severity},
    }


@router.get("/events/summary")
async def safety_events_summary(
    days: int = Query(7, ge=1, le=90),
    company: str | None = Query(None),
    user: dict = Depends(require_permission_any("can_events_all", "can_events_vehicle")),
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

    from capabilities.warehouse.telemetry import warehouse_reader as _wh
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

    if user.get("_matched_perm") == "can_events_vehicle":
        trucks = await get_user_vehicle_nums(user)
        if trucks:
            needles = [t.lower() for t in trucks]
            rows = [e for e in rows if any(n in e["vehicle_name"].lower() for n in needles)]
        else:
            rows = []

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
    if user.get("_matched_perm") == "can_events_vehicle":
        trucks = await get_user_vehicle_nums(user)
        if not trucks:
            raise HTTPException(status_code=403, detail="No truck assignments")
        vname = (evt.get("vehicle") or {}).get("name", "")
        if not any(t.lower() in (vname or "").lower() for t in trucks):
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
):
    """Lat/lon density of safety events over the trailing window
    ( \u2014 LiveMap heat layer).

    Reads directly from the per-tenant ``safety_event_log`` warehouse
    table.  Returns an empty ``points`` list when the warehouse flag
    is off so the heat layer simply renders nothing instead of
    erroring.
    """
    from capabilities.warehouse.telemetry import warehouse_reader as _wh
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
        trucks = await get_user_vehicle_nums(user)
        if not trucks:
            return {"days": days, "points": []}
        needles = [t.lower() for t in trucks]
        rows = [r for r in rows if any(n in (r.get("vehicle_name") or "").lower() for n in needles)]
    points = [
        [r["lat"], r["lon"], 1]
        for r in rows
        if r.get("lat") is not None and r.get("lon") is not None
    ]
    return {"days": days, "count": len(points), "points": points}

