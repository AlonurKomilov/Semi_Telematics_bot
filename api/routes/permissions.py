"""API endpoints for managing role permissions per account."""

import logging
from dataclasses import asdict, fields as dc_fields
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from api.deps import require_permission, get_platform_db, get_tenant_db
from database.models import Role
from permissions import (
    ROLE_PERMISSIONS,
    FeatureSet,
    get_account_permissions,
    invalidate_permissions_cache,
)

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/admin/permissions", tags=["permissions"])

VALID_ROLES = {r.value for r in Role}
VALID_FIELDS = {f.name for f in dc_fields(FeatureSet)}


class UpdatePermissionsRequest(BaseModel):
    role: str
    permissions: dict[str, bool]
    company_id: Optional[int] = None


@router.get("/roles")
async def get_all_roles(
    user: dict = Depends(require_permission("can_manage_account")),
    platform_db=Depends(get_platform_db),
):
    """Get permission sets for all roles in the account.

    Returns both the current (possibly customized) sets and the defaults.
    """
    account_id = user["account_id"]

    # Current permissions (DB or fallback)
    current = {}
    for role in Role:
        perms = await get_account_permissions(role, account_id)
        current[role.value] = asdict(perms)

    # Factory defaults
    defaults = {
        role.value: asdict(fs) for role, fs in ROLE_PERMISSIONS.items()
    }

    return {"current": current, "defaults": defaults, "fields": sorted(VALID_FIELDS)}


@router.get("/roles/overrides")
async def get_company_overrides(
    user: dict = Depends(require_permission("can_manage_account")),
    platform_db=Depends(get_platform_db),
    tenant_db=Depends(get_tenant_db),
):
    """Get companies and their permission overrides for the account."""
    account_id = user["account_id"]

    # Fetch companies from tenant DB
    companies = await tenant_db.get_account_companies(account_id, active_only=True)
    company_list = [
        {"id": c.id, "code": c.code, "display_name": c.display_name}
        for c in companies
    ]

    # Fetch all company-specific overrides from platform DB
    overrides = await platform_db.get_company_overrides(account_id)

    # Group by company_id -> list of roles
    override_map: dict[int, list[str]] = {}
    override_perms: dict[str, dict] = {}  # "company_id:role" -> permissions
    for ov in overrides:
        cid = ov["company_id"]
        override_map.setdefault(cid, []).append(ov["role"])
        override_perms[f"{cid}:{ov['role']}"] = ov["permissions"]

    return {
        "companies": company_list,
        "overrides": override_map,
        "override_perms": override_perms,
    }


@router.get("/roles/{role}")
async def get_role_perms(
    role: str,
    company_id: Optional[int] = None,
    user: dict = Depends(require_permission("can_manage_account")),
    platform_db=Depends(get_platform_db),
):
    """Get permission set for a specific role."""
    if role not in VALID_ROLES:
        raise HTTPException(400, f"Invalid role: {role}")

    account_id = user["account_id"]
    role_enum = Role(role)
    perms = await get_account_permissions(role_enum, account_id, company_id)
    default = asdict(ROLE_PERMISSIONS.get(role_enum, FeatureSet()))

    # Check if a company-specific override actually exists in DB
    has_override = False
    if company_id is not None:
        raw = await platform_db.get_role_permissions(account_id, role, company_id)
        # get_role_permissions with company_id checks company-specific first,
        # then falls back to account-wide. We only set has_override if the
        # company-specific row actually exists.
        async with platform_db.acquire() as conn:
            cur = await conn.execute(
                "SELECT 1 FROM role_permissions "
                "WHERE account_id = ? AND role = ? AND company_id = ?",
                (account_id, role, company_id),
            )
            has_override = (await cur.fetchone()) is not None

    return {
        "role": role,
        "company_id": company_id,
        "permissions": asdict(perms),
        "defaults": default,
        "has_override": has_override,
    }


@router.put("/roles")
async def update_role_perms(
    body: UpdatePermissionsRequest,
    user: dict = Depends(require_permission("can_manage_account")),
    platform_db=Depends(get_platform_db),
):
    """Update permission set for a role in the account."""
    if body.role not in VALID_ROLES:
        raise HTTPException(400, f"Invalid role: {body.role}")

    # Validate permission fields
    invalid_keys = set(body.permissions.keys()) - VALID_FIELDS
    if invalid_keys:
        raise HTTPException(400, f"Invalid permission flags: {sorted(invalid_keys)}")

    # Prevent removing own management access
    caller_role = user.get("role", "")
    if body.role == caller_role:
        if not body.permissions.get("can_manage_account", True):
            raise HTTPException(400, "Cannot remove your own management access")

    account_id = user["account_id"]
    updated_by = int(user["sub"])

    # Merge with defaults for any fields not provided
    role_enum = Role(body.role)
    current = await get_account_permissions(role_enum, account_id, body.company_id)
    merged = asdict(current)
    merged.update(body.permissions)

    await platform_db.set_role_permissions(
        account_id, body.role, merged, updated_by, body.company_id,
    )

    # Clear cache so all layers pick up changes immediately
    invalidate_permissions_cache(account_id)

    logger.info(
        "Permissions updated: account=%d role=%s by=%d",
        account_id, body.role, updated_by,
    )
    return {"ok": True, "role": body.role, "permissions": merged}


@router.post("/roles/reset")
async def reset_role_perms(
    body: UpdatePermissionsRequest,
    user: dict = Depends(require_permission("can_manage_account")),
    platform_db=Depends(get_platform_db),
):
    """Reset a role's permissions to factory defaults."""
    if body.role not in VALID_ROLES:
        raise HTTPException(400, f"Invalid role: {body.role}")

    account_id = user["account_id"]
    updated_by = int(user["sub"])
    role_enum = Role(body.role)

    default = asdict(ROLE_PERMISSIONS.get(role_enum, FeatureSet()))
    await platform_db.set_role_permissions(
        account_id, body.role, default, updated_by, body.company_id,
    )
    invalidate_permissions_cache(account_id)

    return {"ok": True, "role": body.role, "permissions": default}


@router.post("/roles/reset-all")
async def reset_all_perms(
    user: dict = Depends(require_permission("can_manage_account")),
    platform_db=Depends(get_platform_db),
):
    """Reset ALL role permissions for the account to factory defaults."""
    account_id = user["account_id"]
    updated_by = int(user["sub"])

    for role, fs in ROLE_PERMISSIONS.items():
        await platform_db.set_role_permissions(
            account_id, role.value, asdict(fs), updated_by,
        )

    invalidate_permissions_cache(account_id)
    return {"ok": True, "message": "All role permissions reset to defaults"}


class DeleteOverrideRequest(BaseModel):
    role: str
    company_id: int


@router.post("/roles/delete-override")
async def delete_company_override(
    body: DeleteOverrideRequest,
    user: dict = Depends(require_permission("can_manage_account")),
    platform_db=Depends(get_platform_db),
):
    """Delete a company-specific permission override (revert to account-wide)."""
    if body.role not in VALID_ROLES:
        raise HTTPException(400, f"Invalid role: {body.role}")

    account_id = user["account_id"]
    deleted = await platform_db.delete_role_permissions(
        account_id, body.role, body.company_id,
    )
    if not deleted:
        raise HTTPException(404, "No company override found for this role")

    invalidate_permissions_cache(account_id)

    logger.info(
        "Company override deleted: account=%d role=%s company=%d by=%d",
        account_id, body.role, body.company_id, int(user["sub"]),
    )
    return {"ok": True, "role": body.role, "company_id": body.company_id}
