"""Coaching domain models."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Optional


# Rule kinds
KIND_SCORE_THRESHOLD = "score_threshold"   # triggers when score <= score_max
KIND_INCIDENT_COUNT = "incident_count"     # triggers when count(event) >= min_count

VALID_KINDS = (KIND_SCORE_THRESHOLD, KIND_INCIDENT_COUNT)

# Severity
SEVERITY_LOW = "low"
SEVERITY_MEDIUM = "medium"
SEVERITY_HIGH = "high"
VALID_SEVERITIES = (SEVERITY_LOW, SEVERITY_MEDIUM, SEVERITY_HIGH)

# Assignment statuses
STATUS_PENDING = "pending"
STATUS_ACKNOWLEDGED = "acknowledged"
STATUS_CANCELLED = "cancelled"
VALID_STATUSES = (STATUS_PENDING, STATUS_ACKNOWLEDGED, STATUS_CANCELLED)


@dataclass(frozen=True)
class CoachingRule:
    id: int
    name: str
    kind: str
    topic_key: str
    period_days: int = 7
    score_max: Optional[float] = None
    event_type: Optional[str] = None
    min_count: Optional[int] = None
    severity: str = SEVERITY_MEDIUM
    message: str = ""
    active: bool = True


@dataclass(frozen=True)
class ProposedAssignment:
    driver_id: str
    topic_key: str
    severity: str
    reason: str
    rule_id: Optional[int] = None
