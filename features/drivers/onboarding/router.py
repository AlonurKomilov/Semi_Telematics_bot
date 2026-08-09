"""Driver onboarding — the approved queue and the hire.

router.py is interface-layer code co-located with its feature
(docs/FEATURES.md): router.py and config.py are the interface-layer pair — those two may
# import interfaces.api.deps; nothing else in the feature may.

The hire used to live on the Applications router as
``POST /applications/{id}/convert``, gated by ``can_convert_to_driver``.
It moved here 2026-07-30 with the owner's model: the recruiter runs the
pipeline and approves; ONBOARDING mints a user, so it belongs to driver
administration.  The grant moved with it —
``can_convert_to_driver`` → ``can_onboard_drivers`` — which also stops
hiring from riding ``can_manage_drivers``, where a Fleet manager would
have inherited it just for administering trucks' drivers.
"""

from __future__ import annotations

import logging

from fastapi import APIRouter, Depends, HTTPException

from adapters.storage import Role
from interfaces.api.deps import (
    get_current_db_user, get_platform_db, require_permission,
)

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/drivers/onboarding", tags=["driver-onboarding"])

_onboard = require_permission("can_onboard_drivers")

# A 7-day window for a new hire to redeem their invite.
INVITE_HOURS = 168


@router.get("/queue")
async def onboarding_queue(
    user: dict = Depends(_onboard),
    platform_db=Depends(get_platform_db),
):
    """Applicants the recruiter approved and nobody has onboarded yet.

    This is the whole point of the split: whoever administers drivers can
    see and act on the handover WITHOUT needing the recruiting dashboard
    (``can_manage_applications``), which stays the recruiter's.
    """
    rows = await platform_db.list_driver_applications(
        user["account_id"], status="approved", limit=200,
    )
    return {"applicants": rows}


@router.post("/{app_id:int}/convert")
async def convert_to_driver(
    app_id: int,
    user: dict = Depends(_onboard),
    platform_db=Depends(get_platform_db),
):
    """Hire an applicant → mint a driver invite + mark the app hired.

    Returns the invite code + a /signup/<code> link to share with the new
    driver.  The invite carries ``source_application_id`` so
    ``redeem_invite`` stamps this application's ``converted_to_user_id``
    once the driver onboards — closing the application↔driver round-trip.
    """
    app = await platform_db.get_driver_application(user["account_id"], app_id)
    if not app:
        raise HTTPException(status_code=404, detail="Application not found")
    if app.get("status") == "hired":
        raise HTTPException(status_code=409, detail="Applicant already hired")
    # A driver must not be onboarded before their FMCSA vetting (PSP / MVR /
    # Clearinghouse) is reviewed and the application moved to 'approved' —
    # without this the pipeline stages are decorative.
    if app.get("status") != "approved":
        raise HTTPException(
            status_code=409,
            detail="Applicant must be 'approved' before hiring",
        )

    db_user_id = None
    try:
        du = await get_current_db_user(user, platform_db)
        db_user_id = du.id if du else None
    except Exception:
        pass

    # Atomic claim: flip approved → hired CONDITIONALLY first.  Only one
    # concurrent convert wins; the loser gets 0 rows and a 409 — so a
    # double-click can't mint two driver invites for one applicant.
    claimed = await platform_db.update_application_status(
        user["account_id"], app_id, "hired", reviewed_by=db_user_id,
        expect_status="approved",
    )
    if not claimed:
        raise HTTPException(status_code=409, detail="Applicant already being processed")

    # Now mint the invite.  If that fails, roll the claim back to 'approved'
    # so the applicant stays hireable (no stuck 'hired'-without-invite row).
    try:
        invite = await platform_db.create_invite(
            user["account_id"],
            created_by=db_user_id or 0,
            role=Role.DRIVER,
            hours=INVITE_HOURS,
            source_application_id=app_id,
        )
    except Exception:
        await platform_db.update_application_status(
            user["account_id"], app_id, "approved", reviewed_by=db_user_id,
        )
        logger.exception("onboarding: invite mint failed, rolled back app=%s", app_id)
        raise HTTPException(
            status_code=500,
            detail="Could not create the driver invite. Please try again.",
        )
    try:
        await platform_db.add_platform_audit(
            "driver_application_converted",
            account_id=user["account_id"],
            actor=f"onboarding:{db_user_id}",
            details=f"app_id={app_id} ref={app.get('reference')} invite={invite.code}",
        )
    except Exception:
        logger.exception("onboarding audit write failed app=%s", app_id)

    from interfaces.api.auth import _signup_base_url
    return {
        "status": "hired",
        "invite_code": invite.code,
        "invite_link": f"{_signup_base_url()}/signup/{invite.code}",
    }
