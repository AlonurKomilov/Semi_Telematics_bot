"""Safety & Compliance API endpoints — scorecards, events, camera checks."""

import json
import os

from fastapi import APIRouter, Depends, Query, HTTPException
from fastapi.responses import FileResponse

from interfaces.api.deps import require_permission, require_permission_any, get_tenant_db, get_platform_db, get_user_vehicle_nums, get_user_company_codes, validate_company_access, filter_by_allowed_companies
from infra.services import get_client
from capabilities.telemetry.service import get_driver_efficiency as _svc_driver_efficiency
from capabilities.events.severity import classify_event_severity as _classify_severity
from capabilities.scoring.service import evaluate_subjects as _svc_evaluate_subjects
from capabilities.scoring.rules import get_default_rules as _get_default_rules
from pydantic import BaseModel, Field
import infra.cache as _redis_cache

router = APIRouter(prefix="/safety", tags=["safety"])

_PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))


# ── Scorecards ────────────────────────────────────────────────

@router.get("/scorecards")
async def scorecards(
    days: int = Query(7, ge=1, le=90),
    company: str | None = Query(None),
    user: dict = Depends(require_permission_any("can_scorecard_all", "can_scorecard_own")),
):
    """Driver scorecards — efficiency + safety metrics per driver."""
    allowed = await get_user_company_codes(user)
    validate_company_access(allowed, company)
    # Pre-compute truck filter for own-only users so it's applied inside the service
    own_perm = user.get("_matched_perm") == "can_scorecard_own"
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
        description="Phase B: which dimension to score along. "
                    "``driver`` (legacy) returns one card per "
                    "registered driver; ``vehicle`` returns one card per "
                    "truck (fixes the 80-trucks vs 18-drivers credibility "
                    "gap on fleets where most trucks aren't paired with a "
                    "named Samsara driver).  When unset, falls back to the "
                    "tenant's ``scorecard_default_subject`` setting "
                    "(default ``driver`` for back-compat).",
    ),
    user: dict = Depends(require_permission_any("can_scorecard_all", "can_scorecard_own")),
    tenant=Depends(get_tenant_db),
    platform_db=Depends(get_platform_db),
):
    """Composite scorecards — driver- or vehicle-keyed (Phase B).

    Returns each subject's 0-100 composite score plus the pillar /
    bonus / penalty breakdown that produced it.  The original Samsara
    columns are kept under ``inputs`` so the existing UI continues to
    work.  ``driver_id``/``driver_name`` are aliased to the subject id
    and name for back-compat — callers should branch on the new
    ``subject_type`` field when the distinction matters.
    """
    allowed = await get_user_company_codes(user)
    validate_company_access(allowed, company)
    own_perm = user.get("_matched_perm") == "can_scorecard_own"
    vehicle_filter: list[str] | None = await get_user_vehicle_nums(user) if own_perm else None

    # ── Redis cache read (skip for own-only users — their results are
    # filtered by truck and must not leak cross-user cached data) ──
    if not own_perm:
        # Resolve subject early enough to build the cache key.  We replicate
        # the setting-lookup logic below so cache misses still do the full
        # path; on cache hit the setting lookup is avoided entirely.
        _subj_for_cache = subject
        if _subj_for_cache is None:
            try:
                _subj_for_cache = (await tenant.get_account_setting(
                    user["account_id"], tenant.KEY_SCORECARD_DEFAULT_SUBJECT, "vehicle",
                )) or "vehicle"
                if _subj_for_cache not in ("driver", "vehicle"):
                    _subj_for_cache = "vehicle"
            except Exception:
                _subj_for_cache = "vehicle"
        _cache_key = (
            f"scorecards:composite:{user['account_id']}:"
            f"{_subj_for_cache}:{days}:{company or '_'}"
        )
        cached = await _redis_cache.get(_cache_key)
        if cached is not None:
            return cached

    # Phase F: vehicle is now the canonical default subject \u2014 the
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

    cards = await _svc_evaluate_subjects(
        user["account_id"], subject=subject, days=days,
        company=company, vehicle_nums=vehicle_filter,
    )
    cards = filter_by_allowed_companies(cards, allowed, key="company")

    # Driver-role hardening: defence in depth.  ``_svc_evaluate_subjects``
    # already filters by ``vehicle_nums`` but the post-filter here ensures
    # no roster info leaks even if upstream filtering regresses.
    if own_perm and vehicle_filter is not None:
        own_set = {t.strip().lower() for t in vehicle_filter if t}
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

    # Phase F (driver-inline + sparkline) \u2014 enrich the response with:
    #
    #   * ``assigned_driver_name``  manual driver\u2192truck assignment
    #     (admin Users page) for vehicle rows whose Samsara feed has
    #     no paired driver.  One batch query, one map, one lookup.
    #   * ``score_trend``  the last 14 daily totals from
    #     ``daily_scorecard_snapshots`` so the table can render an
    #     in-row sparkline without N parallel history calls.
    #
    # Both lookups are best-effort \u2014 if the underlying DB call fails
    # we leave the field empty rather than failing the whole response.
    manual_map: dict[str, str] = {}
    if subject == "vehicle":
        try:
            manual_map = await platform_db.get_account_driver_vehicle_map(
                user["account_id"],
            )
        except Exception:  # pragma: no cover \u2014 defensive
            manual_map = {}
    trends_map: dict[str, list[int]] = {}
    try:
        trends_map = await tenant.get_scorecard_trends_batch(
            user["account_id"], subject_type=subject, days=14,
        )
    except Exception:  # pragma: no cover \u2014 defensive
        trends_map = {}

    for c in cards:
        sid = str(c.get("subject_id") or c.get("driver_id") or "")
        trend = trends_map.get(sid, [])
        c["score_trend"] = trend
        # Week delta: compare live total against snapshot ~7 days ago.
        # The trend list holds up to 14 daily totals (newest last); the
        # entry at index 0 (oldest) is roughly 14 days ago, index 7 is
        # roughly 7 days ago.  We pick the oldest available entry that
        # is at least 6 positions back so short histories still work.
        if len(trend) >= 2:
            ref_idx = max(0, len(trend) - 8)  # ~7 days back
            c["week_delta"] = int(c.get("total") or 0) - int(trend[ref_idx])
        else:
            c["week_delta"] = None
        if subject == "vehicle":
            # Manual fallback only fires when Samsara has no paired
            # driver of its own — avoids overriding the authoritative
            # source with a stale local assignment.
            if not c.get("paired_driver_name"):
                truck_key = (c.get("subject_name") or c.get("driver_name") or "").strip().lower()
                c["assigned_driver_name"] = manual_map.get(truck_key) or None
            else:
                c["assigned_driver_name"] = None

    # ── Cache the result in Redis (2-minute TTL) ─────────────────
    # Key includes account + subject + days + company so different
    # query params get different cache slots.
    _cache_key = (
        f"scorecards:composite:{user['account_id']}:"
        f"{subject}:{days}:{company or '_'}"
    )
    await _redis_cache.cache_set(_cache_key, {
        "scorecards": cards,
        "count":      len(cards),
        "days":       days,
        "subject":    subject,
    }, ttl=120)

    return {
        "scorecards": cards,
        "count":      len(cards),
        "days":       days,
        "subject":    subject,
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
        description="Phase B canonical name for the subject identifier. "
                    "When both ``driver_id`` and ``subject_id`` are set, "
                    "``subject_id`` wins.",
    ),
    subject: str = Query(
        "driver", pattern="^(driver|vehicle)$",
        description="Phase B: subject type — ``driver`` or ``vehicle``. "
                    "Selects which row family in the snapshot table to read.",
    ),
    days: int = Query(30, ge=1, le=180),
    pillar: str | None = Query(
        None, pattern="^(safety|efficiency|compliance)$",
        description="Optional Option-C pillar filter. When set, returns the "
                    "per-pillar subtotal instead of the composite total. "
                    "Pre-Option-C snapshots without pillar data are skipped.",
    ),
    user: dict = Depends(require_permission_any("can_scorecard_all", "can_scorecard_own")),
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


# ── Driver miniapp scorecard (Phase D) ────────────────────────


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
    user: dict = Depends(require_permission_any("can_scorecard_all", "can_scorecard_own")),
    tenant=Depends(get_tenant_db),
):
    """Single-driver scorecard for the calling user (driver miniapp).

    Returns the caller's own composite card plus:
      * ``rank_in_pillar`` — ``{pillar: {pos, total}}`` across the full
        account leaderboard.  Drivers with ``can_scorecard_own`` only
        see aggregate ranks (no other drivers' details leak).
      * ``week_delta`` — point change vs. the snapshot ~7 days ago,
        per-pillar and total.  Returns ``None`` for any pillar without
        a prior-week snapshot.
    """
    # Driver-role hardening: when the caller has *only* can_scorecard_own
    # (no can_scorecard_all), suppress every cross-driver field
    # (rank_in_pillar, rank_total, account_size) so the response cannot
    # be used to enumerate the rest of the fleet.  The caller's own
    # card and week_delta are still returned.
    own_only = user.get("_matched_perm") == "can_scorecard_own"
    my_trucks = await get_user_vehicle_nums(user)
    if not my_trucks:
        raise HTTPException(
            status_code=404,
            detail="No driver/truck assignment for caller — ask an admin to "
                   "link your Telegram user to a truck_num.",
        )

    # Full leaderboard (driver subject) — needed for rank.  Evaluating
    # the whole account is unavoidable because pillar ranks require
    # comparing against everyone.  ``can_scorecard_own`` users still
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

    # Per-pillar rank — sort the full leaderboard by each pillar's
    # subtotal DESC, find caller's position.  Pillars with
    # ``has_data: false`` are excluded from the leaderboard for that
    # pillar so ranks stay meaningful.
    pillar_names = ("safety", "efficiency", "compliance")
    rank_in_pillar: dict = {}
    for p in pillar_names:
        scores: list[int] = []
        my_subtotal: int | None = None
        for c in all_cards:
            pdata = (c.get("pillars") or {}).get(p) or {}
            if not pdata.get("has_data", False):
                continue
            sub = int(pdata.get("subtotal") or 0)
            scores.append(sub)
            if c["subject_id"] == my_id:
                my_subtotal = sub
        scores.sort(reverse=True)
        if my_subtotal is None:
            rank_in_pillar[p] = {"pos": 0, "total": len(scores)}
        else:
            rank_in_pillar[p] = _rank_in(scores, my_subtotal)

    # Total-score rank (sort all_cards by ``total`` DESC).
    total_scores = sorted(
        (int(c.get("total") or 0) for c in all_cards), reverse=True,
    )
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
        "account_size":     None if own_only else len(all_cards),
    }


# ── Scorecard fleet summary ───────────────────────────────────


@router.get("/scorecards/summary")
async def scorecards_summary(
    days: int = Query(7, ge=1, le=90),
    company: str | None = Query(None),
    subject: str | None = Query(None, pattern="^(driver|vehicle)$"),
    user: dict = Depends(require_permission_any("can_scorecard_all", "can_scorecard_own")),
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
    own_perm = user.get("_matched_perm") == "can_scorecard_own"
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


# ── Scorecard rules admin (Phase 3) ───────────────────────────


class ScoreRuleUpdate(BaseModel):
    points: int = Field(..., ge=-100, le=100)
    cap: int | None = Field(None, ge=-200, le=200)
    enabled: bool = True
    # ── Audit Option C: optional per-rule curve anchors. NULL leaves
    # the code-defined default in place; admins only override the
    # numeric ramp, never the curve kind or the signal source.
    curve_x_zero: float | None = Field(None, ge=-1000, le=10000)
    curve_x_max:  float | None = Field(None, ge=-1000, le=10000)
    curve_y_max:  int   | None = Field(None, ge=-200, le=200)


@router.get("/scorecards/rules")
async def list_score_rules(
    user: dict = Depends(require_permission("can_manage_account")),
    tenant=Depends(get_tenant_db),
):
    """Return every default rule merged with this account's overrides.

    Each item carries the rule's metadata (id, label, category, **pillar**,
    kind, curve fields), its **effective** values, and an ``overridden``
    flag so the UI can render a "reset to default" button per row.
    """
    overrides = await tenant.get_score_rule_overrides(user["account_id"])
    out: list[dict] = []
    for r in _get_default_rules():
        ov = overrides.get(r.id) or {}
        out.append({
            "id":         r.id,
            "label":      r.label,
            "category":   r.category,
            "pillar":     r.pillar,
            "kind":       r.kind,
            "default_points":  r.points,
            "default_cap":     r.cap,
            "points":     int(ov["points"])  if "points"  in ov else r.points,
            "cap":        (ov["cap"] if ov.get("cap") is not None else r.cap)
                          if "cap" in ov else r.cap,
            "enabled":    bool(ov["enabled"]) if "enabled" in ov else r.enabled,
            # Curve metadata — null kind ⇒ legacy flat rule (no curve UI).
            "curve_kind":          r.curve_kind,
            "default_curve_x_zero": r.curve_x_zero,
            "default_curve_x_max":  r.curve_x_max,
            "default_curve_y_max":  r.curve_y_max,
            "curve_x_zero": (ov["curve_x_zero"]
                             if ov.get("curve_x_zero") is not None else r.curve_x_zero),
            "curve_x_max":  (ov["curve_x_max"]
                             if ov.get("curve_x_max")  is not None else r.curve_x_max),
            "curve_y_max":  (ov["curve_y_max"]
                             if ov.get("curve_y_max")  is not None else r.curve_y_max),
            "overridden": bool(ov),
        })
    return {"rules": out, "count": len(out)}


@router.put("/scorecards/rules/{rule_id}")
async def update_score_rule(
    rule_id: str,
    body: ScoreRuleUpdate,
    user: dict = Depends(require_permission("can_manage_account")),
    tenant=Depends(get_tenant_db),
):
    """Override a default rule's points / cap / enabled / curve anchors."""
    defaults = {r.id: r for r in _get_default_rules()}
    rule = defaults.get(rule_id)
    if rule is None:
        raise HTTPException(status_code=404, detail="Unknown rule_id")
    await tenant.upsert_score_rule(
        user["account_id"], rule_id,
        label=rule.label, category=rule.category, kind=rule.kind,
        points=body.points, cap=body.cap, enabled=body.enabled,
        pillar=rule.pillar,
        curve_x_zero=body.curve_x_zero,
        curve_x_max=body.curve_x_max,
        curve_y_max=body.curve_y_max,
    )
    return {"ok": True, "rule_id": rule_id}


@router.delete("/scorecards/rules/{rule_id}")
async def reset_score_rule(
    rule_id: str,
    user: dict = Depends(require_permission("can_manage_account")),
    tenant=Depends(get_tenant_db),
):
    """Drop the per-account override → rule reverts to built-in default."""
    deleted = await tenant.delete_score_rule(user["account_id"], rule_id)
    return {"ok": True, "rule_id": rule_id, "deleted": deleted}


# ── Pillar caps ────────────────────────────────────────────────────────────────


class PillarCapsUpdate(BaseModel):
    safety:     int = Field(..., ge=0, le=100)
    efficiency: int = Field(..., ge=0, le=100)
    compliance: int = Field(..., ge=0, le=100)

    @property
    def total(self) -> int:
        return self.safety + self.efficiency + self.compliance


@router.get("/scorecards/pillar-caps")
async def get_pillar_caps(
    user: dict = Depends(require_permission("can_manage_account")),
    tenant=Depends(get_tenant_db),
):
    """Return current pillar cap weights for this account.

    Defaults (50/25/25) are returned when no override has been set.
    """
    from capabilities.scoring.engine import PILLAR_CAPS
    raw = await tenant.get_account_setting(
        user["account_id"], tenant.KEY_SCORECARD_PILLAR_CAPS, "",
    )
    if raw:
        try:
            caps = json.loads(raw)
            return {"safety": caps.get("safety", PILLAR_CAPS["safety"]),
                    "efficiency": caps.get("efficiency", PILLAR_CAPS["efficiency"]),
                    "compliance": caps.get("compliance", PILLAR_CAPS["compliance"]),
                    "is_custom": True}
        except Exception:
            pass
    return {**PILLAR_CAPS, "is_custom": False}


@router.put("/scorecards/pillar-caps")
async def set_pillar_caps(
    body: PillarCapsUpdate,
    user: dict = Depends(require_permission("can_manage_account")),
    tenant=Depends(get_tenant_db),
):
    """Set per-tenant pillar cap weights. Must sum to exactly 100."""
    if body.total != 100:
        raise HTTPException(
            status_code=422,
            detail=f"Pillar caps must sum to 100 (got {body.total}).",
        )
    caps = {"safety": body.safety, "efficiency": body.efficiency, "compliance": body.compliance}
    await tenant.set_account_setting(
        user["account_id"],
        tenant.KEY_SCORECARD_PILLAR_CAPS,
        json.dumps(caps),
    )
    return {"ok": True, "caps": caps}


@router.delete("/scorecards/pillar-caps")
async def reset_pillar_caps(
    user: dict = Depends(require_permission("can_manage_account")),
    tenant=Depends(get_tenant_db),
):
    """Remove pillar cap override — reverts to built-in defaults (50/25/25)."""
    from capabilities.scoring.engine import PILLAR_CAPS
    await tenant.set_account_setting(
        user["account_id"], tenant.KEY_SCORECARD_PILLAR_CAPS, "",
    )
    return {"ok": True, "caps": PILLAR_CAPS, "is_custom": False}


# ── Single-subject scorecard detail ──────────────────────────────────────────


@router.get("/scorecards/subject/{subject_id}")
async def get_subject_scorecard(
    subject_id: str,
    subject_type: str = Query("vehicle", pattern="^(driver|vehicle)$"),
    days: int = Query(7, ge=1, le=90),
    user: dict = Depends(require_permission("can_scorecard")),
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
    user: dict = Depends(require_permission("can_scorecard")),
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


# ── Safety Events ─────────────────────────────────────────────


@router.get("/events")
async def safety_events(
    days: int = Query(7, ge=1, le=90),
    event_type: str | None = Query(None, description="crash, braking, harshTurn, etc."),
    driver: str | None = Query(None, description="Filter by driver name (substring)"),
    company: str | None = Query(None),
    user: dict = Depends(require_permission_any("can_events_all", "can_events_own")),
):
    """Safety events — harsh braking, crashes, speeding, etc."""
    allowed = await get_user_company_codes(user)
    validate_company_access(allowed, company)
    client = await get_client(user["account_id"])

    async def _live():
        return await client.get_events(days=days, company=company)

    from capabilities.telemetry import warehouse_reader as _wh
    wh_rows = await _wh.get_safety_events(
        user["account_id"],
        days=days,
        event_type=event_type,
        samsara_fallback=_live,
    )
    # Warehouse rows wrap the original live-shape dict in ``raw``;
    # live fallback returns live-shape dicts directly.  Normalise so
    # downstream filters can rely on the live keys (event_id/time/g_force/_org/…).
    raw = [(r.get("raw") or r) for r in wh_rows]
    raw = filter_by_allowed_companies(raw, allowed)

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

    # If user only has _own, filter to their assigned vehicle
    if user.get("_matched_perm") == "can_events_own":
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


@router.get("/events/heatmap")
async def safety_events_heatmap(
    days: int = Query(30, ge=1, le=90),
    user: dict = Depends(require_permission_any("can_events_all", "can_events_own")),
):
    """Lat/lon density of safety events over the trailing window
    (Phase E26 \u2014 LiveMap heat layer).

    Reads directly from the per-tenant ``safety_event_log`` warehouse
    table.  Returns an empty ``points`` list when the warehouse flag
    is off so the heat layer simply renders nothing instead of
    erroring.
    """
    from capabilities.telemetry import warehouse_reader as _wh
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
    if user.get("_matched_perm") == "can_events_own":
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


# ── Camera Checks ────────────────────────────────────────────

@router.get("/cameras")
async def camera_checks(
    vehicle: str | None = Query(None, description="Filter by vehicle name"),
    latest_only: bool = Query(True, description="Only latest check per vehicle"),
    limit: int = Query(100, ge=1, le=500),
    user: dict = Depends(require_permission_any("can_faults", "can_vehicle_all")),
    tenant_db=Depends(get_tenant_db),
):
    """Camera check history — obstruction, alignment, quality per vehicle."""
    checks = await tenant_db.get_camera_check_history(
        user["account_id"],
        limit=limit,
        vehicle_name=vehicle if vehicle else None,
        latest_only=latest_only,
    )
    return {"checks": checks, "count": len(checks)}


@router.get("/cameras/{check_id}/image")
async def camera_check_image(
    check_id: int,
    user: dict = Depends(require_permission_any("can_faults", "can_vehicle_all")),
    tenant_db=Depends(get_tenant_db),
):
    """Serve the dashcam screenshot for a camera check."""
    checks = await tenant_db.get_camera_check_history(
        user["account_id"], limit=500,
    )
    check = next((c for c in checks if c.get("id") == check_id), None)
    if not check:
        raise HTTPException(status_code=404, detail="Camera check not found")
    img_path = check.get("image_path", "")
    if not img_path:
        raise HTTPException(status_code=404, detail="No image available")
    full_path = os.path.realpath(os.path.join(_PROJECT_ROOT, img_path))
    if not full_path.startswith(os.path.realpath(_PROJECT_ROOT)):
        raise HTTPException(status_code=403, detail="Access denied")
    if not os.path.isfile(full_path):
        raise HTTPException(status_code=404, detail="Image file not found")
    return FileResponse(full_path, media_type="image/jpeg")
