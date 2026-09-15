"""Thin aggregator that mounts every provider's integration router.

The route layer is carved into:

  * ``capabilities.integrations.shared.router``     — generic
    (catalog, connect, disconnect, toggles, test-connection, cadences).
    Lives at ``/{provider_id}/...`` so the same code serves every
    provider.

  * ``capabilities.integrations.samsara.router``    — Samsara-only
    (per-company keys, account-wide + per-company history backfill,
    snapshot coverage, aggregate health reconciler).  Hardcoded at
    ``/samsara/...``.

  * ``capabilities.integrations.datatruck.router``  — Datatruck-only
    (TMS sync-preview today; sync writers next).  Hardcoded at
    ``/datatruck/...``.

This file is what ``interfaces/api/app.py`` imports — every endpoint
mounted by ``include_router(integrations_routes.router, prefix="/api")``
flows through here, in a single FastAPI router instance, so existing
operators continue to see one ``/api/integrations`` URL surface.
"""

from __future__ import annotations

from fastapi import APIRouter

from .datatruck.router import router as _datatruck_router
from .samsara.router import router as _samsara_router
from .shared.companies_router import router as _companies_router
from .shared.router import router as _shared_router

# A single APIRouter that includes the sub-routers.  We don't add a
# prefix here because each sub-router carries its own
# ``/integrations`` prefix; including would compound to
# ``/integrations/integrations`` and break the API.
#
# ORDER IS LOAD-BEARING for the last one.  FastAPI matches routes in
# registration order, and ``companies_router`` claims the parametrised
# ``/{provider_id}/companies/...`` family.  Registered before the
# vendor routers it would shadow ``/samsara/companies/...`` — whose
# handlers carry a dual-write to the legacy ``companies.samsara_api_key``
# column that no other provider has.  The shadowing would be silent:
# same paths, same response shape, one missing write.  So the generic
# family goes LAST and only catches the providers that have no vendor
# route of their own.
router = APIRouter()
router.include_router(_shared_router)
router.include_router(_samsara_router)
router.include_router(_datatruck_router)
router.include_router(_companies_router)

__all__ = ["router"]
