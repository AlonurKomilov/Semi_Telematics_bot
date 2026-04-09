"""Safety & Compliance API endpoints — scorecards, events, camera checks."""

from fastapi import APIRouter, Depends, Query

from api.deps import require_permission, get_tenant_db
from bot.state import get_client

router = APIRouter(prefix="/safety", tags=["safety"])


# ── Scorecards ────────────────────────────────────────────────

@router.get("/scorecards")
async def scorecards(
    days: int = Query(7, ge=1, le=90),
    company: str | None = Query(None),
    user: dict = Depends(require_permission("can_scorecard_all")),
):
    """Driver scorecards — efficiency + safety metrics per driver."""
    client = await get_client(user["account_id"])
    drivers = await client.get_driver_efficiency(days=days, company=company)

    cards = []
    for d in drivers:
        cards.append({
            "driver_id": d.get("driver_id", ""),
            "driver_name": d.get("driver_name", "Unknown"),
            "company": d.get("_org", ""),
            "miles": round(d.get("_miles", 0), 1),
            "mpg": round(d.get("_mpg", 0), 1),
            "drive_hours": round(d.get("_drive_h", 0), 1),
            "idle_hours": round(d.get("_idle_h", 0), 1),
            "drive_pct": round(d.get("_drive_pct", 0), 1),
            "idle_pct": round(d.get("_idle_pct", 0), 1),
            "eco_pct": round(d.get("_green_pct", 0), 1),
            "overspeed_min": round(d.get("_overspeed_min", 0), 1),
            "coast_min": round(d.get("_coast_min", 0), 1),
            "cruise_min": round(d.get("_cruise_min", 0), 1),
            "anticipatory_braking_pct": round(d.get("_antic_pct", 0), 1),
        })

    # Sort by eco_pct descending (best first)
    cards.sort(key=lambda c: c["eco_pct"], reverse=True)

    return {"scorecards": cards, "count": len(cards), "days": days}


# ── Safety Events ─────────────────────────────────────────────

EVENT_SEVERITY = {
    "crash": "severe",
    "braking": "moderate",
    "harshTurn": "moderate",
    "rollingStop": "mild",
    "followingDistance": "mild",
    "laneDeparture": "mild",
    "acceleration": "mild",
}


def _classify_severity(event_type: str, g_force: float) -> str:
    """Classify event severity from G-force or event type."""
    if g_force >= 0.8:
        return "severe"
    if g_force >= 0.6:
        return "harsh"
    if g_force >= 0.4:
        return "moderate"
    if g_force > 0:
        return "mild"
    return EVENT_SEVERITY.get(event_type, "mild")


@router.get("/events")
async def safety_events(
    days: int = Query(7, ge=1, le=90),
    event_type: str | None = Query(None, description="crash, braking, harshTurn, etc."),
    driver: str | None = Query(None, description="Filter by driver name (substring)"),
    company: str | None = Query(None),
    user: dict = Depends(require_permission("can_events_all")),
):
    """Safety events — harsh braking, crashes, speeding, etc."""
    client = await get_client(user["account_id"])
    raw = await client.get_events(days=days, company=company)

    events = []
    for e in raw:
        g = e.get("g_force", 0) or 0
        ev = {
            "event_id": e.get("event_id", ""),
            "event_type": e.get("event_type", "unknown"),
            "severity": _classify_severity(e.get("event_type", ""), g),
            "driver_id": e.get("driver_id", ""),
            "driver_name": e.get("driver_name", "Unknown"),
            "vehicle_id": e.get("vehicle_id", ""),
            "vehicle_name": e.get("vehicle_name", ""),
            "time": e.get("time", ""),
            "g_force": round(g, 2),
            "latitude": e.get("latitude"),
            "longitude": e.get("longitude"),
            "video_url": e.get("video_url", ""),
            "inward_video_url": e.get("inward_video_url", ""),
            "coaching_state": e.get("coaching_state", ""),
            "company": e.get("_org", ""),
        }
        events.append(ev)

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


# ── Camera Checks ────────────────────────────────────────────

@router.get("/cameras")
async def camera_checks(
    vehicle: str | None = Query(None, description="Filter by vehicle name"),
    limit: int = Query(50, ge=1, le=200),
    user: dict = Depends(require_permission("can_faults")),
    tenant_db=Depends(get_tenant_db),
):
    """Camera check history — obstruction, alignment, quality per vehicle."""
    checks = await tenant_db.get_camera_check_history(
        user["account_id"],
        limit=limit,
        vehicle_name=vehicle if vehicle else None,
    )
    return {"checks": checks, "count": len(checks)}
