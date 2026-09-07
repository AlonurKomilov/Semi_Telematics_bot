"""Scheduled Reports API — subscribe, list, stop.  URLs unchanged."""

# ═══ Per-user Scheduled-Reports subscriptions (a Reports component) ═══════════════════════════════════════════════════
# Extracted from the governance router — these endpoints belong to THIS
# domain (docs/FEATURES.md feature→component tree).  URLs unchanged.
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from interfaces.api.deps import (
    get_current_db_user, get_platform_db, get_tenant_db, require_permission,
)

#: the historical prefix — every client calls ``/user/scheduled-reports``
user_router = APIRouter(prefix="/user", tags=["user"])

# ── Scheduled Reports ──────────────────────────────────────────
# Recurring report delivery (PDF via Telegram).  Underlying storage
# table is still ``digest_subscriptions`` (legacy name kept to avoid a
# migration); the API + UI surface is fully renamed to "Scheduled
# Reports".  The /subscriptions API aliases were dropped — any client
# still calling them has had one release to update.

VALID_FREQUENCIES = {"daily", "weekly", "monthly"}
VALID_REPORT_TYPES = {"faults", "fuel", "health", "efficiency", "camera"}
VALID_DELIVERY_CHANNELS = {"telegram", "email"}


class ScheduledReportRequest(BaseModel):
    frequency: str = Field("daily", pattern=r"^(daily|weekly|monthly)$")
    send_hour: int = Field(7, ge=0, le=23)
    timezone: str = "America/New_York"
    report_type: str = Field("faults", pattern=r"^(faults|fuel|health|efficiency|camera)$")
    # Delivery channels — at least one of "telegram" or "email".
    # Defaults to ["telegram"] to preserve pre-2026-06 client behaviour
    # for any older SPA bundle still in flight.  Validated below.
    delivery_channels: list[str] = Field(default_factory=lambda: ["telegram"])

    def validated_channels(self) -> list[str]:
        """Return the de-duplicated, normalized channel list.

        Raises ``HTTPException`` if invalid (empty, unknown channel,
        all-bogus).  Keeps the route handlers free of inline validation
        boilerplate and ensures one consistent error shape.
        """
        seen: list[str] = []
        for c in self.delivery_channels:
            v = (c or "").strip().lower()
            if v in VALID_DELIVERY_CHANNELS and v not in seen:
                seen.append(v)
        if not seen:
            raise HTTPException(
                status_code=422,
                detail=(
                    "delivery_channels must include at least one of "
                    f"{sorted(VALID_DELIVERY_CHANNELS)}"
                ),
            )
        return seen


@user_router.get("/scheduled-reports")
async def list_scheduled_reports(
    user: dict = Depends(require_permission("can_view_reports")),
    platform_db=Depends(get_platform_db),
    tenant_db=Depends(get_tenant_db),
):
    """Return every active scheduled-report row for the current user.

    Gated on ``can_view_reports`` — the Reports service's view verb, of
    which this subscription is the sub-feature — so unticking Reports
    for a role closes the dashboard surface, not just the bot delivery.

    Response envelope: ``{"scheduled_reports": [...]}``  (multi-schedule
    model added 2026-06).  Empty list when the user has no schedules.
    """
    db_user = await get_current_db_user(user, platform_db)
    if not db_user:
        raise HTTPException(status_code=404, detail="User not found")
    rows = await tenant_db.get_digest_subscriptions(db_user.id)
    return {"scheduled_reports": rows}


@user_router.put("/scheduled-reports")
async def upsert_scheduled_report(
    body: ScheduledReportRequest,
    user: dict = Depends(require_permission("can_view_reports")),
    platform_db=Depends(get_platform_db),
    tenant_db=Depends(get_tenant_db),
):
    """Create or update the user's scheduled report delivery."""
    db_user = await get_current_db_user(user, platform_db)
    if not db_user:
        raise HTTPException(status_code=404, detail="User not found")

    channels = body.validated_channels()
    # Email delivery requires a verified address — otherwise the
    # scheduler would silently bounce, which hurts sender reputation.
    # We reject up-front so the dashboard can surface the gap as a
    # clear "verify your email" prompt instead of a mute non-delivery.
    if "email" in channels:
        if not db_user.email:
            raise HTTPException(
                status_code=422,
                detail="Email delivery requires an email address on your profile",
            )
        if not getattr(db_user, "email_verified", False):
            raise HTTPException(
                status_code=422,
                detail="Email delivery requires a verified email address — check your inbox for the verification link",
            )

    await tenant_db.subscribe_digest_ext(
        db_user.id,
        frequency=body.frequency,
        send_hour=body.send_hour,
        timezone=body.timezone,
        report_type=body.report_type,
        delivery_channels=channels,
    )
    # Return the FULL list so the dashboard can render the updated
    # schedule grid without a second roundtrip.  The multi-schedule
    # model means a single PUT can both create one row AND leave the
    # other rows visible — clients want them in the same response.
    rows = await tenant_db.get_digest_subscriptions(db_user.id)
    return {"scheduled_reports": rows}


@user_router.delete("/scheduled-reports")
async def delete_scheduled_report(
    report_type: Optional[str] = None,
    user: dict = Depends(require_permission("can_view_reports")),
    platform_db=Depends(get_platform_db),
    tenant_db=Depends(get_tenant_db),
):
    """Stop scheduled report delivery.

    ``?report_type=fuel`` stops only that schedule (per-row delete from
    the dashboard or per-schedule bot wizard).  Omitting the param
    stops EVERY schedule the user has — the "Stop all" button on the
    dashboard.  Validated against the canonical report registry so a
    malformed value can't deactivate by accident.
    """
    db_user = await get_current_db_user(user, platform_db)
    if not db_user:
        raise HTTPException(status_code=404, detail="User not found")
    if report_type is not None:
        if report_type not in VALID_REPORT_TYPES:
            raise HTTPException(
                status_code=422,
                detail=f"Invalid report_type: must be one of {sorted(VALID_REPORT_TYPES)}",
            )
    await tenant_db.unsubscribe_digest(db_user.id, report_type=report_type)
    return {"ok": True}


