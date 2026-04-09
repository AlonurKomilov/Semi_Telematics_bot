"""Admin API endpoints — users, companies, invites, audit log, settings, schedules."""

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field
from typing import Optional

from api.deps import get_current_user, require_permission, get_tenant_db, get_platform_db
from permissions import can

router = APIRouter(prefix="/admin", tags=["admin"])

ROLE_HIERARCHY = {"owner": 5, "admin": 4, "fleet_manager": 3, "dispatcher": 2, "driver": 1}


# ── Users ─────────────────────────────────────────────────────

@router.get("/users")
async def list_users(
    user: dict = Depends(require_permission("can_manage_users")),
    platform_db=Depends(get_platform_db),
):
    """List all users in the account."""
    users = await platform_db.list_account_users(user["account_id"])
    return {
        "users": [
            {
                "id": u.id,
                "telegram_id": u.telegram_id,
                "display_name": u.display_name,
                "role": u.role.value if hasattr(u.role, "value") else u.role,
                "department": u.department,
                "truck_num": u.truck_num,
                "is_active": u.is_active,
                "email": u.email,
                "language": u.language,
                "timezone": u.timezone,
                "created_at": getattr(u, "created_at", None),
            }
            for u in users
        ],
        "count": len(users),
    }


class RoleUpdate(BaseModel):
    role: str = Field(..., pattern=r"^(owner|admin|fleet_manager|dispatcher|driver)$")


@router.put("/users/{user_id}/role")
async def update_user_role(
    user_id: int,
    body: RoleUpdate,
    user: dict = Depends(require_permission("can_manage_users")),
    platform_db=Depends(get_platform_db),
    tenant_db=Depends(get_tenant_db),
):
    """Change a user's role. Cannot promote above own role."""
    caller_rank = ROLE_HIERARCHY.get(user["role"], 0)
    target_rank = ROLE_HIERARCHY.get(body.role, 0)
    if target_rank >= caller_rank:
        raise HTTPException(status_code=403, detail="Cannot assign a role equal to or above your own")

    target = await platform_db.get_user(user_id)
    if not target or target.account_id != user["account_id"]:
        raise HTTPException(status_code=404, detail="User not found")

    existing_rank = ROLE_HIERARCHY.get(
        target.role.value if hasattr(target.role, "value") else target.role, 0
    )
    if existing_rank >= caller_rank:
        raise HTTPException(status_code=403, detail="Cannot modify a user with equal or higher role")

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

    caller_rank = ROLE_HIERARCHY.get(user["role"], 0)
    existing_rank = ROLE_HIERARCHY.get(
        target.role.value if hasattr(target.role, "value") else target.role, 0
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


# ── Invites ───────────────────────────────────────────────────

class InviteCreate(BaseModel):
    role: str = Field("fleet_manager", pattern=r"^(admin|fleet_manager|dispatcher|driver)$")
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
    caller_rank = ROLE_HIERARCHY.get(user["role"], 0)
    target_rank = ROLE_HIERARCHY.get(body.role, 0)
    if target_rank >= caller_rank:
        raise HTTPException(status_code=403, detail="Cannot create invite for role equal to or above your own")

    invite = await platform_db.create_invite(
        account_id=user["account_id"],
        created_by=int(user["sub"]),
        role=body.role,
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
    for key in ("account_name", "alert_defaults", "timezone", "language", "digest_hour"):
        val = await tenant_db.get_account_setting(user["account_id"], key)
        if val:
            settings[key] = val

    # Account info
    account = await platform_db.get_account(user["account_id"])

    # AI usage
    ai_stats = await platform_db.get_ai_usage_stats(user["account_id"], days=30)

    # Work schedules
    schedules = await tenant_db.get_work_schedules(user["account_id"])

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


# ── Work Schedules ────────────────────────────────────────────

class ScheduleCreate(BaseModel):
    label: str = Field(..., min_length=1)
    start_hour: int = Field(..., ge=0, le=23)
    end_hour: int = Field(..., ge=0, le=23)
    target_role: str = "all"


class ScheduleUpdate(BaseModel):
    label: Optional[str] = None
    start_hour: Optional[int] = Field(None, ge=0, le=23)
    end_hour: Optional[int] = Field(None, ge=0, le=23)
    target_role: Optional[str] = None


@router.get("/schedules")
async def list_schedules(
    user: dict = Depends(require_permission("can_manage_account")),
    tenant_db=Depends(get_tenant_db),
):
    """List work schedules."""
    schedules = await tenant_db.get_work_schedules(user["account_id"])
    return {"schedules": schedules, "count": len(schedules)}


@router.post("/schedules")
async def create_schedule(
    body: ScheduleCreate,
    user: dict = Depends(require_permission("can_manage_account")),
    tenant_db=Depends(get_tenant_db),
):
    """Create a work schedule."""
    sched = await tenant_db.create_work_schedule(
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


@router.put("/schedules/{schedule_id}")
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
    ok = await tenant_db.update_work_schedule(schedule_id, account_id=user["account_id"], **kwargs)
    if not ok:
        raise HTTPException(status_code=404, detail="Schedule not found")
    return {"ok": True}


@router.delete("/schedules/{schedule_id}")
async def delete_schedule(
    schedule_id: int,
    user: dict = Depends(require_permission("can_manage_account")),
    tenant_db=Depends(get_tenant_db),
):
    """Delete a work schedule."""
    sched = await tenant_db.get_work_schedule(schedule_id, account_id=user["account_id"])
    if not sched:
        raise HTTPException(status_code=404, detail="Schedule not found")
    await tenant_db.delete_work_schedule(schedule_id, account_id=user["account_id"])
    await tenant_db.add_audit_log(
        user["account_id"], int(user["sub"]),
        "schedule_delete",
        target_type="schedule", target_id=str(schedule_id),
    )
    return {"ok": True}
