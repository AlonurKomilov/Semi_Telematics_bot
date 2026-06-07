"""Admin API endpoints — users, companies, invites, audit log, settings, schedules,
scorecard rules + pillar caps (driver-facing scorecards live under /safety)."""

import asyncio
import json
import logging
import os
import time
from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field, model_validator
from typing import Optional

from interfaces.api.deps import require_permission, get_current_db_user, get_tenant_db, get_platform_db, paginate, resolve_user_id
from adapters.storage.models import Role
from capabilities.iam.permissions import validate_role_change, role_rank
from capabilities.scoring.rules import get_default_rules as _get_default_rules

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/admin", tags=["admin"])


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
                "department": u.department,
                "truck_num": u.truck_num,
                "trucks": truck_map.get(u.id, [u.truck_num] if u.truck_num else []),
                "allowed_companies": company_map.get(u.id, []),
                "is_active": u.is_active,
                "email": u.email,
                "language": u.language,
                "timezone": u.timezone,
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
    """Return Telegram profile photo for a user. Cached locally for 24h."""
    target = await platform_db.get_user(user_id)
    if not target or target.account_id != user["account_id"]:
        raise HTTPException(status_code=404, detail="User not found")

    from adapters.storage.object_store import get_object_store
    store = get_object_store()
    key = f"{target.telegram_id}.jpg"

    # Serve from cache if fresh
    cached = store.local_path("avatars", key)
    if cached and (time.time() - os.path.getmtime(cached)) < AVATAR_MAX_AGE:
        return FileResponse(cached, media_type="image/jpeg")

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
            raise HTTPException(status_code=404, detail="No profile photo")

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
            raise HTTPException(status_code=404, detail="Could not fetch avatar")
        return FileResponse(served, media_type="image/jpeg")
    except HTTPException:
        raise
    except Exception as exc:
        logger.warning("Failed to fetch avatar for user %s: %s", user_id, exc)
        raise HTTPException(status_code=404, detail="Could not fetch avatar")


class RoleUpdate(BaseModel):
    role: str = Field(..., pattern=r"^(owner|admin|fleet|safety|dispatcher|driver)$")


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


async def _archive_driver_folders(
    platform_db, tenant_db, account_id: int,
    user_id: int, removed_company_codes: list[str],
) -> list[str]:
    """Move the driver's docs folder under each removed company to
    that company's ``_archive/{date}/`` subtree.  Updates the bucket
    column on existing driver_documents rows so the download/delete
    routes can still find the files after the move.

    Returns the list of company codes whose folders were actually
    archived (i.e. had a non-empty source folder).  Errors during the
    physical move are logged but don't fail the assignment write —
    the company-change should always succeed at the DB level; storage
    cleanup is best-effort.
    """
    from datetime import datetime, timezone
    from adapters.storage.object_store import get_object_store_for_account
    from capabilities.work_orders.storage import (
        driver_docs_archive_bucket, driver_docs_bucket, resolve_company_folder,
    )

    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    store = await get_object_store_for_account(account_id, tenant_db)
    archived: list[str] = []

    for company_code in removed_company_codes:
        try:
            company_folder = await resolve_company_folder(
                tenant_db, account_id, company_code,
            )
            src = driver_docs_bucket(company_folder, user_id)
            dst = driver_docs_archive_bucket(company_folder, user_id, today)
            moved = store.move_folder(src, dst)
            if moved:
                await platform_db.move_user_documents_bucket(user_id, src, dst)
                archived.append(company_code)
        except Exception as e:
            logger.warning(
                "Archive failed for driver=%d company=%s: %s",
                user_id, company_code, e,
            )
    return archived


# ── Invites ───────────────────────────────────────────────────

# Plain RFC-5321-shaped regex — sufficient for transactional email
# validation without dragging the ``email-validator`` package into
# requirements.txt.  Matches the existing convention at
# interfaces/api/auth.py:_EMAIL_RE (single source of truth would be
# nice; deferred to a tidy-up pass).
_INVITE_EMAIL_RE = __import__("re").compile(
    r"^[A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,}$"
)


class InviteCreate(BaseModel):
    role: str = Field("fleet", pattern=r"^(admin|fleet|safety|dispatcher|driver)$")
    department: str = "general"
    truck_num: Optional[str] = None
    hours: int = Field(24, ge=1, le=720)
    # Email-channel: when present, the server generates the invite
    # AND attempts to send it via SMTP in the same request.  When
    # absent, behaviour is unchanged (link-channel only).  Plain
    # string with regex validation in the route handler (not pydantic
    # EmailStr — that would require the email-validator package which
    # is NOT in requirements.txt and would crash app boot).
    recipient_email: Optional[str] = None


async def _invite_email_rate_check(
    account_id: int, actor_sub: str,
    recipient: Optional[str] = None,
) -> bool:
    """Outbound-email rate limit, SEPARATE from invite_mutate.

    Four buckets, ordered so the most-likely-to-fire CHEAPEST check
    runs first.  Recipient-level is the most protective (one bad
    address can't be hammered) — checked early.

      1. PER-RECIPIENT, per-account (3/24h):
         invite_email_recipient:{account_id}:{sha256(recipient)[:16]}
         Stops an operator from re-mailing the same address inside one
         account.  Hashed (no plaintext PII in Redis) + lowercase-
         normalized (case-insensitive bucket).
      2. PER-RECIPIENT, global (8/24h):
         invite_email_recipient_global:{sha256(recipient)[:16]}
         Caps cross-account abuse — a leaked admin token at one account
         can't use 4truck's relay reputation to harass an external
         mailbox once another account already mailed them today.
      3. PER-ACTOR per-minute (5/min):
         invite_email_send:{account_id}:{actor_sub}
         Burst protection for one compromised admin token.
      4. PER-ACCOUNT per-day (50/day):
         invite_email_send_daily:{account_id}
         Damage cap if buckets 1-3 are bypassed somehow.

    Fail CLOSED on Redis outage — the default ``rate_limit_check``
    fails open which is correct for read-mostly endpoints but
    disastrously wrong for outbound mail (a Redis blip would lift
    the cap entirely and let a compromised admin token blast).

    Fixed-window cliff acknowledged: a recipient hit 3 times at 23:59
    can take 3 more at 00:00.  Sliding window would need a sorted-
    set redesign in infra/cache.py — deferred.  The cliff is
    bounded by the per-actor cap (5/min) so the worst-case scenario
    is ~7-8 sends crossing midnight to the same recipient, which is
    still inside the global 8/24h ceiling.
    """
    from adapters.cache.redis import rate_limit_check, is_available as _redis_ok
    if not _redis_ok():
        return False
    if recipient:
        import hashlib as _hashlib
        rcp_hash = _hashlib.sha256(
            recipient.strip().lower().encode("utf-8"),
        ).hexdigest()[:16]
        # Per-(account, recipient) — protects against intra-account
        # spam to one address.
        if not await rate_limit_check(
            f"invite_email_recipient:{account_id}:{rcp_hash}",
            window_secs=24 * 60 * 60, max_requests=3,
        ):
            return False
        # Global per-recipient — cross-account abuse cap.
        if not await rate_limit_check(
            f"invite_email_recipient_global:{rcp_hash}",
            window_secs=24 * 60 * 60, max_requests=8,
        ):
            return False
    # Per-actor burst cap.
    per_actor_ok = await rate_limit_check(
        f"invite_email_send:{account_id}:{actor_sub}",
        window_secs=60, max_requests=5,
    )
    if not per_actor_ok:
        return False
    # Per-account daily cap.
    per_account_ok = await rate_limit_check(
        f"invite_email_send_daily:{account_id}",
        window_secs=24 * 60 * 60, max_requests=50,
    )
    return per_account_ok


@router.get("/invite/check-recipient")
async def check_invite_recipient(
    email: str,
    user: dict = Depends(require_permission("can_invite")),
    platform_db=Depends(get_platform_db),
):
    """Pre-create duplicate-recipient check.

    The dashboard calls this (debounced) as the operator types in
    the Email-channel recipient input.  When the email matches an
    active user in the SAME account, returns the matched user's
    display name + role so the operator gets an inline warning
    before submitting ("alice@example.com is already a member —
    Alice Smith, Driver").

    Scoped strictly to the operator's account — does NOT reveal
    cross-account existence (tenant isolation: the existence of
    alice@example.com at a competing fleet is not the operator's
    business to know).

    Rate-limited modestly (10/min per actor) to prevent the
    endpoint from becoming an in-account email-existence oracle
    via brute-force enumeration.  The recipient_email path of
    invite-create already runs ``_invite_email_rate_check`` at
    5/min so even a successful enumeration is capped at the same
    rate.
    """
    from adapters.cache.redis import rate_limit_check, is_available as _redis_ok
    if _redis_ok():
        ok = await rate_limit_check(
            f"check_recipient:{user['account_id']}:{user['sub']}",
            window_secs=60, max_requests=10,
        )
        if not ok:
            raise HTTPException(status_code=429, detail="Too many lookups")
    norm = (email or "").strip().lower()
    if not norm or not _INVITE_EMAIL_RE.match(norm):
        # Don't validate format here — the dashboard input has its
        # own format check.  Just return "no match" for anything
        # that wouldn't pass the create-time validator either.
        return {"exists": False}
    matched = await platform_db.get_user_by_email_in_account(
        norm, user["account_id"],
    )
    if not matched:
        return {"exists": False}
    return {
        "exists": True,
        "display_name": matched.display_name or "",
        "role": matched.role.value if hasattr(matched.role, "value") else str(matched.role),
    }


@router.post("/invite")
async def create_invite(
    body: InviteCreate,
    user: dict = Depends(require_permission("can_invite")),
    platform_db=Depends(get_platform_db),
    tenant_db=Depends(get_tenant_db),
):
    """Generate a new invite code; optionally send it via email."""
    caller_rank = role_rank(user["role"])
    target_rank = role_rank(body.role)
    if target_rank >= caller_rank:
        raise HTTPException(status_code=403, detail="Cannot create invite for role equal to or above your own")

    # Resolve DB user.id from telegram_id (JWT sub) — FK requires users.id
    db_user = await get_current_db_user(user, platform_db)
    if not db_user:
        raise HTTPException(status_code=404, detail="User not found")

    # ── Email-channel pre-flight checks ──────────────────────────
    # All checks run BEFORE create_invite so a 503/422/429 doesn't
    # leave an orphan link-channel row no one will use.  When the
    # operator chose email and pre-flight refuses, the invite isn't
    # created — they get a clean error instead of "created but..."
    recipient = (body.recipient_email or "").strip().lower() or None
    if recipient:
        # 1. Format validation
        if not _INVITE_EMAIL_RE.match(recipient):
            raise HTTPException(status_code=422, detail="Invalid recipient email")
        if "," in recipient or ";" in recipient:
            raise HTTPException(
                status_code=422,
                detail="One recipient per invite — create separate invites for each person",
            )
        # 2. SMTP must be configured.  Refuse 503 BEFORE create so
        #    we don't ship "sent_to_email NOT NULL + email_sent_at
        #    NULL" rows that lie about being email-channel.
        from capabilities.notifications.email import is_email_configured
        from capabilities.notifications.resend_transport import is_resend_api_enabled
        # Either provider satisfies the gate.  Resend-only deploys
        # (no SMTP relay) used to hit a misleading 503 about SMTP
        # being unconfigured; the OR honours both providers and the
        # error message names both paths so the operator can
        # self-diagnose.
        if not is_email_configured() and not is_resend_api_enabled():
            raise HTTPException(
                status_code=503,
                detail=(
                    "Email channel not configured — set MAIL_PROVIDER=resend + "
                    "RESEND_API_KEY (preferred, bounce-tracked) or SMTP_HOST + SMTP_FROM"
                ),
            )
        # 3. Rate limit — fail CLOSED on Redis outage.  Passes
        #    recipient so the per-recipient buckets engage.
        if not await _invite_email_rate_check(
            user["account_id"], user["sub"], recipient=recipient,
        ):
            raise HTTPException(
                status_code=429,
                detail="Too many invite emails — wait a moment and try again",
            )

    invite = await platform_db.create_invite(
        account_id=user["account_id"],
        created_by=db_user.id,
        role=Role(body.role),
        department=body.department,
        truck_num=body.truck_num,
        hours=body.hours,
        recipient_email=recipient,
    )

    # Email send happens AFTER the invite row commits — if SMTP
    # hands off successfully we mark email_sent_at; if it fails the
    # invite still exists with sent_to_email populated + null
    # timestamp, and the operator sees "created but email failed"
    # in the response.  The link in their clipboard works either
    # way (fallback path).
    email_status = None
    if recipient:
        try:
            from capabilities.notifications.auth_emails import send_invite_email_async
            account = await platform_db.get_account(user["account_id"])
            account_name = (account.name if account else "your team") or "your team"
            sent, resend_email_id = await send_invite_email_async(
                to=recipient,
                code=invite.code,
                account_id=user["account_id"],
                invite_id=invite.id,
                account_name=account_name,
                role_label=body.role.capitalize(),
                inviter_display_name=db_user.display_name or "Your inviter",
                expires_at=invite.expires_at,
                truck_num=body.truck_num,
            )
        except Exception as e:
            logger.warning("send_invite_email raised: %s", e, exc_info=True)
            sent = False
            resend_email_id = None
        if sent:
            updated = await platform_db.mark_invite_email_sent(
                user["account_id"], invite.id,
            )
            if updated is not None:
                invite = updated
            # Persist the Resend per-send identifier so the bounce
            # webhook handler can match events back to this invite
            # without trusting recipient-address (cross-account
            # hijack vector — see design vet).  SMTP-routed sends
            # have resend_email_id=None; bounce webhooks for SMTP
            # sends never reach us anyway.
            if resend_email_id:
                await platform_db.set_invite_resend_email_id(
                    user["account_id"], invite.id, resend_email_id,
                )
                invite.resend_email_id = resend_email_id
            email_status = "sent"
        else:
            email_status = "queued_failed"  # invite exists, email didn't

    # Audit log — single row per create.  Recipient stays out of
    # the free-text ``details`` field (the audit-log details column
    # has no length cap and recipient emails can be 254 chars +
    # operator-supplied wrapper text); we use a short marker and
    # rely on the invite row's sent_to_email for the actual address.
    detail_extras = ""
    if recipient:
        # Cross-domain heuristic: flag (warn-but-allow) when the
        # recipient's domain doesn't match the inviter's own email
        # domain.  Doesn't BLOCK — fleet onboarding legitimately
        # sends to drivers at various employer / personal addresses
        # — but does flag in the audit log so an operator (or an
        # ops sweep query) can spot a HR-tier user blasting
        # 4truck-branded mail to unrelated external domains.  This
        # is the cheap version of the design's full recipient-
        # domain allowlist (deferred — needs per-account config).
        recipient_domain = recipient.rpartition("@")[-1]
        inviter_email_domain = (
            (db_user.email or "").lower().rpartition("@")[-1]
            if getattr(db_user, "email", None)
            else ""
        )
        cross_domain_marker = ""
        if (
            recipient_domain
            and inviter_email_domain
            and recipient_domain != inviter_email_domain
        ):
            cross_domain_marker = ", cross_domain: true"
        detail_extras = (
            f", channel: email"
            f", email_status: {email_status}"
            f"{cross_domain_marker}"
        )
    await tenant_db.add_audit_log(
        user["account_id"], int(user["sub"]),
        "invite_create",
        target_type="invite", target_id=str(invite.id),
        details=(
            f"Role: {body.role}, dept: {body.department}{detail_extras}"
        )[:500],  # hard-cap defends against future operator-supplied text
    )
    return {
        "id": invite.id,
        "code": invite.code,
        "role": invite.role,
        "department": invite.department,
        "truck_num": invite.truck_num,
        "expires_at": invite.expires_at,
        "channel": invite.channel,
        "email_status": email_status,
        "sent_to_email": invite.sent_to_email,
    }


class InviteResendEmail(BaseModel):
    pass  # body intentionally empty for v1 — resend reuses original recipient


@router.post("/invites/{invite_id}/resend-email")
async def resend_invite_email_endpoint(
    invite_id: int,
    _body: InviteResendEmail,
    user: dict = Depends(require_permission("can_invite")),
    platform_db=Depends(get_platform_db),
    tenant_db=Depends(get_tenant_db),
):
    """Resend the invite-email to the same recipient.

    Refuses on used / revoked (uniform 404) and on expired
    (409 — extend it first, then resend).  Reuses the original
    recipient — no new-recipient flow in v1 because that changes
    who can redeem and needs its own audit shape.
    """
    # Pre-flight order: SMTP check is cheap; load invite next so the
    # recipient address is known BEFORE the rate-check (per-recipient
    # bucket needs it).  This re-orders from the earlier shape where
    # rate-check ran first — the design vet flagged this so the
    # per-recipient bucket can engage on resend too.
    from capabilities.notifications.email import is_email_configured
    if not is_email_configured():
        raise HTTPException(
            status_code=503,
            detail="Email channel not configured on this deployment",
        )

    invite = await platform_db.get_invite_by_id(user["account_id"], invite_id)
    if not invite:
        raise HTTPException(status_code=404, detail="Invite not found")
    if invite.is_used or invite.is_revoked:
        # Uniform 404 — same response as truly-missing, no
        # side-channel for which branch.
        raise HTTPException(status_code=404, detail="Invite not found")
    if invite.is_expired:
        # 409: the operator needs to extend first.  Distinguished
        # status code lets the dashboard render a one-button
        # "Extend + resend" flow.
        raise HTTPException(
            status_code=409,
            detail="Invite expired — extend it first, then resend",
        )
    if not invite.sent_to_email:
        # The invite was issued as link-channel.  No address on file.
        # v1 doesn't support upgrading link → email after the fact
        # (would need a new-recipient input + its own audit shape);
        # operator can revoke + recreate with email channel.
        raise HTTPException(
            status_code=409,
            detail="This invite was not issued via email — revoke and recreate with the email channel",
        )
    if invite.is_bounced:
        # Resending to a bounced address just bounces again.  The
        # operator must revoke + recreate with a corrected address —
        # the dashboard surfaces a "Revoke & recreate" affordance.
        raise HTTPException(
            status_code=409,
            detail="Recipient bounced — revoke and recreate with a corrected address",
        )

    # Per-recipient rate-check fires AFTER the invite load so the
    # recipient hash is available.
    if not await _invite_email_rate_check(
        user["account_id"], user["sub"], recipient=invite.sent_to_email,
    ):
        raise HTTPException(
            status_code=429,
            detail="Too many invite emails — wait a moment and try again",
        )

    # Rank check mirrors revoke/extend — HR can't keep an
    # Admin-tier invite alive past the Owner's intent.
    caller_rank = role_rank(user["role"])
    invite_rank = role_rank(invite.role)
    if invite_rank >= caller_rank:
        raise HTTPException(
            status_code=403,
            detail="Cannot resend an invite for a role equal to or above your own",
        )

    db_user = await get_current_db_user(user, platform_db)
    account = await platform_db.get_account(user["account_id"])
    account_name = (account.name if account else "your team") or "your team"
    try:
        from capabilities.notifications.auth_emails import send_invite_email_async
        sent, resend_email_id = await send_invite_email_async(
            to=invite.sent_to_email,
            code=invite.code,
            account_id=user["account_id"],
            invite_id=invite.id,
            account_name=account_name,
            role_label=invite.role.capitalize(),
            inviter_display_name=(
                (db_user.display_name if db_user else "Your inviter")
                or "Your inviter"
            ),
            expires_at=invite.expires_at,
            truck_num=invite.truck_num,
        )
    except Exception as e:
        logger.warning("send_invite_email raised on resend: %s", e, exc_info=True)
        sent = False
        resend_email_id = None

    if sent:
        updated = await platform_db.mark_invite_email_sent(
            user["account_id"], invite_id,
        )
        if updated is not None:
            invite = updated
        # Each resend gets a fresh Resend email_id (it's per-send,
        # not per-invite).  Overwrite — the most recent send is the
        # one the operator wants tracked for bounce events.
        if resend_email_id:
            await platform_db.set_invite_resend_email_id(
                user["account_id"], invite_id, resend_email_id,
            )
            invite.resend_email_id = resend_email_id
        outcome = "sent"
    else:
        outcome = "queued_failed"

    # Audit row uses ``invite_email_resent`` action so the audit-log
    # viewer renders "Invite email resent" (label added below).
    # ``attempt`` counter = the new email_send_count value, useful
    # forensic signal ("they resent this 4 times").
    await tenant_db.add_audit_log(
        user["account_id"], int(user["sub"]),
        "invite_email_resent",
        target_type="invite", target_id=str(invite_id),
        details=(
            f"Role: {invite.role}, dept: {invite.department}, "
            f"attempt: {invite.email_send_count}, outcome: {outcome}"
        )[:500],
    )
    if not sent:
        # The send failed but the audit row is written so the
        # operator can see WHY.  Surface a 502 so the dashboard
        # toasts an error, not a green success.
        raise HTTPException(
            status_code=502,
            detail="Email send failed — link is still valid; try again later",
        )
    return {
        "ok": True,
        "email_send_count": invite.email_send_count,
        "email_sent_at": invite.email_sent_at,
    }


@router.get("/invites")
async def list_invites(
    pending_only: bool = Query(True),
    include_revoked: bool = Query(False),
    user: dict = Depends(require_permission("can_invite")),
    platform_db=Depends(get_platform_db),
):
    """List invite codes for the account.

    Two orthogonal filters:
      - ``pending_only`` (default True) — hide USED rows.
      - ``include_revoked`` (default False) — keep revoked rows hidden
        unless the operator explicitly asks ("Show all" toggle on the
        dashboard drives both flags).
    """
    invites = await platform_db.list_invites(
        user["account_id"],
        pending_only=pending_only,
        include_revoked=include_revoked,
    )
    return {
        "invites": [
            {
                "id": inv.id,
                "code": inv.code,
                "role": inv.role,
                "department": inv.department,
                "truck_num": inv.truck_num,
                "expires_at": inv.expires_at,
                "used_by": inv.used_by,
                "is_used": inv.is_used,
                "is_expired": inv.is_expired,
                "is_revoked": inv.is_revoked,
                "revoked_at": inv.revoked_at,
                "created_by": inv.created_by,
                # Email-channel fields (migration 090).  Optional in
                # the InviteInfo TS type for deploy-lag tolerance.
                "channel": inv.channel,
                "sent_to_email": inv.sent_to_email,
                "email_sent_at": inv.email_sent_at,
                "email_send_count": inv.email_send_count,
                # Bounce / complaint fields (migration 097).
                "email_bounced_at": inv.email_bounced_at,
                "email_bounce_type": inv.email_bounce_type,
                "email_bounce_reason": inv.email_bounce_reason,
                "email_soft_bounce_count": inv.email_soft_bounce_count,
                "email_complained_at": inv.email_complained_at,
            }
            for inv in invites
        ],
        "count": len(invites),
    }


@router.delete("/invites/{invite_id}")
async def revoke_invite_endpoint(
    invite_id: int,
    user: dict = Depends(require_permission("can_invite")),
    platform_db=Depends(get_platform_db),
    tenant_db=Depends(get_tenant_db),
):
    """Revoke (soft-delete) an unused invite.

    Same permission gate as create (``can_invite``); the caller-vs-
    invite rank check mirrors the rule on ``create_invite`` so an HR
    user (rank 0, can_invite=True) can't undo an Owner's onboarding of
    an Admin by revoking it.  Caller's rank MUST be strictly greater
    than the invited role's rank.

    Rate-limited at 30 revokes per actor per minute — generous for
    legitimate flush-out flows (e.g. an Owner deletes all old test
    invites), tight enough to cap an attacker's audit-log amplification
    if a token is ever leaked.

    Audit log captures role/department/created_by from the row BEFORE
    the UPDATE so an auditor reading the log after a revoke can
    reconstruct what was killed even when the operator's default view
    (revoked rows hidden) doesn't show it.
    """
    # Rate limit first — cheap reject for any flood.  Keyed by
    # (account_id, actor_telegram_id) so two different admins on the
    # same account get their own budget.  Revoke + extend SHARE the
    # bucket (key prefix ``invite_mutate``) because they're sibling
    # write-paths against the same row set, and the audit-log-
    # amplification cap that motivated the rate limit applies to
    # both endpoints equally — keeping disjoint keys would silently
    # double the cap a compromised admin token can flood with.
    from adapters.cache.redis import rate_limit_check
    rl_key = f"invite_mutate:{user['account_id']}:{user['sub']}"
    if not await rate_limit_check(rl_key, window_secs=60, max_requests=30):
        raise HTTPException(
            status_code=429,
            detail="Too many invite changes — wait a moment and try again",
        )

    # Fetch the row before the UPDATE so we have role/department/
    # created_by available for the audit log details — and so we can
    # rank-check the invite's role against the caller's role.  Returns
    # None if the row doesn't exist OR belongs to a different account
    # (cross-account scoping is enforced inside the SELECT WHERE).
    invite = await platform_db.get_invite_by_id(user["account_id"], invite_id)
    if not invite:
        raise HTTPException(status_code=404, detail="Invite not found")

    # Rank check — mirror create_invite (admin.py around line 529-532).
    # An HR / lower-ranked actor must not be able to revoke an invite
    # whose role outranks them.  Same comparison as create.
    caller_rank = role_rank(user["role"])
    invite_rank = role_rank(invite.role)
    if invite_rank >= caller_rank:
        raise HTTPException(
            status_code=403,
            detail="Cannot revoke an invite for a role equal to or above your own",
        )

    revoked = await platform_db.revoke_invite(user["account_id"], invite_id)
    if not revoked:
        # The row existed at the SELECT above but the UPDATE matched 0
        # rows — either a concurrent redeem won the race, or the row
        # was already revoked by another tab.  Either way, treat as
        # "nothing to revoke" with the same 404 so we don't leak which
        # branch (the side-channel that would distinguish them is
        # operationally useless and confusing in the audit log).
        raise HTTPException(status_code=404, detail="Invite not found")

    await tenant_db.add_audit_log(
        user["account_id"], int(user["sub"]),
        "invite_revoke",
        target_type="invite", target_id=str(invite_id),
        details=f"Role: {revoked.role}, dept: {revoked.department}, created_by: {revoked.created_by}",
    )
    return {"ok": True, "revoked_at": revoked.revoked_at}


class InviteExtend(BaseModel):
    hours: int = Field(24, ge=1, le=720)


@router.post("/invites/{invite_id}/extend")
async def extend_invite_endpoint(
    invite_id: int,
    body: InviteExtend,
    user: dict = Depends(require_permission("can_invite")),
    platform_db=Depends(get_platform_db),
    tenant_db=Depends(get_tenant_db),
):
    """Push an unused invite's expiry forward without changing the
    code.  Operator flow this serves: "the invite I sent last week
    expired before they clicked — give it another 24 hours."

    Sibling of revoke, same guards (can_invite gate + rank check +
    rate limit + 404 uniformity).  Keeping the SAME code is the
    whole point — any copy of the original link in chat history
    works again, no re-share needed.

    Refuses to extend revoked invites: that would silently un-revoke
    an operator's prior kill decision.  If the operator really wants
    to bring a revoked code back, they create a new one (and reading
    the audit log makes the intent explicit).
    """
    from adapters.cache.redis import rate_limit_check
    # Shared bucket with revoke — see ``revoke_invite_endpoint``
    # rate-limit docstring for the audit-log-amplification rationale.
    rl_key = f"invite_mutate:{user['account_id']}:{user['sub']}"
    if not await rate_limit_check(rl_key, window_secs=60, max_requests=30):
        raise HTTPException(
            status_code=429,
            detail="Too many invite changes — wait a moment and try again",
        )

    invite = await platform_db.get_invite_by_id(user["account_id"], invite_id)
    if not invite:
        raise HTTPException(status_code=404, detail="Invite not found")

    # Same rank gate as revoke — HR (rank 0, can_invite=True) must
    # not be able to keep an Admin-tier invite alive past the deadline
    # the Owner set when issuing it.
    caller_rank = role_rank(user["role"])
    invite_rank = role_rank(invite.role)
    if invite_rank >= caller_rank:
        raise HTTPException(
            status_code=403,
            detail="Cannot extend an invite for a role equal to or above your own",
        )

    extended = await platform_db.extend_invite(
        user["account_id"], invite_id, hours=body.hours,
    )
    if not extended:
        # Used / revoked / race-lost — uniform 404 like revoke, no
        # side-channel for which branch.
        raise HTTPException(status_code=404, detail="Invite not found")

    await tenant_db.add_audit_log(
        user["account_id"], int(user["sub"]),
        "invite_extend",
        target_type="invite", target_id=str(invite_id),
        # created_by mirrors revoke's audit details — keeps the
        # forensic question "who created this and who keeps extending
        # it?" answerable from the audit log alone.
        details=f"Role: {extended.role}, dept: {extended.department}, created_by: {extended.created_by}, hours: {body.hours}, new_expires_at: {extended.expires_at}",
    )
    return {"ok": True, "expires_at": extended.expires_at}


# ── Companies ─────────────────────────────────────────────────

class CompanyCreate(BaseModel):
    code: str = Field(..., min_length=1, max_length=20)
    samsara_api_key: str = Field(..., min_length=1)
    display_name: str = ""
    active_days: int = Field(30, ge=1, le=365)


class CompanyUpdate(BaseModel):
    display_name: Optional[str] = None
    samsara_api_key: Optional[str] = None
    active_days: Optional[int] = Field(None, ge=1, le=365)


@router.get("/companies")
async def list_companies(
    user: dict = Depends(require_permission("can_manage_companies")),
    tenant_db=Depends(get_tenant_db),
):
    """List companies in the account."""
    companies = await tenant_db.get_account_companies(user["account_id"], active_only=False)
    return {
        "companies": [
            {
                "id": c.id,
                "code": c.code,
                "display_name": c.display_name,
                "active_days": c.active_days,
                "is_active": c.is_active,
                "created_at": c.created_at,
                "has_api_key": bool(c.samsara_api_key),
            }
            for c in companies
        ],
        "count": len(companies),
    }


@router.post("/companies")
async def add_company(
    body: CompanyCreate,
    user: dict = Depends(require_permission("can_manage_companies")),
    tenant_db=Depends(get_tenant_db),
):
    """Add a new company to the account."""
    existing = await tenant_db.get_company_by_code(user["account_id"], body.code)
    if existing:
        raise HTTPException(status_code=409, detail="Company code already exists")

    from interfaces.api.deps import enforce_company_quota
    await enforce_company_quota(user["account_id"], tenant_db=tenant_db)

    company = await tenant_db.add_company(
        account_id=user["account_id"],
        code=body.code,
        samsara_api_key=body.samsara_api_key,
        display_name=body.display_name or body.code,
        active_days=body.active_days,
    )
    await tenant_db.add_audit_log(
        user["account_id"], int(user["sub"]),
        "company_add",
        target_type="company", target_id=str(company.id),
        details=f"Code: {body.code}",
    )

    # Kick off historical backfill on the ARQ worker (4truck-queue
    # service) — runs in a *separate process* so the API stays
    # responsive for live users.  A 90-day backfill on a large fleet
    # can take 30-60s of Samsara round-trips; doing that inline in a
    # FastAPI worker would tie up a connection slot for the duration.
    # Idempotent (every writer dedups via UNIQUE/UPSERT), gap-aware
    # (skips sources that already have coverage), and dedup'd at the
    # queue level via job_id so rapid-fire admin actions don't spawn
    # parallel backfills for the same account.
    try:
        from infra import jobs as _jobs
        await _jobs.enqueue(
            "backfill_account_initial",
            account_id=user["account_id"],
            job_id=f"backfill_acct_{user['account_id']}",
        )
    except Exception:
        logger.exception("Failed to enqueue on-connect backfill for acct=%d", user["account_id"])

    return {"id": company.id, "code": company.code, "status": "created"}


@router.put("/companies/{company_id}")
async def update_company(
    company_id: int,
    body: CompanyUpdate,
    user: dict = Depends(require_permission("can_manage_companies")),
    tenant_db=Depends(get_tenant_db),
):
    """Update company details."""
    kwargs = {k: v for k, v in body.model_dump().items() if v is not None}
    if not kwargs:
        raise HTTPException(status_code=422, detail="No fields to update")

    ok = await tenant_db.update_company(company_id, account_id=user["account_id"], **kwargs)
    if not ok:
        raise HTTPException(status_code=404, detail="Company not found")

    await tenant_db.add_audit_log(
        user["account_id"], int(user["sub"]),
        "company_update",
        target_type="company", target_id=str(company_id),
        details=str(list(kwargs.keys())),
    )

    # Re-run backfill on the ARQ worker when the token is rotated —
    # the new key may expose data the old one couldn't reach (different
    # license / wider scope).  job_id collapses duplicate enqueues so a
    # rapid double-PUT doesn't queue two parallel backfills.
    if "samsara_api_key" in kwargs:
        try:
            from infra import jobs as _jobs
            await _jobs.enqueue(
                "backfill_account_initial",
                account_id=user["account_id"],
                job_id=f"backfill_acct_{user['account_id']}",
            )
        except Exception:
            logger.exception("Failed to enqueue backfill on token rotation acct=%d", user["account_id"])

    return {"ok": True}


@router.delete("/companies/{company_id}")
async def deactivate_company(
    company_id: int,
    user: dict = Depends(require_permission("can_manage_companies")),
    tenant_db=Depends(get_tenant_db),
):
    """Soft-delete (deactivate) a company."""
    ok = await tenant_db.remove_company(company_id, account_id=user["account_id"])
    if not ok:
        raise HTTPException(status_code=404, detail="Company not found")

    await tenant_db.add_audit_log(
        user["account_id"], int(user["sub"]),
        "company_deactivate",
        target_type="company", target_id=str(company_id),
    )
    return {"ok": True}


# ── Audit Log ─────────────────────────────────────────────────

@router.get("/audit-log")
async def get_audit_log(
    limit: int = Query(100, ge=1, le=500),
    user: dict = Depends(require_permission("can_manage_users")),
    tenant_db=Depends(get_tenant_db),
):
    """Get the audit log for the account."""
    entries = await tenant_db.get_audit_log(user["account_id"], limit=limit)
    return {"entries": entries, "count": len(entries)}


# ── Account Settings ──────────────────────────────────────────

class SettingUpdate(BaseModel):
    key: str = Field(..., min_length=1, max_length=50)
    value: str


@router.get("/settings")
async def get_settings(
    user: dict = Depends(require_permission("can_manage_account")),
    platform_db=Depends(get_platform_db),
    tenant_db=Depends(get_tenant_db),
):
    """Get all account settings + AI usage stats."""
    # Fetch common settings
    settings: dict[str, str] = {}
    for key in ("account_name", "alert_defaults", "timezone", "language",
                "digest_hour", "scorecard_default_subject"):
        val = await tenant_db.get_account_setting(user["account_id"], key)
        if val:
            settings[key] = val

    # Account info
    account = await platform_db.get_account(user["account_id"])

    # AI usage
    ai_stats = await platform_db.get_ai_usage_stats(user["account_id"], days=30)

    # Work schedules
    schedules = await tenant_db.get_work_hours(user["account_id"])

    return {
        "account": {
            "id": account.id if account else None,
            "name": account.name if account else "",
            "tier": account.tier if account else "free",
            "is_active": account.is_active if account else False,
        },
        "settings": settings,
        "ai_usage": ai_stats,
        "schedules": schedules,
    }


@router.put("/settings")
async def update_setting(
    body: SettingUpdate,
    user: dict = Depends(require_permission("can_manage_account")),
    tenant_db=Depends(get_tenant_db),
):
    """Update a single account setting."""
    await tenant_db.set_account_setting(user["account_id"], body.key, body.value)
    await tenant_db.add_audit_log(
        user["account_id"], int(user["sub"]),
        "setting_update",
        target_type="setting", target_id=body.key,
        details=f"Set to: {body.value[:100]}",
    )
    return {"ok": True}


# ── Work Hours ─────────────────────────────────────────────

class ScheduleCreate(BaseModel):
    label: str = Field(..., min_length=1)
    start_hour: int = Field(..., ge=0, le=23)
    end_hour: int = Field(..., ge=0, le=23)
    target_role: str = "all"

    @model_validator(mode="after")
    def end_after_start(self) -> "ScheduleCreate":
        if self.end_hour <= self.start_hour:
            raise ValueError("end_hour must be greater than start_hour")
        return self


class ScheduleUpdate(BaseModel):
    label: Optional[str] = None
    start_hour: Optional[int] = Field(None, ge=0, le=23)
    end_hour: Optional[int] = Field(None, ge=0, le=23)
    target_role: Optional[str] = None


@router.get("/work-hours")
async def list_schedules(
    user: dict = Depends(require_permission("can_manage_account")),
    tenant_db=Depends(get_tenant_db),
):
    """List work schedules."""
    schedules = await tenant_db.get_work_hours(user["account_id"])
    return {"schedules": schedules, "count": len(schedules)}


@router.post("/work-hours")
async def create_schedule(
    body: ScheduleCreate,
    user: dict = Depends(require_permission("can_manage_account")),
    tenant_db=Depends(get_tenant_db),
):
    """Create a work schedule."""
    sched = await tenant_db.create_work_hour(
        account_id=user["account_id"],
        label=body.label,
        start_hour=body.start_hour,
        end_hour=body.end_hour,
        created_by=await resolve_user_id(user),
        target_role=body.target_role,
    )
    await tenant_db.add_audit_log(
        user["account_id"], int(user["sub"]),
        "schedule_create",
        target_type="schedule", target_id=str(sched.get("id", "")),
        details=body.label,
    )
    return sched


@router.put("/work-hours/{schedule_id}")
async def update_schedule(
    schedule_id: int,
    body: ScheduleUpdate,
    user: dict = Depends(require_permission("can_manage_account")),
    tenant_db=Depends(get_tenant_db),
):
    """Update a work schedule."""
    kwargs = {k: v for k, v in body.model_dump().items() if v is not None}
    if not kwargs:
        raise HTTPException(status_code=422, detail="No fields to update")
    # Validate end_hour > start_hour when both are provided in the update
    if "start_hour" in kwargs and "end_hour" in kwargs:
        if kwargs["end_hour"] <= kwargs["start_hour"]:
            raise HTTPException(status_code=422, detail="end_hour must be greater than start_hour")
    ok = await tenant_db.update_work_hour(schedule_id, account_id=user["account_id"], **kwargs)
    if not ok:
        raise HTTPException(status_code=404, detail="Schedule not found")
    return {"ok": True}


@router.delete("/work-hours/{schedule_id}")
async def delete_schedule(
    schedule_id: int,
    user: dict = Depends(require_permission("can_manage_account")),
    tenant_db=Depends(get_tenant_db),
):
    """Delete a work schedule."""
    sched = await tenant_db.get_work_hour(schedule_id, account_id=user["account_id"])
    if not sched:
        raise HTTPException(status_code=404, detail="Schedule not found")
    await tenant_db.delete_work_hour(schedule_id, account_id=user["account_id"])
    await tenant_db.add_audit_log(
        user["account_id"], int(user["sub"]),
        "schedule_delete",
        target_type="schedule", target_id=str(schedule_id),
    )
    return {"ok": True}


# ── Bot configuration (owner-only) ───────────────────────────


class BotConfigRequest(BaseModel):
    bot_token: str = Field(..., min_length=30, max_length=100)


@router.put("/bot-config")
async def update_bot_config(
    body: BotConfigRequest,
    user: dict = Depends(require_permission("can_manage_account")),
    platform_db=Depends(get_platform_db),
    tenant_db=Depends(get_tenant_db),
):
    """Configure Telegram bot token for this account (owner-only).

    Validates the token via Telegram getMe(), encrypts it, and stores it.
    If the account already has a running bot, it will be restarted.
    """
    if user["role"] != "owner":
        raise HTTPException(status_code=403, detail="Only the account owner can configure the bot")

    import aiohttp
    from infra.crypto import encrypt

    # Validate token with Telegram API
    try:
        async with aiohttp.ClientSession() as session:
            async with session.get(
                f"https://api.telegram.org/bot{body.bot_token}/getMe",
                timeout=aiohttp.ClientTimeout(total=10),
            ) as resp:
                data = await resp.json()
                if not data.get("ok"):
                    raise HTTPException(
                        status_code=422,
                        detail=f"Invalid bot token: {data.get('description', 'unknown error')}",
                    )
                bot_info = data["result"]
    except aiohttp.ClientError as e:
        raise HTTPException(status_code=502, detail=f"Failed to reach Telegram API: {e}")

    bot_username = bot_info.get("username", "")
    encrypted_token = encrypt(body.bot_token)

    await platform_db.update_account(
        user["account_id"],
        bot_token_encrypted=encrypted_token,
        bot_username=bot_username,
    )

    await tenant_db.add_audit_log(
        user["account_id"], int(user["sub"]),
        "bot_config_update",
        detail=f"Bot configured: @{bot_username}",
    )

    # Hot-reload: start or restart per-account bot
    try:
        from infra.bot_registry import get_registry
        registry = get_registry()
        if registry:
            await registry.restart_bot(
                account_id=user["account_id"],
                encrypted_token=encrypted_token,
            )
    except Exception as e:
        logger.warning("Bot hot-reload failed for account %d: %s", user["account_id"], e)

    return {
        "ok": True,
        "bot_username": bot_username,
        "bot_id": bot_info.get("id"),
    }


@router.delete("/bot-config")
async def delete_bot_config(
    user: dict = Depends(require_permission("can_manage_account")),
    platform_db=Depends(get_platform_db),
    tenant_db=Depends(get_tenant_db),
):
    """Disconnect the Telegram bot for this account (owner-only)."""
    if user["role"] != "owner":
        raise HTTPException(status_code=403, detail="Only the account owner can configure the bot")

    account = await platform_db.get_account(user["account_id"])
    if not account or not account.bot_token_encrypted:
        raise HTTPException(status_code=404, detail="No bot configured for this account")

    await platform_db.update_account(
        user["account_id"],
        bot_token_encrypted=None,
        bot_username="",
        webhook_secret="",
    )

    await tenant_db.add_audit_log(
        user["account_id"], int(user["sub"]),
        "bot_config_delete",
        detail="Bot disconnected",
    )

    # Hot-reload: stop per-account bot
    try:
        from infra.bot_registry import get_registry
        registry = get_registry()
        if registry:
            await registry.stop_bot(user["account_id"])
    except Exception as e:
        logger.warning("Bot stop failed for account %d: %s", user["account_id"], e)

    return {"ok": True}


@router.get("/bot-config")
async def get_bot_config(
    user: dict = Depends(require_permission("can_manage_account")),
    platform_db=Depends(get_platform_db),
):
    """Get bot configuration status for this account."""
    account = await platform_db.get_account(user["account_id"])
    if not account:
        raise HTTPException(status_code=404, detail="Account not found")

    if not account.bot_token_encrypted:
        return {"has_bot": False, "bot_username": ""}

    result: dict = {
        "has_bot": True,
        "bot_username": account.bot_username or "",
    }

    # Cross-process liveness check.  In split-service deployments the
    # API process never starts bots (ENABLE_BOT=0 on the API systemd
    # unit), so its in-memory registry is permanently empty.  The bot
    # service writes a short-TTL Redis key on start + refreshes it
    # every 30 s; we read that key here.  Local/dev runs (where API +
    # bot share a process) also honour this — the same key gets
    # written from the same process.  See infra/bot_registry.py.
    try:
        from infra.bot_registry import is_bot_alive
        result["is_running"] = await is_bot_alive(user["account_id"])
    except Exception:
        result["is_running"] = False

    # Fetch live bot info from Telegram
    try:
        from infra.crypto import decrypt
        import aiohttp

        token = decrypt(account.bot_token_encrypted)
        async with aiohttp.ClientSession() as session:
            async with session.get(
                f"https://api.telegram.org/bot{token}/getMe",
                timeout=aiohttp.ClientTimeout(total=5),
            ) as resp:
                data = await resp.json()
                if data.get("ok"):
                    info = data["result"]
                    result["bot_id"] = info.get("id")
                    result["first_name"] = info.get("first_name", "")
    except Exception as e:
        logger.debug("Telegram getMe failed (partial result returned): %s", e)

    return result


# ── Role AI guidance ──────────────────────────────────────────────────────────

class RoleGuidanceIn(BaseModel):
    guidance: str = Field(..., min_length=10, max_length=4000)


@router.get("/role-guidance")
async def get_all_role_guidance(
    user: dict = Depends(require_permission("can_manage_account")),
    platform_db=Depends(get_platform_db),
):
    """Return all per-account AI guidance overrides.

    Each entry describes how the AI should behave for a given role within
    this account.  Missing roles fall back to the built-in defaults.
    """
    account_id = user["account_id"]
    overrides = await platform_db.get_all_role_ai_guidance(account_id)

    # Return the hardcoded defaults merged with any overrides so callers
    # can see every role with its active guidance in one response.
    from capabilities.iam.permissions import Role as RoleEnum, build_role_guidance
    result = {}
    for role in RoleEnum:
        result[role.value] = {
            "guidance": overrides.get(role.value) or build_role_guidance(role.value),
            "is_custom": role.value in overrides,
        }
    return result


@router.put("/role-guidance/{role}")
async def set_role_guidance(
    role: str,
    body: RoleGuidanceIn,
    user: dict = Depends(require_permission("can_manage_account")),
    platform_db=Depends(get_platform_db),
):
    """Set a custom AI guidance override for a role in this account."""
    from capabilities.iam.permissions import Role as RoleEnum
    valid_roles = {r.value for r in RoleEnum}
    if role not in valid_roles:
        raise HTTPException(status_code=422, detail=f"Unknown role '{role}'. Valid: {sorted(valid_roles)}")

    account_id = user["account_id"]
    await platform_db.set_role_ai_guidance(account_id, role, body.guidance)
    return {"status": "ok", "role": role, "guidance": body.guidance}


@router.delete("/role-guidance/{role}")
async def delete_role_guidance(
    role: str,
    user: dict = Depends(require_permission("can_manage_account")),
    platform_db=Depends(get_platform_db),
):
    """Remove a custom AI guidance override for a role (reverts to built-in default)."""
    account_id = user["account_id"]
    deleted = await platform_db.delete_role_ai_guidance(account_id, role)
    if not deleted:
        raise HTTPException(status_code=404, detail=f"No custom guidance found for role '{role}'")
    return {"status": "ok", "role": role}


# ── Scorecard rules + pillar caps ─────────────────────────────────────────────
#
# Tenant-level scoring config — sits in /admin to match the "Scorecard Rules"
# sidebar entry under Admin. The driver-facing scorecard read endpoints
# remain under /safety/scorecards/* (same feature, different audience).


class ScoreRuleUpdate(BaseModel):
    points: int = Field(..., ge=-100, le=100)
    cap: int | None = Field(None, ge=-200, le=200)
    enabled: bool = True
    curve_x_zero: float | None = Field(None, ge=-1000, le=10000)
    curve_x_max:  float | None = Field(None, ge=-1000, le=10000)
    curve_y_max:  int   | None = Field(None, ge=-200, le=200)


class PillarCapsUpdate(BaseModel):
    safety:     int = Field(..., ge=0, le=100)
    efficiency: int = Field(..., ge=0, le=100)
    compliance: int = Field(..., ge=0, le=100)

    @property
    def total(self) -> int:
        return self.safety + self.efficiency + self.compliance


@router.get("/scorecard-rules")
async def list_score_rules(
    user: dict = Depends(require_permission("can_manage_account")),
    tenant=Depends(get_tenant_db),
):
    """Default rules merged with this account's overrides.

    Each item carries id/label/category/pillar/kind, effective values, and
    an ``overridden`` flag so the UI can show a "reset to default" button.
    """
    overrides = await tenant.get_score_rule_overrides(user["account_id"])
    out: list[dict] = []
    for r in _get_default_rules():
        ov = overrides.get(r.id) or {}
        out.append({
            "id":         r.id,
            "label":      r.label,
            "category":   r.category,
            "pillar":     r.pillar,
            "kind":       r.kind,
            "default_points":  r.points,
            "default_cap":     r.cap,
            "points":     int(ov["points"])  if "points"  in ov else r.points,
            "cap":        (ov["cap"] if ov.get("cap") is not None else r.cap)
                          if "cap" in ov else r.cap,
            "enabled":    bool(ov["enabled"]) if "enabled" in ov else r.enabled,
            "curve_kind":          r.curve_kind,
            "default_curve_x_zero": r.curve_x_zero,
            "default_curve_x_max":  r.curve_x_max,
            "default_curve_y_max":  r.curve_y_max,
            "curve_x_zero": (ov["curve_x_zero"]
                             if ov.get("curve_x_zero") is not None else r.curve_x_zero),
            "curve_x_max":  (ov["curve_x_max"]
                             if ov.get("curve_x_max")  is not None else r.curve_x_max),
            "curve_y_max":  (ov["curve_y_max"]
                             if ov.get("curve_y_max")  is not None else r.curve_y_max),
            "overridden": bool(ov),
        })
    return {"rules": out, "count": len(out)}


@router.put("/scorecard-rules/{rule_id}")
async def update_score_rule(
    rule_id: str,
    body: ScoreRuleUpdate,
    user: dict = Depends(require_permission("can_manage_account")),
    tenant=Depends(get_tenant_db),
):
    """Override a default rule's points / cap / enabled / curve anchors."""
    defaults = {r.id: r for r in _get_default_rules()}
    rule = defaults.get(rule_id)
    if rule is None:
        raise HTTPException(status_code=404, detail="Unknown rule_id")
    await tenant.upsert_score_rule(
        user["account_id"], rule_id,
        label=rule.label, category=rule.category, kind=rule.kind,
        points=body.points, cap=body.cap, enabled=body.enabled,
        pillar=rule.pillar,
        curve_x_zero=body.curve_x_zero,
        curve_x_max=body.curve_x_max,
        curve_y_max=body.curve_y_max,
    )
    return {"ok": True, "rule_id": rule_id}


@router.delete("/scorecard-rules/{rule_id}")
async def reset_score_rule(
    rule_id: str,
    user: dict = Depends(require_permission("can_manage_account")),
    tenant=Depends(get_tenant_db),
):
    """Drop the per-account override → rule reverts to built-in default."""
    deleted = await tenant.delete_score_rule(user["account_id"], rule_id)
    return {"ok": True, "rule_id": rule_id, "deleted": deleted}


@router.get("/scorecard-pillar-caps")
async def get_pillar_caps(
    user: dict = Depends(require_permission("can_manage_account")),
    tenant=Depends(get_tenant_db),
):
    """Pillar cap weights for this account (defaults 50/25/25 when no override)."""
    from capabilities.scoring.engine import PILLAR_CAPS
    raw = await tenant.get_account_setting(
        user["account_id"], tenant.KEY_SCORECARD_PILLAR_CAPS, "",
    )
    if raw:
        try:
            caps = json.loads(raw)
            return {"safety": caps.get("safety", PILLAR_CAPS["safety"]),
                    "efficiency": caps.get("efficiency", PILLAR_CAPS["efficiency"]),
                    "compliance": caps.get("compliance", PILLAR_CAPS["compliance"]),
                    "is_custom": True}
        except Exception:
            pass
    return {**PILLAR_CAPS, "is_custom": False}


@router.put("/scorecard-pillar-caps")
async def set_pillar_caps(
    body: PillarCapsUpdate,
    user: dict = Depends(require_permission("can_manage_account")),
    tenant=Depends(get_tenant_db),
):
    """Set per-tenant pillar cap weights. Must sum to exactly 100."""
    if body.total != 100:
        raise HTTPException(
            status_code=422,
            detail=f"Pillar caps must sum to 100 (got {body.total}).",
        )
    caps = {"safety": body.safety, "efficiency": body.efficiency, "compliance": body.compliance}
    await tenant.set_account_setting(
        user["account_id"],
        tenant.KEY_SCORECARD_PILLAR_CAPS,
        json.dumps(caps),
    )
    return {"ok": True, "caps": caps}


@router.delete("/scorecard-pillar-caps")
async def reset_pillar_caps(
    user: dict = Depends(require_permission("can_manage_account")),
    tenant=Depends(get_tenant_db),
):
    """Remove pillar cap override — reverts to built-in defaults (50/25/25)."""
    from capabilities.scoring.engine import PILLAR_CAPS
    await tenant.set_account_setting(
        user["account_id"], tenant.KEY_SCORECARD_PILLAR_CAPS, "",
    )
    return {"ok": True, "caps": PILLAR_CAPS, "is_custom": False}



# ── Warehouse diagnostics ─────────────────────────────────────
#
# Surfaces the row counts + freshness of every warehouse table for the
# caller's account. Used during the WAREHOUSE_READS_ENABLED rollout to
# verify that the ingestor is populating the tables before flipping the
# read flag, and afterwards to verify that the data stays fresh. Also
# reports the current value of WAREHOUSE_READS_ENABLED so ops can
# confirm the flag is set correctly per pod.

_WAREHOUSE_TABLES: list[tuple[str, str | None]] = [
    # (table_name, ts_column or None if the table has no timestamp)
    ("vehicle_state",             "fetched_at"),
    ("vehicle_health_snapshot",   "fetched_at"),
    ("vehicle_fault_snapshot",    "fetched_at"),
    ("fleet_weather_snapshot",    "fetched_at"),
    ("fleet_efficiency_snapshot", "fetched_at"),
    ("safety_event_log",          "occurred_at"),
    ("driver_efficiency_daily",   "snapshot_date"),
    ("vehicle_telemetry_hourly",  "hour_bucket"),
    ("geofence_definitions",      "fetched_at"),
]


@router.get("/warehouse-status")
async def warehouse_status(
    user: dict = Depends(require_permission("can_manage_account")),
    tenant=Depends(get_tenant_db),
):
    """Per-table row count + most-recent-row timestamp for the caller's
    account warehouse. Exposes ``warehouse_reads_enabled`` so ops can
    verify the env var is set on the pod handling the request.

    Designed as the pre-flight check before flipping
    ``WAREHOUSE_READS_ENABLED=1`` in production: if every table has rows
    and the timestamps are within the expected ingestor cadence (60s /
    5min / hourly), the flag is safe to flip.
    """
    from infra import config as _cfg

    account_id = user["account_id"]
    tables: list[dict] = []

    for name, ts_col in _WAREHOUSE_TABLES:
        entry: dict = {"table": name}
        try:
            count_row = await tenant.read_one(
                f"SELECT COUNT(*) AS n FROM {name} WHERE account_id = ?",
                (account_id,),
            )
            entry["rows"] = int(count_row["n"]) if count_row else 0
        except Exception as e:
            # Table may not exist yet on a freshly-installed tenant DB
            # that hasn't run the warehouse migrations. Surface it as a
            # diagnostic rather than failing the whole endpoint.
            entry["rows"] = 0
            entry["error"] = str(e)[:200]
            tables.append(entry)
            continue

        if ts_col and entry["rows"] > 0:
            try:
                ts_row = await tenant.read_one(
                    f"SELECT MAX({ts_col}) AS ts FROM {name} "
                    f"WHERE account_id = ?",
                    (account_id,),
                )
                entry["last_seen"] = ts_row["ts"] if ts_row else None
            except Exception:
                entry["last_seen"] = None

        tables.append(entry)

    populated = sum(1 for t in tables if t.get("rows", 0) > 0)
    return {
        "account_id": account_id,
        "warehouse_reads_enabled": bool(getattr(_cfg, "WAREHOUSE_READS_ENABLED", False)),
        "tables": tables,
        "summary": {
            "total":     len(tables),
            "populated": populated,
            "empty":     len(tables) - populated,
        },
    }


# ── Job queue diagnostics ───────────────────────────
#
# Read-only endpoints that surface the state of the ARQ job queue.
# Used by ops to:
#   * verify a freshly-enqueued job is being picked up by a worker
#   * poll a long-running job (PDF generation, report export) from the
#     dashboard without holding an HTTP connection open
#   * confirm the `/admin/jobs/enqueue/{name}` admin trigger for the
#     pre-warm fanout actually queued work
#
# Job results are JSON; the queue itself never holds binary payloads.
# Any large artifact (PDF, CSV) is written to object storage and the
# job result holds the URL.

@router.get("/jobs/{job_id}")
async def job_status(
    job_id: str,
    user: dict = Depends(require_permission("can_manage_account")),
):
    """Look up an ARQ background job by id.

    Returns the job's current status (deferred / queued / in_progress /
    complete / not_found), enqueue + start + finish times, and the
    job result when complete.

    Permission: ``can_manage_account`` — ARQ doesn't natively scope
    jobs to tenants so we restrict status access to admins. If you add
    user-facing async jobs (e.g. dashboard "Generate report" button),
    enforce ownership inside the job's result by stamping the requester
    on enqueue.
    """
    from infra import jobs as _jobs
    info = await _jobs.get_job_status(job_id)
    if info is None:
        raise HTTPException(404, f"Job {job_id} not found or queue unavailable")
    return info


@router.post("/jobs/prewarm-scorecards")
async def trigger_prewarm_scorecards(
    days: int = Query(7, ge=1, le=90),
    user: dict = Depends(require_permission("can_manage_account")),
):
    """Manually fire the scorecards cache pre-warm fanout for the
    caller's account. Useful for ops to re-warm the cache after a
    schema/rules change without waiting for the 06:00 cron.

    Returns ``{job_id}`` of the per-account precompute job. Poll
    ``GET /admin/jobs/{job_id}`` for status.
    """
    from infra import jobs as _jobs
    job = await _jobs.enqueue("precompute_scorecards", user["account_id"], days)
    if job is None:
        raise HTTPException(503, "Job queue unavailable — is the ARQ worker running?")
    return {"job_id": job.job_id, "function": "precompute_scorecards", "account_id": user["account_id"], "days": days}


# ── Storage quota (local-disk fallback for driver documents) ─────
#
# Only meaningful for accounts that haven't connected Google Drive —
# Drive-connected accounts use the user's own Drive quota. The local
# fallback enforces a per-account cap so a single tenant can't fill
# the host disk.

class StorageQuotaUpdate(BaseModel):
    quota_bytes: int = Field(..., ge=0)


@router.get("/storage/quota")
async def get_storage_quota(
    user: dict = Depends(require_permission("can_manage_account")),
    tenant_db=Depends(get_tenant_db),
):
    """Return current storage usage and quota for the caller's account."""
    used, quota = await tenant_db.get_storage_usage(user["account_id"])
    return {
        "used_bytes": used,
        "quota_bytes": quota,
        "remaining_bytes": max(0, quota - used),
    }


@router.put("/storage/quota")
async def update_storage_quota(
    body: StorageQuotaUpdate,
    user: dict = Depends(require_permission("can_manage_account")),
    tenant_db=Depends(get_tenant_db),
):
    """Raise/lower the per-account local-disk storage cap."""
    ok = await tenant_db.set_storage_quota(user["account_id"], body.quota_bytes)
    if not ok:
        raise HTTPException(404, "Account not found")
    await tenant_db.add_audit_log(
        user["account_id"], int(user["sub"]),
        "storage_quota_update",
        target_type="account", target_id=str(user["account_id"]),
        details=f"Set storage quota to {body.quota_bytes} bytes",
    )
    used, quota = await tenant_db.get_storage_usage(user["account_id"])
    return {
        "used_bytes": used,
        "quota_bytes": quota,
        "remaining_bytes": max(0, quota - used),
    }


# ── Account timezone (single source of truth for cron + display) ───
#
# Per-user override lives on ``users.timezone`` and is set via the
# Profile page.  This admin endpoint sets the account-wide default —
# every user without an override inherits it, every cron job uses it
# to decide "is it 09:00 here yet?", every formatter renders against it.

class AccountTimezoneUpdate(BaseModel):
    timezone: str = Field(..., min_length=1)


@router.get("/timezone")
async def get_account_timezone(
    user: dict = Depends(require_permission("can_manage_account")),
    platform_db=Depends(get_platform_db),
):
    """Return the account's default timezone plus the supported list."""
    from capabilities.localization.tz import IANA_OPTIONS, DEFAULT_TIMEZONE
    acct = await platform_db.get_account(user["account_id"])
    return {
        "timezone": getattr(acct, "timezone", DEFAULT_TIMEZONE) or DEFAULT_TIMEZONE,
        "options": list(IANA_OPTIONS),
    }


@router.put("/timezone")
async def set_account_timezone(
    body: AccountTimezoneUpdate,
    user: dict = Depends(require_permission("can_manage_account")),
    platform_db=Depends(get_platform_db),
    tenant_db=Depends(get_tenant_db),
):
    """Set the account's default timezone.  Validates against the
    canonical IANA-options list so an admin can't enter ``"EST"``
    (deprecated alias) or a typo."""
    from capabilities.localization.tz import IANA_OPTIONS
    tz_val = body.timezone.strip()
    if tz_val not in IANA_OPTIONS:
        raise HTTPException(
            status_code=422,
            detail=f"Unsupported timezone. Valid values: {', '.join(IANA_OPTIONS)}",
        )
    ok = await platform_db.update_account(user["account_id"], timezone=tz_val)
    if not ok:
        raise HTTPException(status_code=404, detail="Account not found")
    await tenant_db.add_audit_log(
        user["account_id"], int(user["sub"]),
        "timezone_update",
        target_type="account", target_id=str(user["account_id"]),
        details=f"Set account timezone to {tz_val}",
    )
    return {"timezone": tz_val}


# ── Forum routing (Telegram group topics) ─────────────────────

@router.get("/forum-routing")
async def get_forum_routing(
    user: dict = Depends(require_permission("can_manage_account")),
    platform_db=Depends(get_platform_db),
):
    """Return the current forum-routing state for the account.

    Powers the inline "Alert Routing" section in the Telegram Bot
    admin card.  When no group is bound the response signals the
    setup wizard should be rendered; when bound it lists each alert
    type, the topic it maps to, and whether the route is active.
    """
    from capabilities.alerting.forum_topics import FORUM_TOPIC_SPEC

    account_id = user["account_id"]
    group = await platform_db.get_forum_group(account_id)

    # Render the topic catalog regardless of bound state — the
    # dashboard uses it to show "what will be created" in the
    # not-yet-connected state.
    catalog = [
        {
            "alert_type":  spec.key,
            "name":        spec.name,
            "icon_emoji":  spec.icon_emoji,
            "description": spec.description,
            "pinned":      spec.pinned,
        }
        for spec in FORUM_TOPIC_SPEC
    ]

    if group is None:
        return {
            "connected": False,
            "catalog": catalog,
            "routes": [],
        }

    routes = await platform_db.list_alert_routes(account_id)
    by_key = {r.alert_type: r for r in routes}
    route_rows = []
    for spec in FORUM_TOPIC_SPEC:
        r = by_key.get(spec.key)
        route_rows.append({
            "alert_type":          spec.key,
            "name":                spec.name,
            "icon_emoji":          spec.icon_emoji,
            "description":         spec.description,
            "pinned":              spec.pinned,
            "is_mapped":           r is not None,
            "is_active":           bool(r and r.is_active),
            "message_thread_id":   r.message_thread_id if r else None,
            "topic_name_snapshot": r.topic_name_snapshot if r else "",
            # Per-topic "🟢 RESOLVED" receipt toggle (migration 079).
            # When false the auto-resolve pipeline still flips the
            # underlying alert_history row but skips the chat post.
            # Defaults to True on legacy rows; admin flips via the
            # ForumRoutingSection on the dashboard.
            "send_resolve_receipt": bool(r.send_resolve_receipt) if r else True,
        })

    # Account-level group-routing settings.  Per-alert-type AI
    # toggles let admins enable AI for some categories (e.g. Parking
    # AI is useful; Health AI is noise) without an all-or-nothing
    # global switch.  Only the alert types that actually generate AI
    # content are exposed; the rest stay quiet.
    _AI_CAPABLE = ("faults", "health", "parking", "camera")
    ai_per_type: dict[str, bool] = {}
    for key in _AI_CAPABLE:
        val = await platform_db.get_account_setting(
            account_id, f"forum_ai.{key}", default="1",
        )
        ai_per_type[key] = val != "0"

    return {
        "connected":      True,
        "chat_id":        group.chat_id,
        "chat_title":     group.chat_title,
        "setup_status":   group.setup_status,
        "last_setup_at":  group.last_setup_at,
        "last_repair_at": group.last_repair_at,
        "catalog":        catalog,
        "routes":         route_rows,
        "settings": {
            "ai_per_type": ai_per_type,
        },
    }


class ForumRouteToggle(BaseModel):
    is_active: bool


class ForumSettingsUpdate(BaseModel):
    # Map of alert_type → bool.  Only alert types in _AI_CAPABLE are
    # honoured server-side; unknown keys are ignored so the API stays
    # tolerant if the dashboard sends extras.
    ai_per_type: Optional[dict[str, bool]] = None


@router.put("/forum-routing/settings")
async def update_forum_settings(
    body: ForumSettingsUpdate,
    user: dict = Depends(require_permission("can_manage_account")),
    platform_db=Depends(get_platform_db),
    tenant_db=Depends(get_tenant_db),
):
    """Update per-alert-type AI toggles for the group routing.

    Each key in ``ai_per_type`` is a canonical alert key (``faults``,
    ``health``, ``parking`` today — the only types with AI content).
    Setting any of them to False makes future alerts of that type
    post to the topic *without* the AI section; DM fallback (for
    CRITICAL mirrors and non-routed accounts) still respects each
    subscriber's per-user ``ai_*`` preference.
    """
    account_id = user["account_id"]
    _AI_CAPABLE = ("faults", "health", "parking", "camera")
    changed: list[str] = []
    if body.ai_per_type:
        for alert_type, enabled in body.ai_per_type.items():
            if alert_type not in _AI_CAPABLE:
                continue
            await platform_db.set_account_setting(
                account_id, f"forum_ai.{alert_type}",
                "1" if enabled else "0",
            )
            changed.append(f"{alert_type}={'on' if enabled else 'off'}")

    if changed:
        await tenant_db.add_audit_log(
            account_id, int(user["sub"]),
            "forum_settings_update",
            target_type="account", target_id=str(account_id),
            details="ai_per_type: " + ", ".join(changed),
        )
    return {"ok": True, "changed": changed}


@router.put("/forum-routing/{alert_type}")
async def toggle_forum_route(
    alert_type: str,
    body: ForumRouteToggle,
    user: dict = Depends(require_permission("can_manage_account")),
    platform_db=Depends(get_platform_db),
    tenant_db=Depends(get_tenant_db),
):
    """Soft-toggle a single alert→topic route.

    Disabling sends future alerts of that type back to the per-user
    DM path (subscribers respect their personal mute toggles again).
    Re-enabling restores group routing.  The Telegram topic itself
    is never touched — only the database row.
    """
    from adapters.storage.models import ALERT_TYPE_KEYS

    if alert_type not in ALERT_TYPE_KEYS:
        raise HTTPException(status_code=422, detail=f"Unknown alert_type: {alert_type}")

    account_id = user["account_id"]
    route = await platform_db.get_alert_route(account_id, alert_type)
    if route is None:
        # Soft-toggle only works for an existing (possibly inactive)
        # row.  If the row doesn't exist at all the admin needs to
        # run /setupforum or /repairforum first.
        raise HTTPException(
            status_code=404,
            detail=f"No route exists for '{alert_type}'. Run /setupforum or /repairforum first.",
        )

    await platform_db.set_alert_route_active(
        account_id, alert_type, body.is_active,
    )
    await tenant_db.add_audit_log(
        account_id, int(user["sub"]),
        "forum_route_toggle",
        target_type="alert_type", target_id=alert_type,
        details=f"is_active={body.is_active}",
    )
    return {"alert_type": alert_type, "is_active": body.is_active}


# ── Per-topic "🟢 RESOLVED" receipt toggle ──────────────────────────
# Migration 079 added ``alert_routing.send_resolve_receipt``.  This
# endpoint lets admins flip it per route from the ForumRoutingSection
# checkbox.  Defaults to True on existing rows — turning it off
# suppresses the chat receipt only; the underlying alert_history row
# still flips to resolved, so the dashboard monitoring view stays
# accurate.


class ForumRouteReceiptToggle(BaseModel):
    send_resolve_receipt: bool


@router.put("/forum-routing/routes/{alert_type}/receipt")
async def toggle_forum_route_receipt(
    alert_type: str,
    body: ForumRouteReceiptToggle,
    user: dict = Depends(require_permission("can_manage_account")),
    platform_db=Depends(get_platform_db),
    tenant_db=Depends(get_tenant_db),
):
    """Enable or disable the '🟢 RESOLVED' chat receipt for one topic."""
    from adapters.storage.models import ALERT_TYPE_KEYS

    if alert_type not in ALERT_TYPE_KEYS:
        raise HTTPException(status_code=422, detail=f"Unknown alert_type: {alert_type}")

    account_id = user["account_id"]
    route = await platform_db.get_alert_route(account_id, alert_type)
    if route is None:
        raise HTTPException(
            status_code=404,
            detail=f"No route exists for '{alert_type}'. Run /setupforum or /repairforum first.",
        )
    ok = await platform_db.set_alert_route_send_resolve_receipt(
        account_id, alert_type, body.send_resolve_receipt,
    )
    await tenant_db.add_audit_log(
        account_id, int(user["sub"]),
        "forum_route_receipt_toggle",
        target_type="alert_type", target_id=alert_type,
        details=f"send_resolve_receipt={body.send_resolve_receipt}",
    )
    return {
        "alert_type": alert_type,
        "send_resolve_receipt": body.send_resolve_receipt,
        "ok": ok,
    }


@router.post("/forum-routing/disconnect")
async def disconnect_forum_routing(
    user: dict = Depends(require_permission("can_manage_account")),
    platform_db=Depends(get_platform_db),
    tenant_db=Depends(get_tenant_db),
):
    """Unbind the forum group from the account.

    Removes the ``forum_groups`` row and every ``alert_routing`` row
    in one shot — subsequent alerts fall straight back to per-user
    DM delivery.  Topics themselves are NOT deleted from Telegram;
    admins can clean those up via /resetforum in the group when they
    want a clean slate.
    """
    account_id = user["account_id"]
    group = await platform_db.get_forum_group(account_id)
    if group is None:
        return {"ok": True, "was_connected": False}

    await platform_db.delete_forum_group(account_id)
    await tenant_db.add_audit_log(
        account_id, int(user["sub"]),
        "forum_routing_disconnect",
        target_type="account", target_id=str(account_id),
        details=f"chat_id={group.chat_id}",
    )
    return {"ok": True, "was_connected": True, "chat_id": group.chat_id}


# ── Escalations summary ──────────────────────────────────────


@router.get("/escalations")
async def escalation_summary(
    user: dict = Depends(require_permission("can_alerts_all")),
    tenant_db=Depends(get_tenant_db),
):
    """Owner/admin oversight: how many active alerts are past their
    re-escalation window or have hit the max-attempts cap.

    Computed from ``alert_history`` (one row per logical alert) and
    the env-tuned re-escalation knobs.  ``past_due`` means the alert
    is older than ``REESCALATE_AFTER_MINUTES`` and unacked (the
    pipeline is currently re-pinging it).  ``breached`` means it hit
    ``REESCALATE_MAX_ATTEMPTS`` and the pipeline stopped paging — the
    alert is still in the dashboard queue but no longer interrupting
    operators.  Both counts are CRITICAL/WARNING only.

    The ``by_persona`` map groups past_due counts by the persona that
    owns each alert_type (per ``capabilities.alerting.persona_mapping``)
    so the EscalationStatusCard can drill the owner directly into
    "Dispatch has 5 alerts past due" without scanning the queue.
    """
    from datetime import datetime, timezone, timedelta
    from infra.config import (
        REESCALATE_AFTER_MINUTES, REESCALATE_MAX_ATTEMPTS,
    )
    from capabilities.alerting import persona_mapping

    account_id = user["account_id"]
    rows = await tenant_db.get_active_alert_history_for_account(account_id)

    now = datetime.now(timezone.utc)
    cutoff_minutes = max(REESCALATE_AFTER_MINUTES, 0)
    cutoff = now - timedelta(minutes=cutoff_minutes)

    past_due = 0
    breached = 0
    by_persona: dict[str, int] = {}
    for r in rows:
        sev = (r.get("severity") or "").lower()
        if sev not in ("critical", "warning"):
            continue
        # `last_seen` is the latest re-fire timestamp — use it as the
        # age anchor so a chronic alert that fired again 5 minutes ago
        # isn't flagged "past_due" just because its first_seen is old.
        last_seen = r.get("last_seen") or r.get("first_seen") or ""
        try:
            ts = datetime.fromisoformat(last_seen.replace("Z", "+00:00"))
            if ts.tzinfo is None:
                ts = ts.replace(tzinfo=timezone.utc)
        except Exception:
            continue
        reesc = int(r.get("reescalate_count") or 0)
        is_past_due = cutoff_minutes > 0 and ts < cutoff
        if is_past_due:
            past_due += 1
            persona = persona_mapping.persona_for_alert(r.get("alert_type") or "")
            by_persona[persona] = by_persona.get(persona, 0) + 1
        if reesc >= REESCALATE_MAX_ATTEMPTS:
            breached += 1

    return {
        "past_due_count":   past_due,
        "breached_count":   breached,
        "by_persona":       by_persona,
        # Knobs returned so the UI can render a "older than 60m" label
        # without reading env from the browser.
        "reescalate_after_minutes": REESCALATE_AFTER_MINUTES,
        "reescalate_max_attempts":  REESCALATE_MAX_ATTEMPTS,
    }
