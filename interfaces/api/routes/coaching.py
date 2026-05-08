"""Coaching API endpoints — rules, assignments, ack flow, driver /me."""

from __future__ import annotations

import logging
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field

from capabilities.coaching import service as svc
from capabilities.coaching.service import CoachingDisabledError
from interfaces.api.deps import (
    require_permission,
    require_permission_any,
)

log = logging.getLogger(__name__)

router = APIRouter(prefix="/coaching", tags=["coaching"])


# ── Pydantic schemas ──────────────────────────────────────────────

class CoachingRuleIn(BaseModel):
    name: str = Field(min_length=1, max_length=120)
    kind: str = Field(pattern="^(score_threshold|incident_count)$")
    topic_key: str = Field(min_length=1, max_length=64)
    period_days: int = Field(default=7, ge=1, le=365)
    score_max: Optional[float] = Field(default=None, ge=0, le=100)
    event_type: Optional[str] = Field(default=None, max_length=64)
    min_count: Optional[int] = Field(default=None, ge=0, le=10_000)
    severity: str = Field(default="medium", pattern="^(low|medium|high)$")
    message: str = Field(default="", max_length=1000)
    active: bool = True


class CoachingRulePatch(BaseModel):
    name: Optional[str] = Field(default=None, min_length=1, max_length=120)
    period_days: Optional[int] = Field(default=None, ge=1, le=365)
    topic_key: Optional[str] = Field(default=None, min_length=1, max_length=64)
    score_max: Optional[float] = Field(default=None, ge=0, le=100)
    event_type: Optional[str] = Field(default=None, max_length=64)
    min_count: Optional[int] = Field(default=None, ge=0, le=10_000)
    severity: Optional[str] = Field(default=None, pattern="^(low|medium|high)$")
    message: Optional[str] = Field(default=None, max_length=1000)
    active: Optional[bool] = None


class AssignManualIn(BaseModel):
    driver_id: str = Field(min_length=1, max_length=128)
    topic_key: str = Field(min_length=1, max_length=64)
    severity: str = Field(default="medium", pattern="^(low|medium|high)$")
    reason: str = Field(default="", max_length=1000)
    due_at: Optional[str] = Field(default=None, max_length=64)


class AcknowledgeIn(BaseModel):
    note: str = Field(default="", max_length=1000)


def _disabled_to_403(_exc: CoachingDisabledError) -> HTTPException:
    return HTTPException(
        status_code=403,
        detail="Coaching feature is not enabled for this account.",
    )


# ── Topics ─────────────────────────────────────────────────────────

@router.get("/topics")
async def list_topics(
    user: dict = Depends(require_permission_any(
        "can_coaching_admin", "can_coaching_view_own",
    )),
):
    try:
        return await svc.list_topics(user["account_id"])
    except CoachingDisabledError as e:
        raise _disabled_to_403(e)


# ── Rules ──────────────────────────────────────────────────────────

@router.get("/rules")
async def list_rules(
    active_only: bool = Query(False),
    user: dict = Depends(require_permission("can_coaching_admin")),
):
    try:
        return await svc.list_rules(user["account_id"], active_only=active_only)
    except CoachingDisabledError as e:
        raise _disabled_to_403(e)


@router.post("/rules", status_code=201)
async def create_rule(
    body: CoachingRuleIn,
    user: dict = Depends(require_permission("can_coaching_admin")),
):
    try:
        rid = await svc.create_rule(
            user["account_id"], user_id=int(user["sub"]),
            name=body.name, kind=body.kind, topic_key=body.topic_key,
            period_days=body.period_days, score_max=body.score_max,
            event_type=body.event_type, min_count=body.min_count,
            severity=body.severity, message=body.message, active=body.active,
        )
    except CoachingDisabledError as e:
        raise _disabled_to_403(e)
    except ValueError as e:
        raise HTTPException(400, str(e))
    return {"id": rid}


@router.put("/rules/{rule_id}")
async def update_rule(
    rule_id: int,
    body: CoachingRulePatch,
    user: dict = Depends(require_permission("can_coaching_admin")),
):
    fields = body.model_dump(exclude_unset=True)
    try:
        ok = await svc.update_rule(
            user["account_id"], rule_id, user_id=int(user["sub"]), **fields,
        )
    except CoachingDisabledError as e:
        raise _disabled_to_403(e)
    if not ok:
        raise HTTPException(404, "rule not found or no changes")
    return {"ok": True}


@router.delete("/rules/{rule_id}")
async def delete_rule(
    rule_id: int,
    user: dict = Depends(require_permission("can_coaching_admin")),
):
    try:
        await svc.delete_rule(
            user["account_id"], rule_id, user_id=int(user["sub"]),
        )
    except CoachingDisabledError as e:
        raise _disabled_to_403(e)
    return {"ok": True}


# ── Assignments (admin) ────────────────────────────────────────────

@router.get("/assignments")
async def list_assignments(
    driver_id: Optional[str] = Query(default=None, max_length=128),
    status: Optional[str] = Query(default=None, max_length=32),
    limit: int = Query(default=200, ge=1, le=1000),
    user: dict = Depends(require_permission("can_coaching_admin")),
):
    try:
        return await svc.list_assignments(
            user["account_id"], driver_id=driver_id, status=status, limit=limit,
        )
    except CoachingDisabledError as e:
        raise _disabled_to_403(e)
    except ValueError as e:
        raise HTTPException(400, str(e))


@router.post("/assign", status_code=201)
async def assign_manual(
    body: AssignManualIn,
    user: dict = Depends(require_permission("can_coaching_admin")),
):
    try:
        aid = await svc.assign_manual(
            user["account_id"], user_id=int(user["sub"]),
            driver_id=body.driver_id, topic_key=body.topic_key,
            severity=body.severity, reason=body.reason, due_at=body.due_at,
        )
    except CoachingDisabledError as e:
        raise _disabled_to_403(e)
    except ValueError as e:
        raise HTTPException(400, str(e))
    return {"id": aid}


@router.post("/assignments/{assignment_id}/cancel")
async def cancel_assignment(
    assignment_id: int,
    user: dict = Depends(require_permission("can_coaching_admin")),
):
    try:
        ok = await svc.cancel_assignment(
            user["account_id"], assignment_id, user_id=int(user["sub"]),
        )
    except CoachingDisabledError as e:
        raise _disabled_to_403(e)
    if not ok:
        raise HTTPException(404, "assignment not found or not cancellable")
    return {"ok": True}


@router.post("/run-now", status_code=202)
async def run_now(
    days: int = Query(7, ge=1, le=90),
    user: dict = Depends(require_permission("can_coaching_admin")),
):
    """Manually trigger an evaluation pass (admin-only)."""
    try:
        ids = await svc.run_evaluation(
            user["account_id"], user_id=int(user["sub"]), days=days,
        )
    except CoachingDisabledError as e:
        raise _disabled_to_403(e)
    return {"created": ids}


# ── Driver self-service ────────────────────────────────────────────

async def _resolve_caller_driver_id(user: dict) -> Optional[str]:
    """Return the Samsara driver_id explicitly bound to the calling user
    (users.samsara_driver_id). Returns None if no admin has set the binding.

    Earlier versions inferred this from the most-recent safety event on
    the user's assigned truck, which leaked another driver's coaching
    assignments and paystubs whenever a vehicle had been driven by anyone
    else in the prior 90 days. The mapping must now be set explicitly by
    an admin via PUT /admin/users/{id}/samsara-driver-id.
    """
    from infra.platform import get_platform_db
    pdb = get_platform_db()
    if pdb is None:
        return None
    # JWT 'sub' is the Telegram id (auth.create_jwt wraps it as str).
    try:
        tid = int(user["sub"])
    except (KeyError, TypeError, ValueError):
        return None
    me = await pdb.get_user_by_telegram_id(tid)
    if me is None or me.account_id != user["account_id"]:
        return None
    did = (me.samsara_driver_id or "").strip()
    return did or None


@router.get("/me")
async def my_assignments(
    status: Optional[str] = Query(default=None, max_length=32),
    limit: int = Query(default=50, ge=1, le=500),
    user: dict = Depends(require_permission_any(
        "can_coaching_admin", "can_coaching_view_own",
    )),
):
    """Return coaching assignments for the calling driver."""
    driver_id = await _resolve_caller_driver_id(user)
    if not driver_id:
        return {"driver_id": None, "items": []}
    try:
        items = await svc.list_assignments(
            user["account_id"], driver_id=driver_id, status=status, limit=limit,
        )
    except ValueError as e:
        raise HTTPException(400, str(e))
    return {"driver_id": driver_id, "items": items}


@router.post("/me/{assignment_id}/ack")
async def ack_my_assignment(
    assignment_id: int,
    body: AcknowledgeIn,
    user: dict = Depends(require_permission_any(
        "can_coaching_admin", "can_coaching_view_own",
    )),
):
    """Driver acknowledges an assignment.  Must own the assignment."""
    driver_id = await _resolve_caller_driver_id(user)
    if not driver_id:
        raise HTTPException(404, "no driver mapping")
    try:
        ok = await svc.acknowledge(
            user["account_id"], assignment_id,
            driver_id=driver_id, user_id=int(user["sub"]), note=body.note,
        )
    except CoachingDisabledError as e:
        raise _disabled_to_403(e)
    if not ok:
        raise HTTPException(403, "cannot acknowledge this assignment")
    return {"ok": True}
