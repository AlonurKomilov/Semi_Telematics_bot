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

import json
import logging

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field

from interfaces.api.deps import get_current_db_user, get_current_user
from interfaces.api.rate_limit import limiter

from capabilities.alerting.triggers import catalog as cat
from capabilities.alerting.triggers.models import (
    DEFAULT_CHANNELS, MAX_TRIGGERS_PER_USER, TRIGGER_CHANNELS, AlertTrigger,
    DEFAULT_CHANNELS_CSV, clean_channels, clean_vehicle_ids, num_text,
    validate,
)

logger = logging.getLogger("api.alert_triggers")

router = APIRouter(prefix="/alerts/triggers", tags=["alerts"])


class TriggerRequest(BaseModel):
    metric: str
    threshold: float
    severity: str = Field(default="warning")
    #: Extra channels beyond the bell.  Omitted = the default pair.
    #: Capped because only a handful of keys are ever valid — a thousand
    #: strings is not a choice anyone is making.
    channels: list[str] | None = Field(default=None, max_length=8)
    #: ``vehicles.id`` to watch.  Omitted or empty = every vehicle in the
    #: caller's own scope, which is what a trigger meant before targeting.
    #: Capped at the fleet's realistic size — a selection larger than this
    #: is a client bug, not a choice.
    vehicles: list[int] | None = Field(default=None, max_length=500)


class TriggerPatch(BaseModel):
    threshold: float | None = None
    enabled: bool | None = None
    channels: list[str] | None = Field(default=None, max_length=8)
    vehicles: list[int] | None = Field(default=None, max_length=500)


def _meta(raw: str) -> dict:
    """A notice's parsed meta, {} on ANY malformed value — one bad row
    must not 500 a whole history page."""
    if not raw:
        return {}
    try:
        parsed = json.loads(raw)
    except Exception:
        return {}
    return parsed if isinstance(parsed, dict) else {}


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
        # The stored extras, and the full delivery list including the bell
        # — so the UI can show "always in the bell" as a fact rather than
        # a checkbox nobody may untick.
        "channels": trig.chosen_channels,
        # The selection, and the ONE number the row shows.  '' reads as
        # "all my vehicles" rather than "0 vehicles", which is the same
        # distinction the storage column's default carries.
        "vehicles": trig.target_ids,
        "watches_all": trig.targets_all,
        "delivers_to": trig.delivery_channels,
        # None when the catalog has moved on and this row names a metric
        # that no longer exists — the UI shows it as retired so the person
        # can delete it, rather than the row vanishing unexplained.
        "unit": metric.unit if metric else None,
        "direction": metric.direction if metric else None,
    }


async def _validate_targets(user: dict, me, raw) -> str:
    """A caller's vehicle selection → the csv to store, or raise 400.

    Two walls, and neither is optional.

    ACCOUNT — the ids are resolved against ``vehicles`` for the caller's
    own account, so an id belonging to another tenant resolves to nothing
    and is refused rather than silently stored as a target that will
    never match.

    SCOPE — a restricted owner may only target what they can already see.
    Targeting must NARROW, and without this it would be a way to name a
    truck you cannot open in the dashboard.  The evaluator ANDs the owner
    wall again at fire time, so this is defence in depth rather than the
    only wall — but a stored target you were never allowed to pick is a
    latent one: the day your role changes, the owner wall stops
    restricting and the stored list becomes the only filter.

    An EMPTY selection is legal and means "all my vehicles".
    """
    csv = clean_vehicle_ids(raw)
    if not csv:
        return ""
    ids = [int(x) for x in csv.split(",")]
    from interfaces.api.deps import get_user_vehicle_scope
    from infra.platform import get_router as _get_router
    tenant = await _get_router().get_tenant(me.account_id)
    placeholders = ", ".join("?" for _ in ids)
    # Inside with_account, like every other tenant read: `vehicles`
    # carries an RLS policy, and without the GUC stamped this query
    # matches zero rows the day ENABLE_RLS is turned on — which would
    # read as "those vehicles no longer exist" on a perfectly good save.
    # The explicit account_id predicate stays as well; RLS is the floor,
    # not the only wall.
    async with tenant.with_account(me.account_id):
        cur = await tenant._db.execute(
            f"SELECT id, unit_number, telematics_ref FROM vehicles "
            f"WHERE account_id = ? AND id IN ({placeholders})",
            (me.account_id, *ids),
        )
        found = {int(r[0]): {"registry_id": int(r[0]), "vehicle_id": r[2] or "",
                             "name": r[1] or ""} for r in await cur.fetchall()}
    missing = [i for i in ids if i not in found]
    if missing:
        raise HTTPException(
            status_code=400,
            detail="Some of those vehicles no longer exist — reopen the "
                   "picker and choose again",
        )
    scope = await get_user_vehicle_scope(user)
    if scope is not None:
        denied = [i for i in ids if not scope.allows_row(found[i])]
        if denied:
            # Deliberately does NOT name which ones: the caller may not
            # see them, and "vehicle 41 is not yours" confirms that
            # vehicle 41 exists.
            raise HTTPException(
                status_code=403,
                detail="That selection includes vehicles you can’t see",
            )
    return csv


@router.get("/vehicles")
@limiter.limit("30/minute")
async def list_targetable_vehicles(
    request: Request, user: dict = Depends(get_current_user),
):
    """What the caller may put in a trigger's vehicle list.

    Scoped by the caller's OWN vehicle scope, not by company codes: the
    general ``GET /vehicles/`` gates on company access alone, so reusing
    it here would hand a restricted driver their companies' whole roster.
    The enumeration is itself the disclosure — it happens whether or not
    the evaluation-time wall ever fires.

    ``watchable`` is the honest half.  Every metric in the catalog is
    engine or tank telemetry, and the registry deliberately also holds
    trailers and not-yet-telemetered trucks — 86 of 189 active vehicles
    on the live account, every one of the 79 trailers among them.  Those
    can be stored as targets (a trailer that gains a gateway later starts
    working, which is the right behaviour) but a person picking one today
    would get silence with nothing saying why.  So the flag travels with
    the row and the UI can say so, rather than the API quietly dropping
    vehicles the person can see everywhere else.
    """
    from interfaces.api.deps import get_user_vehicle_scope
    from infra.platform import get_router as _get_router
    db, me = await _me(user)
    tenant = await _get_router().get_tenant(me.account_id)
    async with tenant.with_account(me.account_id):
        cur = await tenant._db.execute(
            "SELECT id, unit_number, vehicle_type, telematics_ref, company_code "
            "  FROM vehicles WHERE account_id = ? AND is_active = 1 "
            " ORDER BY unit_number",
            (me.account_id,),
        )
        rows = [dict(r) for r in await cur.fetchall()]
    scope = await get_user_vehicle_scope(user)
    out = []
    for r in rows:
        shaped = {"registry_id": int(r["id"]), "vehicle_id": r["telematics_ref"] or "",
                  "name": r["unit_number"] or ""}
        if scope is not None and not scope.allows_row(shaped):
            continue
        out.append({
            "id": int(r["id"]),
            "name": r["unit_number"] or "",
            "type": r["vehicle_type"] or "",
            "company": r["company_code"] or "",
            "watchable": bool(r["telematics_ref"]),
        })
    return {"vehicles": out,
            "watchable": sum(1 for v in out if v["watchable"])}


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
                # The featureCatalog id that owns this metric.  The id,
                # not a label: the dashboard's featureCatalog is the SSOT
                # for what a feature is called, and a name on the wire
                # would be a second place for "Vehicles" to be spelled.
                "feature": m.feature,
            }
            for m in cat.CATALOG
        ],
        "max_per_user": MAX_TRIGGERS_PER_USER,
        # The channels a trigger may add.  The bell is not among them and
        # is not optional — a trigger that fired and left no record is
        # indistinguishable from one that never fired.
        "channels": list(TRIGGER_CHANNELS),
        # What a trigger created without a ``channels`` body will get.
        # The dashboard's add form no longer asks — delivery is chosen on
        # notification preferences — so this is documentation of the
        # server's answer rather than a value the form seeds itself with,
        # and it stays in the contract because a client that needs to SAY
        # what the default is should not have to hardcode it.
        "default_channels": list(DEFAULT_CHANNELS),
    }


def _fired_shape(row: dict, meta: dict) -> dict:
    """One FIRING as the history grid needs it.

    Built from the notice's own meta, never from the trigger row it names:
    a threshold edited afterwards must not rewrite what last week's alert
    said, and a deleted trigger still has a history.  Rows written before
    meta carried these fields fall back to the notice text, which is why
    ``title``/``body`` are returned alongside the parsed values instead of
    being replaced by them.
    """
    metric = cat.get_metric(str(meta.get("metric") or ""))
    title = str(row.get("title") or "")
    # "Truck 12 — fuel level below 30%" — the name is everything before
    # the dash, and only used when meta predates the vehicle field.
    fallback_vehicle, _, fallback_says = title.partition(" — ")
    try:
        threshold = float(meta["threshold"])
    except (KeyError, TypeError, ValueError):
        threshold = None
    if metric is not None and threshold is not None:
        says = f"{metric.label} {metric.direction} {num_text(threshold)}{metric.unit}"
    else:
        says = fallback_says or title
    return {
        # The NOTICE id: the row is marked read through the ordinary
        # inbox route, so the bell's count and this grid's Status column
        # can never disagree about the same firing.
        "id": int(row["id"]),
        "trigger_id": meta.get("trigger_id"),
        "vehicle": str(meta.get("vehicle") or fallback_vehicle or ""),
        "metric": str(meta.get("metric") or ""),
        "metric_label": metric.label if metric else "",
        "unit": metric.unit if metric else "",
        "threshold": threshold,
        "value": meta.get("value"),
        "says": says,
        "severity": str(row.get("severity") or "warning"),
        "created_at": str(row.get("created_at") or ""),
        "read": bool(row.get("read_at")),
        "title": title,
        "body": str(row.get("body") or ""),
    }


@router.get("/fired")
@limiter.limit("30/minute")
async def list_fired(
    request: Request, limit: int = 100, user: dict = Depends(get_current_user),
):
    """What the caller's triggers have caught, newest first.

    Reads the caller's own inbox rows in the trigger category — the same
    records the bell shows — because a personal trigger writes no board
    row and the notice IS the only record of the firing.  Shaped as
    firings rather than notices so the grid gets columns instead of
    sentences to parse.
    """
    from capabilities.alerting.triggers.notification_category import TRIGGER_FIRED
    db, me = await _me(user)
    rows = await db.list_inbox_notices(
        me.account_id, me.id, category=TRIGGER_FIRED,
        limit=max(1, min(int(limit), 100)))
    return {"fired": [_fired_shape(r, _meta(r.get("meta", ""))) for r in rows]}


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
    vehicles = await _validate_targets(user, me, body.vehicles)
    row = await db.create_alert_trigger(
        me.account_id, me.id, metric=body.metric,
        threshold=float(body.threshold), severity=body.severity,
        channels=(clean_channels(body.channels)
                  if body.channels is not None else DEFAULT_CHANNELS_CSV),
        vehicles=vehicles,
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
    vehicles = (await _validate_targets(user, me, body.vehicles)
                if body.vehicles is not None else None)
    ok = await db.update_alert_trigger(
        me.account_id, me.id, trigger_id,
        threshold=body.threshold, enabled=body.enabled,
        channels=(clean_channels(body.channels)
                  if body.channels is not None else None),
        vehicles=vehicles,
    )
    if not ok:
        raise HTTPException(status_code=404, detail="Trigger not found")
    if vehicles is not None or body.threshold is not None:
        # The crossing flags describe a WATCH that just changed shape.
        # Left alone, removing a vehicle and adding it back inside the
        # 24h flag TTL would look like "still in breach" and swallow its
        # next real crossing — and a moved threshold has the same
        # problem in the other direction.  Clearing re-seeds instead:
        # the next sweep re-learns the fleet's state and says nothing,
        # which is the same promise the UI already makes about a
        # newly-created trigger.
        from capabilities.alerting.triggers.evaluator import forget_trigger_state
        await forget_trigger_state(me.account_id, trigger_id)
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
