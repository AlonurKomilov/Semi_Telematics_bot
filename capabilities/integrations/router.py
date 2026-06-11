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
from .shared.router import router as _shared_router

# A single APIRouter that includes the three sub-routers.  We don't
# add a prefix here because each sub-router carries its own
# ``/integrations`` prefix; including would compound to
# ``/integrations/integrations`` and break the API.
router = APIRouter()
router.include_router(_shared_router)
router.include_router(_samsara_router)
router.include_router(_datatruck_router)

__all__ = ["router"]
