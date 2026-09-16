"""Linking a member to the driver an integration reports.

The roster and the device do not share an identity. An ELD knows
"driver 19192"; we know "Jean Kaneza, user 84". Somebody has to say
those are the same person once, and after that every feed, every
scorecard and every pay run can follow the link.

WHY THIS IS NOT AUTOMATIC
-------------------------
The matcher Datatruck's import uses goes ref → CDL → email and refuses
NAME, because a wrong match writes one driver's licence number onto
another driver's record. Measured on the account this was built for,
that matcher would link exactly ZERO of sixty-nine: the ELD carries a
CDL for every driver and our roster carries none, and the two email
sets do not intersect. There is no key to match on yet.

So the first link is a human decision. After it, the provider's id is
stored and everything else follows from it — including filling the CDL
we are missing, which is what gives a future match something to work
with.

WHY THE PICKER READS OUR OWN TABLE
----------------------------------
The options come from ``driver_hos_live`` — the drivers an ELD is
actually reporting — rather than from a fresh call to the provider.
Three reasons, and the third is the one that matters:

  it is already there, refreshed every five minutes;
  it needs no new protocol method, so a third provider costs nothing;
  it offers the people who ACTUALLY REPORT, not everyone on the
  vendor's roster. ORIENT's roster carries no role and no active flag,
  so it cannot tell a current driver from somebody who left — while a
  row in the duty feed means a person logged onto a vehicle.
"""

from __future__ import annotations

import logging

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from adapters.telematics.catalog import PROVIDER_CATALOG
from capabilities.activity_trail import record_simple
from capabilities.permissions.roles import role_rank
from interfaces.api.deps import (
    get_platform_db,
    get_tenant_db,
    require_permission_any,
    resolve_user_id,
)

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/drivers", tags=["drivers"])

#: Same gate as the Samsara link it generalises — roster admin, or the
#: broader user-management right.
_GATE = require_permission_any("can_manage_users", "can_manage_drivers")


class ProviderLinkUpdate(BaseModel):
    #: The provider's own driver id. Blank unlinks.
    provider_driver_id: str = Field(default="", max_length=128)


def _provider_name(provider_id: str) -> str:
    entry = PROVIDER_CATALOG.get(provider_id)
    return entry.display_name if entry else provider_id


@router.get("/provider-links")
async def list_provider_links(
    user: dict = Depends(_GATE),
    tenant_db=Depends(get_tenant_db),
):
    """Every driver an integration reports, and who they are here.

    Grouped by provider so the drawer can show one section per device.
    ``linked_user_id`` is ``None`` for somebody nobody has matched yet —
    which on a fresh ELD is everybody.
    """
    account_id = int(user["account_id"])
    try:
        rows = await tenant_db.get_driver_hos_live(account_id)
    except Exception:
        logger.exception("provider links: feed read failed acct=%d", account_id)
        rows = []

    by_provider: dict[str, list[dict]] = {}
    for r in rows:
        pid = str(r.get("provider_id") or "")
        if not pid:
            continue
        by_provider.setdefault(pid, []).append({
            "provider_driver_id": str(r.get("provider_driver_id") or ""),
            # The vendor's spelling — the only name we have for an
            # unlinked person, and the whole point of showing it.
            "driver_name": str(r.get("driver_name") or ""),
            "company_code": str(r.get("company_code") or ""),
            "vehicle": str(r.get("provider_vehicle") or ""),
            "linked_user_id": r.get("user_id"),
        })

    return {
        "providers": [
            {
                "provider_id": pid,
                "name": _provider_name(pid),
                "drivers": sorted(
                    drivers, key=lambda d: d["driver_name"].lower()),
                "unlinked": sum(
                    1 for d in drivers if d["linked_user_id"] is None),
            }
            for pid, drivers in sorted(by_provider.items())
        ],
    }


@router.put("/{user_id}/provider-links/{provider_id}")
async def set_provider_link(
    user_id: int,
    provider_id: str,
    body: ProviderLinkUpdate,
    user: dict = Depends(_GATE),
    platform_db=Depends(get_platform_db),
    tenant_db=Depends(get_tenant_db),
):
    """Say that this member IS that provider's driver. Blank unlinks.

    Refuses a driver already bound to somebody else with 409 rather
    than moving it: a silent move would put one person's duty clocks,
    pay and scorecard onto another member.
    """
    account_id = int(user["account_id"])
    if provider_id not in PROVIDER_CATALOG:
        raise HTTPException(404, f"unknown provider {provider_id!r}")

    target = await platform_db.get_user(user_id)
    if not target or target.account_id != account_id:
        raise HTTPException(404, "User not found")

    # The same rank wall the Samsara link carries: an admin may not
    # rewrite the identity of somebody at or above their own level.
    caller_rank = role_rank(user["role"])
    target_role = (
        target.role.value if hasattr(target.role, "value") else target.role
    )
    if role_rank(target_role) >= caller_rank:
        raise HTTPException(
            403, "Cannot modify a user with equal or higher role")

    ref = (body.provider_driver_id or "").strip()
    try:
        await tenant_db.link_provider_driver(
            account_id, provider_id, user_id, ref,
            method="manual", linked_by=await resolve_user_id(user),
        )
    except ValueError as e:
        raise HTTPException(409, str(e))

    await record_simple(
        tenant_db, account_id, await resolve_user_id(user),
        "user_provider_driver_link_set", "user", user_id,
        changes={f"{provider_id}_driver_id": {"new": ref or None}},
    )
    return {"ok": True, "provider_id": provider_id,
            "provider_driver_id": ref, "user_id": user_id}
