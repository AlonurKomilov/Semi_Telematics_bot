"""Scorecard orchestrator — gather signals → engine → composite Scorecards.

This is the entrypoint the API / bot / nightly snapshot job all call.
It is the *only* layer that touches I/O (Samsara client, tenant DB).
``engine.py`` and the signal modules stay pure-data.

Phase B (April 2026) — ``evaluate_subjects(subject='driver'|'vehicle')``
is now the canonical entrypoint.  ``evaluate_drivers`` is preserved as a
thin alias for back-compat of bot/AI-tool callers; the API layer uses
``evaluate_subjects`` directly.
"""

from __future__ import annotations

import asyncio
import logging
from dataclasses import asdict

from capabilities.events.service import get_events
from capabilities.telemetry.service import (
    get_driver_efficiency,
    get_fleet_efficiency,
    get_vehicle_health,
    get_vehicles_with_faults,
)
from capabilities.vehicles.service import prepare_companies
from infra.services import get_tenant_db

from .engine import Scorecard, score
from .rules import apply_overrides, get_default_rules
from .signals import cameras as camera_signals
from .signals import efficiency as eff_signals
from .signals import fuel as fuel_signals
from .signals import maintenance as maint_signals
from .signals import safety_events as safety_signals
from .signals import vehicle_efficiency as veh_eff_signals
from .signals import vehicle_health as health_signals


logger = logging.getLogger(__name__)


# Source-transparency badges for the API response — surfaced in the UI
# breakdown so operators know where each fact came from.
SOURCE_BADGES = {
    "samsara":  "🅢",
    "internal": "🅘",
    "manual":   "🅜",
}


async def evaluate_subjects(
    account_id: int,
    *,
    subject: str = "driver",
    days: int = 7,
    company: str | None = None,
    vehicle_nums: list[str] | None = None,
    write_evidence: bool = False,
    evidence_date: str | None = None,
) -> list[dict]:
    """Compute composite scorecards for every *subject* in scope.

    *subject* selects the dimension along which scores are computed:

    - ``"driver"`` (legacy default): one card per driver, exposure
      aggregated across all the trucks that driver operated.
    - ``"vehicle"`` (Phase B): one card per truck, with telemetry that
      vehicle's own engine/idle/eco data and any safety events tagged
      with that vehicle.  This is the credibility-fix path — a fleet of
      80 trucks with only 18 registered drivers will now produce 80
      cards instead of 18.

    *vehicle_nums* (when set) filters scope to those truck identifiers,
    used by ``can_scorecard_own`` callers in the bot.  In ``vehicle``
    mode it filters by exact (case-insensitive) truck name.  In
    ``driver`` mode it filters drivers whose ``_vehicle_summaries``
    intersect the list (existing behaviour).

    *write_evidence* — when True, fired rule events are written to the
    ``score_events`` table for historical audit / explanation queries.
    This flag should only be set by the nightly snapshot job to avoid
    writing duplicate rows on live API calls.

    Returns a list of API-friendly dicts (already serialisable), sorted
    highest score first.
    """
    if subject not in ("driver", "vehicle"):
        raise ValueError(f"unknown subject {subject!r}; expected driver|vehicle")

    # Structured timing — emits one log line per stage so prod logs can
    # show which call was the bottleneck (e.g. Samsara vs DB) without
    # needing a separate APM. Each stage logs ms; the closing line
    # breaks down the total. The overhead is sub-millisecond.
    import time as _time
    _t0 = _time.perf_counter()
    _stage: dict[str, float] = {}

    # Phase 6 observability — record each stage as a Prometheus
    # histogram alongside the structured-log line. Stays a no-op when
    # prometheus_client isn't installed.
    from infra import observability as _obs

    def _mark(stage: str, since: float) -> None:
        elapsed = _time.perf_counter() - since
        _stage[stage] = round(elapsed * 1000, 1)
        _obs.record_scorecard_stage(stage=stage, subject=subject, seconds=elapsed)

    # ── 1. Pull signals shared between subjects ──────────────────
    # Warm up company display names (idempotent — no-op if already done).
    _t = _time.perf_counter()
    await prepare_companies(account_id)
    _mark("prepare_companies", _t)

    tenant = await get_tenant_db(account_id)

    # Helper coroutines — each wraps a fallible I/O call so they can be
    # gathered in parallel with return_exceptions=True.

    async def _get_maint() -> list[dict]:
        if tenant is None:
            return []
        return await tenant.get_maintenance_tasks(account_id)

    async def _get_cameras() -> list[dict]:
        if tenant is None:
            return []
        return await tenant.get_camera_check_history(account_id, limit=500)

    async def _get_fuel() -> list[dict]:
        if tenant is None:
            return []
        return await tenant.get_fuel_entries(account_id, limit=1000)

    async def _get_overrides() -> dict[str, dict]:
        if tenant is None:
            return {}
        return await tenant.get_score_rule_overrides(account_id)

    async def _get_pillar_caps() -> dict[str, int] | None:
        if tenant is None:
            return None
        import json as _json
        raw_caps = await tenant.get_account_setting(
            account_id, tenant.KEY_SCORECARD_PILLAR_CAPS, "",
        )
        if raw_caps:
            parsed = _json.loads(raw_caps)
            if isinstance(parsed, dict) and sum(parsed.values()) == 100:
                return {str(k): int(v) for k, v in parsed.items()}
        return None

    async def _get_faults() -> list[dict]:
        result, _, _ = await get_vehicles_with_faults(account_id, company=company)
        return result

    # Fire all eight I/O-bound fetches concurrently.
    _t = _time.perf_counter()
    gathered = await asyncio.gather(
        get_events(account_id, days=days, company=company),
        _get_maint(),
        _get_cameras(),
        _get_fuel(),
        _get_overrides(),
        _get_pillar_caps(),
        get_vehicle_health(account_id, company=company),
        _get_faults(),
        return_exceptions=True,
    )
    _mark("signals_gather", _t)

    def _ok_list(idx: int, label: str) -> list[dict]:
        v = gathered[idx]
        if isinstance(v, BaseException):
            logger.exception("scorecard: failed to load %s", label, exc_info=v)
            return []
        return v  # type: ignore[return-value]

    events: list[dict] = _ok_list(0, "events")
    maint_tasks: list[dict] = _ok_list(1, "maintenance tasks")
    camera_checks: list[dict] = _ok_list(2, "camera checks")
    fuel_entries: list[dict] = _ok_list(3, "fuel entries")

    raw_overrides = gathered[4]
    if isinstance(raw_overrides, BaseException):
        logger.exception("scorecard: failed to load rule overrides", exc_info=raw_overrides)
        overrides: dict[str, dict] = {}
    else:
        overrides = raw_overrides  # type: ignore[assignment]

    raw_caps = gathered[5]
    if isinstance(raw_caps, BaseException):
        logger.debug("scorecard: failed to load pillar caps, using defaults")
        pillar_caps: dict[str, int] | None = None
    else:
        pillar_caps = raw_caps  # type: ignore[assignment]

    health_records: list[dict] = _ok_list(6, "vehicle health")
    faulted_vehicles: list[dict] = _ok_list(7, "vehicle faults")

    rules = apply_overrides(get_default_rules(), overrides)

    # ── 2. Per-subject signal aggregation ────────────────────────
    _t = _time.perf_counter()
    if subject == "driver":
        drivers = await get_driver_efficiency(
            account_id, days=days, company=company, vehicle_nums=vehicle_nums,
        )
        eff_by_subject = eff_signals.from_driver_records(drivers)
        events_by_subject = safety_signals.from_events(events)
    else:
        # Vehicle subject: pull fleet efficiency (per-truck) and bucket
        # events by vehicle id/name.  Truck-name filter applies here too.
        vehicles = await get_fleet_efficiency(
            account_id, days=days, company=company,
        )
        if vehicle_nums is not None:
            allowed = {t.strip().lower() for t in vehicle_nums if t}
            vehicles = [
                v for v in vehicles
                if (v.get("name") or "").strip().lower() in allowed
            ]
        eff_by_subject = veh_eff_signals.from_vehicle_records(vehicles)
        events_by_subject = safety_signals.from_events_by_vehicle(events)
    _mark("efficiency_fetch", _t)

    # ── 3. Score each subject ────────────────────────────────────
    _t = _time.perf_counter()
    scorecards: list[Scorecard] = []
    for sid, eff in eff_by_subject.items():
        truck_names = eff["_vehicle_names"]
        sig: dict = {
            "overspeed_min": eff["overspeed_min"],
            "green_pct":     eff["green_pct"],
            "antic_pct":     eff["antic_pct"],
            # Exposure metrics — required by curve evaluators (per-mile,
            # per-hour) and by the engine's insufficient-data flag.
            "miles":         eff["miles"],
            "drive_hours":   eff["drive_hours"],
            "idle_hours":    eff["idle_hours"],
            "idle_pct":      eff["idle_pct"],
            "events":        events_by_subject.get(sid)
                             or events_by_subject.get(eff["driver_name"], {"count": 0, "by_type": {}}),
            "maintenance":   maint_signals.aggregate(
                                 maint_tasks, truck_names, days,
                             ),
            "cameras":       camera_signals.aggregate(
                                 camera_checks, truck_names, days,
                             ),
            "fuel":          fuel_signals.aggregate(
                                 fuel_entries, truck_names, days,
                             ),
            "vehicle_health": health_signals.aggregate(
                                 health_records, faulted_vehicles, truck_names,
                             ),
        }
        sc = score(
            subject_id=sid,
            subject_name=eff["driver_name"],
            signals=sig,
            rules=rules,
            subject_type=subject,
            pillar_caps=pillar_caps,
        )
        scorecards.append(sc)
    _mark("scoring_loop", _t)

    # ── 3b. Write evidence trail (nightly only) ──────────────────
    if write_evidence and tenant is not None:
        from datetime import datetime, timezone as _tz
        occurred = evidence_date or datetime.now(_tz.utc).strftime("%Y-%m-%d")
        event_rows: list[dict] = []
        for sc in scorecards:
            for ev in sc.bonuses + sc.penalties:
                event_rows.append({
                    "subject_type": sc.subject_type,
                    "subject_id":   sc.subject_id,
                    "rule_id":      ev.rule_id,
                    "points":       ev.points,
                    "occurred_at":  occurred,
                    "evidence_type": ev.category,
                    "evidence_id":  "",
                })
        if event_rows:
            try:
                written = await tenant.batch_add_score_events(account_id, event_rows)
                logger.debug("score_events: wrote %d rows for account %d", written, account_id)
            except Exception:
                logger.exception("score_events: batch write failed for account %d", account_id)

    # ── 4. Shape API response (with badges + raw inputs preserved) ─
    out: list[dict] = []
    for sc in scorecards:
        eff_rec = eff_by_subject[sc.subject_id]
        raw     = eff_rec["_raw"]
        # Pillar block (Audit Option C) — the new canonical shape.
        # Retains legacy top-level ``score / base / bonus_total /
        # penalty_total / bonuses / penalties`` aliases so the dashboard
        # can transition over one release.
        pillars_payload = {
            name: {
                "cap":            ps.cap,
                "subtotal":       ps.subtotal,
                "bonus_total":    ps.bonus_total,
                "penalty_total":  ps.penalty_total,
                "has_data":       ps.has_data,
                "events": [
                    _event_dict(e, "internal" if e.category != "safety" else "samsara")
                    for e in (ps.bonuses + ps.penalties)
                ],
            }
            for name, ps in sc.pillars.items()
        }
        out.append({
            # New canonical subject fields (Phase B)
            "subject_id":   sc.subject_id,
            "subject_name": sc.subject_name,
            "subject_type": sc.subject_type,
            # Legacy aliases — driver_id/driver_name carry the subject
            # value either way so existing dashboard rows still render
            # correctly when ``subject_type == 'vehicle'``.
            "driver_id":    sc.subject_id,
            "driver_name":  sc.subject_name,
            "company":      eff_rec["company"],
            # Phase F (driver-inline) — when the subject is a vehicle,
            # surface the Samsara-paired driver name (if any) so the
            # dashboard can show "Truck 42 \u2014 Jane Doe" without an
            # extra round-trip.  ``raw["_driver_name"]`` is populated
            # by ``get_fleet_efficiency`` and is ``None`` for unpaired
            # trucks.  ``assigned_driver_name`` (manual fallback) is
            # filled in by the API route layer using the per-account
            # driver\u2192truck assignment table.
            "paired_driver_name": (
                raw.get("_driver_name")
                if sc.subject_type == "vehicle" else None
            ),
            # New canonical scoring fields
            "total":             sc.total,
            "pillars":           pillars_payload,
            "exposure": {
                "miles":         round(eff_rec["miles"], 1),
                "drive_hours":   round(eff_rec["drive_hours"], 1),
                "idle_hours":    round(eff_rec["idle_hours"], 1),
            },
            "insufficient_data": sc.insufficient_data,
            # Legacy aliases (one-release deprecation window)
            "score":       sc.total,
            "base":        sc.base,
            "bonus_total": sc.bonus_points,
            "penalty_total": sc.penalty_points,
            "bonuses":     [_event_dict(e, "internal" if e.category != "safety" else "samsara")
                            for e in sc.bonuses],
            "penalties":   [_event_dict(e, "internal" if e.category != "safety" else "samsara")
                            for e in sc.penalties],
            # Existing Samsara columns kept for backward-compat & UI "Inputs".
            # Vehicle records expose ``_driving_hours``/``_idle_hours``
            # (different naming) — we read both keys so the same payload
            # shape works for both subjects.
            "inputs": {
                "miles":                     round(raw.get("_miles", 0) or 0, 1),
                "mpg":                       round(raw.get("_mpg", 0) or 0, 1),
                "drive_hours":               round(raw.get("_drive_h") or raw.get("_driving_hours", 0) or 0, 1),
                "idle_hours":                round(raw.get("_idle_h") or raw.get("_idle_hours", 0) or 0, 1),
                "drive_pct":                 round(raw.get("_drive_pct") or raw.get("_driving_pct", 0) or 0, 1),
                "idle_pct":                  round(raw.get("_idle_pct", 0) or 0, 1),
                "eco_pct":                   round(raw.get("_green_pct", 0) or 0, 1),
                "overspeed_min":             round(raw.get("_overspeed_min", 0) or 0, 1),
                "coast_min":                 round(raw.get("_coast_min", 0) or 0, 1),
                "cruise_min":                round(raw.get("_cruise_min", 0) or 0, 1),
                "anticipatory_braking_pct":  round(raw.get("_antic_pct", 0) or 0, 1),
                "_source": SOURCE_BADGES["samsara"],
            },
        })

    out.sort(key=lambda r: r["score"], reverse=True)

    # Structured timing — one log line per request, easy to grep in prod
    # to tell whether the slow step is Samsara, the DB, or our scoring
    # loop. Logs at INFO when total ≥ 1s, DEBUG otherwise to keep noise
    # down on warm-cache hits.
    _total_ms = round((_time.perf_counter() - _t0) * 1000, 1)
    _stage["total"] = _total_ms
    _payload = " ".join(f"{k}={v}ms" for k, v in _stage.items())
    _msg = (
        "scorecard.timing acct=%d subject=%s days=%d cards=%d %s"
    )
    _args = (account_id, subject, days, len(out), _payload)
    if _total_ms >= 1000:
        logger.info(_msg, *_args)
    else:
        logger.debug(_msg, *_args)

    return out


# ── Back-compat alias ────────────────────────────────────────────
# Bot, AI tools, and existing tests still call ``evaluate_drivers``.
# Keep it as a thin shim so we don't have to touch every caller in one
# PR.  Same signature, same return shape (``subject='driver'`` mode is
# byte-for-byte identical to pre-Phase-B output for the legacy fields,
# and adds new ``subject_id/subject_name/subject_type`` fields that
# legacy callers ignore).

async def evaluate_drivers(
    account_id: int,
    *,
    days: int = 7,
    company: str | None = None,
    vehicle_nums: list[str] | None = None,
) -> list[dict]:
    """Back-compat alias — calls ``evaluate_subjects(subject='driver')``."""
    return await evaluate_subjects(
        account_id,
        subject="driver",
        days=days,
        company=company,
        vehicle_nums=vehicle_nums,
    )


def _event_dict(ev, source: str) -> dict:
    d = asdict(ev)
    d["source"] = SOURCE_BADGES.get(source, "")
    return d
