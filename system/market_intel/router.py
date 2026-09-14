"""Market-intel operator API — /system/market-intel/*.

The launch cockpit the owner asked for (replacing the SSH+.env
ritual): the platform-wide switch, the readiness numbers (is the
give-to-get flywheel turning?), and a run-now trigger so the operator
can preview cells BEFORE flipping the switch — computed cells stay
invisible to customers while the gate is off, so run-now is safe.
"""

from __future__ import annotations

import logging
import os
from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Depends
from pydantic import BaseModel

from interfaces.api.deps import get_tenant_db, require_system_owner
from adapters.storage.platform_settings import (
    MARKET_INTEL_ENV, MARKET_INTEL_KEY, _TRUTHY,
)

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/system/market-intel", tags=["system"])


class SwitchBody(BaseModel):
    enabled: bool


@router.get("")
async def status(
    user: dict = Depends(require_system_owner),
    tenant_db=Depends(get_tenant_db),
):
    """Switch state + readiness numbers.  ``env_override`` tells the
    operator when the env var is forcing ON regardless of the switch
    (emergency/back-compat path)."""
    stats = await tenant_db.market_intel_stats()
    return {
        "enabled": await tenant_db.market_intel_enabled(),
        "switch": (await tenant_db.get_platform_setting(MARKET_INTEL_KEY, "0")) == "1",
        "env_override": os.getenv(MARKET_INTEL_ENV, "").strip() in _TRUTHY,
        **stats,
    }


@router.put("")
async def flip(
    body: SwitchBody,
    user: dict = Depends(require_system_owner),
    tenant_db=Depends(get_tenant_db),
):
    """THE launch switch.  Readers cache for ≤60s per worker, so the
    flip propagates within a minute — no restart, no SSH."""
    await tenant_db.set_platform_setting(
        MARKET_INTEL_KEY, "1" if body.enabled else "0",
    )
    logger.info(
        "market intel switch flipped %s by operator %s",
        "ON" if body.enabled else "OFF", user.get("sub"),
    )
    return {"ok": True, "enabled": await tenant_db.market_intel_enabled()}


@router.post("/rollup-now")
async def rollup_now(
    user: dict = Depends(require_system_owner),
    tenant_db=Depends(get_tenant_db),
):
    """Compute the rollups immediately — works even while the switch
    is OFF so the operator can preview readiness (cells are invisible
    to customers until the gate opens).  The nightly job remains the
    steady-state path."""
    since = (datetime.now(timezone.utc) - timedelta(days=365)).isoformat()
    n = await tenant_db.compute_market_rollups(since)
    stats = await tenant_db.market_intel_stats()
    return {"ok": True, "vendor_cells": n, **stats}
