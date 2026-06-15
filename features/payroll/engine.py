"""Payroll computation engine — pure-ish: pulls scorecards + safety events,
applies bonus rules, returns RunItem objects.  Not yet persisted.
"""

from __future__ import annotations

import logging
from datetime import date, datetime, timezone
from typing import Optional

from capabilities.scorecards.service import evaluate_subjects
from infra.services import get_tenant_db

from .models import (
    KIND_INCIDENT_COUNT,
    KIND_SCORE_THRESHOLD,
    BonusRule,
    BreakdownEntry,
    RunItem,
)

log = logging.getLogger(__name__)


def _row_to_rule(row: dict) -> BonusRule:
    return BonusRule(
        id=int(row["id"]),
        name=str(row["name"]),
        kind=str(row["kind"]),
        amount_cents=int(row["amount_cents"]),
        period_days=int(row["period_days"] or 30),
        score_min=(float(row["score_min"]) if row.get("score_min") is not None else None),
        event_type=row.get("event_type") or None,
        max_count=(int(row["max_count"]) if row.get("max_count") is not None else None),
        active=bool(row.get("active", 1)),
    )


def _period_days(period_start: date, period_end: date) -> int:
    days = (period_end - period_start).days + 1
    return max(days, 1)


def evaluate_score_rule(
    rule: BonusRule, score: Optional[float],
) -> Optional[BreakdownEntry]:
    """Return a BreakdownEntry if the driver qualifies, else None."""
    if rule.kind != KIND_SCORE_THRESHOLD or rule.score_min is None:
        return None
    if score is None:
        return None
    if score >= rule.score_min:
        return BreakdownEntry(
            rule_id=rule.id,
            name=rule.name,
            kind=rule.kind,
            amount_cents=rule.amount_cents,
            detail=f"score {score:.0f} ≥ {rule.score_min:.0f}",
        )
    return None


def evaluate_incident_rule(
    rule: BonusRule, incident_count: int,
) -> Optional[BreakdownEntry]:
    if rule.kind != KIND_INCIDENT_COUNT or rule.max_count is None:
        return None
    if incident_count <= rule.max_count:
        et = rule.event_type or "any"
        return BreakdownEntry(
            rule_id=rule.id,
            name=rule.name,
            kind=rule.kind,
            amount_cents=rule.amount_cents,
            detail=f"{et} incidents={incident_count} ≤ {rule.max_count}",
        )
    return None


async def compute_run(
    account_id: int,
    period_start: date,
    period_end: date,
) -> list[RunItem]:
    """Compute payroll for one period.

    Pulls active bonus_rules + driver_pay_settings + scorecards over
    the period window; for incident_count rules, queries warehouse
    safety events filtered by event_type.  Returns one RunItem per
    opted-in driver with at least base pay or one qualifying bonus.
    """
    tenant = await get_tenant_db(account_id)
    if tenant is None:
        return []

    days = _period_days(period_start, period_end)

    # Active bonus rules + per-driver settings
    rule_rows = await tenant.list_bonus_rules(account_id, active_only=True)
    rules = [_row_to_rule(r) for r in rule_rows]

    settings_rows = await tenant.list_driver_pay_settings(account_id)
    settings_by_driver: dict[str, dict] = {
        str(r["driver_id"]): r for r in settings_rows
    }

    # Composite scorecards over the period
    cards = await evaluate_subjects(
        account_id, subject="driver", days=days, write_evidence=False,
    )
    cards_by_driver: dict[str, dict] = {}
    for c in cards:
        did = str(c.get("driver_id") or "").strip()
        if did:
            cards_by_driver[did] = c

    # Pre-pull incident counts per (driver, event_type) for incident rules
    incident_event_types = {
        r.event_type for r in rules
        if r.kind == KIND_INCIDENT_COUNT and r.event_type
    }
    has_any_kind = any(
        r.kind == KIND_INCIDENT_COUNT and not r.event_type for r in rules
    )

    # Drivers we will iterate: union of opted-in pay settings + drivers with cards.
    driver_ids = set(settings_by_driver.keys()) | set(cards_by_driver.keys())

    # Cache: counts_by_driver[driver_id][event_type or "*"] = int
    counts_by_driver: dict[str, dict[str, int]] = {}

    if (incident_event_types or has_any_kind) and driver_ids:
        # Bulk-fetch counts for typed event rules (one grouped query
        # replaces D × E queries — same pattern as coaching/engine.py).
        if incident_event_types:
            typed_counts = await tenant.get_safety_event_counts_grouped(
                account_id,
                days=days,
                event_types=[et for et in incident_event_types if et],
                driver_ids=list(driver_ids),
            )
            for (did, et), cnt in typed_counts.items():
                counts_by_driver.setdefault(did, {})[et or ""] = cnt
        # "Any-kind" rules need the per-driver total across all event
        # types — one ungrouped query collapses D queries to 1.
        if has_any_kind:
            any_counts = await tenant.get_safety_event_counts_grouped(
                account_id,
                days=days,
                event_types=None,
                driver_ids=list(driver_ids),
            )
            totals: dict[str, int] = {}
            for (did, _et), cnt in any_counts.items():
                totals[did] = totals.get(did, 0) + cnt
            for did, total in totals.items():
                counts_by_driver.setdefault(did, {})["*"] = total

    items: list[RunItem] = []
    for did in sorted(driver_ids):
        settings = settings_by_driver.get(did) or {}
        opt_in = bool(settings.get("opt_in", 1))
        if not opt_in:
            continue
        base_pay = int(settings.get("base_pay_cents", 0) or 0)
        card = cards_by_driver.get(did)
        score = float(card["score"]) if card and card.get("score") is not None else None
        driver_name = (
            (card.get("driver_name") if card else None)
            or settings.get("driver_id")
            or did
        )

        breakdown: list[BreakdownEntry] = []
        for rule in rules:
            entry: Optional[BreakdownEntry] = None
            if rule.kind == KIND_SCORE_THRESHOLD:
                entry = evaluate_score_rule(rule, score)
            elif rule.kind == KIND_INCIDENT_COUNT:
                key = rule.event_type or "*"
                count = (counts_by_driver.get(did) or {}).get(key, 0)
                entry = evaluate_incident_rule(rule, count)
            if entry is not None:
                breakdown.append(entry)

        bonus_total = sum(b.amount_cents for b in breakdown)
        total = base_pay + bonus_total

        # Skip drivers with neither base pay nor bonuses to keep runs clean.
        if total <= 0 and not breakdown:
            continue

        items.append(RunItem(
            driver_id=did,
            driver_name=str(driver_name),
            base_pay_cents=base_pay,
            bonus_total_cents=bonus_total,
            total_cents=total,
            breakdown=breakdown,
        ))

    return items


def utcnow() -> datetime:
    return datetime.now(timezone.utc)
