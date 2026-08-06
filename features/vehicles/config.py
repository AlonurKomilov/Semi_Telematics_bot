"""Vehicles config — which provider wins per field.

ONE config endpoint per feature at ``/<feature>/config``, in a file named
``config.py``.  Interface-layer, exactly as ``router.py`` is: both are
co-located with their domain and both may import ``interfaces.api.deps``.

THE URL FOLLOWS THE DOMAIN NOUN, not the page.  The panel that edits this
lives on the Integrations page, but the setting is
``vehicle_field_precedence`` and it decides which provider wins per
VEHICLE field — so the route is ``/vehicles/config``.  Shared and wire
identifiers are named after the domain noun (docs/architecture/PERSONA.md).

⚠️  MOUNT THIS ROUTER BEFORE ``features.vehicles.router``.
``router.py`` carries ``@router.get("/{vehicle_name}")``.  FastAPI matches
in registration order across the whole app, so if the feature router is
mounted first, ``GET /vehicles/config`` matches the parametric route with
``vehicle_name="config"`` and this module is never reached.  Nothing in a
route-count or route-parity check catches that — both routes still exist,
one simply shadows the other.  ``test_config_endpoint_convention`` asserts
the resolution, not just the registration.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from capabilities.integrations import reconciliation
from infra.platform import get_tenant_db as _get_tenant_db
from interfaces.api.deps import require_permission

router = APIRouter(prefix="/vehicles", tags=["vehicles"])

_config = require_permission("can_manage_config_all")


class SourcePrecedenceUpdate(BaseModel):
    # {spec_field: primary_source}, e.g. {"vin": "datatruck", "make": "samsara"}.
    primary: dict[str, str] = Field(default_factory=dict)


@router.get("/config")
@router.get("/source-precedence", deprecated=True)
async def get_config(user: dict = Depends(_config)):
    """Per-field primary source + the choices, for the Integrations-page
    'When sources disagree' panel.

    The READ sits on the config flag with the write, for the reason
    ``GET /kpi/config`` does: its only consumer is the editor panel, so it
    is not a view of integration DATA — it is the settings themselves.
    Leaving a config read on the weaker permission makes the write gate
    decorative, since every value is visible and only Save is refused.
    """
    account_id = int(user["account_id"])
    tenant = await _get_tenant_db(account_id)
    if tenant is None:
        raise HTTPException(503, "tenant DB unavailable")
    return await reconciliation.precedence_options(tenant, account_id, "vehicle")


@router.put("/config")
@router.put("/source-precedence", deprecated=True)
async def put_config(
    body: SourcePrecedenceUpdate,
    user: dict = Depends(_config),
):
    """Set the per-field primary source.  The other source still fills gaps;
    operator hand-edits always win.

    CONFIG, not Manage.  This writes ``vehicle_field_precedence`` — an
    account_settings row owned by the config family — which decides which
    provider WINS per field when Samsara and Datatruck disagree.  Every
    vehicle read downstream resolves through it, so it is exactly the
    "a computation reads it" case the blast-radius rule covers.
    ``can_manage_integrations`` still owns connecting and syncing the
    providers themselves.
    """
    account_id = int(user["account_id"])
    tenant = await _get_tenant_db(account_id)
    if tenant is None:
        raise HTTPException(503, "tenant DB unavailable")
    await reconciliation.set_precedence(tenant, account_id, "vehicle", body.primary)
    return await reconciliation.precedence_options(tenant, account_id, "vehicle")
