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

from interfaces.api.deps import require_permission, get_tenant_db, get_platform_db, paginate
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

    # Fetch from Telegram
    try:
        from infra.config import TELEGRAM_TOKEN as _token
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
    """Set company access for a user. Empty list = access to all companies."""
    target = await platform_db.get_user(user_id)
    if not target or target.account_id != user["account_id"]:
        raise HTTPException(status_code=404, detail="User not found")

    # Owners always have access to all companies — don't restrict them
    target_role = target.role.value if hasattr(target.role, "value") else target.role
    if target_role == "owner":
        raise HTTPException(status_code=400, detail="Cannot restrict company access for owners")

    # Validate company IDs belong to this account
    if body.company_ids:
        all_companies = await tenant_db.get_account_companies(user["account_id"])
        valid_ids = {c.id for c in all_companies}
        invalid = [cid for cid in body.company_ids if cid not in valid_ids]
        if invalid:
            raise HTTPException(status_code=400, detail=f"Invalid company IDs: {invalid}")

    await platform_db.set_user_companies(
        user_id, target.account_id, body.company_ids,
        assigned_by=int(user["sub"]),
    )

    await tenant_db.add_audit_log(
        user["account_id"], int(user["sub"]),
        "company_assignment",
        target_type="user", target_id=str(user_id),
        details=f"Companies: {body.company_ids or 'all (unrestricted)'}",
    )

    return {"ok": True, "company_ids": body.company_ids, "unrestricted": len(body.company_ids) == 0}


# ── Invites ───────────────────────────────────────────────────

class InviteCreate(BaseModel):
    role: str = Field("fleet", pattern=r"^(admin|fleet|safety|dispatcher|driver)$")
    department: str = "general"
    truck_num: Optional[str] = None
    hours: int = Field(24, ge=1, le=720)


@router.post("/invite")
async def create_invite(
    body: InviteCreate,
    user: dict = Depends(require_permission("can_invite")),
    platform_db=Depends(get_platform_db),
    tenant_db=Depends(get_tenant_db),
):
    """Generate a new invite code."""
    caller_rank = role_rank(user["role"])
    target_rank = role_rank(body.role)
    if target_rank >= caller_rank:
        raise HTTPException(status_code=403, detail="Cannot create invite for role equal to or above your own")

    # Resolve DB user.id from telegram_id (JWT sub) — FK requires users.id
    db_user = await platform_db.get_user_by_telegram_id(int(user["sub"]))
    if not db_user:
        raise HTTPException(status_code=404, detail="User not found")

    invite = await platform_db.create_invite(
        account_id=user["account_id"],
        created_by=db_user.id,
        role=Role(body.role),
        department=body.department,
        truck_num=body.truck_num,
        hours=body.hours,
    )
    await tenant_db.add_audit_log(
        user["account_id"], int(user["sub"]),
        "invite_create",
        target_type="invite", target_id=str(invite.id),
        details=f"Role: {body.role}, dept: {body.department}",
    )
    return {
        "id": invite.id,
        "code": invite.code,
        "role": invite.role,
        "department": invite.department,
        "truck_num": invite.truck_num,
        "expires_at": invite.expires_at,
    }


@router.get("/invites")
async def list_invites(
    pending_only: bool = Query(True),
    user: dict = Depends(require_permission("can_invite")),
    platform_db=Depends(get_platform_db),
):
    """List invite codes for the account."""
    invites = await platform_db.list_invites(user["account_id"], pending_only=pending_only)
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
                "created_by": inv.created_by,
            }
            for inv in invites
        ],
        "count": len(invites),
    }


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
        created_by=int(user["sub"]),
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

    # Check if bot is running in the registry
    try:
        from infra.bot_registry import get_registry
        registry = get_registry()
        result["is_running"] = bool(registry and registry.get(user["account_id"]))
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


# ── Job queue diagnostics (Phase 3) ───────────────────────────
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


# ── Dual-write diagnostics (Phase 5b) ─────────────────────────
#
# Surfaces the per-process counters from ``adapters.storage.dualwrite``
# so ops can monitor how the SQLite→PostgreSQL dual-write phase is
# tracking. Endpoint returns the same shape as warehouse-status so the
# admin dashboard can poll it on the same cadence.
#
# Counters reset on process restart (they're in-memory). Aggregating
# across workers requires Phase 6 metrics (Prometheus); this endpoint is
# enough for one-process spot checks during the rollout.

@router.get("/dualwrite-status")
async def dualwrite_status(
    user: dict = Depends(require_permission("can_manage_account")),
):
    """Per-process dual-write counters + flag values.

    Used during the SQLite → PostgreSQL migration to verify that
    secondary writes are succeeding and reads are returning identical
    row counts. Healthy state: ``writes_failed == 0``,
    ``reads_diverged == 0``.
    """
    from adapters.storage import dualwrite as _dw

    return {
        "dual_write_enabled": _dw.is_dual_write_enabled(),
        "read_backend":       _dw.read_backend(),
        "metrics":            _dw.get_metrics(),
    }
