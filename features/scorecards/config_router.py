"""Scorecard rules + pillar-caps config — the Scorecards feature's
admin component (the config surface).

The driver-facing scorecard READS live in the sibling viewer router
(``features/scorecards/router.py``); these endpoints WRITE the
per-tenant scoring config — rule overrides + pillar caps.  URLs unchanged:
``/admin/scorecard-rules`` and ``/admin/scorecard-pillar-caps`` (+ legacy
``/safety/scorecards/{rules,pillar-caps}`` aliases).

The scoring rule MODEL these endpoints edit lives in the HUB
(``capabilities/scorecards/rules/``) — this feature CONSUMES it (feature → hub).

Dependency exception: router files are interface-layer code co-located with
their feature — only router/config_router may import ``interfaces.api.deps``.
"""
import json

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from interfaces.api.deps import require_permission, get_tenant_db
from capabilities.scorecards.rules.defs import get_default_rules as _get_default_rules

admin_router = APIRouter(prefix="/admin", tags=["admin"])

# ── Scorecard rules + pillar caps ─────────────────────────────────────────────
#
# Tenant-level scoring config — sits in /admin to match the "Scorecard Rules"
# sidebar entry under Admin. The driver-facing scorecard read endpoints
# remain under /safety/scorecards/* (same feature, different audience).


class ScoreRuleUpdate(BaseModel):
    points: int = Field(..., ge=-100, le=100)
    cap: int | None = Field(None, ge=-200, le=200)
    enabled: bool = True
    curve_x_zero: float | None = Field(None, ge=-1000, le=10000)
    curve_x_max:  float | None = Field(None, ge=-1000, le=10000)
    curve_y_max:  int   | None = Field(None, ge=-200, le=200)


class PillarCapsUpdate(BaseModel):
    safety:     int = Field(..., ge=0, le=100)
    efficiency: int = Field(..., ge=0, le=100)
    compliance: int = Field(..., ge=0, le=100)

    @property
    def total(self) -> int:
        return self.safety + self.efficiency + self.compliance


@admin_router.get("/scorecard-rules")
async def list_score_rules(
    user: dict = Depends(require_permission("can_manage_config_all")),
    tenant=Depends(get_tenant_db),
):
    """Default rules merged with this account's overrides.

    Each item carries id/label/category/pillar/kind, effective values, and
    an ``overridden`` flag so the UI can show a "reset to default" button.
    """
    overrides = await tenant.get_score_rule_overrides(user["account_id"])
    out: list[dict] = []
    for r in _get_default_rules():
        ov = overrides.get(r.id) or {}
        out.append({
            "id":         r.id,
            "label":      r.label,
            "category":   r.category,
            "pillar":     r.pillar,
            "kind":       r.kind,
            "default_points":  r.points,
            "default_cap":     r.cap,
            "points":     int(ov["points"])  if "points"  in ov else r.points,
            "cap":        (ov["cap"] if ov.get("cap") is not None else r.cap)
                          if "cap" in ov else r.cap,
            "enabled":    bool(ov["enabled"]) if "enabled" in ov else r.enabled,
            "curve_kind":          r.curve_kind,
            "default_curve_x_zero": r.curve_x_zero,
            "default_curve_x_max":  r.curve_x_max,
            "default_curve_y_max":  r.curve_y_max,
            "curve_x_zero": (ov["curve_x_zero"]
                             if ov.get("curve_x_zero") is not None else r.curve_x_zero),
            "curve_x_max":  (ov["curve_x_max"]
                             if ov.get("curve_x_max")  is not None else r.curve_x_max),
            "curve_y_max":  (ov["curve_y_max"]
                             if ov.get("curve_y_max")  is not None else r.curve_y_max),
            "overridden": bool(ov),
        })
    return {"rules": out, "count": len(out)}


@admin_router.put("/scorecard-rules/{rule_id}")
async def update_score_rule(
    rule_id: str,
    body: ScoreRuleUpdate,
    user: dict = Depends(require_permission("can_manage_config_all")),
    tenant=Depends(get_tenant_db),
):
    """Override a default rule's points / cap / enabled / curve anchors."""
    defaults = {r.id: r for r in _get_default_rules()}
    rule = defaults.get(rule_id)
    if rule is None:
        raise HTTPException(status_code=404, detail="Unknown rule_id")
    await tenant.upsert_score_rule(
        user["account_id"], rule_id,
        label=rule.label, category=rule.category, kind=rule.kind,
        points=body.points, cap=body.cap, enabled=body.enabled,
        pillar=rule.pillar,
        curve_x_zero=body.curve_x_zero,
        curve_x_max=body.curve_x_max,
        curve_y_max=body.curve_y_max,
    )
    return {"ok": True, "rule_id": rule_id}


@admin_router.delete("/scorecard-rules/{rule_id}")
async def reset_score_rule(
    rule_id: str,
    user: dict = Depends(require_permission("can_manage_config_all")),
    tenant=Depends(get_tenant_db),
):
    """Drop the per-account override → rule reverts to built-in default."""
    deleted = await tenant.delete_score_rule(user["account_id"], rule_id)
    return {"ok": True, "rule_id": rule_id, "deleted": deleted}


@admin_router.get("/scorecard-pillar-caps")
async def get_pillar_caps(
    user: dict = Depends(require_permission("can_manage_config_all")),
    tenant=Depends(get_tenant_db),
):
    """Pillar cap weights for this account (defaults 50/25/25 when no override)."""
    from capabilities.scorecards.engine import PILLAR_CAPS
    raw = await tenant.get_account_setting(
        user["account_id"], tenant.KEY_SCORECARD_PILLAR_CAPS, "",
    )
    if raw:
        try:
            caps = json.loads(raw)
            return {"safety": caps.get("safety", PILLAR_CAPS["safety"]),
                    "efficiency": caps.get("efficiency", PILLAR_CAPS["efficiency"]),
                    "compliance": caps.get("compliance", PILLAR_CAPS["compliance"]),
                    "is_custom": True}
        except Exception:
            pass
    return {**PILLAR_CAPS, "is_custom": False}


@admin_router.put("/scorecard-pillar-caps")
async def set_pillar_caps(
    body: PillarCapsUpdate,
    user: dict = Depends(require_permission("can_manage_config_all")),
    tenant=Depends(get_tenant_db),
):
    """Set per-tenant pillar cap weights. Must sum to exactly 100."""
    if body.total != 100:
        raise HTTPException(
            status_code=422,
            detail=f"Pillar caps must sum to 100 (got {body.total}).",
        )
    caps = {"safety": body.safety, "efficiency": body.efficiency, "compliance": body.compliance}
    await tenant.set_account_setting(
        user["account_id"],
        tenant.KEY_SCORECARD_PILLAR_CAPS,
        json.dumps(caps),
    )
    return {"ok": True, "caps": caps}


@admin_router.delete("/scorecard-pillar-caps")
async def reset_pillar_caps(
    user: dict = Depends(require_permission("can_manage_config_all")),
    tenant=Depends(get_tenant_db),
):
    """Remove pillar cap override — reverts to built-in defaults (50/25/25)."""
    from capabilities.scorecards.engine import PILLAR_CAPS
    await tenant.set_account_setting(
        user["account_id"], tenant.KEY_SCORECARD_PILLAR_CAPS, "",
    )
    return {"ok": True, "caps": PILLAR_CAPS, "is_custom": False}


# Legacy /safety/scorecards/rules + pillar-caps aliases — delegate to the
# admin_router endpoints defined ABOVE in this same module (they moved
# here from admin.py; this block must stay below those definitions).

legacy_router = APIRouter(tags=["safety"], include_in_schema=False)


@legacy_router.get("/safety/scorecards/rules")
async def _legacy_list_score_rules(
    user: dict = Depends(require_permission("can_manage_config_all")),
    tenant=Depends(get_tenant_db),
):
    return await list_score_rules(user=user, tenant=tenant)


@legacy_router.put("/safety/scorecards/rules/{rule_id}")
async def _legacy_update_score_rule(
    rule_id: str,
    body: ScoreRuleUpdate,
    user: dict = Depends(require_permission("can_manage_config_all")),
    tenant=Depends(get_tenant_db),
):
    return await update_score_rule(rule_id=rule_id, body=body, user=user, tenant=tenant)


@legacy_router.delete("/safety/scorecards/rules/{rule_id}")
async def _legacy_reset_score_rule(
    rule_id: str,
    user: dict = Depends(require_permission("can_manage_config_all")),
    tenant=Depends(get_tenant_db),
):
    return await reset_score_rule(rule_id=rule_id, user=user, tenant=tenant)


@legacy_router.get("/safety/scorecards/pillar-caps")
async def _legacy_get_pillar_caps(
    user: dict = Depends(require_permission("can_manage_config_all")),
    tenant=Depends(get_tenant_db),
):
    return await get_pillar_caps(user=user, tenant=tenant)


@legacy_router.put("/safety/scorecards/pillar-caps")
async def _legacy_set_pillar_caps(
    body: PillarCapsUpdate,
    user: dict = Depends(require_permission("can_manage_config_all")),
    tenant=Depends(get_tenant_db),
):
    return await set_pillar_caps(body=body, user=user, tenant=tenant)


@legacy_router.delete("/safety/scorecards/pillar-caps")
async def _legacy_reset_pillar_caps(
    user: dict = Depends(require_permission("can_manage_config_all")),
    tenant=Depends(get_tenant_db),
):
    return await reset_pillar_caps(user=user, tenant=tenant)
