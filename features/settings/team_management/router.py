"""Settings · Team Management — users, avatars, role/status, vehicle assignments, company access.

router.py is interface-layer code co-located with its feature
(docs/FEATURES.md): ONLY router.py may import interfaces.api.deps.
Keeps the historical ``/admin`` URL prefix.
"""
import asyncio
import logging
import os
import time

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field, model_validator
from typing import Optional

from interfaces.api.deps import (
    require_permission, get_current_db_user, get_tenant_db,
    get_platform_db, paginate, resolve_user_id,
)
from adapters.storage.models import Role
from capabilities.permissions.roles import (
    validate_role_change, role_rank, ASSIGNABLE_ROLES_PATTERN,
    role_supports_manager, role_tier,
)
from .service import _archive_driver_folders

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/admin", tags=["settings"])


# ── Users ─────────────────────────────────────────────────────

@router.get("/users")
async def list_users(
    page: int = Query(1, ge=1),
    page_size: int = Query(100, ge=1, le=500),
    role: str | None = Query(None, description="Filter by role"),
    search: str | None = Query(None, description="Substring match on display name / email"),
    user: dict = Depends(require_permission("can_manage_users")),
    platform_db=Depends(get_platform_db),
):
    """List users in the account with optional filtering and pagination.

    Truck and company assignments are looked up only for the page being
    returned, so the per-user N+1 stays bounded even on accounts with
    thousands of users.
    """
    users = await platform_db.list_account_users(user["account_id"])

    # In-memory filters before pagination — list size is bounded by tenant
    # roster which sits in the low thousands at worst. If/when this grows
    # large enough to matter, push these into the SQL query.
    if role:
        users = [u for u in users if (u.role.value if hasattr(u.role, "value") else u.role) == role]
    if search:
        q = search.lower()
        users = [
            u for u in users
            if q in (u.display_name or "").lower() or q in (u.email or "").lower()
        ]

    paged = paginate(users, page, page_size)
    page_users = paged["items"]

    # Per-user lookups only for the visible page — fan out concurrently.
    # Sequential per-user awaits turned a 50-user account into ~100 DB
    # roundtrips; gather() collapses that into one round-trip per user
    # (and the asyncpg pool serves them concurrently).
    if page_users:
        truck_results, company_results = await asyncio.gather(
            asyncio.gather(*(platform_db.get_user_vehicle_nums(u.id) for u in page_users)),
            asyncio.gather(*(platform_db.get_user_company_codes(u.id) for u in page_users)),
        )
        truck_map: dict[int, list[str]] = {
            u.id: t for u, t in zip(page_users, truck_results) if t
        }
        company_map: dict[int, list[str]] = {
            u.id: c for u, c in zip(page_users, company_results) if c
        }
    else:
        truck_map = {}
        company_map = {}

    return {
        "users": [
            {
                "id": u.id,
                "telegram_id": u.telegram_id,
                "display_name": u.display_name,
                "role": u.role.value if hasattr(u.role, "value") else u.role,
                # Manager tier (per-user seniority on the base role).
                # ``manager_capable`` tells the UI whether to OFFER the
                # Manager toggle at all (only roles with a MANAGER_GRANTS
                # entry have a tier).
                "is_manager": u.is_manager,
                "manager_capable": role_supports_manager(u.role),
                # Senior-tier label for this role (drives the toggle copy):
                # "Manager" for recruiter, "Full admin" for admin, else null.
                "tier_senior_label": (lambda t: t.senior_label if t else None)(role_tier(u.role)),
                # Owner tier: primary (main, un-demotable) vs co-owner.
                "is_primary_owner": u.is_primary_owner,
                "truck_num": u.truck_num,
                "trucks": truck_map.get(u.id, [u.truck_num] if u.truck_num else []),
                "allowed_companies": company_map.get(u.id, []),
                "is_active": u.is_active,
                "email": u.email,
                "language": u.language,
                "timezone": u.timezone,
                # Per-user quiet-hours override.  When ``quiet_start`` and
                # ``quiet_end`` are both non-null the user has a personal
                # override that wins over the role-level Working Hours
                # schedule.  Surfaced so the Team Management drawer can
                # show the override state + let operators edit it.
                "quiet_start": getattr(u, "quiet_start", None),
                "quiet_end": getattr(u, "quiet_end", None),
                # FK to a work_hours.id (migration 101) — admin-assigned
                # named schedule.  Wins over free-form quiet_start/end
                # when set; drawer dropdown reads this back to show which
                # schedule is currently selected.
                "assigned_work_hours_id": getattr(u, "assigned_work_hours_id", None),
                "created_at": getattr(u, "created_at", None),
            }
            for u in page_users
        ],
        "count": paged["total"],
        "page": paged["page"],
        "page_size": paged["page_size"],
        "total_pages": paged["total_pages"],
    }


# ── User avatar (Telegram profile photo) ─────────────────────

AVATAR_MAX_AGE = 86400  # re-fetch after 24 hours


@router.get("/users/{user_id}/avatar")
async def get_user_avatar(
    user_id: int,
    user: dict = Depends(require_permission("can_manage_users")),
    platform_db=Depends(get_platform_db),
):
    """Return Telegram profile photo for a user. Cached locally for 24h.

    Three response shapes:
      * 200 + image/jpeg — user has a photo (cached or fresh fetch)
      * 204 No Content — user has no photo (cached as a marker file
        so subsequent requests don't re-hit Telegram).  Browsers
        treat 204 as success → no red console spam for unphotoed users
      * 404 — caller asked for a user that doesn't exist in the account

    The 204 negative-cache fix matters: before this, every Team
    Management page load fired N Telegram API calls (where N = users
    without a photo) and the dashboard console showed N red 404 lines.
    """
    target = await platform_db.get_user(user_id)
    if not target or target.account_id != user["account_id"]:
        raise HTTPException(status_code=404, detail="User not found")

    from fastapi.responses import Response as _Response

    # Email-only users (no Telegram link) have no profile photo by
    # definition.  Returning 204 short-circuits before we try to
    # call ``get_user_profile_photos(user_id=None)`` which would
    # crash with a less-helpful TypeError inside the Telegram SDK.
    if not target.telegram_id:
        return _Response(status_code=204)

    from adapters.storage.object_store import get_object_store
    store = get_object_store()
    key = f"{target.telegram_id}.jpg"
    # Marker file for "we've checked Telegram and there's no photo"
    # — same TTL as the avatar cache, so a user who later uploads a
    # profile photo gets picked up on the next 24-hour cycle.
    none_key = f"{target.telegram_id}.none"

    # Serve from positive cache if fresh
    cached = store.local_path("avatars", key)
    if cached and (time.time() - os.path.getmtime(cached)) < AVATAR_MAX_AGE:
        return FileResponse(cached, media_type="image/jpeg")

    # Serve from negative cache if fresh — Telegram said "no photo"
    # within the last 24h, skip the API round-trip.
    cached_none = store.local_path("avatars", none_key)
    if cached_none and (time.time() - os.path.getmtime(cached_none)) < AVATAR_MAX_AGE:
        return _Response(status_code=204)

    # Fetch from Telegram via the SYSTEM bot — any bot can call
    # ``get_user_profile_photos`` for any user, but we anchor to the
    # operator-stable token so this works even when the login bot is
    # being rotated.
    try:
        from infra.config import TELEGRAM_SYSTEM_BOT_TOKEN as _token
        import telegram
        bot = telegram.Bot(token=_token)
        photos = await bot.get_user_profile_photos(user_id=target.telegram_id, limit=1)
        if not photos.photos:
            # Negative-cache the "no photo" answer so we don't pound
            # Telegram on every dashboard render.
            store.put("avatars", none_key, b"")
            return _Response(status_code=204)

        # Get the largest size of the first photo
        photo_sizes = photos.photos[0]
        best = max(photo_sizes, key=lambda s: s.width * s.height)
        file = await bot.get_file(best.file_id)
        # Download into a bytearray then persist via the object store so
        # the backend (disk / future S3) is the single source of truth.
        import io
        buf = io.BytesIO()
        await file.download_to_memory(out=buf)
        store.put("avatars", key, buf.getvalue())
        served = store.local_path("avatars", key)
        if not served:
            # Persist failed — treat as no-photo for this cycle
            # so we don't loop the operator into endless retries.
            store.put("avatars", none_key, b"")
            return _Response(status_code=204)
        return FileResponse(served, media_type="image/jpeg")
    except HTTPException:
        raise
    except Exception as exc:
        # Genuine fetch failure (network, Telegram 5xx, etc.) — log
        # and return 204 instead of 404.  Failing closed as "no photo"
        # is safer than spamming the console with reds; the next
        # request will retry once the negative-cache TTL expires.
        logger.warning("Failed to fetch avatar for user %s: %s", user_id, exc)
        return _Response(status_code=204)


class RoleUpdate(BaseModel):
    role: str = Field(..., pattern=ASSIGNABLE_ROLES_PATTERN)


@router.put("/users/{user_id}/role")
async def update_user_role(
    user_id: int,
    body: RoleUpdate,
    user: dict = Depends(require_permission("can_manage_users")),
    platform_db=Depends(get_platform_db),
    tenant_db=Depends(get_tenant_db),
):
    """Change a user's role. Cannot promote above own role."""
    target = await platform_db.get_user(user_id)
    if not target or target.account_id != user["account_id"]:
        raise HTTPException(status_code=404, detail="User not found")

    # The primary owner is immutable here; co-owners are managed via the
    # dedicated promote/demote-owner flow (owner is not an assignable role).
    if target.is_primary_owner:
        raise HTTPException(status_code=403, detail="The primary owner's role cannot be changed.")

    target_current_role = target.role.value if hasattr(target.role, "value") else target.role
    ok, reason = validate_role_change(user["role"], target_current_role, body.role)
    if not ok:
        if reason == "cant_modify_higher":
            raise HTTPException(status_code=403, detail="Cannot modify a user with equal or higher role")
        else:
            raise HTTPException(status_code=403, detail="Cannot assign a role equal to or above your own")

    ok = await platform_db.update_user(user_id, role=body.role)
    if ok:
        await tenant_db.add_audit_log(
            user["account_id"], int(user["sub"]),
            "role_change",
            target_type="user", target_id=str(user_id),
            details=f"Changed role to {body.role}",
        )
    return {"ok": ok}


class ManagerUpdate(BaseModel):
    is_manager: bool


@router.put("/users/{user_id}/manager")
async def update_user_manager(
    user_id: int,
    body: ManagerUpdate,
    user: dict = Depends(require_permission("can_manage_users")),
    platform_db=Depends(get_platform_db),
    tenant_db=Depends(get_tenant_db),
):
    """Set/clear a user's manager tier — a per-user seniority on the base
    role (capabilities/permissions/roles.MANAGER_GRANTS), NOT a role change.

    Rank-gated like role changes (can't modify a peer or higher).  Granting
    requires the base role to HAVE a manager tier; clearing is always fine.
    Enforcement picks up the change on the user's next token refresh (same
    propagation model as a role change); their /me view updates immediately.
    """
    target = await platform_db.get_user(user_id)
    if not target or target.account_id != user["account_id"]:
        raise HTTPException(status_code=404, detail="User not found")

    target_role = target.role.value if hasattr(target.role, "value") else target.role
    if role_rank(target_role) >= role_rank(user["role"]):
        raise HTTPException(status_code=403, detail="Cannot modify a user with equal or higher role")
    if body.is_manager and not role_supports_manager(target_role):
        raise HTTPException(status_code=400, detail=f"The {target_role} role has no manager tier")

    ok = await platform_db.update_user(user_id, is_manager=body.is_manager)
    if ok:
        await tenant_db.add_audit_log(
            user["account_id"], int(user["sub"]),
            "manager_tier_change",
            target_type="user", target_id=str(user_id),
            details=f"Set manager tier to {body.is_manager} ({target_role})",
        )
    return {"ok": ok}


# ── Co-owner promotion (primary-owner only; 2FA: password + email code) ──
# "Owner" is never assignable via the generic role endpoint (excluded from
# ASSIGNABLE_ROLES_PATTERN + blocked by rank).  Creating a co-owner is a
# deliberate, primary-owner-only action behind TWO factors: the primary
# owner's password AND a 6-digit code emailed to them.  A co-owner gets
# role='owner' but is_primary_owner=0 — full owner access, but they cannot
# create/remove owners or do destructive account actions.

async def _require_primary_owner(user: dict, platform_db):
    """Resolve + assert the caller is the PRIMARY owner; return their row.
    403 for everyone else (including co-owners)."""
    caller = await get_current_db_user(user, platform_db)
    if not caller or not caller.is_primary_owner:
        raise HTTPException(status_code=403, detail="Only the primary owner can manage owners.")
    return caller


class PromoteOwnerRequest(BaseModel):
    password: str = Field(..., min_length=1, max_length=256)


@router.post("/users/{user_id}/promote-owner")
async def promote_owner_request(
    user_id: int,
    body: PromoteOwnerRequest,
    user: dict = Depends(require_permission("can_manage_users")),
    platform_db=Depends(get_platform_db),
):
    """Step 1 — the primary owner authorizes making an Admin a co-owner:
    verify their password, then email them a 6-digit confirmation code."""
    caller = await _require_primary_owner(user, platform_db)
    if not caller.password_hash:
        raise HTTPException(status_code=422, detail="Set a dashboard password (Profile → Sign-in methods) before managing owners.")
    if not caller.email:
        raise HTTPException(status_code=422, detail="Your profile has no email to send the confirmation code to.")

    from interfaces.api.auth import _verify_password
    if not _verify_password(body.password, caller.password_hash):
        raise HTTPException(status_code=403, detail="Password incorrect.")

    target = await platform_db.get_user(user_id)
    if not target or target.account_id != user["account_id"]:
        raise HTTPException(status_code=404, detail="User not found")
    target_role = target.role.value if hasattr(target.role, "value") else target.role
    if target_role != "admin":
        raise HTTPException(status_code=400, detail="Only an Admin can be promoted to co-owner.")

    code = await platform_db.create_owner_promotion_code(
        user["account_id"], caller.id, target.id, ttl_minutes=15,
    )
    acct = await platform_db.get_account(user["account_id"])
    from capabilities.email.lifecycle_emails import send_owner_promotion_code_email
    sent = send_owner_promotion_code_email(
        to=caller.email, code=code,
        account_name=acct.name if acct else "",
        recipient_name=caller.display_name or "",
        target_name=target.display_name or (target.email or ""),
    )
    return {"status": "code_sent", "email": caller.email, "expires_minutes": 15, "email_sent": sent}


class PromoteOwnerConfirm(BaseModel):
    code: str = Field(..., min_length=6, max_length=6)


@router.post("/users/{user_id}/promote-owner/confirm")
async def promote_owner_confirm(
    user_id: int,
    body: PromoteOwnerConfirm,
    user: dict = Depends(require_permission("can_manage_users")),
    platform_db=Depends(get_platform_db),
    tenant_db=Depends(get_tenant_db),
):
    """Step 2 — verify the emailed code and apply the promotion (Admin →
    co-owner).  The code is bound to this initiator + target."""
    caller = await _require_primary_owner(user, platform_db)
    target_id = await platform_db.consume_owner_promotion_code(
        user["account_id"], caller.id, body.code.strip(),
    )
    if target_id is None or int(target_id) != int(user_id):
        raise HTTPException(status_code=400, detail="Invalid or expired code. Request a new one and try again.")

    target = await platform_db.get_user(user_id)
    if not target or target.account_id != user["account_id"]:
        raise HTTPException(status_code=404, detail="User not found")
    target_role = target.role.value if hasattr(target.role, "value") else target.role
    if target_role != "admin":
        raise HTTPException(status_code=409, detail="That user is no longer an Admin.")

    ok = await platform_db.update_user(user_id, role="owner", is_primary_owner=False)
    if ok:
        from capabilities.permissions.roles import invalidate_permissions_cache
        invalidate_permissions_cache(user["account_id"])
        await tenant_db.add_audit_log(
            user["account_id"], int(user["sub"]),
            "co_owner_added",
            target_type="user", target_id=str(user_id),
            details=f"Promoted {target.display_name or target.email or user_id} to co-owner",
        )
    return {"ok": ok}


class DemoteOwnerRequest(BaseModel):
    password: str = Field(..., min_length=1, max_length=256)


@router.post("/users/{user_id}/demote-owner")
async def demote_owner(
    user_id: int,
    body: DemoteOwnerRequest,
    user: dict = Depends(require_permission("can_manage_users")),
    platform_db=Depends(get_platform_db),
    tenant_db=Depends(get_tenant_db),
):
    """Remove a co-owner (→ Admin).  Primary-owner only, password-confirmed.
    The primary owner can never be demoted here."""
    caller = await _require_primary_owner(user, platform_db)
    if not caller.password_hash:
        raise HTTPException(status_code=422, detail="Set a dashboard password before managing owners.")
    from interfaces.api.auth import _verify_password
    if not _verify_password(body.password, caller.password_hash):
        raise HTTPException(status_code=403, detail="Password incorrect.")

    target = await platform_db.get_user(user_id)
    if not target or target.account_id != user["account_id"]:
        raise HTTPException(status_code=404, detail="User not found")
    target_role = target.role.value if hasattr(target.role, "value") else target.role
    if target_role != "owner":
        raise HTTPException(status_code=400, detail="That user is not an owner.")
    if target.is_primary_owner:
        raise HTTPException(status_code=403, detail="The primary owner cannot be removed.")

    ok = await platform_db.update_user(user_id, role="admin")
    if ok:
        from capabilities.permissions.roles import invalidate_permissions_cache
        invalidate_permissions_cache(user["account_id"])
        await tenant_db.add_audit_log(
            user["account_id"], int(user["sub"]),
            "co_owner_removed",
            target_type="user", target_id=str(user_id),
            details=f"Removed co-owner {target.display_name or user_id} (→ admin)",
        )
    return {"ok": ok}


class UserDeactivate(BaseModel):
    is_active: bool


class SamsaraDriverIdUpdate(BaseModel):
    samsara_driver_id: Optional[str] = Field(default=None, max_length=128)


@router.put("/users/{user_id}/samsara-driver-id")
async def update_user_samsara_driver_id(
    user_id: int,
    body: SamsaraDriverIdUpdate,
    user: dict = Depends(require_permission("can_manage_users")),
    platform_db=Depends(get_platform_db),
    tenant_db=Depends(get_tenant_db),
):
    """Bind a Telegram user to a Samsara driver_id. Required before the
    user's /coaching/me and /payroll/me endpoints will return any data —
    these endpoints used to infer the binding from "most recent safety
    event by truck", which leaked another driver's data after a vehicle
    was reassigned. Setting NULL unbinds the user."""
    target = await platform_db.get_user(user_id)
    if not target or target.account_id != user["account_id"]:
        raise HTTPException(status_code=404, detail="User not found")

    caller_rank = role_rank(user["role"])
    existing_rank = role_rank(
        target.role.value if hasattr(target.role, "value") else target.role
    )
    if existing_rank >= caller_rank:
        raise HTTPException(status_code=403, detail="Cannot modify a user with equal or higher role")

    new_did = (body.samsara_driver_id or "").strip() or None
    ok = await platform_db.update_user(user_id, samsara_driver_id=new_did)
    if ok:
        await tenant_db.add_audit_log(
            user["account_id"], int(user["sub"]),
            "user_samsara_driver_id_set",
            target_type="user", target_id=str(user_id),
            details=f"driver_id={new_did or '(unset)'}",
        )
    return {"ok": ok, "samsara_driver_id": new_did}


class WorkHoursOverrideUpdate(BaseModel):
    # Per-user Working Hours override.  Field names ``quiet_start`` /
    # ``quiet_end`` are historical (the underlying columns kept their
    # original names to avoid a backfill migration), but semantically
    # they define the ACTIVE window — when alerts DELIVER, not when
    # they're silenced.  Outside the window non-critical alerts queue
    # until shift-start.
    #
    # ``None`` on either field clears that side of the override; both
    # ``None`` clears the override entirely so the user inherits the
    # role-level Working Hours.  Half-set (one null, one int) is
    # rejected: a one-sided window has no meaning (start without end
    # never closes, end without start never opens).  Hours are 0-23
    # in the user's effective timezone.
    quiet_start: Optional[int] = Field(default=None, ge=0, le=23)
    quiet_end:   Optional[int] = Field(default=None, ge=0, le=23)


@router.put("/users/{user_id}/quiet-hours")
async def update_user_work_hours_override(
    user_id: int,
    body: WorkHoursOverrideUpdate,
    user: dict = Depends(require_permission("can_manage_users")),
    platform_db=Depends(get_platform_db),
    tenant_db=Depends(get_tenant_db),
):
    """Set or clear a user's per-user Working Hours override.

    Defines the active window during which alerts DELIVER to this user;
    outside the window non-critical alerts queue until shift-start.
    Three valid states:

      * ``{quiet_start: H, quiet_end: H}`` → personal override active;
        wraps across midnight when start >= end (night-shift case).
      * ``{quiet_start: null, quiet_end: null}`` → clear override; user
        inherits the role-level Working Hours (or alerts 24/7 when
        neither is configured).
      * any half-set combo (one null, one int) → 422.

    URL path stays ``/quiet-hours`` for backward compat with existing
    integrations / docs / audit-log filters.  Renaming the route would
    break any third-party caller bound to the old path.

    Same caller gate as role-change + deactivate (``can_manage_users``)
    plus a rank check so HR/Fleet can't shift the working window of an
    Admin / Owner.  Audit-logged because shifting a teammate's
    on-call window is sensitive — operators should be accountable.
    """
    target = await platform_db.get_user(user_id)
    if not target or target.account_id != user["account_id"]:
        raise HTTPException(status_code=404, detail="User not found")

    caller_rank = role_rank(user["role"])
    target_rank = role_rank(
        target.role.value if hasattr(target.role, "value") else target.role
    )
    if target_rank >= caller_rank:
        raise HTTPException(
            status_code=403,
            detail="Cannot modify Working Hours for a user with equal or higher role",
        )

    half_set = (body.quiet_start is None) != (body.quiet_end is None)
    if half_set:
        raise HTTPException(
            status_code=422,
            detail="quiet_start and quiet_end must both be set, or both cleared",
        )

    await platform_db.update_user(
        user_id,
        quiet_start=body.quiet_start,
        quiet_end=body.quiet_end,
    )

    cleared = body.quiet_start is None and body.quiet_end is None
    detail = (
        "cleared (inherits role Working Hours)"
        if cleared
        else f"alerts deliver {body.quiet_start:02d}:00 – {body.quiet_end:02d}:00"
    )
    await tenant_db.add_audit_log(
        user["account_id"], int(user["sub"]),
        # Action name kept ``user_quiet_hours_set`` so existing audit-
        # log searches / dashboards continue to surface this row.
        # Renaming would orphan historical rows under the old action.
        "user_quiet_hours_set",
        target_type="user", target_id=str(user_id),
        details=detail,
    )
    return {
        "ok": True,
        "quiet_start": body.quiet_start,
        "quiet_end": body.quiet_end,
    }


class AssignedWorkHoursUpdate(BaseModel):
    # ``None`` clears the assignment (user falls back to role-level
    # Working Hours).  Integer must reference a ``work_hours.id`` that
    # belongs to the caller's account — validated below; a stale or
    # cross-account FK gets a 404 rather than corrupting the user row.
    schedule_id: Optional[int] = Field(default=None)


@router.put("/users/{user_id}/assigned-work-hours")
async def update_user_assigned_work_hours(
    user_id: int,
    body: AssignedWorkHoursUpdate,
    user: dict = Depends(require_permission("can_manage_users")),
    platform_db=Depends(get_platform_db),
    tenant_db=Depends(get_tenant_db),
):
    """Assign a user to a named Working Hours schedule from the catalog,
    or clear the assignment so they inherit the role-level schedule.

    The schedule selector in the Team Management drawer Settings tab
    posts here.  Replaces the older free-form quiet-hours picker — the
    Working Hours table is the single source of truth for shift
    definitions; per-user customization happens by POINTING at a row
    instead of duplicating its hours into the user row.

    Same caller gate as role-change + deactivate (``can_manage_users``)
    plus a rank check so HR/Fleet can't reshape an Admin/Owner's
    schedule.  Schedule ownership validated to prevent cross-account
    pointers.  Audit-logged because shifting someone's on-call window
    is sensitive.
    """
    target = await platform_db.get_user(user_id)
    if not target or target.account_id != user["account_id"]:
        raise HTTPException(status_code=404, detail="User not found")

    caller_rank = role_rank(user["role"])
    target_rank = role_rank(
        target.role.value if hasattr(target.role, "value") else target.role
    )
    if target_rank >= caller_rank:
        raise HTTPException(
            status_code=403,
            detail="Cannot modify Working Hours for a user with equal or higher role",
        )

    schedule_label = "cleared (inherits role schedule)"
    if body.schedule_id is not None:
        # Validate the schedule belongs to this account — prevents an
        # operator from pointing one of their users at another tenant's
        # row (defence in depth; the per-account scope on the storage
        # method below would already filter, but explicit > implicit).
        sched = await tenant_db.get_work_hour(body.schedule_id, user["account_id"])
        if not sched:
            raise HTTPException(
                status_code=404,
                detail="Schedule not found in this account's Working Hours",
            )
        schedule_label = (
            f"{sched.get('label', '#' + str(body.schedule_id))} "
            f"({int(sched.get('start_hour', 0)):02d}:00 – "
            f"{int(sched.get('end_hour', 0)):02d}:00)"
        )

    await platform_db.update_user(
        user_id,
        assigned_work_hours_id=body.schedule_id,
    )

    await tenant_db.add_audit_log(
        user["account_id"], int(user["sub"]),
        "user_assigned_work_hours_set",
        target_type="user", target_id=str(user_id),
        details=f"assigned schedule: {schedule_label}",
    )
    return {
        "ok": True,
        "assigned_work_hours_id": body.schedule_id,
    }


@router.put("/users/{user_id}/status")
async def update_user_status(
    user_id: int,
    body: UserDeactivate,
    user: dict = Depends(require_permission("can_manage_users")),
    platform_db=Depends(get_platform_db),
    tenant_db=Depends(get_tenant_db),
):
    """Activate or deactivate a user."""
    target = await platform_db.get_user(user_id)
    if not target or target.account_id != user["account_id"]:
        raise HTTPException(status_code=404, detail="User not found")

    # The primary owner can never be deactivated (lockout / orphaned-account
    # protection).
    if target.is_primary_owner:
        raise HTTPException(status_code=403, detail="The primary owner cannot be deactivated.")

    caller_rank = role_rank(user["role"])
    existing_rank = role_rank(
        target.role.value if hasattr(target.role, "value") else target.role
    )
    if existing_rank >= caller_rank:
        raise HTTPException(status_code=403, detail="Cannot modify a user with equal or higher role")

    ok = await platform_db.update_user(user_id, is_active=body.is_active)
    if ok:
        action = "user_activate" if body.is_active else "user_deactivate"
        await tenant_db.add_audit_log(
            user["account_id"], int(user["sub"]),
            action,
            target_type="user", target_id=str(user_id),
        )
    return {"ok": ok}


# ── Vehicle assignments ─────────────────────────────────────────

class VehicleAssignment(BaseModel):
    trucks: list[str] = Field(..., max_length=200)  # kept as 'trucks' for API compat


@router.get("/users/{user_id}/trucks")
async def get_user_vehicles(
    user_id: int,
    user: dict = Depends(require_permission("can_manage_users")),
    platform_db=Depends(get_platform_db),
):
    """Get all vehicle assignments for a user."""
    target = await platform_db.get_user(user_id)
    if not target or target.account_id != user["account_id"]:
        raise HTTPException(status_code=404, detail="User not found")

    vehicles = await platform_db.get_user_vehicles(user_id)
    return {
        "user_id": user_id,
        "trucks": [
            {
                "vehicle_num": t.vehicle_num,
                "is_primary": t.is_primary,
                "assigned_at": t.assigned_at,
            }
            for t in vehicles
        ],
        "legacy_truck_num": target.truck_num,
    }


@router.put("/users/{user_id}/trucks")
async def set_user_vehicles(
    user_id: int,
    body: VehicleAssignment,
    user: dict = Depends(require_permission("can_manage_users")),
    platform_db=Depends(get_platform_db),
    tenant_db=Depends(get_tenant_db),
):
    """Set vehicle assignments for a user. First vehicle in list is primary."""
    target = await platform_db.get_user(user_id)
    if not target or target.account_id != user["account_id"]:
        raise HTTPException(status_code=404, detail="User not found")

    # Validate: non-empty strings only
    cleaned = [t.strip() for t in body.trucks if t.strip()]
    if len(cleaned) != len(set(cleaned)):
        raise HTTPException(status_code=400, detail="Duplicate vehicle numbers")

    vehicles = await platform_db.set_user_vehicles(
        user_id, target.account_id, cleaned, assigned_by=int(user["sub"]),
    )

    await tenant_db.add_audit_log(
        user["account_id"], int(user["sub"]),
        "vehicle_assignment",
        target_type="user", target_id=str(user_id),
        details=f"Vehicles: {', '.join(cleaned) or 'none'}",
    )

    return {
        "ok": True,
        "trucks": [
            {"vehicle_num": t.vehicle_num, "is_primary": t.is_primary}
            for t in vehicles
        ],
    }


# ── Company access ────────────────────────────────────────────

class CompanyAssignment(BaseModel):
    company_ids: list[int] = Field(default_factory=list, description="Company IDs to grant access to. Empty = all companies.")


@router.get("/users/{user_id}/companies")
async def get_user_companies(
    user_id: int,
    user: dict = Depends(require_permission("can_manage_users")),
    platform_db=Depends(get_platform_db),
    tenant_db=Depends(get_tenant_db),
):
    """Get company access assignments for a user."""
    target = await platform_db.get_user(user_id)
    if not target or target.account_id != user["account_id"]:
        raise HTTPException(status_code=404, detail="User not found")

    assignments = await platform_db.get_user_companies(user_id)
    all_companies = await tenant_db.get_account_companies(user["account_id"])

    return {
        "user_id": user_id,
        "companies": [
            {
                "company_id": a.company_id,
                "company_code": a.company_code,
                "assigned_at": a.assigned_at,
            }
            for a in assignments
        ],
        "all_companies": [
            {"id": c.id, "code": c.code, "display_name": c.display_name}
            for c in all_companies
        ],
        "unrestricted": len(assignments) == 0,
    }


@router.put("/users/{user_id}/companies")
async def set_user_companies(
    user_id: int,
    body: CompanyAssignment,
    user: dict = Depends(require_permission("can_manage_users")),
    platform_db=Depends(get_platform_db),
    tenant_db=Depends(get_tenant_db),
):
    """Set company access for a user. Empty list = access to all companies.

    Drivers are restricted to a single company at a time.  When a
    driver's assignment changes (Company A → Company B), the existing
    driver documents folder is moved to
    ``{Company A}/drivers/_archive/{YYYY-MM-DD}/user-{id}/`` so the
    new company's folder starts empty but the carrier retains a dated
    audit trail of records that were on file before the change.
    """
    target = await platform_db.get_user(user_id)
    if not target or target.account_id != user["account_id"]:
        raise HTTPException(status_code=404, detail="User not found")

    # Owners always have access to all companies — don't restrict them
    target_role = target.role.value if hasattr(target.role, "value") else target.role
    if target_role == "owner":
        raise HTTPException(status_code=400, detail="Cannot restrict company access for owners")

    # Drivers can only be assigned to one company at a time.  Multi-
    # company drivers would split DOT 49 CFR Part 391 driver-qualification
    # files across carriers in ways the archive flow can't reconcile.
    if target_role == "driver" and len(body.company_ids) > 1:
        raise HTTPException(
            status_code=400,
            detail=(
                "A driver can only be assigned to one company at a time. "
                "Reassign to a single company; the previous folder will "
                "be archived automatically."
            ),
        )

    # Validate company IDs belong to this account
    if body.company_ids:
        all_companies = await tenant_db.get_account_companies(user["account_id"])
        valid_ids = {c.id for c in all_companies}
        invalid = [cid for cid in body.company_ids if cid not in valid_ids]
        if invalid:
            raise HTTPException(status_code=400, detail=f"Invalid company IDs: {invalid}")

    # Capture the BEFORE state so we know which companies the driver
    # is leaving and can archive each one's folder before the
    # assignment row is rewritten by set_user_companies().
    archived_companies: list[str] = []
    if target_role == "driver":
        old_assignments = await platform_db.get_user_companies(user_id)
        new_company_ids = set(body.company_ids)
        codes_being_removed = [
            a.company_code for a in old_assignments
            if a.company_id not in new_company_ids
        ]
        if codes_being_removed:
            archived_companies = await _archive_driver_folders(
                platform_db, tenant_db, user["account_id"],
                user_id, codes_being_removed,
            )

    await platform_db.set_user_companies(
        user_id, target.account_id, body.company_ids,
        assigned_by=int(user["sub"]),
    )

    audit_details = f"Companies: {body.company_ids or 'all (unrestricted)'}"
    if archived_companies:
        audit_details += f"; archived from: {archived_companies}"
    await tenant_db.add_audit_log(
        user["account_id"], int(user["sub"]),
        "company_assignment",
        target_type="user", target_id=str(user_id),
        details=audit_details,
    )

    return {
        "ok": True,
        "company_ids": body.company_ids,
        "unrestricted": len(body.company_ids) == 0,
        "archived_companies": archived_companies,
    }


