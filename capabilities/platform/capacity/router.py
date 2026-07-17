"""Capacity API — operator console reads (``require_system_owner``).

Serves the Capacity page: current gauges, the 24h minute series, the
30d hourly PEAK series, today's per-surface request split, the
headroom estimate, and per-account usage.  Read-only over the history
the sampler writes; no probe runs here (a page load must never add
load to the thing it measures).
"""

from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Depends, Query

from interfaces.api.deps import get_platform_db, require_system_owner

from . import requests as metering

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/system/capacity", tags=["system"])

# Capacity-planning thresholds (fractions of a resource at PEAK):
# below WATCH = comfortable; WATCH..ACT = plan the upgrade; above ACT =
# upgrade now.  Shared with the frontend via the overview payload so
# the bands can't drift between the math and the display.
WATCH_PCT = 70.0
ACT_PCT = 85.0


def _headroom(hours: list[dict], latest: dict | None) -> dict:
    """Estimate growth headroom from the last 7 days of hourly PEAKS.

    Model (honest and linear): resource peaks scale with active
    vehicles from a fixed baseline.  vehicles_at_watch = vehicles ×
    (WATCH / peak_pct) for the BINDING resource (the one closest to
    its ceiling).  Disk is a runway in days instead (it grows with
    time, not vehicle count directly).  Confidence is 'low' until a
    week of history exists — the number firms up as peaks accumulate.
    """
    week_ago = (datetime.now(timezone.utc) - timedelta(days=7)).strftime(
        "%Y-%m-%dT%H:00"
    )
    week = [h for h in hours if h["hour"] >= week_ago]
    out: dict = {
        "watch_pct": WATCH_PCT,
        "act_pct": ACT_PCT,
        "window_hours": len(week),
        "confidence": "ok" if len(week) >= 7 * 24 * 0.8 else "low",
        "binding_resource": None,
        "peak_cpu_pct": None,
        "peak_mem_pct": None,
        "vehicles_now": (latest or {}).get("vehicles_active"),
        "vehicles_at_watch": None,
        "disk_days_to_act": None,
    }
    if not week:
        return out

    def _peak(col: str) -> float | None:
        vals = [h[col] for h in week if h.get(col) is not None]
        return max(vals) if vals else None

    peak_cpu = _peak("peak_cpu_pct")
    peak_mem = _peak("peak_mem_pct")
    out["peak_cpu_pct"] = peak_cpu
    out["peak_mem_pct"] = peak_mem

    candidates = [
        ("cpu", peak_cpu),
        ("memory", peak_mem),
    ]
    candidates = [(n, v) for n, v in candidates if v]
    vehicles = out["vehicles_now"]
    if candidates and vehicles:
        name, peak = max(candidates, key=lambda c: c[1])
        out["binding_resource"] = name
        # Linear model: peak% per vehicle × vehicles_at_watch = WATCH.
        out["vehicles_at_watch"] = int(vehicles * (WATCH_PCT / peak))

    # Disk runway: growth of used GB across the window → days until the
    # ACT threshold of the filesystem.
    disks = [
        (h["hour"], h["disk_used_gb"], h["disk_pct"])
        for h in week
        if h.get("disk_used_gb") is not None and h.get("disk_pct") is not None
    ]
    if len(disks) >= 24:
        first_gb, last_gb = disks[0][1], disks[-1][1]
        last_pct = disks[-1][2]
        span_days = max(len(disks) / 24.0, 1.0)
        growth_per_day = max((last_gb - first_gb) / span_days, 0.0)
        if growth_per_day > 0 and last_pct > 0:
            total_gb = last_gb / (last_pct / 100.0)
            gb_to_act = total_gb * (ACT_PCT / 100.0) - last_gb
            out["disk_days_to_act"] = int(gb_to_act / growth_per_day)
    return out


@router.get("/overview")
async def capacity_overview(
    _user: dict = Depends(require_system_owner),
    platform_db=Depends(get_platform_db),
):
    """Gauges + headroom + today's surface split — the page's top half."""
    latest = await platform_db.get_system_metrics_latest()
    month_ago = (datetime.now(timezone.utc) - timedelta(days=30)).strftime(
        "%Y-%m-%dT%H:00"
    )
    hours = await platform_db.get_system_metrics_hours(month_ago)
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    return {
        "latest": latest,
        "headroom": _headroom(hours, latest),
        "surfaces_today": await metering.surface_counts(today),
        "hourly_count": len(hours),
    }


@router.get("/series")
async def capacity_series(
    window: str = Query("24h", pattern="^(24h|7d|30d)$"),
    _user: dict = Depends(require_system_owner),
    platform_db=Depends(get_platform_db),
):
    """Chart data.  ``24h`` returns raw minutes; ``7d``/``30d`` return
    the hourly avg+peak tier."""
    now = datetime.now(timezone.utc)
    if window == "24h":
        since = (now - timedelta(hours=24)).strftime("%Y-%m-%dT%H:%M")
        return {"window": window, "tier": "minute",
                "points": await platform_db.get_system_metrics_minutes(since)}
    days = 7 if window == "7d" else 30
    since = (now - timedelta(days=days)).strftime("%Y-%m-%dT%H:00")
    return {"window": window, "tier": "hourly",
            "points": await platform_db.get_system_metrics_hours(since)}


@router.get("/accounts")
async def capacity_accounts(
    days: int = Query(30, ge=1, le=400),
    account_id: int | None = Query(None),
    _user: dict = Depends(require_system_owner),
    platform_db=Depends(get_platform_db),
):
    """Per-account request usage (durable daily rows + today's live
    Redis hash merged in), labeled with account names.  ``account_id``
    narrows to one account for the AccountDetail card."""
    since = (datetime.now(timezone.utc) - timedelta(days=days)).strftime("%Y-%m-%d")
    rows = await platform_db.get_account_usage(since, account_id=account_id)

    # Today's counts are still accumulating in Redis — merge them so
    # the table never looks a day behind (GREATEST semantics mirror
    # the durable flush).
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    live = await metering.account_counts(today)
    if account_id is not None:
        live = {k: v for k, v in live.items() if k == account_id}
    by_key = {(r["day"], r["account_id"]): r for r in rows}
    for acct, req in live.items():
        key = (today, acct)
        if key in by_key:
            by_key[key]["requests"] = max(by_key[key]["requests"], req)
        else:
            rows.append({"day": today, "account_id": acct, "requests": req})

    # Name labels + active vehicle counts for the growth table.
    names: dict[int, str] = {}
    vehicles: dict[int, int] = {}
    try:
        accounts = await platform_db.list_accounts(active_only=False)
        names = {a.id: a.name for a in accounts}
        cur = await platform_db._db.execute(
            "SELECT account_id, COUNT(*) FROM vehicles WHERE is_active = 1 "
            "GROUP BY account_id"
        )
        vehicles = {int(r[0]): int(r[1]) for r in await cur.fetchall()}
    except Exception as e:
        logger.debug("capacity accounts labels failed: %s", e)

    return {
        "days": days,
        "rows": rows,
        "accounts": [
            {"account_id": aid, "name": names.get(aid, f"#{aid}"),
             "vehicles": vehicles.get(aid, 0)}
            for aid in sorted({r["account_id"] for r in rows} | set(vehicles))
        ],
    }
