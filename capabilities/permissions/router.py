"""API endpoints for managing role permissions per account.

router.py is interface-layer code co-located with its domain
(docs/FEATURES.md): router.py and config.py are the interface-layer pair — those two may
# import interfaces.api.deps; nothing else in the feature may.
"""

import json
import logging
from dataclasses import asdict, fields as dc_fields
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from interfaces.api.deps import require_permission, get_platform_db, get_tenant_db, resolve_user_id
from capabilities.activity_trail import record_simple
from adapters.storage.models import Role
from capabilities.permissions.roles import (
    ROLE_PERMISSIONS,
    FeatureSet,
    get_account_permissions,
    get_user_permissions,
    invalidate_permissions_cache,
    OWNER_PROTECTED_PERMS,
    DERIVED_SERVICE_FIELDS,
    MANAGER_GRANTS,
    TIER_GRANTS,
    perm_tier_key,
    senior_default_featureset,
    role_supports_manager,
    wire_perms,
    normalize_stored_perm_keys,
)

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/admin/permissions", tags=["permissions"])

VALID_ROLES = {r.value for r in Role}
VALID_FIELDS = {f.name for f in dc_fields(FeatureSet)}


async def _assert_company_belongs_to_account(
    tenant_db, account_id: int, company_id: Optional[int],
) -> None:
    """Defense-in-depth: when a permission route accepts a company_id
    from the request body, refuse the write unless the company is
    owned by the caller's account.

    Without this check, an Owner of Account A could send
    ``company_id=99`` where company 99 belongs to Account B and write
    a row tagged ``(account_id=A, company_id=99)``.  Today's read
    queries always filter by account_id so the row is functionally a
    no-op, but it pollutes the table and would become a real leak if
    any future query reads by company_id alone.  Validating here
    keeps the table clean and forecloses the future-bug surface.
    """
    if company_id is None:
        return
    co = await tenant_db.get_company_in_account(account_id, company_id)
    if co is None:
        raise HTTPException(
            status_code=404,
            detail=f"Company {company_id} not found in this account",
        )


def _permissions_diff(before: dict, after: dict) -> dict:
    """Return only the changed permission flags as ``{field: [old, new]}``.

    Used to keep audit_log details compact — a full FeatureSet dump
    would put 40 unchanged fields into every audit row.
    """
    keys = set(before) | set(after)
    return {k: [before.get(k), after.get(k)] for k in keys
            if before.get(k) != after.get(k)}


class UpdatePermissionsRequest(BaseModel):
    role: str
    permissions: dict[str, bool]
    company_id: Optional[int] = None
    # Which TIER of the role to edit: None/"base" = the base role's own perms;
    # "senior" = the role's senior tier (Full admin / Manager), stored + edited
    # INDEPENDENTLY under the ``{role}__manager`` key.
    tier: Optional[str] = None


@router.get("/roles")
async def get_all_roles(
    user: dict = Depends(require_permission("can_manage_permissions")),
    platform_db=Depends(get_platform_db),
):
    """Get permission sets for all roles in the account.

    Returns both the current (possibly customized) sets and the defaults.
    """
    account_id = user["account_id"]

    # Current permissions (DB or fallback).  Each base role, plus — for tiered
    # roles — the SENIOR tier's own resolved perms under ``{role}__manager`` so
    # the two-level matrix shows + edits each tier independently.
    current = {}
    for role in Role:
        perms = await get_account_permissions(role, account_id)
        current[role.value] = wire_perms(perms)
        if role_supports_manager(role):
            senior = await get_user_permissions(role, account_id, is_manager=True)
            current[perm_tier_key(role, True)] = wire_perms(senior)
    # Co-owner tier — the restrictable owner (its own "owner__co" row).
    co_owner = await get_user_permissions(Role.OWNER, account_id, is_primary_owner=False)
    current["owner__co"] = wire_perms(co_owner)

    # Factory defaults
    defaults = {
        role.value: wire_perms(fs) for role, fs in ROLE_PERMISSIONS.items()
    }

    # Manager-tier grants (role → the extra flags a MANAGER of that role gets).
    # The matrix marks these cells as manager-only rather than adding columns —
    # "manager" is a per-user tier (is_manager), not a role.  Code-defined
    # (MANAGER_GRANTS), so shown read-only.
    manager_grants = {
        role.value: sorted(flags) for role, flags in MANAGER_GRANTS.items()
    }

    # Per-role TIERS — labels + grants for the two-level Role→Tier matrix.
    # A role here has a senior tier (Manager / Full admin) grantable per-user
    # via ``is_manager``; the senior column = base + these grants.
    tiers = {
        role.value: {
            "senior_label": t.senior_label,
            "base_label": t.base_label,
            "grants": sorted(t.grants),
        }
        for role, t in TIER_GRANTS.items()
    }

    # How many ACTIVE people each role holds — the blast radius of a
    # toggle.  Counts only, no names: the page shows a number on the tab
    # so "turn this off for Fleet" reads as seven people, and a role with
    # nobody in it visibly recedes instead of looking like the others.
    # Folded into this payload rather than a second endpoint — the page
    # already fetches it, and one GROUP BY costs less than a route with
    # its own permission question.
    payload = {
        "current": current, "defaults": defaults,
        "fields": sorted(VALID_FIELDS),
        "manager_grants": manager_grants,
        "tiers": tiers,
    }
    try:
        payload["people"] = await platform_db.count_users_by_role(account_id)
    except Exception as e:
        # A count is decoration; the matrix is the page.  Never let it
        # take the permissions grid down with it.
        #
        # OMIT the key rather than sending {}: the caller of this endpoint
        # is themselves an active user of this account, so an empty map is
        # never a real answer — it could only mean the count failed.  Sent
        # as {}, the page would fill every tab with a confident "0" and dim
        # them, telling an owner that a toggle affects nobody at the one
        # moment nobody actually knows.  Absent means unknown, and the page
        # shows no number at all.
        logger.warning("role people-count failed for account %s: %s", account_id, e)
    return payload


@router.get("/roles/overrides")
async def get_company_overrides(
    user: dict = Depends(require_permission("can_manage_permissions")),
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
    user: dict = Depends(require_permission("can_manage_permissions")),
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
    user: dict = Depends(require_permission("can_manage_permissions")),
    platform_db=Depends(get_platform_db),
    tenant_db=Depends(get_tenant_db),
):
    """Update permission set for a role (or one of its tiers) in the account."""
    if body.role not in VALID_ROLES:
        raise HTTPException(400, f"Invalid role: {body.role}")

    is_senior = body.tier == "senior"
    is_co_owner = body.tier == "co"
    if is_senior and not role_supports_manager(body.role):
        raise HTTPException(400, f"Role '{body.role}' has no senior tier")
    if is_co_owner and body.role != "owner":
        raise HTTPException(400, "Only the owner role has a co-owner tier")

    # Validate permission fields
    # The canonical verb grammar maps onto legacy fields before
    # validation — a client speaking new names writes the same grant.
    # Pair VIEW verbs (an OR of two legacy fields) stay invalid as
    # writes: which field they mean is ambiguous until the storage
    # flip, and a 400 here is honest.
    body.permissions = normalize_stored_perm_keys(body.permissions)
    invalid_keys = set(body.permissions.keys()) - VALID_FIELDS
    if invalid_keys:
        raise HTTPException(400, f"Invalid permission flags: {sorted(invalid_keys)}")

    # Prevent removing own management access — a caller editing the tier they
    # themselves hold can't strip can_manage_permissions from under their feet.
    caller_role = user.get("role", "")
    caller_is_senior = bool(user.get("is_manager"))
    if body.role == caller_role and is_senior == caller_is_senior:
        if not body.permissions.get("can_manage_permissions", True):
            raise HTTPException(400, "Cannot remove your own management access")

    account_id = user["account_id"]
    updated_by = int(user["sub"])

    # Defense-in-depth: when a company_id is provided, refuse the
    # write unless the company belongs to the caller's account.
    await _assert_company_belongs_to_account(tenant_db, account_id, body.company_id)

    # Merge with the CURRENT set of the tier being edited (so unprovided fields
    # keep their value).  The senior tier reads/writes its OWN key
    # ({role}__manager) — independent from the base row.
    role_enum = Role(body.role)
    if is_co_owner:
        storage_key = "owner__co"
        current = await get_user_permissions(
            role_enum, account_id, is_primary_owner=False, company_id=body.company_id,
        )
    elif is_senior:
        storage_key = perm_tier_key(role_enum, True)
        current = await get_user_permissions(
            role_enum, account_id, is_manager=True, company_id=body.company_id,
        )
    else:
        storage_key = body.role
        current = await get_account_permissions(role_enum, account_id, body.company_id)
    before = asdict(current)
    merged = dict(before)
    merged.update(body.permissions)

    # Alerts inbox + AI chat are DERIVED service surfaces (see
    # derive_service_perms): access follows the role's feature permissions,
    # never a stored toggle.  Strip them from both the persisted row and the
    # diff base so the override row stays honest and the resolver's derivation
    # remains the single source of truth.  (The current matrix still sends
    # these keys until the rows are removed in the frontend pass; accepting
    # and dropping them keeps that save working without a 422.)
    for _dk in DERIVED_SERVICE_FIELDS:
        merged.pop(_dk, None)
        before.pop(_dk, None)

    # Owner lockout protection — the owner can never lose the account-control
    # permissions that are the only way back from a misconfiguration.  Base
    # owner row only (senior tiers are admin/recruiter, never owner).
    # Only the PRIMARY owner row (base "owner") is escape-hatch-locked.  The
    # co-owner tier is intentionally restrictable, so it is NOT force-locked.
    if body.role == "owner" and not is_senior and not is_co_owner:
        for _k in OWNER_PROTECTED_PERMS:
            merged[_k] = True

    await platform_db.set_role_permissions(
        account_id, storage_key, merged, updated_by, body.company_id,
    )

    # Clear cache so all layers pick up changes immediately
    invalidate_permissions_cache(account_id)

    # Audit log — record only the diff so a future Owner asking
    # "who changed this and when" gets a clean per-flag answer
    # instead of a 40-row before/after dump.
    diff = _permissions_diff(before, merged)
    scope = f"company={body.company_id}" if body.company_id else "account-wide"
    audit_role = f"{body.role} ({body.tier})" if body.tier else body.role
    target_id = f"{audit_role}:{body.company_id}" if body.company_id else audit_role
    try:
        await record_simple(
            tenant_db, account_id, await resolve_user_id(user),
            "permissions_update", "role", target_id,
            # the per-flag old→new diff IS the trail's native shape
            changes={k: {"from": v[0], "to": v[1]} for k, v in diff.items()},
            context={"scope": scope},
        )
    except Exception as e:
        # Trail failures must not block the permission write itself —
        # the write already succeeded and is the operationally
        # important part.
        logger.warning("Permissions trail write failed: %s", e)

    logger.info(
        "Permissions updated: account=%d role=%s scope=%s by=%d changed=%d",
        account_id, body.role, scope, updated_by, len(diff),
    )
    return {"ok": True, "role": body.role, "permissions": merged}


@router.post("/roles/reset")
async def reset_role_perms(
    body: UpdatePermissionsRequest,
    user: dict = Depends(require_permission("can_manage_permissions")),
    platform_db=Depends(get_platform_db),
    tenant_db=Depends(get_tenant_db),
):
    """Reset a role's permissions to factory defaults."""
    if body.role not in VALID_ROLES:
        raise HTTPException(400, f"Invalid role: {body.role}")

    account_id = user["account_id"]
    updated_by = int(user["sub"])
    role_enum = Role(body.role)

    # Defense-in-depth: verify company_id belongs to caller's account.
    await _assert_company_belongs_to_account(tenant_db, account_id, body.company_id)

    default = asdict(ROLE_PERMISSIONS.get(role_enum, FeatureSet()))
    await platform_db.set_role_permissions(
        account_id, body.role, default, updated_by, body.company_id,
    )
    invalidate_permissions_cache(account_id)

    scope = f"company={body.company_id}" if body.company_id else "account-wide"
    target_id = f"{body.role}:{body.company_id}" if body.company_id else body.role
    try:
        await record_simple(
            tenant_db, account_id, await resolve_user_id(user),
            "permissions_reset", "role", target_id,
            context={"scope": scope},
        )
    except Exception as e:
        logger.warning("Permissions audit-log write failed: %s", e)

    return {"ok": True, "role": body.role, "permissions": default}


@router.post("/roles/reset-all")
async def reset_all_perms(
    user: dict = Depends(require_permission("can_manage_permissions")),
    platform_db=Depends(get_platform_db),
    tenant_db=Depends(get_tenant_db),
):
    """Reset ALL role permissions for the account to factory defaults."""
    account_id = user["account_id"]
    updated_by = int(user["sub"])

    for role, fs in ROLE_PERMISSIONS.items():
        await platform_db.set_role_permissions(
            account_id, role.value, asdict(fs), updated_by,
        )

    invalidate_permissions_cache(account_id)

    try:
        await record_simple(
            tenant_db, account_id, await resolve_user_id(user),
            "permissions_reset_all", "account", account_id,
        )
    except Exception as e:
        logger.warning("Permissions audit-log write failed: %s", e)

    return {"ok": True, "message": "All role permissions reset to defaults"}


class DeleteOverrideRequest(BaseModel):
    role: str
    company_id: int


@router.post("/roles/delete-override")
async def delete_company_override(
    body: DeleteOverrideRequest,
    user: dict = Depends(require_permission("can_manage_permissions")),
    platform_db=Depends(get_platform_db),
    tenant_db=Depends(get_tenant_db),
):
    """Delete a company-specific permission override (revert to account-wide)."""
    if body.role not in VALID_ROLES:
        raise HTTPException(400, f"Invalid role: {body.role}")

    account_id = user["account_id"]
    updated_by = int(user["sub"])

    # Defense-in-depth: verify company_id belongs to caller's account
    # BEFORE attempting the delete.  Without this an attacker could
    # probe whether arbitrary company ids exist by checking 404 vs
    # success — and on success would write an audit row referencing a
    # cross-account company id.
    await _assert_company_belongs_to_account(tenant_db, account_id, body.company_id)

    deleted = await platform_db.delete_role_permissions(
        account_id, body.role, body.company_id,
    )
    if not deleted:
        raise HTTPException(404, "No company override found for this role")

    invalidate_permissions_cache(account_id)

    try:
        await record_simple(
            tenant_db, account_id, await resolve_user_id(user),
            "permissions_override_deleted", "role",
            f"{body.role}:{body.company_id}",
            context={"company_id": body.company_id},
        )
    except Exception as e:
        logger.warning("Permissions audit-log write failed: %s", e)

    logger.info(
        "Company override deleted: account=%d role=%s company=%d by=%d",
        account_id, body.role, body.company_id, updated_by,
    )
    return {"ok": True, "role": body.role, "company_id": body.company_id}


