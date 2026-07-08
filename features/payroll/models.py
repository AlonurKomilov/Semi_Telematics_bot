"""Payroll domain models."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional


# Bonus rule kinds
KIND_SCORE_THRESHOLD = "score_threshold"   # awarded when total_score >= score_min
KIND_INCIDENT_COUNT = "incident_count"     # awarded when count(event_type) <= max_count

VALID_KINDS = (KIND_SCORE_THRESHOLD, KIND_INCIDENT_COUNT)

# Run statuses
STATUS_DRAFT = "draft"
STATUS_FINALIZED = "finalized"
STATUS_CANCELLED = "cancelled"

VALID_STATUSES = (STATUS_DRAFT, STATUS_FINALIZED, STATUS_CANCELLED)


@dataclass(frozen=True)
class BonusRule:
    id: int
    name: str
    kind: str
    amount_cents: int
    period_days: int = 30
    score_min: Optional[float] = None
    event_type: Optional[str] = None
    max_count: Optional[int] = None
    active: bool = True


@dataclass(frozen=True)
class BreakdownEntry:
    name: str
    kind: str
    amount_cents: int
    # Bonus-rule entries carry the rule id; computed components
    # (load_earnings / extra_items) have no rule.
    rule_id: Optional[int] = None
    detail: str = ""

    def to_dict(self) -> dict:
        return {
            "rule_id": self.rule_id,
            "name": self.name,
            "kind": self.kind,
            "amount_cents": self.amount_cents,
            "detail": self.detail,
        }


@dataclass(frozen=True)
class RunItem:
    driver_id: str
    driver_name: str
    base_pay_cents: int
    bonus_total_cents: int
    total_cents: int
    breakdown: list[BreakdownEntry] = field(default_factory=list)
    # Settlement components (computed from the canonical loads):
    # earnings for delivered loads + extra pay items (layover/TONU/…).
    load_earnings_cents: int = 0
    extras_cents: int = 0
    loads_count: int = 0
