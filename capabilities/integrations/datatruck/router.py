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

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel

from infra.platform import get_platform_db
from infra.services import get_telematics_client
from interfaces.api.deps import require_permission

from ..shared.helpers import audit, spawn_background, validate_provider
from .sync import (
    RESOURCES,
    apply_resource,
    collect_sync_overview,
    get_preview,
    get_sync_status,
    run_preview,
    sync_resource,
)

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/integrations", tags=["integrations"])

_owner_only = require_permission("can_manage_integrations")

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


# ── Sync triggers + status ───────────────────────────────────────


@router.post("/datatruck/sync/{resource}")
async def trigger_sync(
    resource: str,
    days: int | None = Query(
        None, ge=1, le=365,
        description="History window in days for date-ranged resources "
        "(orders, work_orders).  Roster resources ignore it.  Omit for "
        "the provider default (work_orders: 90d, orders: no filter).",
    ),
    user: dict = Depends(_owner_only),
):
    """Queue one resource's sync (fire-and-forget).

    Validates the resource name against the engine registry, requires
    the integration to be connected with the matching capability
    toggle enabled, and refuses a duplicate while a run for the same
    resource is in flight (Redis-backed preflight, multi-worker safe).

    ``days`` is an optional history window applied to date-ranged
    resources (orders, work_orders); roster syncs ignore it.

    Returns ``{state: "queued", resource}`` immediately; the dashboard
    polls ``GET /datatruck/sync-status`` to watch progress.
    """
    validate_provider(_PROVIDER_ID)
    account_id = int(user["account_id"])
    triggered_by = int(user.get("id") or 0)

    spec = RESOURCES.get(resource)
    if spec is None:
        raise HTTPException(
            404,
            f"unknown resource {resource!r} — one of: "
            f"{', '.join(sorted(RESOURCES))}",
        )

    db = get_platform_db()
    ai = await db.get_account_integration(account_id, _PROVIDER_ID)
    if ai is None:
        raise HTTPException(404, "datatruck integration not configured")
    if ai.status != "connected":
        raise HTTPException(
            409, f"integration status is {ai.status!r}, not connected",
        )
    cap_cfg = (ai.feature_toggles or {}).get(spec.capability) or {}
    if not cap_cfg.get("enabled", False):
        raise HTTPException(
            409,
            f"the {resource} sync toggle is disabled — enable it on "
            "the Integration card first",
        )

    running = await get_sync_status(account_id, resource)
    if running and running.get("state") in ("running", "queued"):
        raise HTTPException(
            409,
            f"a {resource} sync is already running — wait for it to "
            "complete",
        )

    async def _run() -> None:
        # sync_resource never raises — failures land in its returned
        # status + Redis record, so this wrapper only exists to keep
        # the GC-safe spawner's signature happy.
        await sync_resource(
            account_id, resource, triggered_by=triggered_by, days=days,
        )

    spawn_background(_run())
    logger.info(
        "datatruck sync queued acct=%d resource=%s by user=%d",
        account_id, resource, triggered_by,
    )
    await audit(
        account_id, triggered_by, "integration.datatruck_sync",
        f"{_PROVIDER_ID}:{resource}",
    )
    return {
        "state": "queued",
        "account_id": account_id,
        "provider_id": _PROVIDER_ID,
        "resource": resource,
    }


async def _require_enabled(account_id: int, resource: str):
    """Shared preflight: the resource is known, the integration is
    connected, and the resource's toggle is on.  Raises HTTPException
    otherwise; returns the ResourceSpec."""
    spec = RESOURCES.get(resource)
    if spec is None:
        raise HTTPException(
            404,
            f"unknown resource {resource!r} — one of: "
            f"{', '.join(sorted(RESOURCES))}",
        )
    db = get_platform_db()
    ai = await db.get_account_integration(account_id, _PROVIDER_ID)
    if ai is None:
        raise HTTPException(404, "datatruck integration not configured")
    if ai.status != "connected":
        raise HTTPException(
            409, f"integration status is {ai.status!r}, not connected",
        )
    cap_cfg = (ai.feature_toggles or {}).get(spec.capability) or {}
    if not cap_cfg.get("enabled", False):
        raise HTTPException(
            409,
            f"the {resource} sync toggle is disabled — enable it on "
            "the Integration card first",
        )
    return spec


@router.post("/datatruck/sync/{resource}/preview")
async def sync_preview_start(
    resource: str,
    days: int | None = Query(None, ge=1, le=365),
    user: dict = Depends(_owner_only),
):
    """Kick off a dry-run for a previewable resource (trucks / trailers /
    work_orders): fetch + diff WITHOUT writing.  Runs in the background
    (the rate-gated upstream fetch can outlast the 30s HTTP timeout) and
    returns a ``preview_id`` immediately — poll
    ``GET …/preview/{preview_id}`` until ``state='ready'`` for the diff.
    """
    validate_provider(_PROVIDER_ID)
    account_id = int(user["account_id"])
    triggered_by = int(user.get("id") or 0)
    await _require_enabled(account_id, resource)
    if resource not in ("trucks", "trailers", "work_orders"):
        raise HTTPException(400, f"{resource!r} does not support preview")

    import secrets
    preview_id = secrets.token_hex(8)

    async def _run() -> None:
        await run_preview(
            account_id, resource, preview_id, days=days,
            triggered_by=triggered_by,
        )

    spawn_background(_run())
    return {"state": "queued", "resource": resource, "preview_id": preview_id}


@router.get("/datatruck/sync/{resource}/preview/{preview_id}")
async def sync_preview_status(
    resource: str,
    preview_id: str,
    user: dict = Depends(_owner_only),
):
    """Poll a running preview.  Returns ``{state: pending|running|ready|
    failed|expired}`` plus the diff once ``ready``."""
    validate_provider(_PROVIDER_ID)
    account_id = int(user["account_id"])
    return await get_preview(account_id, resource, preview_id)


class _ApplyBody(BaseModel):
    preview_id: str


@router.post("/datatruck/sync/{resource}/apply")
async def sync_apply(
    resource: str,
    body: _ApplyBody,
    user: dict = Depends(_owner_only),
):
    """Commit a previewed resource from its cached rows (fire-and-forget).

    Same preflight + 409-on-duplicate as a direct sync.  The cached
    preview (Redis, 10-min TTL) is consumed on apply, so a stale preview
    can't be replayed.  Poll ``GET /datatruck/sync-status`` for progress.
    """
    validate_provider(_PROVIDER_ID)
    account_id = int(user["account_id"])
    triggered_by = int(user.get("id") or 0)
    await _require_enabled(account_id, resource)

    running = await get_sync_status(account_id, resource)
    if running and running.get("state") in ("running", "queued"):
        raise HTTPException(
            409,
            f"a {resource} sync is already running — wait for it to "
            "complete",
        )

    preview_id = body.preview_id

    async def _run() -> None:
        await apply_resource(
            account_id, resource, preview_id, triggered_by=triggered_by,
        )

    spawn_background(_run())
    logger.info(
        "datatruck apply queued acct=%d resource=%s preview=%s by user=%d",
        account_id, resource, preview_id, triggered_by,
    )
    await audit(
        account_id, triggered_by, "integration.datatruck_apply",
        f"{_PROVIDER_ID}:{resource}",
    )
    return {
        "state": "queued",
        "account_id": account_id,
        "provider_id": _PROVIDER_ID,
        "resource": resource,
        "preview_id": preview_id,
    }


@router.get("/datatruck/sync-status")
async def sync_status(user: dict = Depends(_owner_only)):
    """Per-resource sync overview for the Integration card.

    For each of the five resources: whether its toggle is enabled,
    what's stored locally (count + last synced stamp), and the live
    Redis state of any in-flight or recent run.  One call backs the
    whole "Synced data" panel so the dashboard doesn't fan out five
    requests on every poll tick.
    """
    validate_provider(_PROVIDER_ID)
    account_id = int(user["account_id"])
    return {
        "account_id":  account_id,
        "provider_id": _PROVIDER_ID,
        "resources":   await collect_sync_overview(account_id),
    }
