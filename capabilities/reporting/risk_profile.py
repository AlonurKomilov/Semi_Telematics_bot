"""Stakeholder Risk Summary — data assembly.

Pulls every input the Risk Summary report needs into a single
``RiskProfile`` dataclass.  All sources are read-only queries against
existing tables — this module never writes.

Inputs aggregated:
    - Latest scorecard (live or daily snapshot)
    - Daily score history (for the trend chart)
    - Score events (the rules that fired, with severity)
    - Safety events (Samsara warehouse — collisions, harsh-brake, etc.)
    - Maintenance tasks (overdue + pending)
    - Fleet comparison (subject's score vs fleet median / percentile)
"""
from __future__ import annotations

import asyncio
import logging
import statistics
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Optional

from capabilities.scoring.service import evaluate_subjects
from infra.platform import get_tenant_db

logger = logging.getLogger(__name__)


@dataclass
class RiskProfile:
    """All data required by the Stakeholder Risk Summary PDF."""
    # Identity
    account_id: int
    subject_type: str                       # "driver" | "vehicle"
    subject_id: str                         # canonical key (driver_id or vehicle name)
    subject_display: str                    # human-readable label
    company: Optional[str] = None
    days: int = 30

    # Score
    total_score: Optional[int] = None       # 0-100
    grade: Optional[str] = None             # "A" / "B" / "C" / ...
    pillars: dict[str, dict] = field(default_factory=dict)  # safety/efficiency/compliance/maintenance
    breakdown: dict[str, Any] = field(default_factory=dict)  # raw scoring engine breakdown

    # History
    daily_history: list[dict] = field(default_factory=list)  # snapshot rows, oldest→newest
    score_events: list[dict] = field(default_factory=list)   # fired rules

    # Incidents (Samsara safety event log)
    safety_events: list[dict] = field(default_factory=list)

    # Maintenance
    overdue_tasks: list[dict] = field(default_factory=list)
    pending_tasks: list[dict] = field(default_factory=list)

    # Fleet comparison
    fleet_median: Optional[float] = None
    fleet_percentile: Optional[float] = None  # subject's percentile within fleet (0-100)
    fleet_size: int = 0

    # Vehicle/driver enrichment (optional metadata blocks)
    vehicle_meta: dict[str, Any] = field(default_factory=dict)
    driver_meta: dict[str, Any] = field(default_factory=dict)

    # Generation metadata
    generated_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))


def _grade_from_score(score: Optional[int]) -> str:
    if score is None:
        return "—"
    if score >= 90:
        return "A"
    if score >= 80:
        return "B"
    if score >= 70:
        return "C"
    if score >= 60:
        return "D"
    return "F"


def _percentile(value: float, population: list[float]) -> int:
    """Return the percentile (0-100) of ``value`` within ``population``."""
    if not population:
        return 0
    below = sum(1 for v in population if v < value)
    return int(round(100 * below / len(population)))


def _card_score(card: dict[str, Any]) -> Optional[float]:
    for key in ("total_score", "total", "score"):
        value = card.get(key)
        if isinstance(value, (int, float)):
            return float(value)
    return None


def _card_subject_key(card: dict[str, Any], subject_type: str) -> str:
    if subject_type == "vehicle":
        return str(card.get("vehicle_name") or card.get("subject_id") or "")
    return str(card.get("driver_id") or card.get("subject_id") or "")


def _card_subject_label(card: dict[str, Any], subject_type: str, default: str) -> str:
    if subject_type == "vehicle":
        return str(card.get("vehicle_name") or card.get("subject_name") or default)
    return str(card.get("driver_name") or card.get("subject_name") or default)


def _card_company(card: dict[str, Any]) -> str:
    breakdown = card.get("breakdown") or {}
    return str(card.get("company") or breakdown.get("company") or "").strip()


async def build_risk_profile(
    account_id: int,
    *,
    subject_type: str,
    subject_id: str,
    days: int = 30,
    company: Optional[str] = None,
) -> RiskProfile:
    """Assemble a RiskProfile for one driver or vehicle.

    Args:
        account_id: Tenant account.
        subject_type: ``"driver"`` or ``"vehicle"``.
        subject_id: Driver ID (Samsara) or vehicle name (e.g. truck #).
        days: History window in days. Defaults to 30.
        company: Optional company code to scope fleet comparison.

    Raises:
        ValueError: on invalid subject_type.
    """
    if subject_type not in ("driver", "vehicle"):
        raise ValueError(f"unknown subject_type {subject_type!r}")

    tenant = await get_tenant_db(account_id)

    async def _load_snapshot_cards() -> list[dict[str, Any]]:
        if tenant is None:
            return []
        rows = await tenant.get_latest_scorecard_snapshots(
            account_id, subject_type=subject_type,
        )
        if company:
            want = company.strip().lower()
            rows = [c for c in rows if _card_company(c).lower() == want]
        return rows

    # ── 1. Compute scorecards for the whole fleet (drives comparison too) ──
    try:
        if subject_type == "vehicle":
            cards = await asyncio.wait_for(
                evaluate_subjects(
                    account_id, subject="vehicle",
                    days=min(days, 30),
                    company=company,
                ),
                timeout=15,
            )
        else:
            cards = await asyncio.wait_for(
                evaluate_subjects(
                    account_id, subject="driver",
                    days=min(days, 30),
                    company=company,
                ),
                timeout=15,
            )
    except Exception as e:
        logger.warning(
            "evaluate_subjects failed for risk_profile, falling back to snapshots: %s",
            e,
        )
        cards = await _load_snapshot_cards()

    if not cards:
        cards = await _load_snapshot_cards()

    # Locate this subject's card.
    target = None
    for c in cards:
        key = _card_subject_key(c, subject_type)
        if key.lower() == subject_id.lower():
            target = c
            break

    total_score = None
    pillars: dict[str, dict] = {}
    breakdown: dict[str, Any] = {}
    subject_display = subject_id
    if target:
        raw_score = _card_score(target)
        total_score = int(raw_score) if raw_score is not None else None
        breakdown = target.get("breakdown") or {}
        # Pillar scores live under breakdown["pillars"] in the standard shape
        # but tolerate a flat "pillars" key as well.
        pl = target.get("pillars") or breakdown.get("pillars") or {}
        if isinstance(pl, dict):
            pillars = {str(k): (v if isinstance(v, dict) else {"subtotal": v}) for k, v in pl.items()}
        subject_display = _card_subject_label(target, subject_type, subject_id)

    # ── 2. Fleet comparison ──────────────────────────────────────
    population = [
        score
        for c in cards
        if (score := _card_score(c)) is not None
    ]
    fleet_median = statistics.median(population) if population else None
    fleet_percentile = (
        _percentile(float(total_score), population)
        if total_score is not None and population else None
    )

    # ── 3. Snapshot history ──────────────────────────────────────
    try:
        history = await tenant.get_scorecard_snapshot_history(
            account_id,
            subject_type=subject_type,
            subject_id=subject_id,
            days=days,
        )
        # Returned newest-first; reverse for time-series rendering.
        history.reverse()
    except Exception as e:
        logger.debug("scorecard history unavailable: %s", e)
        history = []

    # ── 4. Score events (fired rules) ────────────────────────────
    try:
        events = await tenant.get_score_events(
            account_id,
            subject_type=subject_type,
            subject_id=subject_id,
            days=days,
        )
    except Exception as e:
        logger.debug("score_events unavailable: %s", e)
        events = []

    # ── 5. Safety events (Samsara warehouse) ─────────────────────
    safety: list[dict] = []
    try:
        if subject_type == "vehicle":
            safety = await tenant.get_safety_events_warehouse(
                account_id, days=days, vehicle_name=subject_id, limit=500,
            )
            if not safety:
                safety = await tenant.get_safety_events_warehouse(
                    account_id, days=days, vehicle_id=subject_id, limit=500,
                )
        else:
            safety = await tenant.get_safety_events_warehouse(
                account_id, days=days, driver_id=subject_id, limit=500,
            )
    except Exception as e:
        logger.debug("safety events unavailable: %s", e)
        safety = []

    # ── 6. Maintenance (only meaningful for vehicle subjects) ────
    overdue: list[dict] = []
    pending: list[dict] = []
    if subject_type == "vehicle":
        try:
            overdue_all = await tenant.get_maintenance_tasks(
                account_id, status="overdue", vehicle_name=subject_id,
            )
            pending_all = await tenant.get_maintenance_tasks(
                account_id, status="pending", vehicle_name=subject_id,
            )
            overdue = overdue_all
            pending = pending_all
        except Exception as e:
            logger.debug("maintenance tasks unavailable: %s", e)

    return RiskProfile(
        account_id=account_id,
        subject_type=subject_type,
        subject_id=subject_id,
        subject_display=subject_display,
        company=company,
        days=days,
        total_score=total_score,
        grade=_grade_from_score(total_score),
        pillars=pillars,
        breakdown=breakdown,
        daily_history=history,
        score_events=events,
        safety_events=safety,
        overdue_tasks=overdue,
        pending_tasks=pending,
        fleet_median=fleet_median,
        fleet_percentile=fleet_percentile,
        fleet_size=len(population),
    )
