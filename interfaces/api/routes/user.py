"""User profile API endpoint."""

import re
from typing import Optional

import bcrypt
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from interfaces.api.deps import (
    get_current_db_user,
    get_current_user,
    get_platform_db,
    get_tenant_db,
    require_permission,
)
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
    db_user = await get_current_db_user(user, platform_db)
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
        # Stable internal primary key — survives Telegram re-linking and
        # is the value used by ownership checks on KB articles, work
        # orders, PTI media, etc.  Frontend prefers this over
        # ``telegram_id`` for "is this row mine?" comparisons.
        "id": db_user.id,
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
        # Surface verification state so the dashboard can render a
        # "verify your email" banner on the Sign-in methods panel until
        # the user clicks the verification link.
        "email_verified": (
            await platform_db.is_email_verified(db_user.id)
            if db_user.email else True
        ),
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
    """Add (or change) the email + password for the current user.

    Verification semantics
    ----------------------
    The new email is stored with ``email_verified = 0`` and a
    verification link is dispatched.  The user can KEEP using the
    dashboard via their existing session (Telegram or current email);
    but sign-in via the NEW email is blocked until they click the
    link.  Mirrors the /register flow exactly — no path adds an
    email-login method without a verified inbox.

    This closes a gap where a compromised Telegram session could
    attach an arbitrary email + password and persist after the
    Telegram link was revoked.
    """
    import logging
    from interfaces.api.auth import _validate_password_strength

    if not _EMAIL_RE.match(body.email):
        raise HTTPException(status_code=422, detail="Invalid email address")
    # Defer the password rule to the single source of truth shared with
    # /register and /reset — otherwise a user setting credentials here
    # could sneak in a weak password they couldn't use at signup.
    _validate_password_strength(body.password)

    # Check email not already taken by another user
    existing = await platform_db.get_user_by_email(body.email)
    db_user = await get_current_db_user(user, platform_db)
    if not db_user:
        raise HTTPException(status_code=404, detail="User not found")
    if existing and existing.id != db_user.id:
        raise HTTPException(status_code=409, detail="Email already in use")

    pw_hash = bcrypt.hashpw(body.password.encode(), bcrypt.gensalt()).decode()
    # ``reset_verification=True`` forces email_verified back to 0 so the
    # new address must be confirmed before it can be used for sign-in.
    await platform_db.set_user_email_password(
        db_user.id, body.email, pw_hash, reset_verification=True,
    )

    # Mint a verification token + send the link.  Soft-fail: storage
    # already committed the new email, and the user can request a fresh
    # link from the login screen if delivery hiccups.
    verification_sent = False
    try:
        token = await platform_db.create_email_verification_token(
            db_user.id, body.email,
        )
        from capabilities.notifications.auth_emails import (
            send_verification_email,
        )
        send_verification_email(
            to=body.email, token=token,
            recipient_name=db_user.display_name or "",
        )
        verification_sent = True
    except Exception as e:
        logging.getLogger("api.user").warning(
            "verification email send failed on add-credentials for %s: %s",
            body.email, e,
        )

    return {
        "detail":               "Credentials saved",
        "verification_required": True,
        "verification_sent":     verification_sent,
        "email":                 body.email,
        "message": (
            "Check your inbox for a verification link.  You can keep "
            "using the dashboard via your current session, but signing "
            "in with this email is blocked until you confirm it."
        ),
    }


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


@router.get("/scheduled-reports")
async def list_scheduled_reports(
    user: dict = Depends(require_permission("can_digest")),
    platform_db=Depends(get_platform_db),
    tenant_db=Depends(get_tenant_db),
):
    """Return every active scheduled-report row for the current user.

    Gated on ``can_digest`` so toggling the flag OFF for a role in
    RolePermissions actually disables the dashboard surface (not just
    the bot delivery).  Without this guard the page kept loading and
    accepting writes that the bot would silently never deliver.

    Response envelope: ``{"scheduled_reports": [...]}``  (multi-schedule
    model added 2026-06).  Empty list when the user has no schedules.
    """
    db_user = await get_current_db_user(user, platform_db)
    if not db_user:
        raise HTTPException(status_code=404, detail="User not found")
    rows = await tenant_db.get_digest_subscriptions(db_user.id)
    return {"scheduled_reports": rows}


@router.put("/scheduled-reports")
async def upsert_scheduled_report(
    body: ScheduledReportRequest,
    user: dict = Depends(require_permission("can_digest")),
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


@router.delete("/scheduled-reports")
async def delete_scheduled_report(
    report_type: Optional[str] = None,
    user: dict = Depends(require_permission("can_digest")),
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


# ── User Preferences ────────────────────────────────────────────

class PreferencesRequest(BaseModel):
    language: Optional[str] = Field(None, pattern=r"^(en|es|ru|uk|fr|so|am|uz|pa)$")
    timezone: Optional[str] = None
    quiet_start: Optional[int] = Field(None, ge=0, le=23)
    quiet_end: Optional[int] = Field(None, ge=0, le=23)
    display_name: Optional[str] = Field(None, min_length=1, max_length=100)


# ── Personal alert preferences (per-user DM toggles) ───────────────
#
# These endpoints power the dashboard "My Notifications" page
# (avatar menu → My Notifications).  Each user owns their own
# per-alert-type DM toggle + the new "🟢 Resolve receipts" opt-in
# (migration 080).  The toggle list is role-tailored — a Safety
# user doesn't see a Fuel toggle; a Dispatcher doesn't see Health.
# See ``capabilities/alerting/relevance.py`` for the role → type
# mapping.
#
# Admin-side per-topic config (group/forum routing + resolve-
# receipt toggle per topic) lives separately in
# ``interfaces/api/routes/admin.py``; the two surfaces are
# independent — neither overrides the other.


class AlertPrefsRequest(BaseModel):
    """Per-user alert preferences PATCH body.

    Every field is optional so the dashboard can send partial
    updates (toggle one switch at a time).  Unknown alert types are
    silently ignored at the adapter layer so a stale UI doesn't
    500 the request.
    """
    alerts_on: Optional[bool] = None
    alert_faults: Optional[bool] = None
    alert_health: Optional[bool] = None
    alert_fuel: Optional[bool] = None
    alert_geofence: Optional[bool] = None
    alert_events: Optional[bool] = None
    alert_parking: Optional[bool] = None
    alert_camera: Optional[bool] = None
    alert_resolve_receipts: Optional[bool] = None


@router.get("/me/alerts")
async def get_my_alerts(
    user: dict = Depends(get_current_user),
    platform_db=Depends(get_platform_db),
):
    """Return the role-tailored alert preferences for the current user.

    Response shape::

        {
          "alerts_on": true,
          "alert_resolve_receipts": false,
          "relevant_types": ["faults", "health", "fuel", ...],
          "toggles": {
            "alert_faults": true,
            "alert_health": true,
            ...  // only includes relevant types
          }
        }

    The ``relevant_types`` list drives which toggles the dashboard
    renders.  ``toggles`` mirrors that filter so the client never
    sees a stale value for an irrelevant type.
    """
    db_user = await get_current_db_user(user, platform_db)
    if not db_user:
        raise HTTPException(status_code=404, detail="User not found")
    from capabilities.alerting.relevance import alert_types_for_role
    relevant = alert_types_for_role(db_user.role)
    toggles: dict[str, bool] = {}
    for atype in relevant:
        attr = f"alert_{atype}"
        toggles[attr] = bool(getattr(db_user, attr, True))
    return {
        "alerts_on": bool(db_user.alerts_on),
        "alert_resolve_receipts": bool(
            getattr(db_user, "alert_resolve_receipts", False)
        ),
        "relevant_types": relevant,
        "toggles": toggles,
    }


@router.put("/me/alerts")
async def update_my_alerts(
    body: AlertPrefsRequest,
    user: dict = Depends(get_current_user),
    platform_db=Depends(get_platform_db),
):
    """Patch the current user's alert preferences.

    Role-tailored: requests to toggle an alert type the user's role
    doesn't have permission for are silently dropped (the dashboard
    UI shouldn't render that toggle anyway, but this is defense in
    depth so a crafted request can't enable irrelevant alerts).
    """
    db_user = await get_current_db_user(user, platform_db)
    if not db_user:
        raise HTTPException(status_code=404, detail="User not found")

    from capabilities.alerting.relevance import alert_types_for_role
    relevant_attrs = {
        f"alert_{atype}" for atype in alert_types_for_role(db_user.role)
    }
    # ``alerts_on`` (master switch) and ``alert_resolve_receipts``
    # apply regardless of role — every role can opt in/out of these.
    relevant_attrs.add("alerts_on")
    relevant_attrs.add("alert_resolve_receipts")

    updates: dict[str, bool] = {}
    for field, value in body.model_dump(exclude_unset=True).items():
        if value is None:
            continue
        if field not in relevant_attrs:
            # Silently drop irrelevant toggles instead of 422'ing —
            # the dashboard might briefly include a stale field
            # during a role-change race, and we don't want to error
            # the whole save for that.
            continue
        updates[field] = bool(value)

    if updates:
        await platform_db.update_user(db_user.id, **updates)

    # Echo the post-update state so the dashboard re-syncs without
    # a second roundtrip.
    fresh = await platform_db.get_user_by_id(db_user.id) if hasattr(
        platform_db, "get_user_by_id"
    ) else db_user
    relevant = alert_types_for_role(fresh.role if fresh else db_user.role)
    toggles: dict[str, bool] = {}
    target = fresh or db_user
    for atype in relevant:
        attr = f"alert_{atype}"
        toggles[attr] = bool(getattr(target, attr, True))
    return {
        "alerts_on": bool(target.alerts_on),
        "alert_resolve_receipts": bool(
            getattr(target, "alert_resolve_receipts", False)
        ),
        "relevant_types": relevant,
        "toggles": toggles,
    }


@router.put("/preferences")
async def update_preferences(
    body: PreferencesRequest,
    user: dict = Depends(get_current_user),
    platform_db=Depends(get_platform_db),
):
    """Update user display preferences (language, timezone, DND)."""
    db_user = await get_current_db_user(user, platform_db)
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


# ── Activity log + GDPR export ──────────────────────────────────────


@router.get("/me/activity")
async def my_activity(
    limit: int = 20,
    user: dict = Depends(get_current_user),
    platform_db=Depends(get_platform_db),
):
    """Recent login attempts for the requesting user.

    Drives the "Recent activity" panel on the profile page so a user
    can spot logins they don't recognise.  Bounded by an index on
    ``(user_id, attempted_at DESC)``.
    """
    db_user = await get_current_db_user(user, platform_db)
    if not db_user:
        raise HTTPException(status_code=404, detail="User not found")
    rows = await platform_db.list_my_login_attempts(db_user.id, limit=limit)
    return {"items": rows, "count": len(rows)}


@router.get("/me/export")
async def my_data_export(
    user: dict = Depends(get_current_user),
    platform_db=Depends(get_platform_db),
):
    """Return everything we know about the requesting user as JSON.

    GDPR Article 15 ("right of access") + Article 20 ("right to data
    portability").  Output is a single JSON document with one section
    per logical record type:

      * ``profile``   — the row from ``users``, including driver
                        fields (decrypted on the way out).
      * ``sessions``  — every active session row from ``user_sessions``.
      * ``activity``  — last 200 login attempts.
      * ``companies`` — company-code memberships from ``user_companies``.
      * ``vehicles``  — assigned trucks from ``driver_trucks`` /
                        ``driver_vehicle_assignments``.

    No telemetry / Samsara-sourced data lives here yet (that's per-
    fleet, not per-user).  Adding it later is additive — the structure
    is a list of named sections so consumers can ignore unknown ones.
    """
    db_user = await get_current_db_user(user, platform_db)
    if not db_user:
        raise HTTPException(status_code=404, detail="User not found")

    # Profile — full users row.  ``_row_to_user`` doesn't carry driver-
    # PII columns, so we re-select them here and decrypt through the
    # driver mixin's helper.  Best-effort: missing columns just absent
    # from the export, never a 500.
    try:
        profile = await platform_db.get_driver_profile(db_user.id)
        profile_dict = profile.__dict__ if profile else {}
    except Exception:
        profile_dict = {}

    # Sessions + activity + memberships.  Each lookup tolerates the
    # underlying table not existing (fresh dev DBs) so the export
    # endpoint always returns SOMETHING.
    try:
        sessions = await platform_db.list_user_sessions(db_user.id)
    except Exception:
        sessions = []
    try:
        activity = await platform_db.list_my_login_attempts(db_user.id, limit=200)
    except Exception:
        activity = []
    try:
        companies = await platform_db.get_user_company_codes(db_user.id)
    except Exception:
        companies = []
    try:
        vehicles = await platform_db.get_user_vehicle_nums(db_user.id)
    except Exception:
        vehicles = []

    return {
        "exported_at": _iso_now(),
        "profile": profile_dict,
        "user_row": {
            "id": db_user.id,
            "telegram_id": db_user.telegram_id,
            "account_id": db_user.account_id,
            "display_name": db_user.display_name,
            "role": db_user.role.value if hasattr(db_user.role, "value") else str(db_user.role),
            "department": db_user.department,
            "truck_num": db_user.truck_num,
            "email": db_user.email,
            "language": getattr(db_user, "language", None),
            "timezone": getattr(db_user, "timezone", None),
            "is_active": db_user.is_active,
            "created_at": db_user.created_at,
        },
        "sessions": sessions,
        "activity": activity,
        "companies": companies,
        "vehicles": vehicles,
    }


def _iso_now() -> str:
    from datetime import datetime, timezone
    return datetime.now(timezone.utc).isoformat()


@router.get("/sessions")
async def list_my_sessions(
    user: dict = Depends(get_current_user),
    platform_db=Depends(get_platform_db),
):
    """Return the current user's active dashboard / Mini App sessions.

    Powers the "Active sessions" panel on /profile.  ``current_jti`` is
    echoed back so the frontend can pin the requesting browser at the
    top of the list and disable the revoke button on its own row.
    """
    db_user = await get_current_db_user(user, platform_db)
    if not db_user:
        raise HTTPException(status_code=404, detail="User not found")
    sessions = await platform_db.list_user_sessions(db_user.id)
    return {
        "items": sessions,
        "current_jti": str(user.get("jti") or ""),
    }


@router.delete("/sessions/{session_id}")
async def revoke_my_session(
    session_id: int,
    user: dict = Depends(get_current_user),
    platform_db=Depends(get_platform_db),
):
    """Revoke one of the current user's own sessions.

    The session must belong to the requesting user — we scope by
    ``owning_user_id`` so a JWT for user A can't revoke user B's
    sessions even with a guessed session_id.  After the DB update, the
    jti is pushed onto the Redis denylist so ``get_current_user``
    rejects any further requests with that jti.
    """
    db_user = await get_current_db_user(user, platform_db)
    if not db_user:
        raise HTTPException(status_code=404, detail="User not found")
    revoked = await platform_db.revoke_user_session(
        session_id, owning_user_id=db_user.id,
    )
    if not revoked:
        raise HTTPException(
            status_code=404,
            detail="Session not found or already revoked.",
        )
    from interfaces.api.auth import mark_jti_revoked
    await mark_jti_revoked(revoked["jti"], revoked.get("expires_at"))
    return {"ok": True, "revoked": {"id": revoked["id"], "jti": revoked["jti"]}}


@router.post("/sessions/terminate-others")
async def terminate_other_sessions(
    user: dict = Depends(get_current_user),
    platform_db=Depends(get_platform_db),
):
    """Revoke every active session for the current user except this one.

    The requesting browser stays signed in (its jti is excluded from
    the revoke), every other device is kicked out at the next request.
    Tokens minted before the jti-claim rollout don't have a jti in the
    JWT, so we treat ``current_jti`` as empty-string in that case and
    revoke EVERYTHING — including the current browser — which forces a
    fresh login that backfills a session row.  Safer than leaking a
    legacy session past a "terminate all" click.
    """
    db_user = await get_current_db_user(user, platform_db)
    if not db_user:
        raise HTTPException(status_code=404, detail="User not found")
    current_jti = str(user.get("jti") or "")
    revoked = await platform_db.revoke_other_user_sessions(
        db_user.id, current_jti,
    )
    from interfaces.api.auth import mark_jti_revoked
    for row in revoked:
        await mark_jti_revoked(row["jti"], row.get("expires_at"))
    return {"ok": True, "revoked_count": len(revoked)}


# ── Telegram link / unlink ────────────────────────────────────────
#
# An email-registered user has ``telegram_id IS NULL`` until they open
# the bot and approve the link.  Mirrors the bot-login flow:
#
#   1. Dashboard POST /user/telegram/link/init      → returns deep link
#   2. User taps it, opens the bot with /start link_<token>
#   3. Bot writes the link result back to Redis
#   4. Dashboard polls /user/telegram/link/status/{token} until done
#
# Unlinking is one-shot — DELETE /user/telegram and the column is
# cleared, but only if the user still has email + password (otherwise
# they'd lock themselves out).

from interfaces.bot.config import TELEGRAM_LINK_PREFIX, TELEGRAM_LINK_TTL  # noqa: E402


@router.post("/telegram/link/init")
async def telegram_link_init(
    user: dict = Depends(get_current_user),
    platform_db=Depends(get_platform_db),
):
    """Start a Telegram-link flow.  Returns a deep link the user opens.

    The token is bound to the current user.id and expires in 5 minutes.
    Refuses if the account already has a Telegram link — call
    DELETE /user/telegram first to swap.
    """
    import secrets
    from infra.cache import cache_set as redis_set
    from interfaces.bot.config import bot_username as global_bot_username

    db_user = await get_current_db_user(user, platform_db)
    if not db_user:
        raise HTTPException(status_code=404, detail="User not found")
    if db_user.telegram_id:
        raise HTTPException(
            status_code=409,
            detail="This account is already linked to Telegram.",
        )

    # Prefer the account's own bot, fall back to the global bot_username
    # the daemon resolved at startup.  Without either, the deep link has
    # no host, so refuse with a clear error.
    bot_un = ""
    try:
        acct = await platform_db.get_account(db_user.account_id)
        if acct and acct.bot_username:
            bot_un = acct.bot_username
    except Exception:
        pass
    if not bot_un:
        bot_un = global_bot_username or ""
    if not bot_un:
        raise HTTPException(
            status_code=503,
            detail=(
                "Telegram bot is not configured for this account — "
                "ask an admin to set it up in Admin → Settings."
            ),
        )

    token = secrets.token_urlsafe(32)
    await redis_set(
        f"{TELEGRAM_LINK_PREFIX}{token}",
        {"status": "pending", "user_id": db_user.id},
        ttl=TELEGRAM_LINK_TTL,
    )
    return {
        "token": token,
        "deep_link": f"https://t.me/{bot_un}?start=link_{token}",
        "ttl": TELEGRAM_LINK_TTL,
    }


@router.get("/telegram/link/status/{token}")
async def telegram_link_status(
    token: str,
    user: dict = Depends(get_current_user),
    platform_db=Depends(get_platform_db),
):
    """Poll for the outcome of a Telegram-link flow.

    Returned shapes:
      - {"status": "pending"}                                — still waiting
      - {"status": "linked",   "telegram_id": <int>}         — success
      - {"status": "rejected", "reason": "..."}              — bot refused
      - {"status": "expired"}                                — token gone
    """
    from infra.cache import get as redis_get, delete as redis_del

    if not token or len(token) > 64:
        raise HTTPException(status_code=400, detail="Invalid token")

    db_user = await get_current_db_user(user, platform_db)
    if not db_user:
        raise HTTPException(status_code=404, detail="User not found")

    data = await redis_get(f"{TELEGRAM_LINK_PREFIX}{token}")
    if data is None:
        return {"status": "expired"}
    # Token is bound to the user that started it — never let someone
    # else's link result get returned across accounts.
    if data.get("user_id") != db_user.id:
        return {"status": "expired"}

    status = data.get("status")
    if status in ("linked", "rejected"):
        await redis_del(f"{TELEGRAM_LINK_PREFIX}{token}")
    if status == "linked":
        return {"status": "linked", "telegram_id": data.get("telegram_id")}
    if status == "rejected":
        return {"status": "rejected", "reason": data.get("reason", "Refused")}
    return {"status": "pending"}


@router.delete("/telegram")
async def telegram_unlink(
    user: dict = Depends(get_current_user),
    platform_db=Depends(get_platform_db),
):
    """Detach the Telegram link from the requesting user.

    Refuses (400) if the user has no email + password — removing the
    last sign-in method would lock them out.
    """
    db_user = await get_current_db_user(user, platform_db)
    if not db_user:
        raise HTTPException(status_code=404, detail="User not found")
    if not db_user.telegram_id:
        return {"ok": True, "already_unlinked": True}
    try:
        await platform_db.unlink_telegram_from_user(db_user.id)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    return {"ok": True}
