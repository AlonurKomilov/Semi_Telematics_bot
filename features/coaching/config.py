"""Coaching config — the rules that decide when coaching fires.

ONE config endpoint per feature at ``/<feature>/config``, in a file named
``config.py``.  Interface-layer, exactly as ``router.py`` is: both are
co-located with their domain and both may import ``interfaces.api.deps``.

CONFIG, NOT MANAGE — the same call Scorecards already got, on the same
evidence.  These rules are account-wide ROWS that govern a computation:
they decide which drivers get coaching assigned and when.  The
blast-radius rule ("anything a computation reads is account-wide,
always") reaches them exactly as it reaches scorecard rules and pillar
caps, which live at ``/scorecards/config/*`` behind
``can_manage_config_all``.  Living in a feature's own table rather than
``account_settings`` never disqualified scorecards and does not
disqualify these.

``can_coaching_admin`` keeps the feature's real operational work —
assigning, cancelling, running the sweep now, reading the roster.  What
it no longer owns is the rule set those operations execute.

LIKE SCORECARDS, the verbs stay per-row.  Rules are rows with create /
edit / delete, not fields in a settings blob; collapsing them into one
PUT would mean read-modify-write of the whole set per edit, so two admins
editing different rules would silently clobber each other.  The URL
convention holds — everything under ``/coaching/config/`` — while the
verbs stay CRUD.

⚠️  MOUNT THIS ROUTER BEFORE ``features.coaching.router``.  FastAPI
matches in registration order across the whole app; a feature router
mounted first can shadow ``/coaching/config``.  The convention test
asserts resolution, not registration.

``/coaching/rules`` stays registered as a DEPRECATED ALIAS so a browser
holding the previous JS bundle keeps working across a deploy.
"""

from __future__ import annotations

import logging
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field

from features.coaching import service as svc
from features.coaching.service import CoachingDisabledError
from features.coaching.router import disabled_to_403
from interfaces.api.deps import require_permission, resolve_user_id

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/coaching", tags=["coaching"])

_config = require_permission("can_manage_config_all")


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


@router.get("/config/rules")
@router.get("/rules", deprecated=True)
async def list_rules(
    active_only: bool = Query(False),
    user: dict = Depends(_config),
):
    try:
        return await svc.list_rules(user["account_id"], active_only=active_only)
    except CoachingDisabledError as e:
        raise disabled_to_403(e)


@router.post("/config/rules", status_code=201)
@router.post("/rules", status_code=201, deprecated=True)
async def create_rule(
    body: CoachingRuleIn,
    user: dict = Depends(_config),
):
    try:
        rid = await svc.create_rule(
            user["account_id"], user_id=await resolve_user_id(user),
            name=body.name, kind=body.kind, topic_key=body.topic_key,
            period_days=body.period_days, score_max=body.score_max,
            event_type=body.event_type, min_count=body.min_count,
            severity=body.severity, message=body.message, active=body.active,
        )
    except CoachingDisabledError as e:
        raise disabled_to_403(e)
    except ValueError as e:
        raise HTTPException(400, str(e))
    return {"id": rid}


@router.put("/config/rules/{rule_id}")
@router.put("/rules/{rule_id}", deprecated=True)
async def update_rule(
    rule_id: int,
    body: CoachingRulePatch,
    user: dict = Depends(_config),
):
    fields = body.model_dump(exclude_unset=True)
    try:
        ok = await svc.update_rule(
            user["account_id"], rule_id, user_id=await resolve_user_id(user), **fields,
        )
    except CoachingDisabledError as e:
        raise disabled_to_403(e)
    if not ok:
        raise HTTPException(404, "rule not found or no changes")
    return {"ok": True}


@router.delete("/config/rules/{rule_id}")
@router.delete("/rules/{rule_id}", deprecated=True)
async def delete_rule(
    rule_id: int,
    user: dict = Depends(_config),
):
    try:
        await svc.delete_rule(
            user["account_id"], rule_id, user_id=await resolve_user_id(user),
        )
    except CoachingDisabledError as e:
        raise disabled_to_403(e)
    return {"ok": True}
