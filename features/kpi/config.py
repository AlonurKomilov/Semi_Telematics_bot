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

VIEW AND CONFIG ARE TWO ACTIONS, not two strengths of one.  ``can_kpi``
is the VIEW action — the grades, already computed.  The thresholds are
the CONFIG action: the ``kpi_thresholds`` account_settings row, which
config.md's table puts in the Config column's account scope and the
blast-radius rule pins there ("anything a computation reads is
account-wide, always").

So BOTH verbs are gated here, the READ included.  Gating the GET on
can_kpi was the mixing in miniature: it looked like a harmless read, but
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
    merged = await service.set_kpi_thresholds(
        tenant, account_id, body.thresholds,
    )
    return {"thresholds": merged}
