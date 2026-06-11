"""Safety & Compliance API endpoints — scorecards, events, camera checks."""

import json
import os
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, Query, HTTPException
from fastapi.responses import FileResponse

from interfaces.api.deps import require_permission, require_permission_any, get_tenant_db, get_platform_db, get_user_vehicle_nums, get_user_company_codes, validate_company_access, filter_by_allowed_companies
from infra.services import get_client
from capabilities.telemetry.service import get_driver_efficiency as _svc_driver_efficiency
from features.events.severity import classify_event_severity as _classify_severity
from capabilities.scoring.service import evaluate_subjects as _svc_evaluate_subjects
import infra.cache as _redis_cache

router = APIRouter(prefix="/safety", tags=["safety"])

_PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

# grace period: drivers with fewer than this many daily
# snapshots haven't been around long enough to be ranked fairly.
# Two weeks of daily history is the practical minimum — enough for
# trend logic to work and for one-week-vs-prior-week comparison.
# Tenants whose drivers have shorter tenure (just-onboarded fleets)
# will see everyone flagged probationary until snapshots accrue.
PROBATIONARY_MIN_SNAPSHOTS = 14


# ── Scorecards ────────────────────────────────────────────────

@router.get("/scorecards")
async def scorecards(
    days: int = Query(7, ge=1, le=90),
    company: str | None = Query(None),
    user: dict = Depends(require_permission_any("can_scorecard_all", "can_scorecard_vehicle")),
):
    """Driver scorecards — efficiency + safety metrics per driver."""
    allowed = await get_user_company_codes(user)
    validate_company_access(allowed, company)
    # Pre-compute truck filter for own-only users so it's applied inside the service
    own_perm = user.get("_matched_perm") == "can_scorecard_vehicle"
    vehicle_filter: list[str] | None = await get_user_vehicle_nums(user) if own_perm else None
    drivers = await _svc_driver_efficiency(user["account_id"], days=days, company=company, vehicle_nums=vehicle_filter)
    drivers = filter_by_allowed_companies(drivers, allowed)

    # Driver-role hardening: even though the service was already filtered by
    # ``vehicle_nums``, some upstream paths (Samsara warehouse fallback, mock
    # data) may return rows for unmapped drivers in the same window.
    # Restrict to caller's own truck(s) one more time before exposing names.
    if own_perm and vehicle_filter is not None:
        own_set = {t.strip().lower() for t in vehicle_filter if t}
        drivers = [
            d for d in drivers
            if any(
                (str(t) or "").strip().lower() in own_set
                for t in (d.get("_truck_nums") or [d.get("_truck_num")])
                if t
            )
        ]

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

    # ``count`` is the number of *active* drivers (those with > 0 ms of
    # drive+idle time in the window).  Drivers with zero activity are
    # filtered out by ``client.get_driver_efficiency`` because their
    # percentages are undefined.  Exposing the field name explicitly
    # avoids the previous ambiguity where the dashboard tile read
    # "Drivers: N" but really meant "Active Drivers: N".
    return {
        "scorecards":     cards,
        "count":          len(cards),
        "active_drivers": len(cards),
        "days":           days,
    }


@router.get("/scorecards/composite")
async def scorecards_composite(
    days: int = Query(7, ge=1, le=90),
    company: str | None = Query(None),
    subject: str | None = Query(
        None, pattern="^(driver|vehicle)$",
        description="which dimension to score along. "
                    "``driver`` (legacy) returns one card per "
                    "registered driver; ``vehicle`` returns one card per "
                    "truck (fixes the 80-trucks vs 18-drivers credibility "
                    "gap on fleets where most trucks aren't paired with a "
                    "named Samsara driver).  When unset, falls back to the "
                    "tenant's ``scorecard_default_subject`` setting "
                    "(default ``driver`` for back-compat).",
    ),
    user: dict = Depends(require_permission_any("can_scorecard_all", "can_scorecard_vehicle")),
    tenant=Depends(get_tenant_db),
    platform_db=Depends(get_platform_db),
):
    """Composite scorecards — driver- or vehicle-keyed.

    Returns each subject's 0-100 composite score plus the pillar /
    bonus / penalty breakdown that produced it.  The original Samsara
    columns are kept under ``inputs`` so the existing UI continues to
    work.  ``driver_id``/``driver_name`` are aliased to the subject id
    and name for back-compat — callers should branch on the new
    ``subject_type`` field when the distinction matters.
    """
    allowed = await get_user_company_codes(user)
    validate_company_access(allowed, company)
    own_perm = user.get("_matched_perm") == "can_scorecard_vehicle"
    vehicle_filter: list[str] | None = await get_user_vehicle_nums(user) if own_perm else None

    # ── Cache strategy: stale-while-revalidate (SWR) ─────────────
    # Skip the shared cache for own-only users — their results are
    # filtered by truck assignment and must not leak across drivers.
    # For everyone else we use a SWR wrapper so the second user inside
    # ``fresh_for`` seconds gets the cached payload instantly, and within
    # ``max_stale`` they still get cached data while a single background
    # task refreshes. Combined with the in-process single-flight
    # collapser this kills the cache-stampede + cold-start tail latency
    # that was producing 504s under load.

    # vehicle is now the canonical default subject \u2014 the
    # dashboard no longer offers a driver/truck toggle and per-vehicle
    # data is universally richer than per-driver in this telematics
    # stack.  The per-tenant ``KEY_SCORECARD_DEFAULT_SUBJECT`` setting
    # is still respected for tenants that explicitly pinned ``driver``,
    # but the implicit fallback flips to ``vehicle``.
    if subject is None:
        try:
            pref = (await tenant.get_account_setting(
                user["account_id"],
                tenant.KEY_SCORECARD_DEFAULT_SUBJECT,
                "vehicle",
            )) or "vehicle"
        except Exception:  # pragma: no cover — best-effort
            pref = "vehicle"
        subject = pref if pref in ("driver", "vehicle") else "vehicle"

    # SWR-cached path (account-wide reads).
    if not own_perm:
        async def _compute_payload() -> dict:
            return await _build_scorecards_payload(
                account_id=user["account_id"],
                subject=subject,
                days=days,
                company=company,
                vehicle_nums=None,
                allowed=allowed,
                tenant=tenant,
                platform_db=platform_db,
            )

        _cache_key = (
            f"scorecards:composite:{user['account_id']}:"
            f"{subject}:{days}:{company or '_'}"
        )
        # Scorecard data changes slowly (composite scores reflect
        # multi-day windows of activity), so we can hold the cache
        # much longer than the default 2-min fresh / 10-min stale.
        # New tuning: 30 min fresh, 2 h stale.  Combined with an
        # every-2-h prewarm cron, that means a freshly-prewarmed
        # entry serves the common dashboard windows (7/14/30/60/90)
        # for the *entire interval* between cron fires.  Users no
        # longer pay the 30-45 s cold-compute cost for "Last 30 days".
        return await _redis_cache.get_or_compute(
            _cache_key, _compute_payload,
            fresh_for=1800, max_stale=7200, lock_ttl=45,
        )

    # Own-perm path bypasses the shared SWR cache because results are
    # filtered per-user by truck assignment. Same builder, scoped input.
    payload = await _build_scorecards_payload(
        account_id=user["account_id"],
        subject=subject,
        days=days,
        company=company,
        vehicle_nums=vehicle_filter,
        allowed=allowed,
        tenant=tenant,
        platform_db=platform_db,
    )

    # Driver-role hardening: defence in depth. ``_svc_evaluate_subjects``
    # already filters by ``vehicle_nums``; this post-filter ensures no
    # roster info leaks even if upstream filtering regresses.
    if vehicle_filter is not None:
        own_set = {t.strip().lower() for t in vehicle_filter if t}
        cards = payload["scorecards"]
        if subject == "vehicle":
            cards = [
                c for c in cards
                if (c.get("subject_name") or c.get("driver_name") or "").strip().lower() in own_set
            ]
        else:  # driver subject
            cards = [
                c for c in cards
                if any(
                    (str(t) or "").strip().lower() in own_set
                    for t in (c.get("_truck_nums") or [])
                    if t
                )
            ]
        payload["scorecards"] = cards
        payload["count"] = len(cards)

    return payload


async def _build_scorecards_payload(
    *,
    account_id: int,
    subject: str,
    days: int,
    company: str | None,
    vehicle_nums: list[str] | None,
    allowed: list[str],
    tenant,
    platform_db,
) -> dict:
    """Cache-friendly payload builder.

    Pulled out of the request handler so the SWR cache layer can call it
    in the background to refresh stale entries without holding a request
    open. Pure function of (account_id, subject, days, company) plus the
    allowed-company filter — no FastAPI dependencies, no user context
    leakage. Own-perm callers should not use this path because their
    ``vehicle_nums`` filter scopes the result per-user and would poison
    the shared cache.
    """
    cards = await _svc_evaluate_subjects(
        account_id, subject=subject, days=days,
        company=company, vehicle_nums=vehicle_nums,
    )
    cards = filter_by_allowed_companies(cards, allowed, key="company")

    manual_map: dict[str, str] = {}
    if subject == "vehicle":
        try:
            manual_map = await platform_db.get_account_driver_vehicle_map(account_id)
        except Exception:  # pragma: no cover — defensive
            manual_map = {}

    _trend_days = max(14, min(int(days), 30))
    trends_map: dict[str, list[int]] = {}
    try:
        trends_map = await tenant.get_scorecard_trends_batch(
            account_id, subject_type=subject, days=_trend_days,
        )
    except Exception:  # pragma: no cover — defensive
        trends_map = {}

    for c in cards:
        sid = str(c.get("subject_id") or c.get("driver_id") or "")
        trend = trends_map.get(sid, [])
        c["score_trend"] = trend
        # grace period — drivers with fewer than
        # ``PROBATIONARY_MIN_SNAPSHOTS`` daily snapshots haven't
        # accrued enough history to be ranked fairly.  Marked
        # ``probationary`` so the UI can show a banner and the
        # ranking endpoints can exclude them from the leaderboard.
        c["probationary"] = len(trend) < PROBATIONARY_MIN_SNAPSHOTS
        if len(trend) >= 2:
            ref_idx = max(0, len(trend) - 8)
            c["week_delta"] = int(c.get("total") or 0) - int(trend[ref_idx])
        else:
            c["week_delta"] = None
        if subject == "vehicle":
            if not c.get("paired_driver_name"):
                truck_key = (c.get("subject_name") or c.get("driver_name") or "").strip().lower()
                c["assigned_driver_name"] = manual_map.get(truck_key) or None
            else:
                c["assigned_driver_name"] = None

    return {
        "scorecards": cards,
        "count":      len(cards),
        "days":       days,
        "subject":    subject,
        # Wall-clock at the moment the scorecards were computed. The
        # dashboard surfaces this as "Last updated …" so operators
        # know whether the warehouse snapshot is minutes or hours old.
        "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
    }


@router.get("/scorecards/history")
async def scorecards_history(
    driver_id: str | None = Query(
        None,
        description="Subject id.  Legacy spelling — ``subject_id`` is the "
                    "preferred field but ``driver_id`` is still accepted "
                    "for back-compat with existing dashboard/bot calls.",
    ),
    subject_id: str | None = Query(
        None,
        description="canonical name for the subject identifier. "
                    "When both ``driver_id`` and ``subject_id`` are set, "
                    "``subject_id`` wins.",
    ),
    subject: str = Query(
        "driver", pattern="^(driver|vehicle)$",
        description="subject type — ``driver`` or ``vehicle``. "
                    "Selects which row family in the snapshot table to read.",
    ),
    days: int = Query(30, ge=1, le=180),
    pillar: str | None = Query(
        None, pattern="^(safety|efficiency|compliance)$",
        description="Optional Option-C pillar filter. When set, returns the "
                    "per-pillar subtotal instead of the composite total. "
                    "Pre-Option-C snapshots without pillar data are skipped.",
    ),
    user: dict = Depends(require_permission_any("can_scorecard_all", "can_scorecard_vehicle")),
    tenant=Depends(get_tenant_db),
):
    """Score-over-time history for one subject (driver or vehicle)."""
    sid = subject_id or driver_id
    if not sid:
        raise HTTPException(
            status_code=422,
            detail="either subject_id or driver_id is required",
        )
    rows = await tenant.get_scorecard_snapshot_history(
        user["account_id"], subject_type=subject, subject_id=sid, days=days,
    )
    # Trim payload — UI only needs date + score (or per-pillar subtotal)
    series: list[dict] = []
    for r in reversed(rows):  # oldest → newest for charts
        if pillar is None:
            series.append({"date": r["snapshot_date"], "score": r["total_score"]})
            continue
        # Option-C path: pull the requested pillar's subtotal from
        # ``breakdown_json``.  Snapshots predating the pillar rollout do
        # not carry a ``pillars`` block — drop them silently rather than
        # fabricating a value the UI would mistake for "0".
        try:
            bd = json.loads(r.get("breakdown_json") or "{}")
        except (TypeError, ValueError):
            continue
        pillars = bd.get("pillars") or {}
        p = pillars.get(pillar)
        if not isinstance(p, dict) or "subtotal" not in p:
            continue
        series.append({
            "date": r["snapshot_date"],
            "score": int(p["subtotal"]),
            "has_data": bool(p.get("has_data", True)),
        })
    return {
        # Legacy alias — dashboard still reads ``driver_id``.
        "driver_id":    sid,
        "subject_id":   sid,
        "subject_type": subject,
        "pillar":       pillar,
        "history":      series,
        "count":        len(series),
    }


# ── Score explanation ─────────────────────────────────
#
# "Why did my score change?" — diffs the latest snapshot against
# one ~N days ago.  No new table is needed: the existing
# ``daily_scorecard_snapshots.breakdown_json`` already carries the
# full bonuses + penalties arrays at each snapshot.  This endpoint
# is the lightweight audit trail discussed in the roadmap.

def _index_events(events: list[dict]) -> dict[str, dict]:
    """Index a snapshot's ``bonuses`` or ``penalties`` array by ``rule_id``.

    Same rule fired multiple times in a window already aggregates into
    a single event with ``occurrences > 1``, so a flat dict keyed by
    rule_id is correct.  Falls back to ``label`` when ``rule_id`` is
    missing (legacy snapshots predating the rule_id field).
    """
    out: dict[str, dict] = {}
    for e in events or []:
        key = str(e.get("rule_id") or e.get("label") or "")
        if key:
            out[key] = e
    return out


def _diff_event_sets(prev: dict[str, dict], curr: dict[str, dict]) -> dict:
    """Compute the four-way diff between two indexed event maps.

    Returns ``added`` / ``cleared`` / ``increased`` / ``decreased``
    lists so the UI can render a clear "since {N} days ago" panel.
    """
    added: list[dict] = []
    cleared: list[dict] = []
    increased: list[dict] = []
    decreased: list[dict] = []
    for rid, ev in curr.items():
        if rid not in prev:
            added.append(ev)
        else:
            occ_from = int(prev[rid].get("occurrences", 1) or 1)
            occ_to = int(ev.get("occurrences", 1) or 1)
            if occ_to > occ_from:
                increased.append({**ev, "occ_from": occ_from, "occ_to": occ_to,
                                  "occ_delta": occ_to - occ_from})
            elif occ_to < occ_from:
                decreased.append({**ev, "occ_from": occ_from, "occ_to": occ_to,
                                  "occ_delta": occ_to - occ_from})
    for rid, ev in prev.items():
        if rid not in curr:
            cleared.append(ev)
    return {
        "added": added,
        "cleared": cleared,
        "increased": increased,
        "decreased": decreased,
    }


@router.get("/scorecards/me/explanation")
async def my_scorecard_explanation(
    days: int = Query(
        7, ge=1, le=90,
        description="Compare today's snapshot to the one ~N days ago.",
    ),
    user: dict = Depends(require_permission_any("can_scorecard_all", "can_scorecard_vehicle")),
    tenant=Depends(get_tenant_db),
):
    """Driver-facing explanation: diff between today's snapshot and one
    ~N days ago for the calling user.  Auto-resolves the caller's
    subject from their truck assignment so the miniapp doesn't need
    to know its own subject_id.  Same diff payload as
    ``/scorecards/{subject_id}/explanation`` but with RBAC inlined:
    drivers can only see their own explanation, never another driver's.

    Must be registered BEFORE the path-param explanation endpoint —
    Starlette matches routes in registration order and ``/me`` would
    otherwise be captured as a literal subject_id by the wildcard.
    """
    my_trucks = await get_user_vehicle_nums(user)
    if not my_trucks:
        raise HTTPException(
            status_code=404,
            detail="No driver/truck assignment for caller — ask an admin to "
                   "link your Telegram user to a truck_num.",
        )
    my_cards = await _svc_evaluate_subjects(
        user["account_id"], subject="driver", days=days, vehicle_nums=my_trucks,
    )
    if not my_cards:
        raise HTTPException(
            status_code=404,
            detail="No scorecard for your truck(s) in the requested window.",
        )
    my_id = str(my_cards[0].get("subject_id") or my_cards[0].get("driver_id") or "")

    rows = await tenant.get_scorecard_snapshot_history(
        user["account_id"],
        subject_type="driver",
        subject_id=my_id,
        days=days + 1,
    )
    if not rows or len(rows) < 2:
        return {
            "subject_id": my_id,
            "subject_type": "driver",
            "available": False,
            "reason": "not_enough_snapshots",
            "snapshots_available": len(rows or []),
        }
    curr_row = rows[0]
    prev_row = rows[-1]
    try:
        curr_bd = json.loads(curr_row.get("breakdown_json") or "{}")
        prev_bd = json.loads(prev_row.get("breakdown_json") or "{}")
    except (TypeError, ValueError):
        return {
            "subject_id": my_id,
            "subject_type": "driver",
            "available": False,
            "reason": "breakdown_unparseable",
        }
    penalties_diff = _diff_event_sets(
        _index_events(prev_bd.get("penalties", [])),
        _index_events(curr_bd.get("penalties", [])),
    )
    bonuses_diff = _diff_event_sets(
        _index_events(prev_bd.get("bonuses", [])),
        _index_events(curr_bd.get("bonuses", [])),
    )
    return {
        "subject_id": my_id,
        "subject_type": "driver",
        "available": True,
        "from_date": prev_row["snapshot_date"],
        "to_date": curr_row["snapshot_date"],
        "from_score": int(prev_row.get("total_score") or 0),
        "to_score": int(curr_row.get("total_score") or 0),
        "score_delta": int(curr_row.get("total_score") or 0) - int(prev_row.get("total_score") or 0),
        "penalties_added":     penalties_diff["added"],
        "penalties_cleared":   penalties_diff["cleared"],
        "penalties_increased": penalties_diff["increased"],
        "penalties_decreased": penalties_diff["decreased"],
        "bonuses_earned":      bonuses_diff["added"],
        "bonuses_lost":        bonuses_diff["cleared"],
        "bonuses_increased":   bonuses_diff["increased"],
        "bonuses_decreased":   bonuses_diff["decreased"],
    }


@router.get("/scorecards/{subject_id}/explanation")
async def scorecard_explanation(
    subject_id: str,
    subject_type: str = Query("driver", pattern="^(driver|vehicle)$"),
    days: int = Query(
        7, ge=1, le=90,
        description="Compare today's snapshot to the one ~N days ago.",
    ),
    user: dict = Depends(require_permission_any("can_scorecard_all", "can_scorecard_vehicle")),
    tenant=Depends(get_tenant_db),
):
    """Score explanation — diff between today's snapshot and one N days ago.

    Surfaces what changed: new penalties, cleared penalties, bonuses
    earned/lost, and per-rule occurrence-count changes.  Returns
    ``{"available": False, ...}`` when fewer than two snapshots exist
    in the window so the UI can render an empty-state message rather
    than a confusing "nothing changed" panel.
    """
    rows = await tenant.get_scorecard_snapshot_history(
        user["account_id"],
        subject_type=subject_type,
        subject_id=subject_id,
        days=days + 1,
    )
    # ``rows`` is newest → oldest; we need at least two snapshots to diff.
    if not rows or len(rows) < 2:
        return {
            "subject_id": subject_id,
            "subject_type": subject_type,
            "available": False,
            "reason": "not_enough_snapshots",
            "snapshots_available": len(rows or []),
        }
    curr_row = rows[0]
    # The oldest row in the window is the comparison baseline — picks
    # whatever exists if the snapshotter missed a day.
    prev_row = rows[-1]

    try:
        curr_bd = json.loads(curr_row.get("breakdown_json") or "{}")
        prev_bd = json.loads(prev_row.get("breakdown_json") or "{}")
    except (TypeError, ValueError):
        return {
            "subject_id": subject_id,
            "subject_type": subject_type,
            "available": False,
            "reason": "breakdown_unparseable",
        }

    penalties_diff = _diff_event_sets(
        _index_events(prev_bd.get("penalties", [])),
        _index_events(curr_bd.get("penalties", [])),
    )
    bonuses_diff = _diff_event_sets(
        _index_events(prev_bd.get("bonuses", [])),
        _index_events(curr_bd.get("bonuses", [])),
    )
    return {
        "subject_id": subject_id,
        "subject_type": subject_type,
        "available": True,
        "from_date": prev_row["snapshot_date"],
        "to_date": curr_row["snapshot_date"],
        "from_score": int(prev_row.get("total_score") or 0),
        "to_score": int(curr_row.get("total_score") or 0),
        "score_delta": int(curr_row.get("total_score") or 0) - int(prev_row.get("total_score") or 0),
        # Each list is a flat array of ScoreEventBreakdown-shaped dicts;
        # ``increased`` / ``decreased`` entries additionally carry
        # ``occ_from`` / ``occ_to`` / ``occ_delta``.
        "penalties_added":     penalties_diff["added"],
        "penalties_cleared":   penalties_diff["cleared"],
        "penalties_increased": penalties_diff["increased"],
        "penalties_decreased": penalties_diff["decreased"],
        "bonuses_earned":      bonuses_diff["added"],
        "bonuses_lost":        bonuses_diff["cleared"],
        "bonuses_increased":   bonuses_diff["increased"],
        "bonuses_decreased":   bonuses_diff["decreased"],
    }


# ── Driver miniapp scorecard ────────────────────────


def _rank_in(sorted_scores: list[int], my_score: int) -> dict:
    """Return ``{"pos": 1-based, "total": N}`` for ``my_score`` in
    ``sorted_scores`` (already sorted DESC).  Ties: min-rank (best
    position among equal scores).
    """
    total = len(sorted_scores)
    if total == 0:
        return {"pos": 0, "total": 0}
    pos = total
    for i, s in enumerate(sorted_scores):
        if my_score >= s:
            pos = i + 1
            break
    return {"pos": pos, "total": total}


@router.get("/scorecards/me")
async def my_scorecard(
    days: int = Query(7, ge=1, le=90),
    user: dict = Depends(require_permission_any("can_scorecard_all", "can_scorecard_vehicle")),
    tenant=Depends(get_tenant_db),
):
    """Single-driver scorecard for the calling user (driver miniapp).

    Returns the caller's own composite card plus:
      * ``rank_in_pillar`` — ``{pillar: {pos, total}}`` across the full
        account leaderboard.  Drivers with ``can_scorecard_vehicle`` only
        see aggregate ranks (no other drivers' details leak).
      * ``week_delta`` — point change vs. the snapshot ~7 days ago,
        per-pillar and total.  Returns ``None`` for any pillar without
        a prior-week snapshot.
    """
    # Driver-role hardening: when the caller has *only* can_scorecard_vehicle
    # (no can_scorecard_all), suppress every cross-driver field
    # (rank_in_pillar, rank_total, account_size) so the response cannot
    # be used to enumerate the rest of the fleet.  The caller's own
    # card and week_delta are still returned.
    own_only = user.get("_matched_perm") == "can_scorecard_vehicle"
    my_trucks = await get_user_vehicle_nums(user)
    if not my_trucks:
        raise HTTPException(
            status_code=404,
            detail="No driver/truck assignment for caller — ask an admin to "
                   "link your Telegram user to a truck_num.",
        )

    # Full leaderboard (driver subject) — needed for rank.  Evaluating
    # the whole account is unavoidable because pillar ranks require
    # comparing against everyone.  ``can_scorecard_vehicle`` users still
    # only ever see their *own* card; ranks are aggregate counts.
    all_cards = await _svc_evaluate_subjects(
        user["account_id"], subject="driver", days=days,
    )
    if not all_cards:
        raise HTTPException(status_code=404, detail="No scorecards available yet.")

    # Caller's card: re-evaluate with truck filter; take the first
    # match.  This reuses scoring/service.py's existing
    # vehicle_nums-intersect-_vehicle_summaries logic, so we don't have
    # to duplicate the matching rules here.
    my_cards = await _svc_evaluate_subjects(
        user["account_id"], subject="driver", days=days, vehicle_nums=my_trucks,
    )
    if not my_cards:
        raise HTTPException(
            status_code=404,
            detail="No scorecard for your truck(s) in the requested window.",
        )
    me = my_cards[0]
    my_id = me["subject_id"]

    # Probationary detection — pull the trend batch once
    # and mark every driver whose snapshot count is below the grace
    # threshold.  Probationary drivers are excluded from the rank
    # pool below so new hires don't outrank veterans simply because
    # they have no events yet.
    _probationary_ids: set[str] = set()
    try:
        _trend_days = max(PROBATIONARY_MIN_SNAPSHOTS, min(int(days), 30))
        _trends = await tenant.get_scorecard_trends_batch(
            user["account_id"], subject_type="driver", days=_trend_days,
        )
        for c in all_cards:
            sid = str(c.get("subject_id") or c.get("driver_id") or "")
            trend = _trends.get(sid, [])
            is_probationary = len(trend) < PROBATIONARY_MIN_SNAPSHOTS
            c["probationary"] = is_probationary
            if is_probationary:
                _probationary_ids.add(sid)
        # Propagate flag onto the caller's own card too.
        me["probationary"] = my_id in _probationary_ids
    except Exception:  # pragma: no cover — defensive
        me["probationary"] = False

    # Per-pillar rank — sort the full leaderboard by each pillar's
    # subtotal DESC, find caller's position.  Pillars with
    # ``has_data: false`` are excluded.  Probationary drivers are
    # excluded so they neither claim a top rank nor inflate ``total``.
    # If the CALLER is probationary, rank is left null entirely (a
    # rank chip alongside the "your score is still building" banner
    # would contradict itself).
    pillar_names = ("safety", "efficiency", "compliance")
    rank_in_pillar: dict | None
    rank_total: dict | None
    if me.get("probationary"):
        rank_in_pillar = None
        rank_total = None
    else:
        # Single walk over all_cards collecting per-pillar score lists
        # + total-score list + each pillar's "mine" subtotal.  Previous
        # version walked the full leaderboard 4× (3 pillars + total).
        pillar_scores: dict[str, list[int]] = {p: [] for p in pillar_names}
        my_pillar_subtotal: dict[str, int | None] = {p: None for p in pillar_names}
        total_scores: list[int] = []
        for c in all_cards:
            sid = str(c.get("subject_id") or "")
            if sid in _probationary_ids:
                continue
            total_scores.append(int(c.get("total") or 0))
            pillars_data = c.get("pillars") or {}
            is_me = c.get("subject_id") == my_id
            for p in pillar_names:
                pdata = pillars_data.get(p) or {}
                if not pdata.get("has_data", False):
                    continue
                sub = int(pdata.get("subtotal") or 0)
                pillar_scores[p].append(sub)
                if is_me:
                    my_pillar_subtotal[p] = sub

        rank_in_pillar = {}
        for p in pillar_names:
            scores = pillar_scores[p]
            scores.sort(reverse=True)
            mine = my_pillar_subtotal[p]
            if mine is None:
                rank_in_pillar[p] = {"pos": 0, "total": len(scores)}
            else:
                rank_in_pillar[p] = _rank_in(scores, mine)

        total_scores.sort(reverse=True)
        rank_total = _rank_in(total_scores, int(me.get("total") or 0))

    # Week delta — compare against the snapshot ~7 days ago.  Pull
    # 14 days of history so we can pick the entry closest to "7 days
    # ago" even if the snapshotter missed a day.
    week_delta: dict = {"total": None, "safety": None, "efficiency": None, "compliance": None}
    try:
        history = await tenant.get_scorecard_snapshot_history(
            user["account_id"], subject_type="driver", subject_id=my_id, days=14,
        )
    except Exception:  # pragma: no cover — best-effort
        history = []
    if history:
        # Snapshots are ordered newest → oldest.  Find the row at index
        # ≥ 6 (i.e. ~7 days back) — clamp to the oldest available.
        ref = history[min(6, len(history) - 1)]
        try:
            ref_total = int(ref.get("total_score") or 0)
            week_delta["total"] = int(me.get("total") or 0) - ref_total
            ref_bd = json.loads(ref.get("breakdown_json") or "{}")
            ref_pillars = ref_bd.get("pillars") or {}
            for p in pillar_names:
                rp = ref_pillars.get(p) or {}
                if not isinstance(rp, dict) or "subtotal" not in rp:
                    continue
                cur = (me.get("pillars") or {}).get(p) or {}
                if not cur.get("has_data", False):
                    continue
                week_delta[p] = int(cur.get("subtotal") or 0) - int(rp.get("subtotal") or 0)
        except (TypeError, ValueError, json.JSONDecodeError):
            pass

    return {
        "scorecard":        me,
        "rank_in_pillar":   None if own_only else rank_in_pillar,
        "rank_total":       None if own_only else rank_total,
        "week_delta":       week_delta,
        "days":             days,
        # ``account_size`` excludes probationary drivers so the
        # miniapp "You vs N drivers" line matches the cohort the
        # caller is actually being ranked against.
        "account_size":     None if own_only else sum(
            1 for c in all_cards
            if str(c.get("subject_id") or "") not in _probationary_ids
        ),
        # Wall-clock at compute time — surfaced as the miniapp footer
        # "Updated …" line so drivers know freshness without polling.
        "generated_at":     datetime.now(timezone.utc).isoformat(timespec="seconds"),
    }


# ── Scorecard fleet summary ───────────────────────────────────


@router.get("/scorecards/summary")
async def scorecards_summary(
    days: int = Query(7, ge=1, le=90),
    company: str | None = Query(None),
    subject: str | None = Query(None, pattern="^(driver|vehicle)$"),
    user: dict = Depends(require_permission_any("can_scorecard_all", "can_scorecard_vehicle")),
    tenant=Depends(get_tenant_db),
    platform_db=Depends(get_platform_db),
):
    """Fleet-level score aggregates — avg, distribution, per-company breakdown.

    Cheaper than loading the full composite when you only need the KPI
    strip (fleet avg, count at-risk, distribution bars).  Returns a
    ``cached`` flag so the UI can show a "from cache" indicator.

    Response shape::

        {
          "subject": "vehicle",
          "days": 7,
          "account_avg": 87,
          "account_size": 77,
          "at_risk_count": 4,          # total < 60
          "distribution": [            # score bands [0,10), [10,20) … [90,100]
            {"band": "0–9",   "count": 0},
            {"band": "10–19", "count": 0},
            ...
            {"band": "90–100","count": 42}
          ],
          "by_company": [
            {"company": "CFT", "count": 12, "avg": 91, "min": 72, "max": 98},
            ...
          ],
          "pillar_avgs": {"safety": 44, "efficiency": 21, "compliance": 22}
        }
    """
    allowed = await get_user_company_codes(user)
    validate_company_access(allowed, company)
    own_perm = user.get("_matched_perm") == "can_scorecard_vehicle"
    vehicle_filter: list[str] | None = await get_user_vehicle_nums(user) if own_perm else None

    # Resolve subject (mirrors composite logic)
    if subject is None:
        try:
            pref = (await tenant.get_account_setting(
                user["account_id"], tenant.KEY_SCORECARD_DEFAULT_SUBJECT, "vehicle",
            )) or "vehicle"
            subject = pref if pref in ("driver", "vehicle") else "vehicle"
        except Exception:
            subject = "vehicle"

    # Try cache first — same key as composite so a warm composite
    # response is reused here without a second Samsara round-trip.
    _cache_key = (
        f"scorecards:composite:{user['account_id']}:"
        f"{subject}:{days}:{company or '_'}"
    )
    cached_payload = await _redis_cache.get(_cache_key)
    if cached_payload is not None:
        cards = cached_payload.get("scorecards", [])
        from_cache = True
    else:
        cards = await _svc_evaluate_subjects(
            user["account_id"], subject=subject, days=days,
            company=company, vehicle_nums=vehicle_filter,
        )
        cards = filter_by_allowed_companies(cards, allowed, key="company")
        from_cache = False

    if not cards:
        return {
            "subject": subject, "days": days,
            "account_avg": None, "account_size": 0, "at_risk_count": 0,
            "distribution": [], "by_company": [], "pillar_avgs": {},
            "cached": from_cache,
        }

    totals = [int(c.get("total") or 0) for c in cards]
    account_avg = round(sum(totals) / len(totals)) if totals else 0

    # 10-point bands
    bands = []
    for lo in range(0, 100, 10):
        hi = lo + 9 if lo < 90 else 100
        label = f"{lo}–{hi}"
        count = sum(1 for t in totals if lo <= t <= hi)
        bands.append({"band": label, "count": count})

    # Per-company breakdown
    by_company_map: dict[str, list[int]] = {}
    for c in cards:
        co = c.get("company") or "—"
        by_company_map.setdefault(co, []).append(int(c.get("total") or 0))
    by_company = sorted([
        {
            "company": co,
            "count":   len(scores),
            "avg":     round(sum(scores) / len(scores)),
            "min":     min(scores),
            "max":     max(scores),
        }
        for co, scores in by_company_map.items()
    ], key=lambda x: x["avg"], reverse=True)

    # Pillar averages (only cards that have pillar data)
    pillar_sums: dict[str, list[int]] = {"safety": [], "efficiency": [], "compliance": []}
    for c in cards:
        for p, accum in pillar_sums.items():
            pdata = (c.get("pillars") or {}).get(p) or {}
            if pdata.get("has_data"):
                accum.append(int(pdata.get("subtotal") or 0))
    pillar_avgs = {
        p: round(sum(v) / len(v)) if v else None
        for p, v in pillar_sums.items()
    }

    return {
        "subject":      subject,
        "days":         days,
        "account_avg":    account_avg,
        "account_size":   len(cards),
        "at_risk_count": sum(1 for t in totals if t < 60),
        "distribution": bands,
        "by_company":   by_company,
        "pillar_avgs":  pillar_avgs,
        "cached":       from_cache,
    }


# ── Scorecard rules + pillar caps ──────────────────────────────────────────────
# Tenant-level scoring config moved to /admin/scorecard-rules and
# /admin/scorecard-pillar-caps. Legacy /safety/scorecards/rules and
# /safety/scorecards/pillar-caps aliases are mounted in app.py via
# `legacy_router` below to keep old bookmarks working.

from interfaces.api.routes import admin as _admin_routes  # noqa: E402

legacy_router = APIRouter(tags=["safety"], include_in_schema=False)


@legacy_router.get("/safety/scorecards/rules")
async def _legacy_list_score_rules(
    user: dict = Depends(require_permission("can_manage_account")),
    tenant=Depends(get_tenant_db),
):
    return await _admin_routes.list_score_rules(user=user, tenant=tenant)


@legacy_router.put("/safety/scorecards/rules/{rule_id}")
async def _legacy_update_score_rule(
    rule_id: str,
    body: _admin_routes.ScoreRuleUpdate,
    user: dict = Depends(require_permission("can_manage_account")),
    tenant=Depends(get_tenant_db),
):
    return await _admin_routes.update_score_rule(rule_id=rule_id, body=body, user=user, tenant=tenant)


@legacy_router.delete("/safety/scorecards/rules/{rule_id}")
async def _legacy_reset_score_rule(
    rule_id: str,
    user: dict = Depends(require_permission("can_manage_account")),
    tenant=Depends(get_tenant_db),
):
    return await _admin_routes.reset_score_rule(rule_id=rule_id, user=user, tenant=tenant)


@legacy_router.get("/safety/scorecards/pillar-caps")
async def _legacy_get_pillar_caps(
    user: dict = Depends(require_permission("can_manage_account")),
    tenant=Depends(get_tenant_db),
):
    return await _admin_routes.get_pillar_caps(user=user, tenant=tenant)


@legacy_router.put("/safety/scorecards/pillar-caps")
async def _legacy_set_pillar_caps(
    body: _admin_routes.PillarCapsUpdate,
    user: dict = Depends(require_permission("can_manage_account")),
    tenant=Depends(get_tenant_db),
):
    return await _admin_routes.set_pillar_caps(body=body, user=user, tenant=tenant)


@legacy_router.delete("/safety/scorecards/pillar-caps")
async def _legacy_reset_pillar_caps(
    user: dict = Depends(require_permission("can_manage_account")),
    tenant=Depends(get_tenant_db),
):
    return await _admin_routes.reset_pillar_caps(user=user, tenant=tenant)


# ── Single-subject scorecard detail ──────────────────────────────────────────


@router.get("/scorecards/subject/{subject_id}")
async def get_subject_scorecard(
    subject_id: str,
    subject_type: str = Query("vehicle", pattern="^(driver|vehicle)$"),
    days: int = Query(7, ge=1, le=90),
    user: dict = Depends(require_permission_any("can_scorecard_all", "can_scorecard_vehicle")),
):
    """Compute and return a scorecard for a single subject (vehicle or driver).

    Avoids loading all 77+ subjects when deep-linking to one truck
    (e.g. ``?truck=111``).  Response shape is identical to one element
    of the ``/scorecards/composite`` array.
    """
    vehicle_nums: list[str] | None = None
    if subject_type == "vehicle":
        vehicle_nums = [subject_id]
    else:
        vehicle_nums = [subject_id]

    cards = await _svc_evaluate_subjects(
        user["account_id"],
        subject=subject_type,
        days=days,
        vehicle_nums=vehicle_nums,
    )
    if not cards:
        raise HTTPException(status_code=404, detail=f"No scorecard found for {subject_type} '{subject_id}'")
    return cards[0]


@router.get("/scorecards/{subject_id}/events")
async def get_subject_score_events(
    subject_id: str,
    subject_type: str = Query("vehicle", pattern="^(driver|vehicle)$"),
    since: str | None = Query(None, description="ISO date YYYY-MM-DD"),
    user: dict = Depends(require_permission_any("can_scorecard_all", "can_scorecard_vehicle")),
    tenant=Depends(get_tenant_db),
):
    """Return the score_events evidence trail for one subject.

    Rows are written by the nightly snapshot job — one row per fired rule
    per scoring run.  Use *since* to narrow the window (e.g. `since=2026-04-01`).

    Example response:
        [
          {
            "id": 1, "rule_id": "safety.harsh_braking",
            "points": -5, "occurred_at": "2026-05-03",
            "evidence_type": "safety", "evidence_id": ""
          },
          ...
        ]
    """
    events = await tenant.get_score_events(
        user["account_id"],
        subject_type=subject_type,
        subject_id=subject_id,
        since=since,
    )
    return events
