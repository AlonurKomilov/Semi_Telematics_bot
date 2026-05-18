"""User profile API endpoint."""

import re
from typing import Optional

import bcrypt
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from interfaces.api.deps import get_current_user, get_platform_db, get_tenant_db
from capabilities.iam.permissions import get_account_permissions
from capabilities.localization.tz import effective_tz_for_user, IANA_OPTIONS
from adapters.storage import Role

router = APIRouter(prefix="/user", tags=["user"])

_EMAIL_RE = re.compile(r"^[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}$")


@router.get("/me")
async def user_me(
    user: dict = Depends(get_current_user),
    platform_db=Depends(get_platform_db),
):
    """Return the current user's profile and permissions."""
    db_user = await platform_db.get_user_by_telegram_id(int(user["sub"]))
    if not db_user:
        return {"error": "User not found"}

    role_enum = Role(user["role"])
    account_id = user["account_id"]
    acct = await platform_db.get_account(account_id)
    perms = await get_account_permissions(role_enum, account_id)
    perm_dict = {
        field: getattr(perms, field)
        for field in perms.__dataclass_fields__
    }

    # Get multi-truck assignments
    trucks = await platform_db.get_user_vehicle_nums(db_user.id)
    if not trucks and db_user.truck_num:
        trucks = [db_user.truck_num]

    # Get company access restrictions
    allowed_companies = await platform_db.get_user_company_codes(db_user.id)

    # ``timezone`` is the per-user override stored on the row (may be
    # empty / 'UTC' / default 'America/New_York' depending on history).
    # ``effective_timezone`` is what the rest of the app should actually
    # render in — user override first, then account default, then the
    # hard fallback.  The dashboard reads ``effective_timezone`` for
    # every formatter; ``timezone`` is shown in the Profile UI as the
    # override the user can clear back to inherit.
    effective_tz = await effective_tz_for_user(db_user.id)

    # DND-derived-from-Working-Hours summary so the dashboard can
    # render the right UI ("auto from Working Hours" vs "personal
    # override active") without re-implementing the SSoT logic.
    has_override = (db_user.quiet_start is not None
                    and db_user.quiet_end is not None)
    work_hours_rows = []
    try:
        work_hours_rows = await platform_db.get_work_hours_for_role(
            account_id, user["role"],
        )
    except Exception:
        pass
    dnd_source = (
        "user_override" if has_override
        else "work_hours" if work_hours_rows
        else "none"
    )

    return {
        "telegram_id": db_user.telegram_id,
        "display_name": db_user.display_name,
        "role": user["role"],
        "department": db_user.department,
        "account_id": user["account_id"],
        "truck_num": db_user.truck_num,
        "trucks": trucks,
        "allowed_companies": allowed_companies,
        "language": db_user.language,
        "timezone": db_user.timezone,
        "effective_timezone": effective_tz,
        "account_timezone": getattr(acct, "timezone", None),
        "quiet_start": db_user.quiet_start,
        "quiet_end": db_user.quiet_end,
        # DND derivation summary for the dashboard UI:
        #   "user_override" → personal quiet_start/end is active
        #   "work_hours"    → derived from account Working Hours for this role
        #   "none"          → DND inactive (no override AND no work_hours for role)
        "dnd_source": dnd_source,
        "work_hours_for_role": [
            {
                "id":          int(r["id"]),
                "label":       r.get("label", ""),
                "start_hour":  int(r.get("start_hour", 0)),
                "end_hour":    int(r.get("end_hour", 0)),
                "target_role": r.get("target_role", "all"),
            }
            for r in work_hours_rows
        ],
        "email": db_user.email,
        "has_password": db_user.password_hash is not None,
        "permissions": perm_dict,
        "payroll_enabled": bool(getattr(acct, "payroll_enabled", False)),
        "coaching_enabled": bool(getattr(acct, "coaching_enabled", False)),
    }


class SetCredentialsRequest(BaseModel):
    email: str
    password: str


@router.put("/credentials")
async def set_credentials(
    body: SetCredentialsRequest,
    user: dict = Depends(get_current_user),
    platform_db=Depends(get_platform_db),
):
    """Set email+password for the current user.

    Allows Telegram-authenticated users to add email/password
    login credentials for the dashboard.
    """
    if not _EMAIL_RE.match(body.email):
        raise HTTPException(status_code=422, detail="Invalid email address")
    if len(body.password) < 8:
        raise HTTPException(
            status_code=422, detail="Password must be at least 8 characters"
        )

    # Check email not already taken by another user
    existing = await platform_db.get_user_by_email(body.email)
    db_user = await platform_db.get_user_by_telegram_id(int(user["sub"]))
    if not db_user:
        raise HTTPException(status_code=404, detail="User not found")
    if existing and existing.id != db_user.id:
        raise HTTPException(status_code=409, detail="Email already in use")

    pw_hash = bcrypt.hashpw(body.password.encode(), bcrypt.gensalt()).decode()
    await platform_db.set_user_email_password(db_user.id, body.email, pw_hash)
    return {"detail": "Credentials saved"}


# ── Subscriptions ────────────────────────────────────────────────

VALID_FREQUENCIES = {"daily", "weekly", "monthly"}
VALID_REPORT_TYPES = {"faults", "fuel", "health", "efficiency", "camera"}


class SubscriptionRequest(BaseModel):
    frequency: str = Field("daily", pattern=r"^(daily|weekly|monthly)$")
    send_hour: int = Field(7, ge=0, le=23)
    timezone: str = "America/New_York"
    report_type: str = Field("faults", pattern=r"^(faults|fuel|health|efficiency|camera)$")


@router.get("/subscriptions")
async def get_subscription(
    user: dict = Depends(get_current_user),
    platform_db=Depends(get_platform_db),
    tenant_db=Depends(get_tenant_db),
):
    """Get the current user's auto-report subscription."""
    db_user = await platform_db.get_user_by_telegram_id(int(user["sub"]))
    if not db_user:
        raise HTTPException(status_code=404, detail="User not found")
    sub = await tenant_db.get_digest_subscription(db_user.id)
    return {"subscription": sub}


@router.put("/subscriptions")
async def upsert_subscription(
    body: SubscriptionRequest,
    user: dict = Depends(get_current_user),
    platform_db=Depends(get_platform_db),
    tenant_db=Depends(get_tenant_db),
):
    """Create or update auto-report subscription."""
    db_user = await platform_db.get_user_by_telegram_id(int(user["sub"]))
    if not db_user:
        raise HTTPException(status_code=404, detail="User not found")
    await tenant_db.subscribe_digest_ext(
        db_user.id,
        frequency=body.frequency,
        send_hour=body.send_hour,
        timezone=body.timezone,
        report_type=body.report_type,
    )
    sub = await tenant_db.get_digest_subscription(db_user.id)
    return {"subscription": sub}


@router.delete("/subscriptions")
async def delete_subscription(
    user: dict = Depends(get_current_user),
    platform_db=Depends(get_platform_db),
    tenant_db=Depends(get_tenant_db),
):
    """Unsubscribe from auto-reports."""
    db_user = await platform_db.get_user_by_telegram_id(int(user["sub"]))
    if not db_user:
        raise HTTPException(status_code=404, detail="User not found")
    await tenant_db.unsubscribe_digest(db_user.id)
    return {"ok": True}


# ── User Preferences ────────────────────────────────────────────

class PreferencesRequest(BaseModel):
    language: Optional[str] = Field(None, pattern=r"^(en|es|ru|uk|fr|so|am|uz|pa)$")
    timezone: Optional[str] = None
    quiet_start: Optional[int] = Field(None, ge=0, le=23)
    quiet_end: Optional[int] = Field(None, ge=0, le=23)
    display_name: Optional[str] = Field(None, min_length=1, max_length=100)


@router.put("/preferences")
async def update_preferences(
    body: PreferencesRequest,
    user: dict = Depends(get_current_user),
    platform_db=Depends(get_platform_db),
):
    """Update user display preferences (language, timezone, DND)."""
    db_user = await platform_db.get_user_by_telegram_id(int(user["sub"]))
    if not db_user:
        raise HTTPException(status_code=404, detail="User not found")

    updates: dict = {}
    # Pydantic's ``model_fields_set`` distinguishes "field not sent" from
    # "field sent explicitly as null".  We need that for quiet_start /
    # quiet_end so the user can CLEAR their override (= explicit null)
    # to fall back to the Working-Hours-derived DND.  Without this, a
    # null in the JSON body would silently be skipped and the old
    # override would persist forever.
    sent = body.model_fields_set
    if body.language is not None:
        updates["language"] = body.language
    if "timezone" in sent and body.timezone is not None:
        # Validate against the supported IANA list — an empty string is
        # allowed as "clear my override, fall back to account default."
        tz_val = body.timezone.strip()
        if tz_val and tz_val not in IANA_OPTIONS:
            raise HTTPException(
                status_code=422,
                detail=f"Unsupported timezone. Valid values: {', '.join(IANA_OPTIONS)}",
            )
        updates["timezone"] = tz_val
    if "quiet_start" in sent:
        # Accept explicit null to clear; otherwise stored as int.
        updates["quiet_start"] = body.quiet_start
    if "quiet_end" in sent:
        updates["quiet_end"] = body.quiet_end
    if body.display_name is not None:
        updates["display_name"] = body.display_name

    if not updates:
        raise HTTPException(status_code=422, detail="No fields to update")

    await platform_db.update_user(db_user.id, **updates)
    return {"ok": True, "updated": list(updates.keys())}
