"""FastAPI dependencies: auth, database, role checks, pagination."""

from fastapi import Cookie, Depends, HTTPException, Header

from jose import JWTError

from interfaces.api.auth import AUTH_COOKIE_NAME, decode_jwt
from infra.platform import get_router as _get_router
from capabilities.iam.permissions import get_account_permissions
from adapters.storage import Role

# ── Pagination defaults ──────────────────────────────────────────
_DEFAULT_PAGE_SIZE = 50
_MAX_PAGE_SIZE = 200


def paginate(items: list, page: int = 1, page_size: int = _DEFAULT_PAGE_SIZE) -> dict:
    """Apply offset-based pagination to a list and return a paginated response envelope.

    Returns dict with: items, page, page_size, total, total_pages.
    """
    page = max(1, page)
    page_size = max(1, min(page_size, _MAX_PAGE_SIZE))
    total = len(items)
    total_pages = max(1, (total + page_size - 1) // page_size)
    start = (page - 1) * page_size
    end = start + page_size
    return {
        "items": items[start:end],
        "page": page,
        "page_size": page_size,
        "total": total,
        "total_pages": total_pages,
    }


async def get_current_user(
    authorization: str | None = Header(default=None),
    auth_token: str | None = Cookie(default=None, alias=AUTH_COOKIE_NAME),
):
    """Extract and validate JWT from either the Authorization header
    or the cross-subdomain auth cookie.

    Both sources are accepted so the same dependency works for the
    desktop dashboard (cookie set on ``.4truck.us``), the Telegram Mini
    App (Bearer header from localStorage — WebView can't share cookies
    with the regular browser context), and direct API integrations
    (Bearer header).

    The Bearer header is tried FIRST; if it's present but invalid
    (stale localStorage token, expired or tampered) we fall through to
    the cookie rather than rejecting outright.  Without this fallback,
    a user whose dash.4truck.us localStorage still holds an old token
    from before the cross-subdomain rollout would get permanently
    locked out even when their fresh ``.4truck.us`` cookie is valid.

    Returns the decoded token payload dict with keys:
    sub (telegram_id str), account_id, role, exp, iat.
    """
    candidates: list[str] = []
    if authorization and authorization.startswith("Bearer "):
        candidates.append(authorization[7:])
    if auth_token:
        candidates.append(auth_token)

    if not candidates:
        raise HTTPException(status_code=401, detail="Not authenticated")

    last_error: JWTError | None = None
    for token in candidates:
        try:
            return decode_jwt(token)
        except JWTError as e:
            last_error = e
            continue

    # Every candidate token failed to decode — surface the last error so
    # logs say "Invalid" not "Not authenticated" (the credentials were
    # supplied; they're just no good).
    raise HTTPException(
        status_code=401,
        detail=f"Invalid or expired token: {last_error}" if last_error else "Invalid token",
    )


async def get_user_vehicle_num(user: dict) -> str | None:
    """Look up the driver's assigned truck_num from the DB."""
    platform_db = _get_router().platform
    db_user = await platform_db.get_user_by_telegram_id(int(user["sub"]))
    return db_user.truck_num if db_user else None


async def get_user_vehicle_nums(user: dict) -> list[str]:
    """Look up all assigned vehicle_nums for a driver from driver_trucks table.

    Falls back to users.truck_num if no junction table rows exist.
    """
    platform_db = _get_router().platform
    db_user = await platform_db.get_user_by_telegram_id(int(user["sub"]))
    if not db_user:
        return []
    trucks = await platform_db.get_user_vehicle_nums(db_user.id)
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
    trucks = await get_user_vehicle_nums(user)
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
    """Get the tenant database for the current user's account.

    The yield form lets us wrap the request in ``with_account`` so
    Postgres RLS (migration 057, gated by ENABLE_RLS) can filter every
    query by ``account_id``.  When RLS is off, ``with_account`` just
    SET-and-resets a session variable — harmless overhead.

    The previous return form returned the bare ``Database``; callers
    don't need to change since FastAPI handles both forms.
    """
    tenant = await _get_router().get_tenant(user["account_id"])
    async with tenant.with_account(user["account_id"]):
        yield tenant


async def get_platform_db():
    """Get the platform database."""
    return _get_router().platform


async def enforce_user_quota(account_id: int, platform_db=None) -> None:
    """Raise HTTP 429 if the account has reached its user quota.

    Looks up the account tier, then checks the active user count against
    ``QUOTA_MAX_USERS``.  Call before creating a new user.
    """
    from infra.config import QUOTA_MAX_USERS
    if platform_db is None:
        platform_db = _get_router().platform
    account = await platform_db.get_account(account_id)
    tier = (account.tier if account else None) or "free"
    limit = QUOTA_MAX_USERS.get(tier, QUOTA_MAX_USERS.get("free", 0))
    if limit == 0:
        return  # unlimited
    count = await platform_db.count_account_users(account_id)
    if count >= limit:
        raise HTTPException(
            status_code=429,
            detail=(
                f"Fleet user limit reached ({count}/{limit} users on the '{tier}' plan). "
                "Contact support or upgrade your plan to add more drivers and staff."
            ),
        )


async def enforce_company_quota(account_id: int, tenant_db=None) -> None:
    """Raise HTTP 429 if the account has reached its company quota.

    Looks up the account tier, then checks the active company count against
    ``QUOTA_MAX_COMPANIES``.  Call before creating a new company.
    """
    from infra.config import QUOTA_MAX_COMPANIES
    platform_db = _get_router().platform
    account = await platform_db.get_account(account_id)
    tier = (account.tier if account else None) or "free"
    limit = QUOTA_MAX_COMPANIES.get(tier, QUOTA_MAX_COMPANIES.get("free", 1))
    if limit == 0:
        return  # unlimited
    if tenant_db is None:
        tenant_db = await _get_router().get_tenant(account_id)
    count = await tenant_db.count_account_companies(account_id)
    if count >= limit:
        raise HTTPException(
            status_code=429,
            detail=(
                f"Samsara integration limit reached ({count}/{limit} orgs on the '{tier}' plan). "
                "Contact support or upgrade your plan to connect more fleet divisions."
            ),
        )
