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
from capabilities.permissions.roles import get_account_permissions, get_user_permissions
from capabilities.permissions.modules import enabled_modules as _enabled_modules
from capabilities.permissions.modules import parse_disabled as _parse_disabled
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
    # Resolve the USER's effective perms — the role baseline plus the
    # per-user manager tier.  Sourced from the fresh DB row (not the JWT
    # claim) so a just-promoted/demoted manager sees the change on the next
    # /me refetch without waiting for a new token.
    perms = await get_user_permissions(
        role_enum, account_id,
        is_manager=db_user.is_manager,
        is_primary_owner=db_user.is_primary_owner,
    )
    perm_dict = {
        field: getattr(perms, field)
        for field in perms.__dataclass_fields__
    }

    # Get multi-truck assignments
    trucks = await platform_db.get_user_vehicle_nums(db_user.id)
    if not trucks and db_user.truck_num:
        trucks = [db_user.truck_num]

    # Company access restrictions — per-user assignment (Team Management);
    # empty = all companies.
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
        # Per-user manager tier (orthogonal to role) — the SPA shows the
        # "Manager" badge + gates manager-only affordances on this.
        "is_manager": db_user.is_manager,
        # Owner tier — primary (main) owner vs co-owner.  Gates the co-owner
        # management actions + destructive account actions in the SPA.
        "is_primary_owner": db_user.is_primary_owner,
        "account_id": user["account_id"],
        "truck_num": db_user.truck_num,
        "trucks": trucks,
        "allowed_companies": allowed_companies,
        "language": db_user.language,
        "timezone": db_user.timezone,
        "effective_timezone": effective_tz,
        "account_timezone": getattr(acct, "timezone", None),
        # ``quiet_start`` / ``quiet_end`` are read-only here since
        # migration 100 (admin-managed via Team Management).  Returned
        # so Profile.tsx can render the schedule preview text alongside
        # the user's personal DND toggle.
        "quiet_start": db_user.quiet_start,
        "quiet_end": db_user.quiet_end,
        # Personal DND toggle (migration 100).  True = user honours
        # the schedule above (queue non-critical alerts outside the
        # window).  False = user receives all non-critical alerts 24/7.
        "dnd_enabled": db_user.dnd_enabled,
        # DND derivation summary for the dashboard UI:
        #   "user_override" → admin-set personal quiet_start/end is active
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
        # Payroll is an Accounting feature now — "available" == Accounting
        # module on (per-user access is the can_payroll_admin permission).
        # Field name kept for frontend compat.
        "payroll_enabled": "accounting" not in _parse_disabled(
            getattr(acct, "disabled_modules", ""),
        ),
        "coaching_enabled": bool(getattr(acct, "coaching_enabled", False)),
        # Enabled department modules (Fleet/Dispatch/Safety/HR/Accounting).
        # Drives module-aware sidebar filtering; Core + Account admin are
        # always on and not listed.  See capabilities/permissions/modules.py.
        "enabled_modules": _enabled_modules(getattr(acct, "disabled_modules", "")),
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
        from capabilities.email.auth_emails import (
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


# ── User Preferences ────────────────────────────────────────────

class PreferencesRequest(BaseModel):
    language: Optional[str] = Field(None, pattern=r"^(en|es|ru|uk|fr|so|am|uz|pa)$")
    timezone: Optional[str] = None
    # ``dnd_enabled`` (migration 100) is the user's personal toggle
    # over honouring the admin-set Working Hours schedule.  True =
    # respect schedule (queue non-critical alerts outside the window),
    # False = receive all non-critical alerts 24/7.  Critical alerts
    # bypass either way via the pipeline's ``bypasses_dnd`` flag.
    dnd_enabled: Optional[bool] = None
    # ``quiet_start`` / ``quiet_end`` were here when users could
    # self-edit their personal Working Hours window.  Since
    # migration 100 those columns are admin-managed only (via
    # PUT /admin/users/:id/quiet-hours); a non-null value on this
    # endpoint is rejected below with a 403 pointing at the admin
    # path.  The fields stay on the schema so old clients posting
    # them get the explicit rejection instead of a silent ignore.
    quiet_start: Optional[int] = Field(None, ge=0, le=23)
    quiet_end: Optional[int] = Field(None, ge=0, le=23)
    display_name: Optional[str] = Field(None, min_length=1, max_length=100)


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


# ── UI preferences (opaque per-user KV) ─────────────────────────────
#
# Distinct from the profile ``PUT /preferences`` endpoint just below —
# this is a generic key-value store the dashboard uses for UI state
# (DataTable layouts, last-used filters, etc.) so an operator's
# customizations follow them across devices instead of being trapped
# in one browser's localStorage.  Keys are opaque (frontend coins them
# as ``table.maintenance-tasks.visibility``, etc.); values are
# JSON-encoded strings.
#
# Key safety: bound to ``user_id`` from the JWT — no path is ever
# constructible to read another user's preferences.  Keys are length-
# capped to 200 chars and restricted to a safe charset to keep them
# usable in URLs and log lines.

import re as _re

_UI_PREF_KEY_RE = _re.compile(r"^[A-Za-z0-9._-]{1,200}$")
_UI_PREF_VALUE_MAX = 64 * 1024   # 64 KB ceiling per single preference


def _validate_pref_key(key: str) -> None:
    if not _UI_PREF_KEY_RE.match(key or ""):
        raise HTTPException(
            status_code=422,
            detail="Invalid preference key (alphanumeric/./_/- only, max 200 chars)",
        )


class UiPrefBody(BaseModel):
    value: str = Field("", max_length=_UI_PREF_VALUE_MAX)


@router.get("/preferences/ui/{key}")
async def get_ui_preference(
    key: str,
    user: dict = Depends(get_current_user),
    platform_db=Depends(get_platform_db),
):
    """Read a single UI preference scoped to the requesting user.

    Returns ``{"value": "..."}`` (empty string when the key is unset)
    rather than 404 so the frontend treats first-read-of-a-new-key the
    same as fresh-default — no special error path needed in callers.
    """
    _validate_pref_key(key)
    db_user = await get_current_db_user(user, platform_db)
    if not db_user:
        raise HTTPException(status_code=404, detail="User not found")
    value = await platform_db.get_user_preference(db_user.id, key)
    return {"key": key, "value": value}


@router.put("/preferences/ui/{key}")
async def set_ui_preference(
    key: str,
    body: UiPrefBody,
    user: dict = Depends(get_current_user),
    platform_db=Depends(get_platform_db),
):
    """Upsert a single UI preference for the requesting user."""
    _validate_pref_key(key)
    db_user = await get_current_db_user(user, platform_db)
    if not db_user:
        raise HTTPException(status_code=404, detail="User not found")
    await platform_db.set_user_preference(db_user.id, key, body.value)
    return {"ok": True, "key": key}


@router.delete("/preferences/ui/{key}")
async def delete_ui_preference(
    key: str,
    user: dict = Depends(get_current_user),
    platform_db=Depends(get_platform_db),
):
    """Delete a single UI preference — used by the dashboard's
    "Reset to defaults" so cleared state stops syncing back from the
    last device that wrote it."""
    _validate_pref_key(key)
    db_user = await get_current_db_user(user, platform_db)
    if not db_user:
        raise HTTPException(status_code=404, detail="User not found")
    await platform_db.delete_user_preference(db_user.id, key)
    return {"ok": True, "key": key}


@router.get("/preferences/ui")
async def list_ui_preferences(
    prefix: Optional[str] = None,
    user: dict = Depends(get_current_user),
    platform_db=Depends(get_platform_db),
):
    """Bulk-load every UI preference for the requesting user.

    ``prefix`` narrows to keys starting with that string (e.g.
    ``table.``) so the dashboard can fetch every DataTable layout in
    one round-trip on initial load instead of one request per table.
    """
    if prefix is not None:
        # Validate the prefix shape too — same charset as keys, just
        # no length floor (an empty prefix is allowed; falls back to
        # "everything").
        if prefix and not _re.match(r"^[A-Za-z0-9._-]{0,200}$", prefix):
            raise HTTPException(status_code=422, detail="Invalid prefix")
    db_user = await get_current_db_user(user, platform_db)
    if not db_user:
        raise HTTPException(status_code=404, detail="User not found")
    items = await platform_db.list_user_preferences(db_user.id, prefix=prefix)
    return {"items": items, "count": len(items)}


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
    # Working-Hours hours are admin-managed since migration 100.
    # Reject any attempt by the user to self-edit ``quiet_start`` or
    # ``quiet_end`` from their Profile — point them at the admin path.
    # Sending ``dnd_enabled`` from the same body is the supported
    # personal control (Profile DND toggle).
    if "quiet_start" in sent or "quiet_end" in sent:
        raise HTTPException(
            status_code=403,
            detail=(
                "Working Hours are managed by your administrator. "
                "Use the DND toggle to opt in/out of receiving alerts "
                "outside your shift, or ask your admin to update your "
                "schedule via Team Management."
            ),
        )
    if "dnd_enabled" in sent and body.dnd_enabled is not None:
        updates["dnd_enabled"] = body.dnd_enabled
    if body.display_name is not None:
        updates["display_name"] = body.display_name

    if not updates:
        raise HTTPException(status_code=422, detail="No fields to update")

    await platform_db.update_user(db_user.id, **updates)
    return {"ok": True, "updated": list(updates.keys())}


# ── Account lifecycle: owner self-delete (90-day grace) ─────────────
#
# Three-step flow, all owner-gated:
#   1. POST /user/account/delete/request  → 6-digit code emailed
#   2. POST /user/account/delete/confirm  → code consumed, account
#      deactivated, purge scheduled at +90d, all sessions revoked
#   3. POST /user/account/delete/cancel   → within the grace window,
#      restores the account (owner can still log in during grace —
#      see the role-aware gate in auth.mint_session_token)
#
# GET /user/account/lifecycle feeds the dashboard's Danger Zone card.

import logging as _logging

_lifecycle_log = _logging.getLogger("api.user.lifecycle")


def _require_owner(user: dict) -> None:
    if user.get("role") != "owner":
        raise HTTPException(
            status_code=403,
            detail="Only the account owner can manage account deletion.",
        )


def _require_primary_owner(db_user) -> None:
    """Destructive account actions (delete / cancel) are PRIMARY-owner only.
    Co-owners (role=owner, is_primary_owner=False) are blocked — the nuclear
    button stays with the one primary owner."""
    if not db_user or not getattr(db_user, "is_primary_owner", False):
        raise HTTPException(
            status_code=403,
            detail="Only the primary owner can manage account deletion.",
        )


@router.get("/account/lifecycle")
async def account_lifecycle(
    user: dict = Depends(get_current_user),
    platform_db=Depends(get_platform_db),
):
    """Lifecycle state for the current account — drives the Danger
    Zone card (idle / deletion-pending + countdown)."""
    lc = await platform_db.get_account_lifecycle(user["account_id"])
    if not lc:
        raise HTTPException(status_code=404, detail="Account not found")
    # Suspension audit fields are operator-internal; the customer only
    # needs the booleans + dates that drive their UI.
    return {
        "account_id":   lc["account_id"],
        "name":         lc["name"],
        "is_active":    lc["is_active"],
        "suspended":    bool(lc["suspended_at"]),
        "deleted_at":   lc["deleted_at"],
        "purge_at":     lc["purge_at"],
    }


@router.post("/account/delete/request")
async def account_delete_request(
    user: dict = Depends(get_current_user),
    platform_db=Depends(get_platform_db),
):
    """Step 1: mint + email the 6-digit confirmation code."""
    _require_owner(user)
    db_user = await get_current_db_user(user, platform_db)
    _require_primary_owner(db_user)
    if not db_user or not db_user.email:
        raise HTTPException(
            status_code=422,
            detail="Your profile has no verified email — add one in "
                   "Profile settings before deleting the account.",
        )

    lc = await platform_db.get_account_lifecycle(user["account_id"])
    if lc and lc.get("deleted_at"):
        raise HTTPException(
            status_code=409, detail="Deletion is already scheduled.",
        )

    code = await platform_db.create_deletion_code(
        user["account_id"], db_user.id, ttl_minutes=15,
    )
    acct = await platform_db.get_account(user["account_id"])
    from capabilities.email.lifecycle_emails import send_deletion_code_email
    sent = send_deletion_code_email(
        to=db_user.email, code=code,
        account_name=acct.name if acct else "",
        recipient_name=db_user.display_name or "",
    )
    return {
        "status": "code_sent",
        "email": db_user.email,
        "expires_minutes": 15,
        "email_sent": sent,
    }


class DeleteConfirmRequest(BaseModel):
    code: str = Field(..., min_length=6, max_length=6)


@router.post("/account/delete/confirm")
async def account_delete_confirm(
    body: DeleteConfirmRequest,
    user: dict = Depends(get_current_user),
    platform_db=Depends(get_platform_db),
):
    """Step 2: verify the code, schedule the purge, revoke sessions."""
    _require_owner(user)
    db_user = await get_current_db_user(user, platform_db)
    if not db_user:
        raise HTTPException(status_code=404, detail="User not found")
    _require_primary_owner(db_user)

    ok = await platform_db.consume_deletion_code(
        user["account_id"], db_user.id, body.code.strip(),
    )
    if not ok:
        raise HTTPException(
            status_code=400,
            detail="Invalid or expired code. Request a new one and try again.",
        )

    purge_at = await platform_db.request_account_deletion(
        user["account_id"], owner_tg_id=db_user.id, grace_days=90,
    )
    if purge_at is None:
        raise HTTPException(status_code=409, detail="Deletion already scheduled.")

    # Kill every session in the account.  The owner can sign back in
    # during the grace window (mint_session_token allows role=owner) —
    # everyone else is locked out immediately.
    revoked = await platform_db.revoke_account_sessions(user["account_id"])
    from interfaces.api.auth import mark_jti_revoked
    for row in revoked:
        try:
            await mark_jti_revoked(row["jti"], row.get("expires_at"))
        except Exception:
            _lifecycle_log.exception("delete-confirm: denylist push failed")

    try:
        await platform_db.add_platform_audit(
            "account_deletion_requested",
            account_id=user["account_id"],
            actor=f"owner:{db_user.id}",
            details=f"purge_at={purge_at} sessions_revoked={len(revoked)}",
        )
    except Exception:
        _lifecycle_log.exception("delete-confirm: audit write failed")

    acct = await platform_db.get_account(user["account_id"])
    if db_user.email:
        from capabilities.email.lifecycle_emails import (
            send_deletion_confirmed_email,
        )
        try:
            send_deletion_confirmed_email(
                to=db_user.email,
                account_name=acct.name if acct else "",
                purge_at=purge_at,
            )
        except Exception:
            _lifecycle_log.exception("delete-confirm: email failed")

    return {
        "status": "deletion_scheduled",
        "purge_at": purge_at,
        "grace_days": 90,
        "sessions_revoked": len(revoked),
    }


@router.post("/account/delete/cancel")
async def account_delete_cancel(
    user: dict = Depends(get_current_user),
    platform_db=Depends(get_platform_db),
):
    """Step 3 (optional): reactivate within the grace window."""
    _require_owner(user)
    db_user = await get_current_db_user(user, platform_db)
    _require_primary_owner(db_user)

    ok = await platform_db.cancel_account_deletion(user["account_id"])
    if not ok:
        raise HTTPException(
            status_code=409, detail="No deletion is pending for this account.",
        )

    try:
        await platform_db.add_platform_audit(
            "account_deletion_cancelled",
            account_id=user["account_id"],
            actor=f"owner:{db_user.id if db_user else 0}",
        )
    except Exception:
        _lifecycle_log.exception("delete-cancel: audit write failed")

    acct = await platform_db.get_account(user["account_id"])
    if db_user and db_user.email:
        from capabilities.email.lifecycle_emails import (
            send_deletion_cancelled_email,
        )
        try:
            send_deletion_cancelled_email(
                to=db_user.email, account_name=acct.name if acct else "",
            )
        except Exception:
            _lifecycle_log.exception("delete-cancel: email failed")

    return {"status": "reactivated", "account_id": user["account_id"]}
