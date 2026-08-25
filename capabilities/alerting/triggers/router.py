"""``/alerts/triggers`` — a person managing their own watches.

Self-scoped, like notification preferences: every route resolves the
caller's own user id and works only on rows carrying it.  There is no
permission flag for "may I watch my own trucks" — anyone who can receive
an alert at all can decide the number at which they want it.

What the caller may NOT do is name a warehouse column, choose a
comparator, pick a check interval, or write for somebody else.  The
metric owns its direction, band, freshness and cadence; the catalog is a
whitelist; the row's owner is taken from the session, never the body.
"""

from __future__ import annotations

import logging

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field

from interfaces.api.deps import get_current_db_user, get_current_user
from interfaces.api.rate_limit import limiter

from capabilities.alerting.triggers import catalog as cat
from capabilities.alerting.triggers.models import (
    MAX_TRIGGERS_PER_USER, AlertTrigger, validate,
)

logger = logging.getLogger("api.alert_triggers")

router = APIRouter(prefix="/alerts/triggers", tags=["alerts"])


class TriggerRequest(BaseModel):
    metric: str
    threshold: float
    severity: str = Field(default="warning")


class TriggerPatch(BaseModel):
    threshold: float | None = None
    enabled: bool | None = None


async def _me(user: dict):
    from infra.platform import get_platform_db
    db = get_platform_db()
    db_user = await get_current_db_user(user, db)
    if not db_user:
        raise HTTPException(status_code=401, detail="User not found")
    return db, db_user


def _shape(row: dict) -> dict:
    """One trigger as the UI needs it — the row plus the sentence a person
    reads, so the client never rebuilds the phrasing from parts."""
    trig = AlertTrigger.from_row(row)
    metric = trig.spec
    return {
        "id": trig.id,
        "metric": trig.metric,
        "threshold": trig.threshold,
        "enabled": trig.enabled,
        "severity": trig.severity,
        "scope": trig.scope,
        "describes": trig.describe(),
        # None when the catalog has moved on and this row names a metric
        # that no longer exists — the UI shows it as retired so the person
        # can delete it, rather than the row vanishing unexplained.
        "unit": metric.unit if metric else None,
        "direction": metric.direction if metric else None,
    }


@router.get("/metrics")
@limiter.limit("30/minute")
async def list_metrics(request: Request, user: dict = Depends(get_current_user)):
    """The watchable vocabulary — everything the editor needs to render a
    form without hardcoding a single metric."""
    return {
        "metrics": [
            {
                "key": m.key, "label": m.label, "unit": m.unit,
                "direction": m.direction,
                "min": m.settable[0], "max": m.settable[1],
                "hysteresis": m.hysteresis,
                "requires_engine": m.requires_engine,
                "checked_every_minutes": m.check_every_minutes,
                "hint": m.hint,
            }
            for m in cat.CATALOG
        ],
        "max_per_user": MAX_TRIGGERS_PER_USER,
    }


@router.get("")
@limiter.limit("30/minute")
async def list_my_triggers(request: Request, user: dict = Depends(get_current_user)):
    db, me = await _me(user)
    rows = await db.list_alert_triggers(me.account_id, owner_user_id=me.id)
    return {"triggers": [_shape(r) for r in rows],
            "max_per_user": MAX_TRIGGERS_PER_USER}


@router.post("")
@limiter.limit("20/minute")
async def create_trigger(
    request: Request, body: TriggerRequest, user: dict = Depends(get_current_user),
):
    err = validate(body.metric, body.threshold)
    if err:
        raise HTTPException(status_code=400, detail=err)
    db, me = await _me(user)
    # The cap is policy, so it is enforced HERE rather than in storage —
    # the adapters layer may not import from capabilities, and a limit
    # that lives beside the catalog it belongs to is one a reader finds.
    if await db.count_alert_triggers(me.account_id, me.id) >= MAX_TRIGGERS_PER_USER:
        raise HTTPException(
            status_code=409,
            detail=f"You already have {MAX_TRIGGERS_PER_USER} triggers — "
                   "delete one to add another",
        )
    row = await db.create_alert_trigger(
        me.account_id, me.id, metric=body.metric,
        threshold=float(body.threshold), severity=body.severity,
    )
    return _shape(row)


@router.patch("/{trigger_id:int}")
@limiter.limit("30/minute")
async def update_trigger(
    request: Request, trigger_id: int, body: TriggerPatch,
    user: dict = Depends(get_current_user),
):
    db, me = await _me(user)
    if body.threshold is not None:
        rows = await db.list_alert_triggers(me.account_id, owner_user_id=me.id)
        current = next((r for r in rows if int(r["id"]) == trigger_id), None)
        if current is None:
            raise HTTPException(status_code=404, detail="Trigger not found")
        err = validate(str(current["metric"]), body.threshold)
        if err:
            raise HTTPException(status_code=400, detail=err)
    ok = await db.update_alert_trigger(
        me.account_id, me.id, trigger_id,
        threshold=body.threshold, enabled=body.enabled,
    )
    if not ok:
        raise HTTPException(status_code=404, detail="Trigger not found")
    rows = await db.list_alert_triggers(me.account_id, owner_user_id=me.id)
    row = next((r for r in rows if int(r["id"]) == trigger_id), None)
    return _shape(row) if row else {"ok": True}


@router.delete("/{trigger_id:int}")
@limiter.limit("30/minute")
async def delete_trigger(
    request: Request, trigger_id: int, user: dict = Depends(get_current_user),
):
    db, me = await _me(user)
    ok = await db.delete_alert_trigger(me.account_id, me.id, trigger_id)
    if not ok:
        raise HTTPException(status_code=404, detail="Trigger not found")
    return {"ok": True}
