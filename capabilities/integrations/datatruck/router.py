"""Datatruck-specific integration routes (TMS sync endpoints).

Today's surface is intentionally tiny — a single ``sync-preview``
endpoint that reads the first page of each resource (drivers, trucks,
trailers, orders) so the operator can confirm "yes, this is my fleet"
before any persistence wiring lands.  Account-wide sync (writes into
``datatruck_*`` tables) lands in the next phase alongside the storage
mixins under ``adapters/storage/datatruck/``.

The Connect / Disconnect / Toggles / Test-connection routes are
served by ``capabilities.integrations.shared.router`` because every
provider needs them; Datatruck doesn't re-implement them here.

Paths are hardcoded to ``/datatruck/...`` so the URL surface is
honest about which provider applies.  This file is mounted by the
top-level aggregator alongside the shared and Samsara routers.
"""
# router.py is interface-layer code co-located with its hub/domain
# (docs/FEATURES.md): ONLY router.py may import interfaces.api.deps.


from __future__ import annotations

import asyncio
import logging
from typing import Any

from fastapi import APIRouter, Depends, HTTPException

from infra.platform import get_platform_db
from infra.services import get_telematics_client
from interfaces.api.deps import require_permission

from ..shared.helpers import validate_provider

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/integrations", tags=["integrations"])

_owner_only = require_permission("can_manage_account")

_PROVIDER_ID = "datatruck"


@router.get("/datatruck/sync-preview")
async def sync_preview(user: dict = Depends(_owner_only)):
    """First-page preview of every Datatruck resource the operator
    will be able to sync.

    Returns the COUNT and first 3 records of drivers / trucks /
    trailers / orders so the dashboard can render a "About to sync:
    60 drivers, 70 trucks, 76 trailers, 17,093 orders" confirmation
    panel.  No persistence — read-only.

    Honest about the 18 req/min rate gate: four parallel fetches
    will burn 4 of the budget but stay well under the cap.

    Returns 404 when the integration isn't configured, 503 when the
    provider can't be constructed (missing creds, etc.).
    """
    validate_provider(_PROVIDER_ID)
    account_id = int(user["account_id"])

    db = get_platform_db()
    ai = await db.get_account_integration(account_id, _PROVIDER_ID)
    if ai is None:
        raise HTTPException(404, "datatruck integration not configured")

    try:
        provider = await get_telematics_client(account_id, _PROVIDER_ID)
    except NotImplementedError as e:
        raise HTTPException(503, str(e))
    except KeyError as e:
        raise HTTPException(503, f"provider not registered: {e}")

    async def _one(label: str, awaitable) -> tuple[str, dict[str, Any]]:
        try:
            body = await awaitable
        except Exception as e:
            logger.warning(
                "datatruck sync-preview %s failed acct=%d: %s",
                label, account_id, e,
            )
            return label, {"count": None, "sample": [], "error": str(e)[:200]}
        if isinstance(body, dict):
            count = body.get("count")
            sample = (body.get("results") or [])[:3]
        else:
            count, sample = None, []
        return label, {"count": count, "sample": sample, "error": None}

    # Fire all four reads in parallel — the client's rate gate
    # serialises them under the hood, so we still respect the
    # 18 req/min budget.  Doing them in parallel just keeps the
    # round-trip count down to one wall-clock-equivalent.
    results = await asyncio.gather(
        _one("drivers",  provider.fetch_drivers_page()),  # type: ignore[attr-defined]
        _one("trucks",   provider.fetch_trucks_page()),   # type: ignore[attr-defined]
        _one("trailers", provider.fetch_trailers_page()), # type: ignore[attr-defined]
        _one("orders",   provider.fetch_orders_page()),   # type: ignore[attr-defined]
    )
    by_resource = {label: payload for label, payload in results}

    return {
        "account_id":  account_id,
        "provider_id": _PROVIDER_ID,
        "resources":   by_resource,
    }
