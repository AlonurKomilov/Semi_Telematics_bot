"""Admin API endpoints — users, companies, invites, audit log, settings, schedules."""

import logging
import os
import time
from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field
from typing import Optional

from interfaces.api.deps import require_permission, get_tenant_db, get_platform_db
from adapters.storage.models import Role
from capabilities.iam.permissions import validate_role_change, role_rank

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/admin", tags=["admin"])


# ── Users ─────────────────────────────────────────────────────

@router.get("/users")
async def list_users(
    user: dict = Depends(require_permission("can_manage_users")),
    platform_db=Depends(get_platform_db),
):
    """List all users in the account."""
    users = await platform_db.list_account_users(user["account_id"])

    # Bulk-fetch truck assignments for all users
    truck_map: dict[int, list[str]] = {}
    company_map: dict[int, list[str]] = {}
    for u in users:
        trucks = await platform_db.get_user_truck_nums(u.id)
        if trucks:
            truck_map[u.id] = trucks
        codes = await platform_db.get_user_company_codes(u.id)
        if codes:
            company_map[u.id] = codes

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
            for u in users
        ],
        "count": len(users),
    }


# ── User avatar (Telegram profile photo) ─────────────────────

AVATAR_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(__file__))), "data", "avatars")
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

    os.makedirs(AVATAR_DIR, exist_ok=True)
    cached = os.path.join(AVATAR_DIR, f"{target.telegram_id}.jpg")

    # Serve from cache if fresh
    if os.path.exists(cached) and (time.time() - os.path.getmtime(cached)) < AVATAR_MAX_AGE:
        return FileResponse(cached, media_type="image/jpeg")

    # Fetch from Telegram
    try:
        from core.config import TELEGRAM_TOKEN as _token
        import telegram
        bot = telegram.Bot(token=_token)
        photos = await bot.get_user_profile_photos(user_id=target.telegram_id, limit=1)
        if not photos.photos:
            raise HTTPException(status_code=404, detail="No profile photo")

        # Get the largest size of the first photo
        photo_sizes = photos.photos[0]
        best = max(photo_sizes, key=lambda s: s.width * s.height)
        file = await bot.get_file(best.file_id)
        await file.download_to_drive(cached)

        return FileResponse(cached, media_type="image/jpeg")
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


# ── Truck assignments ─────────────────────────────────────────

class TruckAssignment(BaseModel):
    trucks: list[str] = Field(..., max_length=200)


@router.get("/users/{user_id}/trucks")
async def get_user_trucks(
    user_id: int,
    user: dict = Depends(require_permission("can_manage_users")),
    platform_db=Depends(get_platform_db),
):
    """Get all truck assignments for a user."""
    target = await platform_db.get_user(user_id)
    if not target or target.account_id != user["account_id"]:
        raise HTTPException(status_code=404, detail="User not found")

    trucks = await platform_db.get_user_trucks(user_id)
    return {
        "user_id": user_id,
        "trucks": [
            {
                "truck_num": t.truck_num,
                "is_primary": t.is_primary,
                "assigned_at": t.assigned_at,
            }
            for t in trucks
        ],
        "legacy_truck_num": target.truck_num,
    }


@router.put("/users/{user_id}/trucks")
async def set_user_trucks(
    user_id: int,
    body: TruckAssignment,
    user: dict = Depends(require_permission("can_manage_users")),
    platform_db=Depends(get_platform_db),
    tenant_db=Depends(get_tenant_db),
):
    """Set truck assignments for a user. First truck in list is primary."""
    target = await platform_db.get_user(user_id)
    if not target or target.account_id != user["account_id"]:
        raise HTTPException(status_code=404, detail="User not found")

    # Validate: non-empty strings only
    cleaned = [t.strip() for t in body.trucks if t.strip()]
    if len(cleaned) != len(set(cleaned)):
        raise HTTPException(status_code=400, detail="Duplicate truck numbers")

    trucks = await platform_db.set_user_trucks(
        user_id, target.account_id, cleaned, assigned_by=int(user["sub"]),
    )

    await tenant_db.add_audit_log(
        user["account_id"], int(user["sub"]),
        "truck_assignment",
        target_type="user", target_id=str(user_id),
        details=f"Trucks: {', '.join(cleaned) or 'none'}",
    )

    return {
        "ok": True,
        "trucks": [
            {"truck_num": t.truck_num, "is_primary": t.is_primary}
            for t in trucks
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
    for key in ("account_name", "alert_defaults", "timezone", "language", "digest_hour"):
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
    from adapters.crypto import encrypt

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
        from core.bot_registry import get_registry
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
        from core.bot_registry import get_registry
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
        from core.bot_registry import get_registry
        registry = get_registry()
        result["is_running"] = bool(registry and registry.get(user["account_id"]))
    except Exception:
        result["is_running"] = False

    # Fetch live bot info from Telegram
    try:
        from adapters.crypto import decrypt
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
