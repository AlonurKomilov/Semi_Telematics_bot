"""Coaching service — orchestrates rule CRUD, manual assignment,
acknowledgement, and engine.evaluate persistence.

Respects the per-account ``coaching_enabled`` kill-switch.
"""

from __future__ import annotations

import logging
from typing import Optional

from infra.services import get_db, get_tenant_db

from .engine import evaluate
from .models import (
    KIND_INCIDENT_COUNT,
    KIND_SCORE_THRESHOLD,
    STATUS_ACKNOWLEDGED,
    STATUS_CANCELLED,
    STATUS_PENDING,
    VALID_KINDS,
    VALID_SEVERITIES,
    VALID_STATUSES,
)

log = logging.getLogger(__name__)


class CoachingDisabledError(RuntimeError):
    """Raised when coaching operations are attempted on an account where
    the ``coaching_enabled`` kill-switch is OFF."""


async def _assert_enabled(account_id: int) -> None:
    pdb = get_db()
    acct = await pdb.get_account(account_id)
    if acct is None or not getattr(acct, "coaching_enabled", False):
        raise CoachingDisabledError(
            f"coaching feature is disabled for account {account_id}"
        )


async def _audit(
    account_id: int, user_id: int, action: str, target_id: str = "",
) -> None:
    tenant = await get_tenant_db(account_id)
    if tenant is None:
        return
    try:
        await tenant.add_audit_log(
            account_id=account_id, user_id=user_id, action=action,
            target_type="coaching", target_id=target_id,
        )
    except Exception:  # pragma: no cover — audit must never break a flow
        log.exception("coaching audit failed")


# ── Topics ─────────────────────────────────────────────────────────

async def list_topics(account_id: int) -> list[dict]:
    await _assert_enabled(account_id)
    tenant = await get_tenant_db(account_id)
    if tenant is None:
        return []
    rows = await tenant.list_coaching_topics(account_id)
    if not rows:
        await tenant.seed_default_coaching_topics(account_id)
        rows = await tenant.list_coaching_topics(account_id)
    return rows


# ── Rule CRUD ──────────────────────────────────────────────────────

async def list_rules(
    account_id: int, *, active_only: bool = False,
) -> list[dict]:
    await _assert_enabled(account_id)
    tenant = await get_tenant_db(account_id)
    if tenant is None:
        return []
    return await tenant.list_coaching_rules(account_id, active_only=active_only)


async def create_rule(
    account_id: int, *, user_id: int, name: str, kind: str,
    topic_key: str, period_days: int = 7,
    score_max: Optional[float] = None,
    event_type: Optional[str] = None,
    min_count: Optional[int] = None,
    severity: str = "medium",
    message: str = "",
    active: bool = True,
) -> int:
    if kind not in VALID_KINDS:
        raise ValueError(f"invalid rule kind {kind!r}")
    if severity not in VALID_SEVERITIES:
        raise ValueError(f"invalid severity {severity!r}")
    if not topic_key:
        raise ValueError("topic_key is required")
    if kind == KIND_SCORE_THRESHOLD and score_max is None:
        raise ValueError("score_threshold rule requires score_max")
    if kind == KIND_INCIDENT_COUNT and min_count is None:
        raise ValueError("incident_count rule requires min_count")

    await _assert_enabled(account_id)
    tenant = await get_tenant_db(account_id)
    rule_id = await tenant.create_coaching_rule(
        account_id, name=name, kind=kind, topic_key=topic_key,
        period_days=period_days, score_max=score_max,
        event_type=event_type, min_count=min_count,
        severity=severity, message=message, active=active,
    )
    await _audit(account_id, user_id, "coaching_rule_created", str(rule_id))
    return rule_id


async def update_rule(
    account_id: int, rule_id: int, *, user_id: int, **fields,
) -> bool:
    await _assert_enabled(account_id)
    tenant = await get_tenant_db(account_id)
    ok = await tenant.update_coaching_rule(account_id, rule_id, **fields)
    if ok:
        await _audit(account_id, user_id, "coaching_rule_updated", str(rule_id))
    return ok


async def delete_rule(
    account_id: int, rule_id: int, *, user_id: int,
) -> bool:
    await _assert_enabled(account_id)
    tenant = await get_tenant_db(account_id)
    await tenant.delete_coaching_rule(account_id, rule_id)
    await _audit(account_id, user_id, "coaching_rule_deleted", str(rule_id))
    return True


# ── Assignments ────────────────────────────────────────────────────

async def assign_manual(
    account_id: int, *, user_id: int, driver_id: str, topic_key: str,
    severity: str = "medium", reason: str = "",
    due_at: Optional[str] = None,
) -> int:
    if severity not in VALID_SEVERITIES:
        raise ValueError(f"invalid severity {severity!r}")
    if not driver_id or not topic_key:
        raise ValueError("driver_id and topic_key are required")
    await _assert_enabled(account_id)
    tenant = await get_tenant_db(account_id)
    aid = await tenant.create_coaching_assignment(
        account_id, driver_id=driver_id, topic_key=topic_key,
        rule_id=None, severity=severity, reason=reason or "manual",
        assigned_by=user_id, due_at=due_at,
    )
    await _audit(account_id, user_id, "coaching_assigned_manual", str(aid))
    return aid


async def list_assignments(
    account_id: int, *,
    driver_id: Optional[str] = None,
    status: Optional[str] = None,
    limit: int = 200,
) -> list[dict]:
    if status is not None and status not in VALID_STATUSES:
        raise ValueError(f"invalid status {status!r}")
    await _assert_enabled(account_id)
    tenant = await get_tenant_db(account_id)
    if tenant is None:
        return []
    return await tenant.list_coaching_assignments(
        account_id, driver_id=driver_id, status=status, limit=limit,
    )


async def get_assignment(
    account_id: int, assignment_id: int,
) -> Optional[dict]:
    tenant = await get_tenant_db(account_id)
    if tenant is None:
        return None
    return await tenant.get_coaching_assignment(account_id, assignment_id)


async def acknowledge(
    account_id: int, assignment_id: int, *,
    driver_id: str, user_id: int, note: str = "",
) -> bool:
    """Driver acknowledges the assignment. Caller must verify driver_id and
    pass the actual Telegram user_id so the audit row attributes the action
    to the real human (not the Samsara driver_id, which a bad caller could
    spoof to dismiss someone else's pending assignments)."""
    await _assert_enabled(account_id)
    tenant = await get_tenant_db(account_id)
    a = await tenant.get_coaching_assignment(account_id, assignment_id)
    if a is None or a["driver_id"] != driver_id:
        return False
    if a["status"] != STATUS_PENDING:
        return False
    await tenant.set_coaching_assignment_status(
        account_id, assignment_id, STATUS_ACKNOWLEDGED,
    )
    await tenant.add_coaching_acknowledgment(
        assignment_id=assignment_id, driver_id=driver_id, note=note,
    )
    await _audit(account_id, user_id, "coaching_acknowledged", str(assignment_id))
    return True


async def cancel_assignment(
    account_id: int, assignment_id: int, *, user_id: int,
) -> bool:
    await _assert_enabled(account_id)
    tenant = await get_tenant_db(account_id)
    a = await tenant.get_coaching_assignment(account_id, assignment_id)
    if a is None:
        return False
    if a["status"] not in (STATUS_PENDING, STATUS_ACKNOWLEDGED):
        return False
    await tenant.set_coaching_assignment_status(
        account_id, assignment_id, STATUS_CANCELLED,
    )
    await _audit(
        account_id, user_id, "coaching_cancelled", str(assignment_id),
    )
    return True


async def run_evaluation(
    account_id: int, *, user_id: int = 0, days: int = 7,
) -> list[int]:
    """Run engine.evaluate and persist proposed assignments.

    Returns the list of newly-created assignment ids.
    """
    await _assert_enabled(account_id)
    proposals = await evaluate(account_id, days=days)
    if not proposals:
        return []
    tenant = await get_tenant_db(account_id)
    # One executemany + one commit instead of P INSERT-then-commit
    # round-trips. Per-row commits dominated wall time at ~20+ proposals.
    created = await tenant.create_coaching_assignments_bulk(
        account_id,
        items=[
            {
                "driver_id": p.driver_id,
                "topic_key": p.topic_key,
                "rule_id": p.rule_id,
                "severity": p.severity,
                "reason": p.reason,
            }
            for p in proposals
        ],
        assigned_by=user_id,
    )
    await _audit(
        account_id, user_id, "coaching_run_completed",
        f"{len(created)} assignments",
    )
    return created
