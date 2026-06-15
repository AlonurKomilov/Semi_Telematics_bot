"""Coaching evaluation engine.

Pulls active coaching_rules + driver scorecards + safety event counts,
returns ProposedAssignment objects.  Idempotent: drivers with a pending
assignment for the same topic within ``period_days`` are skipped.
"""

from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone
from typing import Optional

from capabilities.scorecards.service import evaluate_subjects
from infra.services import get_tenant_db

from .models import (
    KIND_INCIDENT_COUNT,
    KIND_SCORE_THRESHOLD,
    CoachingRule,
    ProposedAssignment,
)

log = logging.getLogger(__name__)


def _row_to_rule(row: dict) -> CoachingRule:
    return CoachingRule(
        id=int(row["id"]),
        name=str(row["name"]),
        kind=str(row["kind"]),
        topic_key=str(row.get("topic_key") or ""),
        period_days=int(row.get("period_days") or 7),
        score_max=(float(row["score_max"]) if row.get("score_max") is not None else None),
        event_type=row.get("event_type") or None,
        min_count=(int(row["min_count"]) if row.get("min_count") is not None else None),
        severity=str(row.get("severity") or "medium"),
        message=str(row.get("message") or ""),
        active=bool(row.get("active", 1)),
    )


def evaluate_score_rule(
    rule: CoachingRule, score: Optional[float],
) -> Optional[ProposedAssignment]:
    if rule.kind != KIND_SCORE_THRESHOLD or rule.score_max is None:
        return None
    if score is None:
        return None
    if score <= rule.score_max:
        return ProposedAssignment(
            driver_id="",  # filled in by caller
            topic_key=rule.topic_key,
            severity=rule.severity,
            reason=f"score {score:.0f} ≤ {rule.score_max:.0f}",
            rule_id=rule.id,
        )
    return None


def evaluate_incident_rule(
    rule: CoachingRule, count: int,
) -> Optional[ProposedAssignment]:
    if rule.kind != KIND_INCIDENT_COUNT or rule.min_count is None:
        return None
    if count >= rule.min_count:
        et = rule.event_type or "any"
        return ProposedAssignment(
            driver_id="",
            topic_key=rule.topic_key,
            severity=rule.severity,
            reason=f"{et} incidents={count} ≥ {rule.min_count}",
            rule_id=rule.id,
        )
    return None


async def evaluate(
    account_id: int, *, days: int = 7,
) -> list[ProposedAssignment]:
    """Evaluate active coaching rules over the last ``days`` and return
    proposed assignments.  Skips drivers already having a pending
    assignment for the same topic within ``rule.period_days``."""
    from infra import observability as _obs
    timings: dict[str, float] = {}

    tenant = await get_tenant_db(account_id)
    if tenant is None:
        return []

    with _obs.time_block(timings, "rules"):
        rule_rows = await tenant.list_coaching_rules(account_id, active_only=True)
    rules = [_row_to_rule(r) for r in rule_rows]
    if not rules:
        return []

    with _obs.time_block(timings, "evaluate_subjects"):
        cards = await evaluate_subjects(
            account_id, subject="driver", days=days, write_evidence=False,
        )
    cards_by_driver: dict[str, dict] = {}
    for c in cards:
        did = str(c.get("driver_id") or "").strip()
        if did:
            cards_by_driver[did] = c

    incident_event_types = {
        r.event_type for r in rules
        if r.kind == KIND_INCIDENT_COUNT and r.event_type
    }

    # ── Bulk-fetch incident counts (replaces D × E N+1 of
    #    get_safety_events_warehouse). One grouped SQL query
    #    returns {(driver_id, event_type): count}; missing pairs
    #    default to 0 below.
    counts_grouped: dict[tuple[str, str], int] = {}
    if incident_event_types and cards_by_driver:
        with _obs.time_block(timings, "incident_counts"):
            counts_grouped = await tenant.get_safety_event_counts_grouped(
                account_id,
                days=days,
                event_types=[et for et in incident_event_types if et],
                driver_ids=list(cards_by_driver.keys()),
            )

    # ── Bulk-fetch pending assignments (replaces D × R N+1 of
    #    has_pending_coaching_assignment). Pre-load every pending
    #    row within the *widest* rule window, then re-filter per-rule
    #    in Python so each rule's idempotency window stays correct.
    max_period_days = max((r.period_days for r in rules), default=0)
    pending_recent: dict[tuple[str, str], str] = {}
    if max_period_days > 0:
        with _obs.time_block(timings, "pending_assignments"):
            pending_recent = await tenant.get_pending_assignments_recent(
                account_id, max_period_days,
            )
    now_utc = datetime.now(timezone.utc)

    proposals: list[ProposedAssignment] = []
    seen_keys: set[tuple[str, str]] = set()
    with _obs.time_block(timings, "rule_eval_loop"):
        for did, card in cards_by_driver.items():
            score = float(card["score"]) if card.get("score") is not None else None
            for rule in rules:
                entry: Optional[ProposedAssignment] = None
                if rule.kind == KIND_SCORE_THRESHOLD:
                    entry = evaluate_score_rule(rule, score)
                elif rule.kind == KIND_INCIDENT_COUNT:
                    key_et = rule.event_type or ""
                    count = counts_grouped.get((did, key_et), 0)
                    entry = evaluate_incident_rule(rule, count)
                if entry is None:
                    continue

                # Idempotency: skip if a pending row for (driver, topic)
                # falls within this rule's period_days window.
                recent_at = pending_recent.get((did, entry.topic_key))
                if recent_at:
                    try:
                        rec_dt = datetime.fromisoformat(
                            recent_at.replace("Z", "+00:00"),
                        )
                        if rec_dt.tzinfo is None:
                            rec_dt = rec_dt.replace(tzinfo=timezone.utc)
                        if (now_utc - rec_dt) <= timedelta(days=rule.period_days):
                            continue
                    except (ValueError, TypeError):
                        # Malformed timestamp — fall through and propose
                        pass

                # In-batch dedup: don't propose two rules with the same
                # (driver, topic) within this single evaluation pass.
                dedup_key = (did, entry.topic_key)
                if dedup_key in seen_keys:
                    continue
                seen_keys.add(dedup_key)

                proposals.append(ProposedAssignment(
                    driver_id=did,
                    topic_key=entry.topic_key,
                    severity=entry.severity,
                    reason=entry.reason,
                    rule_id=entry.rule_id,
                ))

    log.info(
        "coaching evaluate acct=%s drivers=%d rules=%d proposals=%d timings_ms=%s",
        account_id, len(cards_by_driver), len(rules), len(proposals), timings,
    )
    return proposals
