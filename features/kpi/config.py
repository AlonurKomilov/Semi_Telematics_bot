"""KPI config — the account's grading thresholds.

ONE config endpoint per feature, at ``/<feature>/config``, in a file
called ``config.py``.  Three layers spell it the same way::

    features/kpi/config.py  ->  /kpi/config  ->  features/kpi/config/…Panel.tsx

WHY ITS OWN FILE rather than a section of router.py.  Config is not the
feature's data; it is the values the feature's data is computed against,
granted by a different permission and edited by a different surface.
Leaving it in router.py made "where do I change this?" a search rather
than a path, and pushed the routers toward god objects — applications'
was 1,999 lines across 48 endpoints before the same split.

This module is interface-layer, exactly as ``router.py`` is: both are
co-located with their domain and both may import ``interfaces.api.deps``.

VIEW AND CONFIG ARE TWO ACTIONS, not two strengths of one.  ``can_view_kpi``
is the VIEW action — the grades, already computed.  The thresholds are
the CONFIG action: the ``kpi_thresholds`` account_settings row, which
config.md's table puts in the Config column's account scope and the
blast-radius rule pins there ("anything a computation reads is
account-wide, always").

So BOTH verbs are gated here, the READ included.  Gating the GET on
can_view_kpi was the mixing in miniature: it looked like a harmless read, but
its only caller is the editor panel.  The KPI page never asks for
thresholds, because grades arrive already graded.  A read that exists
solely to populate an editor is part of Config, not part of View.

``can_manage_account`` was in this gate too and is gone: that is General
settings' MANAGE action, a peer feature's operating permission.  It
reached the same roles by seed (owner only), so this narrows nothing by
default — it only stops an account that delegated General settings from
picking up KPI grading rules it was never granted.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from features.kpi import service
from infra.platform import get_tenant_db as _get_tenant_db
from interfaces.api.deps import require_permission

# Same prefix as the feature's router — FastAPI mounts both, and keeping
# the prefix here is what lets the deprecated alias below live beside the
# primary instead of reaching back into router.py for its router object.
router = APIRouter(prefix="/kpi", tags=["kpi"])

_config = require_permission("can_manage_config_all")


class ThresholdsUpdate(BaseModel):
    thresholds: dict[str, float] = Field(default_factory=dict)


# ``/kpi/thresholds`` stays registered as a DEPRECATED ALIAS rather than
# being deleted.  The API and the dashboard deploy from one repo but not
# in one instant: a browser holding yesterday's JS bundle would 404 on
# every threshold read during the window.  Same recipe the wire-key
# renames use — alias the same object, and test that the alias and the
# primary resolve identically (tests/test_config_endpoint_convention.py).
@router.get("/config")
@router.get("/thresholds", deprecated=True)
async def get_config(user: dict = Depends(_config)):
    account_id = int(user["account_id"])
    tenant = await _get_tenant_db(account_id)
    if tenant is None:
        raise HTTPException(503, "tenant DB unavailable")
    return {
        "thresholds": await service.get_kpi_thresholds(tenant, account_id),
        "defaults": service.DEFAULT_KPI_THRESHOLDS,
    }


@router.put("/config")
@router.put("/thresholds", deprecated=True)
async def put_config(
    body: ThresholdsUpdate,
    user: dict = Depends(_config),
):
    """Set what counts as good/bad.  Grades everywhere recompute from the
    new values on the next read (metrics are computed live)."""
    account_id = int(user["account_id"])
    tenant = await _get_tenant_db(account_id)
    if tenant is None:
        raise HTTPException(503, "tenant DB unavailable")
    try:
        merged = await service.set_kpi_thresholds(
            tenant, account_id, body.thresholds,
        )
    except ValueError as e:
        # Task-language message ("RPM: 'good…' must be higher…") — the
        # panel shows it verbatim.
        raise HTTPException(422, str(e))
    return {"thresholds": merged}


# ── Incentives (the dispatcher payout rules) ─────────────────────────
#
# Same surface (/kpi/config/*), same gate (can_manage_config_all), a
# different payload: the customer's INCENTIVE configuration — model, tier
# table, cadence, exception policy, and the per-company weekly targets.
# Validation lives in the engine (features/kpi/dispatch/engine.py),
# because the engine is what will consume the rows; a config it refuses
# is refused HERE, at write time, with the message an admin can act on —
# never discovered as a silent 0% at run time.


class IncentiveConfigUpdate(BaseModel):
    model: str = Field("ladder")
    combine_rule: str = Field("lower")
    calc_cadence: str = Field("weekly")
    calc_custom_days: int | None = None
    exception_cap_pct: float | None = None
    floor_weekly_gross: float | None = None
    floor_rpm: float | None = None
    # The tier table travels WHOLE: a ladder is one coherent value (rows
    # interact), so partial edits have no meaning — unlike scorecard
    # rules, which are independent rows with per-row CRUD.
    tiers: list[dict] = Field(default_factory=list)


@router.post("/config/incentives/preview")
async def preview_incentives(
    body: IncentiveConfigUpdate,
    user: dict = Depends(_config),
):
    """Dry-run a CANDIDATE rule set against the latest run — the effect
    shown BEFORE the commit is asked for."""
    from features.kpi.dispatch import runs as runs_service
    try:
        return await runs_service.preview_rules(
            int(user["account_id"]),
            body.model_dump(exclude={"tiers"}),
            body.tiers,
        )
    except runs_service.RunError as e:
        raise HTTPException(422, str(e))


class CompanyTargetsUpdate(BaseModel):
    # {company_id: weekly_gross_target} — the whole set; a company left
    # out loses its bar, visibly (its trucks resolve 0% until re-set).
    targets: dict[int, float] = Field(default_factory=dict)


@router.get("/config/incentives")
async def get_incentives_config(user: dict = Depends(_config)):
    """The incentive configuration + targets + the companies the editor
    can set targets FOR.  ``config`` is null until first configured — the
    UI shows the setup state rather than inventing defaults for money."""
    account_id = int(user["account_id"])
    tenant = await _get_tenant_db(account_id)
    if tenant is None:
        raise HTTPException(503, "tenant DB unavailable")
    companies = await tenant.get_account_companies(account_id)

    # Anchors for the targets editor: what each company's trucks ACTUALLY
    # gross per week (last 4 full weeks) — a target set with no reference
    # is a guess.  Truck-week = one unit's gross in one ISO week.
    company_stats: dict[str, dict] = {}
    try:
        from datetime import date, timedelta

        from features.loads import service as loads_service

        until = date.today()
        since = until - timedelta(days=28)
        loads = await loads_service.get_loads(
            account_id, since=since.isoformat(), until=until.isoformat(),
            limit=None,
        )
        per: dict[str, dict] = {}
        for l in loads:
            if l.get("status") == "canceled":
                continue
            code = l.get("company_code") or ""
            unit = l.get("vehicle_unit") or ""
            if not code:
                continue
            d = str(l.get("pickup_date") or "")[:10]
            try:
                week = date.fromisoformat(d).isocalendar()[1]
            except ValueError:
                continue
            slot = per.setdefault(code, {"units": set(), "weeks": {}})
            slot["units"].add(unit)
            key = (unit, week)
            slot["weeks"][key] = slot["weeks"].get(key, 0.0) + float(
                l.get("total_rate") or 0)
        for code, slot in per.items():
            sums = list(slot["weeks"].values())
            company_stats[code] = {
                "trucks": len(slot["units"]),
                "avg_truck_week": round(sum(sums) / len(sums), 2) if sums else 0.0,
            }
    except Exception:
        pass                     # anchors are optional; the editor works without

    return {
        "config": await tenant.get_kpi_incentive_config(account_id),
        "targets": await tenant.list_kpi_company_targets(account_id),
        "companies": [
            {"id": c.id, "code": c.code, "name": c.display_name or c.code}
            for c in companies
        ],
        "company_stats": company_stats,
    }


@router.put("/config/incentives")
async def put_incentives_config(
    body: IncentiveConfigUpdate,
    user: dict = Depends(_config),
):
    """Replace the incentive configuration.  Engine-validated first;
    trail-recorded because this decides what dispatchers are PAID."""
    from capabilities.activity_trail import record_simple
    from features.kpi.dispatch import engine
    from interfaces.api.deps import resolve_user_id

    values = body.model_dump(exclude={"tiers"})
    try:
        engine.validate_config(values)
        engine.validate_tiers(body.model, body.tiers)
    except ValueError as e:
        raise HTTPException(422, str(e))

    account_id = int(user["account_id"])
    tenant = await _get_tenant_db(account_id)
    if tenant is None:
        raise HTTPException(503, "tenant DB unavailable")
    await tenant.set_kpi_incentive_config(
        account_id, values, body.tiers,
        updated_by=await resolve_user_id(user),
    )
    await record_simple(
        tenant, account_id, await resolve_user_id(user),
        "kpi_incentive_config_update", "account", account_id,
        changes={"model": {"from": None, "to": body.model},
                 "tiers": {"from": None, "to": len(body.tiers)}},
    )
    return await get_incentives_config(user)


@router.put("/config/incentives/targets")
async def put_incentive_targets(
    body: CompanyTargetsUpdate,
    user: dict = Depends(_config),
):
    """Replace the per-company weekly targets.

    Company ids are verified against the account's OWN companies — a
    target for another account's company id must 422, not silently
    insert a row the join will never surface."""
    from capabilities.activity_trail import record_simple
    from interfaces.api.deps import resolve_user_id

    for cid, target in body.targets.items():
        if target <= 0:
            raise HTTPException(
                422, f"weekly target for company {cid} must be positive")

    account_id = int(user["account_id"])
    tenant = await _get_tenant_db(account_id)
    if tenant is None:
        raise HTTPException(503, "tenant DB unavailable")
    own = {c.id for c in await tenant.get_account_companies(
        account_id, active_only=False)}
    foreign = set(body.targets) - own
    if foreign:
        raise HTTPException(
            422, f"unknown company id(s) for this account: {sorted(foreign)}")
    await tenant.set_kpi_company_targets(account_id, body.targets)
    await record_simple(
        tenant, account_id, await resolve_user_id(user),
        "kpi_incentive_targets_update", "account", account_id,
        changes={"companies": {"from": None, "to": len(body.targets)}},
    )
    return {"targets": await tenant.list_kpi_company_targets(account_id)}
