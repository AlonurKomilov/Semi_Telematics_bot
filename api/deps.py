"""FastAPI dependencies: auth, database, role checks."""

from fastapi import Depends, HTTPException, Header

from jose import JWTError

from api.auth import decode_jwt
from core.platform import get_router as _get_router
from permissions import can, get_account_permissions
from database import Role


async def get_current_user(authorization: str = Header(...)):
    """Extract and validate JWT from Authorization header.

    Returns the decoded token payload dict with keys:
    sub (telegram_id str), account_id, role, exp, iat.
    """
    if not authorization.startswith("Bearer "):
        raise HTTPException(status_code=401, detail="Invalid authorization header")

    token = authorization[7:]
    try:
        payload = decode_jwt(token)
    except JWTError:
        raise HTTPException(status_code=401, detail="Invalid or expired token")

    return payload


async def get_user_truck_num(user: dict) -> str | None:
    """Look up the driver's assigned truck_num from the DB."""
    platform_db = _get_router().platform
    db_user = await platform_db.get_user_by_telegram_id(int(user["sub"]))
    return db_user.truck_num if db_user else None


async def get_user_truck_nums(user: dict) -> list[str]:
    """Look up all assigned truck_nums for a driver from driver_trucks table.

    Falls back to users.truck_num if no junction table rows exist.
    """
    platform_db = _get_router().platform
    db_user = await platform_db.get_user_by_telegram_id(int(user["sub"]))
    if not db_user:
        return []
    trucks = await platform_db.get_user_truck_nums(db_user.id)
    if trucks:
        return trucks
    # Fallback: legacy single truck_num
    if db_user.truck_num:
        return [db_user.truck_num]
    return []


async def get_user_company_codes(user: dict) -> list[str]:
    """Get the company codes a user is restricted to.

    Returns empty list if user has no restrictions (all companies allowed).
    Owners always get empty list (unrestricted).
    """
    if user.get("role") == "owner":
        return []
    platform_db = _get_router().platform
    db_user = await platform_db.get_user_by_telegram_id(int(user["sub"]))
    if not db_user:
        return []
    return await platform_db.get_user_company_codes(db_user.id)


def validate_company_access(allowed_codes: list[str], requested_code: str | None) -> None:
    """Raise 403 if the user requests a company they don't have access to.

    If allowed_codes is empty, the user has access to all companies.
    If requested_code is None, no filtering is applied (allowed).
    """
    if not allowed_codes or not requested_code:
        return
    if requested_code.upper() not in {c.upper() for c in allowed_codes}:
        raise HTTPException(
            status_code=403,
            detail=f"You don't have access to company '{requested_code}'",
        )


def filter_by_allowed_companies(
    data: list[dict],
    allowed_codes: list[str],
    key: str = "_org",
) -> list[dict]:
    """Filter a list of dicts to only include items matching allowed companies.

    If allowed_codes is empty, returns all data (no restriction).
    """
    if not allowed_codes:
        return data
    allowed_upper = {c.upper() for c in allowed_codes}
    return [d for d in data if (d.get(key) or "").upper() in allowed_upper]


async def filter_by_assigned_trucks(
    data: list[dict],
    user: dict,
    name_key: str = "name",
) -> list[dict]:
    """Filter vehicle list to only assigned trucks when user is a driver.

    Non-driver roles get all data unfiltered.
    Drivers with no truck assignments also get all data (legacy behavior).
    """
    if user.get("role") != "driver":
        return data
    trucks = await get_user_truck_nums(user)
    if not trucks:
        return data
    needles = {t.lower() for t in trucks}
    return [d for d in data if any(n in (d.get(name_key) or "").lower() for n in needles)]


def require_permission(feature: str):
    """Dependency factory: check the user's role has a specific permission."""
    async def _check(user: dict = Depends(get_current_user)):
        perms = await get_account_permissions(Role(user["role"]), user["account_id"])
        if not getattr(perms, feature, False):
            raise HTTPException(status_code=403, detail="Insufficient permissions")
        return user
    return _check


def require_permission_any(*features: str):
    """Dependency factory: check that the user has ANY of the listed permissions.

    Returns a dict with the user payload + ``_matched_perm`` key indicating
    which permission was matched (useful for ``_all`` vs ``_own`` logic).
    """
    async def _check(user: dict = Depends(get_current_user)):
        perms = await get_account_permissions(Role(user["role"]), user["account_id"])
        for f in features:
            if getattr(perms, f, False):
                user["_matched_perm"] = f
                return user
        raise HTTPException(status_code=403, detail="Insufficient permissions")
    return _check


async def get_tenant_db(user: dict = Depends(get_current_user)):
    """Get the tenant database for the current user's account."""
    return await _get_router().get_tenant(user["account_id"])


async def get_platform_db():
    """Get the platform database."""
    return _get_router().platform
