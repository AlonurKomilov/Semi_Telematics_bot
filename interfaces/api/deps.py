"""FastAPI dependencies: auth, database, role checks, pagination."""

import asyncio
import logging
import time as _time
from datetime import datetime, timezone

from fastapi import Cookie, Depends, HTTPException, Header, Request

from jose import JWTError

from interfaces.api.auth import AUTH_COOKIE_NAME, decode_jwt, is_jti_revoked
from infra.platform import get_router as _get_router
from capabilities.permissions.roles import get_user_permissions
from adapters.storage import Role

_log = logging.getLogger(__name__)

# Throttled last-seen heartbeats.  Each authenticated request fires at
# most one UPDATE per user (keyed by telegram_id) AND at most one UPDATE
# per session (keyed by jti) per _LAST_SEEN_THROTTLE_S seconds.  The two
# throttles are independent so a user with multiple browsers gets a
# heartbeat per browser without one drowning out the others.
# Process-local dicts — with multiple workers the throttle is per-worker,
# but worst case is still ~one tiny UPDATE per minute per user/session.
_LAST_SEEN_THROTTLE_S = 60.0
_user_touched: dict[int, float] = {}
_session_touched: dict[str, float] = {}
# Keep strong refs to fire-and-forget tasks so they aren't GC'd before
# the UPDATE actually runs.
_bg_tasks: set[asyncio.Task] = set()


def _throttle_take(bucket: dict, key, window: float) -> bool:
    now = _time.monotonic()
    last = bucket.get(key, 0.0)
    if now - last < window:
        return False
    bucket[key] = now
    return True


async def _touch_user_safe(tg_id: int) -> None:
    try:
        platform_db = _get_router().platform
        now_iso = datetime.now(timezone.utc).isoformat()
        await platform_db.touch_user_last_seen(tg_id, now_iso)
    except Exception as e:
        _log.debug("users.last_seen touch failed for tg=%s: %s", tg_id, e)


async def _touch_session_safe(jti: str) -> None:
    try:
        platform_db = _get_router().platform
        now_iso = datetime.now(timezone.utc).isoformat()
        await platform_db.touch_user_session_last_seen(jti, now_iso)
    except Exception as e:
        _log.debug("user_sessions.last_seen touch failed for jti=%s: %s", jti, e)


def _spawn(coro) -> None:
    try:
        task = asyncio.create_task(coro)
        _bg_tasks.add(task)
        task.add_done_callback(_bg_tasks.discard)
    except RuntimeError:
        # No running loop (shouldn't happen inside an async handler) —
        # silently bail rather than blocking auth.
        pass


def _fire_heartbeats(payload: dict) -> None:
    """Fire user + session last_seen heartbeats from a decoded JWT.

    Each is independently throttled so multi-browser users get a per-
    browser heartbeat.  Old tokens (pre-jti rollout) carry no ``jti``
    claim and skip the session stamp silently.
    """
    try:
        tg_id = int(payload.get("sub") or 0)
    except (TypeError, ValueError):
        tg_id = 0
    if tg_id and _throttle_take(_user_touched, tg_id, _LAST_SEEN_THROTTLE_S):
        _spawn(_touch_user_safe(tg_id))
    jti = str(payload.get("jti") or "")
    if jti and _throttle_take(_session_touched, jti, _LAST_SEEN_THROTTLE_S):
        _spawn(_touch_session_safe(jti))


# Positive-cache for the denylist check so we don't hit Redis on every
# authed request.  A revoked jti takes at most ``_JTI_POSITIVE_CACHE_S``
# seconds to take effect inside one worker (and is also enforced
# globally via Redis for newly-arriving JWTs).  Trades a small revoke
# latency for substantial Redis traffic savings.
_JTI_POSITIVE_CACHE_S = 15.0
_jti_ok_until: dict[str, float] = {}


async def _is_revoked_with_cache(jti: str) -> bool:
    if not jti:
        return False
    now = _time.monotonic()
    ok_until = _jti_ok_until.get(jti, 0.0)
    if ok_until > now:
        return False
    revoked = await is_jti_revoked(jti)
    if revoked:
        # Drop any positive cache entry — the jti is dead.  We
        # intentionally don't cache "revoked = True" because the Redis
        # entry already has a TTL that matches the JWT's life; double-
        # caching just adds memory pressure for the same effect.
        _jti_ok_until.pop(jti, None)
        return True
    _jti_ok_until[jti] = now + _JTI_POSITIVE_CACHE_S
    return False

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
    request: Request,
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
    saw_revoked = False
    for token in candidates:
        try:
            payload = decode_jwt(token)
        except JWTError as e:
            last_error = e
            continue
        # Decode succeeded — now check the revocation denylist before
        # we commit to this token.  Falls through to the next candidate
        # (stale Bearer + fresh cookie scenario) so a user whose old
        # localStorage holds a revoked jti can still get in via the SSO
        # cookie if it's signed in from a different login.  Done BEFORE
        # the heartbeat so a revoked session doesn't keep stamping
        # itself as active.
        jti = str(payload.get("jti") or "")
        if jti and await _is_revoked_with_cache(jti):
            saw_revoked = True
            continue
        _fire_heartbeats(payload)
        # Stamp the account onto the request so outer middleware (the
        # capacity metering counter) can attribute this request to a
        # customer without re-decoding the JWT.
        try:
            request.state.account_id = payload.get("account_id")
        except Exception:
            pass
        return payload

    if saw_revoked and last_error is None:
        raise HTTPException(
            status_code=401,
            detail="Session revoked. Sign in again.",
        )

    # Every candidate token failed to decode — surface the last error so
    # logs say "Invalid" not "Not authenticated" (the credentials were
    # supplied; they're just no good).
    raise HTTPException(
        status_code=401,
        detail=f"Invalid or expired token: {last_error}" if last_error else "Invalid token",
    )


async def resolve_user_id(user: dict) -> int:
    """Return the stable internal ``users.id`` for the current request.

    Resolution order:
      1. The ``uid`` claim from the JWT — present on every token minted
         after the user-id rollout.  No DB call, immediate return.
      2. Legacy fallback: look up by the ``sub`` claim (telegram_id).
         Path used only by old tokens that were issued before ``uid``
         existed; expires naturally with the legacy JWTs.

    Returns ``0`` when neither path resolves — caller decides whether
    that's a 401 or just a "ownership unknown" condition.  Most write
    sites should refuse to insert a row with ``created_by=0``; the
    audit-trail integrity comes from this column, so a missing user_id
    is operationally significant.
    """
    uid = user.get("uid")
    if uid:
        try:
            return int(uid)
        except (TypeError, ValueError):
            pass
    # Legacy JWT (no uid claim).  Look up by telegram_id.  This is the
    # path that disappears once every active session has rotated to a
    # uid-bearing token — at most JWT_EXPIRY_LONG_SECONDS after the
    # rollout.
    sub = user.get("sub")
    if sub is None:
        return 0
    platform_db = _get_router().platform
    try:
        db_user = await platform_db.get_user_by_telegram_id(int(sub))
    except (TypeError, ValueError):
        return 0
    return int(db_user.id) if db_user else 0


async def get_current_db_user(user: dict, platform_db=None):
    """Resolve the requesting user to a full ``User`` row.

    Prefers the ``uid`` claim (always present on tokens minted after the
    user-id rollout) so it works for email-only accounts whose
    ``telegram_id`` is NULL.  Falls back to the legacy ``sub`` claim
    (telegram_id) for tokens issued before that rollout — those expire
    naturally with the JWT TTL.

    ``platform_db`` is optional so callers that already hold one don't
    have to re-fetch; pass it in to avoid a router lookup.

    Returns None if neither claim resolves to an active user — call
    sites decide whether that's a 401, a 404, or just "skip".
    """
    if platform_db is None:
        platform_db = _get_router().platform
    uid = user.get("uid")
    if uid:
        try:
            db_user = await platform_db.get_user_by_id(int(uid))
        except (TypeError, ValueError):
            db_user = None
        if db_user:
            return db_user
    sub = user.get("sub")
    if sub is None:
        return None
    try:
        return await platform_db.get_user_by_telegram_id(int(sub))
    except (TypeError, ValueError):
        return None


async def get_user_vehicle_num(user: dict) -> str | None:
    """Look up the driver's assigned truck_num from the DB."""
    db_user = await get_current_db_user(user)
    return db_user.truck_num if db_user else None


async def get_user_vehicle_nums(user: dict) -> list[str]:
    """Look up all assigned vehicle_nums for a driver from driver_trucks table.

    Falls back to users.truck_num if no junction table rows exist.
    """
    platform_db = _get_router().platform
    db_user = await get_current_db_user(user, platform_db)
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
    db_user = await get_current_db_user(user, platform_db)
    if not db_user:
        return []
    # Per-user company assignment (Team Management); empty = all companies.
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


def filter_by_company_map(
    rows: list[dict],
    allowed_codes: list[str],
    company_map: dict,
    key: str = "vehicle_name",
) -> list[dict]:
    """Company-filter rows whose company ISN'T on the row itself.

    For endpoints whose rows carry no company column (alerts → vehicle,
    drivers → user_id, coaching → samsara driver_id): the caller builds a
    ``row[key] → company-code(s)`` map and passes it here.

    FAIL-OPEN on ambiguity so legitimate data is never hidden:
      • ``allowed_codes`` empty → unrestricted → all rows.
      • ``company_map`` empty (source cold/unavailable) → all rows.
      • a row whose key isn't in the map (unresolved) → kept.
    A row is dropped ONLY when its company is KNOWN and excluded.  Map
    values may be a single code (str) or a list of codes.
    """
    if not allowed_codes or not company_map:
        return rows
    allowed_upper = {c.upper() for c in allowed_codes}
    out = []
    for r in rows:
        codes = company_map.get(r.get(key))
        if not codes:
            out.append(r)  # unresolved → keep (fail-open)
            continue
        if isinstance(codes, str):
            codes = [codes]
        if any((c or "").upper() in allowed_upper for c in codes):
            out.append(r)
    return out


async def get_user_vehicle_scope(user: dict) -> "VehicleScope | None":
    """The identity-aware visibility scope for a restricted user.

    ``None`` means unrestricted (non-driver roles, and — legacy
    behaviour kept deliberately — a driver with no assignments at all).
    """
    from capabilities.permissions.vehicle_scope import build_vehicle_scope
    if user.get("role") != "driver":
        return None
    trucks = await get_user_vehicle_nums(user)
    if not trucks:
        return None
    tenant = await _get_router().get_tenant(user["account_id"])
    return await build_vehicle_scope(tenant, user["account_id"], trucks)


async def filter_by_assigned_trucks(
    data: list[dict],
    user: dict,
    name_key: str = "name",
) -> list[dict]:
    """Filter vehicle list to only assigned trucks when user is a driver.

    Non-driver roles get all data unfiltered.
    Drivers with no truck assignments also get all data (legacy behavior).

    Membership is decided by the identity ladder in
    ``capabilities/permissions/vehicle_scope.py`` — registry id first,
    provider id next, exact name last.  The old comparison was a
    SUBSTRING over display names, which over-matched exactly where it
    mattered: a driver assigned 230 also saw 2303, and 100 saw trailer
    AK1001.  These are the rows a driver is ALLOWED to see, so an
    over-match was a disclosure.
    """
    scope = await get_user_vehicle_scope(user)
    if scope is None:
        return data
    if scope.empty:
        return []
    return [d for d in data if scope.allows_row(d, name_key=name_key)]


def require_permission(feature: str):
    """Dependency factory: check the user's role has a specific permission."""
    async def _check(user: dict = Depends(get_current_user)):
        perms = await get_user_permissions(
            Role(user["role"]), user["account_id"],
            is_manager=bool(user.get("is_manager")),
            is_primary_owner=bool(user.get("is_primary_owner")),
        )
        if not getattr(perms, feature, False):
            raise HTTPException(status_code=403, detail="Insufficient permissions")
        return user
    return _check


async def require_system_owner(user: dict = Depends(get_current_user)) -> dict:
    """Gate for cross-account operator endpoints (``/system/*``).

    Unlike ``require_permission``, this dep is **not** scoped to the
    user's own account — it grants access to *any* account in the
    platform.  The allowlist is the ``SYSTEM_OWNER_IDS`` env var (a
    comma-separated list of Telegram user ids), checked via
    ``is_system_owner``.

    Customer-account admins (Owner / Admin role) are NOT system owners
    by default.  This separation is what lets us put dangerous
    cross-tenant tools (account search, comp grant for someone else's
    fleet, force-sync) behind a single env-flip without leaking the
    surface to every paying tenant.
    """
    from capabilities.permissions.roles import is_system_owner
    try:
        tg_id = int(user.get("sub", 0))
    except (TypeError, ValueError):
        tg_id = 0
    if not is_system_owner(tg_id):
        raise HTTPException(
            status_code=403,
            detail="Restricted to platform operators (SYSTEM_OWNER_IDS).",
        )
    return user


def require_permission_any(*features: str):
    """Dependency factory: check that the user has ANY of the listed permissions.

    Returns a dict with the user payload + ``_matched_perm`` key indicating
    which permission was matched (useful for ``_all`` vs ``_own`` logic).
    """
    async def _check(user: dict = Depends(get_current_user)):
        perms = await get_user_permissions(
            Role(user["role"]), user["account_id"],
            is_manager=bool(user.get("is_manager")),
            is_primary_owner=bool(user.get("is_primary_owner")),
        )
        for f in features:
            if getattr(perms, f, False):
                user["_matched_perm"] = f
                # Effective per-account FeatureSet (overrides + manager
                # tier + module masks applied) — downstream reads gate
                # row-level visibility on it (e.g. the Alerts Board
                # filters alert TYPES to what the caller may access).
                user["_perms"] = perms
                return user
        raise HTTPException(status_code=403, detail="Insufficient permissions")
    return _check


# Roles allowed in the ``X-View-As`` header.  Mirrors the dashboard's
# PREVIEWABLE_ROLES (RoleViewContext.tsx) — keep in sync if the SPA
# adds a new persona.  ``driver`` is excluded: the dashboard
# persona-switcher hides it (drivers use the Mini App) and accepting
# it from a desktop request would let an Owner masquerade as a Driver
# to test per-vehicle scoping, which is a separate code path that
# already runs end-to-end on the dedicated driver_token surface.
_VALID_VIEW_ROLES: frozenset[str] = frozenset({
    "owner", "admin", "fleet", "safety", "dispatcher", "hr", "accounting",
    "recruiter",
})

# Roles whose user can preview ANY persona via X-View-As.  Everyone
# else can only "view as themselves" — sending a different role gets
# silently ignored.  Matches the dashboard's SWITCHABLE_ROLES list.
_SWITCHABLE_ROLES: frozenset[str] = frozenset({"owner", "admin"})


async def active_view(
    user: dict = Depends(get_current_user),
    x_view_as: str | None = Header(default=None, alias="X-View-As"),
) -> str:
    """Return the role the user is currently OPERATING AS for
    persona-aware filtering (Overview counts, Alerts badge, etc.).

    Resolution:
      • If ``X-View-As`` is absent → the user's JWT role (default,
        identical to pre-strict-binding behavior).
      • If present AND valid AND the user is allowed to preview that
        role (owner/admin can preview anything; others can only
        preview their own role) → that role.
      • Otherwise (unknown role, non-switchable user, etc.) → fall
        back to JWT role.  Never raises — the worst case is "user
        sees their default view," never a 4xx on a routine count
        endpoint.

    The header is the contract between the SPA's role switcher
    (RoleViewContext.activeView → ``X-View-As``) and persona-aware
    backend endpoints.  Backend endpoints that need persona scoping
    should ``Depends(active_view)`` and use the returned role for
    ``alert_types_for_role()`` lookups instead of reading
    ``user["role"]`` directly.
    """
    real_role = (user.get("role") or "").strip().lower()
    requested = (x_view_as or "").strip().lower()
    if not requested:
        return real_role
    if requested not in _VALID_VIEW_ROLES:
        return real_role
    if requested == real_role:
        return real_role
    if real_role in _SWITCHABLE_ROLES:
        return requested
    # Non-switchable user tried to view as a different role —
    # silently ignore.  No 403: the desktop dashboard can boot with a
    # stale ``X-View-As`` from before a logout/login, and we'd rather
    # serve the correct data than reject the request.
    return real_role


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
