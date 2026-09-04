"""Telegram initData validation, email/password auth, and JWT authentication."""

import hashlib
import hmac
import json
import os
import re
import secrets
import time
from typing import Literal
from urllib.parse import parse_qs, unquote

import bcrypt
from fastapi import APIRouter, Cookie, Header, HTTPException, Request, Response
from pydantic import BaseModel, Field

from jose import jwt
from jose.exceptions import JWTError
from interfaces.api.rate_limit import limiter

import adapters.storage as database
import logging

from infra.config import (
    # Customer-facing fallback bot token — used when an account has no
    # per-account bot for HMAC validation of the Login Widget / Mini
    # App initData.  See infra/config.py for the full split rationale.
    TELEGRAM_LOGIN_BOT_TOKEN,
    # System / operator bot token — never used for customer auth.  The
    # /api/auth/system-* endpoints below validate against this.
    TELEGRAM_SYSTEM_BOT_TOKEN,
)
# Legacy alias — preserved for any old call site that imports
# ``TELEGRAM_TOKEN`` from this module instead of from infra.config.
# Points at the system bot (matches what infra.config did before the
# split).  Net-new code should use the explicit names above.
TELEGRAM_TOKEN = TELEGRAM_SYSTEM_BOT_TOKEN

# JWT settings — JWT_SECRET is REQUIRED.  Previously this code fell back
# to TELEGRAM_TOKEN, but that meant any bot-token rotation silently
# invalidated every issued JWT and logged out the whole fleet.  The
# fail-fast at startup forces operators to set a stable, dedicated
# secret.  Tests provide one via conftest.py.
JWT_SECRET = os.getenv("JWT_SECRET", "").strip()
if not JWT_SECRET:
    raise RuntimeError(
        "JWT_SECRET is not set.  Generate one with `openssl rand -hex 32` "
        "and add it to .env before starting the API.  Without it the API "
        "cannot sign or verify access tokens, and the old fallback to "
        "TELEGRAM_TOKEN has been removed because rotating the bot token "
        "would silently log every dashboard/miniapp user out."
    )
if len(JWT_SECRET) < 32:
    logging.getLogger("api.auth").warning(
        "JWT_SECRET is shorter than 32 chars (%d). Recommended: "
        "openssl rand -hex 32 (gives 64 chars).", len(JWT_SECRET),
    )
JWT_ALGORITHM = "HS256"

# Two session lifetimes, picked at login time based on the
# "Remember me" checkbox.  The checkbox used to be cosmetic — it only
# controlled whether the client persisted the token in localStorage
# (survives browser restart) vs sessionStorage (cleared on close) but
# both paths got a 30-day JWT, so anyone who left their browser open
# stayed logged in for a month regardless of their preference.
#
# Now:
#   - "Remember me" checked  → 30-day token, persisted to localStorage
#   - "Remember me" unchecked → 8-hour token, persisted to sessionStorage
#                                (typical office shift, then re-auth)
#
# Both are env-overridable for ops flexibility.  The refresh endpoint
# preserves whichever lifetime the original login chose, by reading the
# ``remember`` claim baked into the JWT payload.
JWT_EXPIRY_LONG_SECONDS = int(
    os.getenv("JWT_EXPIRY_LONG_SECONDS", str(30 * 24 * 60 * 60))
)
JWT_EXPIRY_SHORT_SECONDS = int(
    os.getenv("JWT_EXPIRY_SHORT_SECONDS", str(8 * 60 * 60))
)

router = APIRouter(prefix="/auth", tags=["auth"])


class AuthRequest(BaseModel):
    init_data: str
    # ``remember_me`` defaults to False — clients must opt-in to
    # 30-day tokens.  The dashboard's Mini App / Login flows always
    # send an explicit value driven by the "Remember me" checkbox, so
    # this default only fires for clients that omit the field (e.g.
    # scripted callers).  Defaulting to False keeps the 8-hour short
    # session the safe baseline.
    remember_me: bool = False
    # Which client is signing in: the Chrome extension sends
    # ``"extension"`` (and gets a scoped token); the mini app and the
    # scripted callers send nothing.  Optional, because the handler
    # reads it unconditionally and a missing field is not an error —
    # it raised AttributeError on every mini-app login instead.
    client: str | None = None
class AuthResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"
    user: dict


def validate_telegram_init_data(init_data: str, bot_token: str) -> dict:
    """Validate Telegram Mini App initData using HMAC-SHA256.

    Returns the parsed user dict if valid.
    Raises ValueError if validation fails.
    """
    parsed = parse_qs(init_data, keep_blank_values=True)
    received_hash = parsed.get("hash", [None])[0]
    if not received_hash:
        raise ValueError("Missing hash in initData")

    # Build check string: sorted key=value pairs excluding hash
    data_pairs = []
    for key, values in parsed.items():
        if key == "hash":
            continue
        data_pairs.append(f"{key}={values[0]}")
    data_pairs.sort()
    data_check_string = "\n".join(data_pairs)

    # HMAC-SHA256 with WebAppData key
    secret_key = hmac.new(
        b"WebAppData", bot_token.encode(), hashlib.sha256
    ).digest()
    computed_hash = hmac.new(
        secret_key, data_check_string.encode(), hashlib.sha256
    ).hexdigest()

    if not hmac.compare_digest(computed_hash, received_hash):
        raise ValueError("Invalid initData signature")

    # Check auth_date is not too old (allow 24h window)
    auth_date = int(parsed.get("auth_date", ["0"])[0])
    if time.time() - auth_date > 86400:
        raise ValueError("initData expired")

    # Parse user JSON
    user_json = parsed.get("user", [None])[0]
    if not user_json:
        raise ValueError("Missing user in initData")

    return json.loads(unquote(user_json))


# ── Audience-scoped tokens ───────────────────────────────────────────
# A token for a client that needs a SLICE of the account, not all of
# it.  The browser extension shows a truck list; if its token is ever
# lifted off a machine it opens a truck list, not the account.  ``aud``
# names the client, ``scope`` lists the FeatureSet fields it may
# exercise — every other permission reads False for that token, however
# senior the person behind it.  Unknown audiences are rejected at decode.
EXTENSION_AUDIENCE = "extension"
KNOWN_AUDIENCES = frozenset({EXTENSION_AUDIENCE})
# Canonical first; the two legacy names ride along for one release so
# an installed extension that still checks the old scope string keeps
# connecting (interfaces/browser_extension/src/connect.ts moved to the
# canonical one).  Drop the legacy pair with the alias layer.
EXTENSION_SCOPE: tuple[str, ...] = ("can_view_location", "can_location_map", "can_location_vehicle")
#: Where a scoped token may go AT ALL — matched exactly by
#: deps.get_current_user after the /api and /api/v1 mount prefixes and a
#: trailing slash are removed; anything else is 403.  Not a prefix list:
#: "/map/" would admit every future login-only route mounted there.
#: The scope says what the token may DO, this says where it may KNOCK;
#: they live together so they are read together.  refresh and logout
#: decode the token themselves and never pass through the gate — they
#: are listed for intent.
EXTENSION_ROUTES: frozenset[str] = frozenset({
    "/map/vehicles", "/map/vehicles/live", "/extension/me",
    "/extension/vehicle-link",
    "/auth/refresh", "/auth/logout",
})


def create_jwt(
    telegram_id: int,
    account_id: int,
    role: str,
    *,
    remember_me: bool = False,
    jti: str | None = None,
    user_id: int | None = None,
    is_manager: bool = False,
    is_primary_owner: bool = False,
    aud: str | None = None,
    scope: "tuple[str, ...] | list[str] | None" = None,
) -> str:
    """Create a JWT token for an authenticated user.

    ``remember_me=True``  → 30-day TTL (long session).
    ``remember_me=False`` → 8-hour TTL (short session, default).

    Every token carries a ``jti`` (JWT id) claim so the issuing flow can
    record a row in ``user_sessions`` and the future revoke path can
    deny-list one device without affecting the user's other sessions.
    Callers that want to record the session pass in the jti they
    intend to insert; callers that just need a token (tests, legacy
    paths) can omit it and let one be generated.

    ``user_id`` is the stable internal ``users.id`` primary key.  When
    present it lands in the JWT as the ``uid`` claim — used downstream
    for ownership checks on records like KB articles, work orders, and
    PTI media, where comparing against the volatile ``telegram_id``
    breaks when a user later links/unlinks their Telegram account.
    Legacy callers that don't pass it still mint a working token;
    those tokens just need the telegram-id fallback path in
    ``resolve_user_id`` until they expire.
    """
    ttl = JWT_EXPIRY_LONG_SECONDS if remember_me else JWT_EXPIRY_SHORT_SECONDS
    now = int(time.time())
    # ``sub`` historically held the user's Telegram ID, which is also
    # the dictionary key the bot path uses.  For email-only users
    # whose Telegram isn't linked yet, ``telegram_id`` can be None /
    # 0; we fall back to ``user_id`` so the claim is always set to
    # SOMETHING usable (and the API's ``resolve_user_id`` helper
    # always prefers the explicit ``uid`` claim anyway).
    sub_value = str(telegram_id) if telegram_id else (
        str(user_id) if (user_id and user_id > 0) else "0"
    )
    payload = {
        "sub": sub_value,
        "account_id": account_id,
        "role": role,
        "remember": bool(remember_me),
        "exp": now + ttl,
        "iat": now,
        "jti": jti or secrets.token_urlsafe(16),
    }
    if user_id and user_id > 0:
        payload["uid"] = int(user_id)
    # Manager tier — carried so the permission overlay is stateless (no
    # per-request DB read).  Omitted when False to keep legacy tokens
    # forward-compatible; deps default a missing claim to False.
    if is_manager:
        payload["is_manager"] = True
    # Primary-owner flag — lets enforcement resolve the PRIMARY owner (full)
    # vs a CO-OWNER (restrictable) permission row without a DB read.
    if is_primary_owner:
        payload["is_primary_owner"] = True
    if aud:
        payload["aud"] = aud
        payload["scope"] = list(scope or ())
    return jwt.encode(payload, JWT_SECRET, algorithm=JWT_ALGORITHM)


# ── Session recording (Active Sessions panel) ─────────────────────────
#
# Each login that produces a JWT also writes a ``user_sessions`` row so
# the dashboard "My Profile / Active sessions" panel and the operator
# console can show where a user is logged in.  Captures: parsed device
# label, raw User-Agent, client IP (from X-Forwarded-For), the JWT's
# ``jti`` (so a future revoke can target a specific device), and the
# JWT's expiry.  Failures are warning-logged but never break auth.

def _parse_user_agent(ua: str) -> str:
    """Return a short, human-readable device label from a User-Agent.

    Best-effort regex-free parsing — good enough for an in-app display
    without pulling in a full UA-parser library.  Telegram's own apps
    set a UA string that contains ``Telegram`` so we surface those
    first; otherwise we fall back to a ``Browser on OS`` shape.
    """
    if not ua:
        return "Unknown device"
    ua_l = ua.lower()
    if "telegram" in ua_l:
        if "android" in ua_l:
            return "Telegram on Android"
        if "iphone" in ua_l or "ipad" in ua_l or " ios" in ua_l:
            return "Telegram on iOS"
        if "tdesktop" in ua_l or "telegramdesktop" in ua_l:
            return "Telegram Desktop"
        return "Telegram WebApp"
    if "iphone" in ua_l or "ipad" in ua_l:
        os_name = "iOS"
    elif "android" in ua_l:
        os_name = "Android"
    elif "windows" in ua_l:
        os_name = "Windows"
    elif "macintosh" in ua_l or "mac os" in ua_l:
        os_name = "macOS"
    elif "linux" in ua_l:
        os_name = "Linux"
    else:
        os_name = "Unknown OS"
    # Edge identifies as Chrome too, so check Edge first.
    if "edg/" in ua_l:
        browser = "Edge"
    elif "opr/" in ua_l or "opera" in ua_l:
        browser = "Opera"
    elif "chrome/" in ua_l:
        browser = "Chrome"
    elif "firefox/" in ua_l:
        browser = "Firefox"
    elif "safari/" in ua_l:
        browser = "Safari"
    else:
        browser = "Browser"
    return f"{browser} on {os_name}"


def _client_ip(request: Request) -> str:
    """Return the leftmost IP from X-Forwarded-For (nginx-set), falling
    back to ``request.client.host`` for direct connections."""
    xff = request.headers.get("x-forwarded-for", "").strip()
    if xff:
        return xff.split(",")[0].strip()[:64]
    return (request.client.host if request.client else "")[:64]


async def mint_session_token(
    db,
    request: Request,
    *,
    user_id: int,
    telegram_id: int,
    account_id: int,
    role: str,
    remember_me: bool,
    is_manager: bool = False,
    is_primary_owner: bool = False,
    aud: str | None = None,
    scope: "tuple[str, ...] | list[str] | None" = None,
    device_label: str | None = None,
    always_notify: bool = False,
) -> str:
    """Mint a JWT *and* record the session row in one shot.

    ``always_notify`` skips the new-device check: an explicit grant to a
    client (the browser extension) is always worth a notice, and label
    matching would otherwise hide a second, uninvited connection beside
    a legitimate one.

    Use this from every login flow in this module.  ``user_id`` is the
    integer PK from ``users``, NOT the Telegram id — needed so the
    sessions panel can list one user's devices without an extra
    telegram_id → users.id lookup per row.  When ``user_id <= 0``
    (legacy paths that don't have a backing DB user — e.g. the
    SSO-only owner fallback) we still mint the token but skip the
    session insert.

    Account-lifecycle gate lives here because this is the single choke
    point every login flow funnels through: a suspended account blocks
    EVERY role; an account in the 90-day deletion grace blocks every
    role EXCEPT the owner, who must still be able to sign in to hit
    "Reactivate account" on the dashboard.
    """
    from datetime import datetime, timezone

    if account_id and account_id > 0:
        try:
            lc = await db.get_account_lifecycle(account_id)
        except Exception:
            lc = None  # lifecycle read failure must not lock everyone out
        if lc:
            if lc.get("suspended_at"):
                raise HTTPException(
                    status_code=403,
                    detail={
                        "error_code": "account_suspended",
                        "message": (
                            "This account has been suspended. "
                            "Contact support if you believe this is a mistake."
                        ),
                    },
                )
            if lc.get("deleted_at") and role != "owner":
                raise HTTPException(
                    status_code=403,
                    detail={
                        "error_code": "account_deletion_pending",
                        "message": (
                            "This account is scheduled for deletion. "
                            "Only the account owner can sign in to reactivate it."
                        ),
                    },
                )

    jti = secrets.token_urlsafe(16)
    token = create_jwt(
        telegram_id, account_id, role,
        remember_me=remember_me, jti=jti,
        user_id=user_id if (user_id and user_id > 0) else None,
        is_manager=is_manager,
        is_primary_owner=is_primary_owner,
        aud=aud, scope=scope,
    )
    if user_id and user_id > 0:
        try:
            now_dt = datetime.now(timezone.utc)
            exp_ttl = JWT_EXPIRY_LONG_SECONDS if remember_me else JWT_EXPIRY_SHORT_SECONDS
            ua = (request.headers.get("user-agent") or "")[:500]
            # A client that names itself gets its own label in Active
            # Sessions — "Browser extension" is a different device from
            # "Chrome on Windows" even when it is the same laptop, because
            # it holds a different token.
            device_label = device_label or _parse_user_agent(ua)
            client_ip = _client_ip(request)
            # New-device sign-in notice (system.security) — checked BEFORE
            # the new session row is inserted so it can't match itself.
            # Both the check and the announce are non-fatal by construction.
            from interfaces.api.security_notifications import (
                announce_new_device_signin, is_new_device)
            notify_new_device = always_notify or await is_new_device(db, user_id, device_label)
            session_id = await db.create_user_session(
                user_id=user_id,
                jti=jti,
                device_label=device_label,
                user_agent=ua,
                ip=client_ip,
                created_at=now_dt.isoformat(),
                last_seen=now_dt.isoformat(),
                expires_at=datetime.fromtimestamp(
                    int(now_dt.timestamp()) + exp_ttl, tz=timezone.utc
                ).isoformat(),
            )
            if notify_new_device:
                await announce_new_device_signin(
                    db, account_id, user_id,
                    device_label=device_label, ip=client_ip,
                    session_id=session_id)
        except Exception as e:
            # Session bookkeeping is non-critical — never break login.
            logging.getLogger("api.auth").warning(
                "session record failed for user_id=%s: %s", user_id, e
            )
    return token


def decode_jwt(token: str) -> dict:
    """Decode and validate a JWT token. Raises JWTError on failure."""
    return jwt.decode(token, JWT_SECRET, algorithms=[JWT_ALGORITHM],
                      options={"verify_aud": False})


# ── JTI denylist (revoked sessions) ─────────────────────────────────
#
# When a session is revoked, two things happen in order:
#   1. ``user_sessions.revoked_at`` gets a timestamp (DB is source of
#      truth — the operator console + the user's own /profile reflect
#      the change immediately).
#   2. The jti is pushed onto a short-lived Redis flag with TTL equal
#      to the JWT's remaining lifetime.  ``get_current_user`` consults
#      the flag on every authenticated request and 401s if it hits.
#
# Redis is a fast-path cache, not the source of truth.  When Redis is
# unavailable both ``setex_flag`` and ``exists`` no-op (see infra.cache),
# which means revocation silently soft-degrades to "kicks in at JWT
# expiry".  That trade-off is acceptable for a fleet console — the row
# is still marked revoked in the DB and the operator can see it.

_DENYLIST_KEY_PREFIX = "revoked_jti:"


def _denylist_key(jti: str) -> str:
    return f"{_DENYLIST_KEY_PREFIX}{jti}"


async def mark_jti_revoked(jti: str, expires_at_iso: str | None = None) -> None:
    """Push a jti onto the Redis denylist for the remainder of its
    JWT lifetime.

    ``expires_at_iso`` is the original JWT expiry as stored in the
    ``user_sessions`` row.  We compute remaining-seconds-until-expiry
    and use that as the Redis TTL so the denylist entry naturally
    sloughs off once the JWT can't be used anyway.  When the column is
    missing or unparseable we fall back to the long JWT lifetime —
    over-deny is safer than under-deny.
    """
    if not jti:
        return
    from datetime import datetime, timezone
    from infra.cache import setex_flag
    ttl = JWT_EXPIRY_LONG_SECONDS
    if expires_at_iso:
        try:
            exp_dt = datetime.fromisoformat(expires_at_iso)
            if exp_dt.tzinfo is None:
                exp_dt = exp_dt.replace(tzinfo=timezone.utc)
            remaining = int((exp_dt - datetime.now(timezone.utc)).total_seconds())
            if remaining > 0:
                ttl = remaining
            else:
                return  # already expired — no point flagging it
        except (ValueError, TypeError):
            pass
    await setex_flag(_denylist_key(jti), ttl=ttl)


async def is_jti_revoked(jti: str) -> bool:
    """Return True when the jti is on the Redis denylist."""
    if not jti:
        return False
    from infra.cache import exists as _cache_exists
    try:
        return await _cache_exists(_denylist_key(jti))
    except Exception:
        # Soft-degrade: any unexpected error here is treated as "not
        # revoked" so a Redis hiccup can't lock everyone out of the
        # dashboard.  The DB row is still correct for audit / display.
        return False


# ── Cookie-based session for cross-subdomain SSO ──────────────────────────────
#
# Login at any *.4truck.us subdomain (or apex) sets an HttpOnly cookie scoped
# to ``.4truck.us`` so the same JWT is read on every subdomain — dash, fleet,
# dispatch, safety.  This eliminates the per-host login that localStorage-only
# auth forced, and lets a single Telegram Login Widget configured for one
# domain (4truck.us) work for every persona entry point.
#
# The cookie is set IN ADDITION to returning ``access_token`` in the response
# body so the Mini App (Telegram WebView, can't share cookies with the browser
# context) continues to function with the existing Bearer-header flow.  The
# server-side dependency in ``deps.get_current_user`` accepts either source.
#
# AUTH_COOKIE_DOMAIN defaults to ``.4truck.us`` so the cookie is sent to every
# subdomain.  Operators on a non-production host (preview deploys, local dev)
# override via the env var; setting it empty disables the cookie entirely.
AUTH_COOKIE_NAME = "auth_token"
AUTH_COOKIE_DOMAIN = os.getenv("AUTH_COOKIE_DOMAIN", ".4truck.us").strip() or None
# SameSite=Lax is correct here — the cookie should survive top-level
# navigations between subdomains (the role-based forward after login) but
# stay out of cross-site iframe contexts.  Strict would block the post-login
# redirect; None would expose us to CSRF for no benefit.
AUTH_COOKIE_SAMESITE: Literal["lax", "strict", "none"] = "lax"


def _set_auth_cookie(response: Response, token: str, *, remember_me: bool) -> None:
    """Attach the auth-token cookie to a response.

    Lifetime matches the JWT itself so the browser cleans the cookie up when
    the token expires; the cookie's Max-Age is the same TTL the JWT carries.
    ``HttpOnly`` keeps the token out of JS reach (no XSS exfiltration);
    ``Secure`` requires HTTPS (apex + every subdomain serve HTTPS in
    production); ``SameSite=Lax`` keeps the cookie out of cross-site contexts
    while still flowing on top-level navigations (the post-login redirect to
    a persona subdomain works).
    """
    if not AUTH_COOKIE_DOMAIN:
        return
    max_age = JWT_EXPIRY_LONG_SECONDS if remember_me else JWT_EXPIRY_SHORT_SECONDS
    response.set_cookie(
        AUTH_COOKIE_NAME,
        token,
        max_age=max_age,
        domain=AUTH_COOKIE_DOMAIN,
        path="/",
        secure=True,
        httponly=True,
        samesite=AUTH_COOKIE_SAMESITE,
    )


def _clear_auth_cookie(response: Response) -> None:
    """Expire the auth cookie on the same domain it was set.

    Some browsers (notably Chrome with strict cookie matching enabled)
    refuse to clear a cookie when the delete response's attributes
    diverge from the original Set-Cookie.  We therefore re-issue the
    same name/domain/path/secure/httponly/samesite tuple with an
    empty value and Max-Age=0 — guaranteed match, guaranteed clear.
    Starlette's bare ``delete_cookie`` omits secure+httponly+samesite,
    which is enough for most browsers but not all.
    """
    if not AUTH_COOKIE_DOMAIN:
        return
    response.set_cookie(
        AUTH_COOKIE_NAME,
        value="",
        max_age=0,
        expires=0,
        domain=AUTH_COOKIE_DOMAIN,
        path="/",
        secure=True,
        httponly=True,
        samesite=AUTH_COOKIE_SAMESITE,
    )


@router.post("/refresh", response_model=AuthResponse)
@limiter.limit("20/minute")
async def refresh_token(request: Request, response: Response, authorization: str = __import__("fastapi").Header(...)):
    """Refresh a JWT token. Issue a new token if the current one is still valid.

    The client should call this before the current token expires
    (e.g., when less than 1 hour remains).
    """
    from jose import JWTError as _JE
    from infra.platform import get_platform_db
    db = get_platform_db()

    if not authorization.startswith("Bearer "):
        raise HTTPException(status_code=401, detail="Invalid authorization header")

    token = authorization[7:]
    try:
        payload = decode_jwt(token)
    except _JE:
        raise HTTPException(status_code=401, detail="Invalid or expired token")
    # A revoked session does not get a fresh lease: a refresh would carry
    # its expiry past the denylist entry and the session would come back.
    if payload.get("jti") and await is_jti_revoked(str(payload["jti"])):
        raise HTTPException(status_code=401, detail="Session revoked. Sign in again.")

    telegram_id = int(payload["sub"])
    user = await db.get_user_by_telegram_id(telegram_id)
    if not user or not user.is_active:
        raise HTTPException(status_code=403, detail="User no longer active")

    # Preserve the original session length — if the user opted into a
    # long-lived session at login, refresh extends THAT, not a short
    # one.  Tokens issued before this field existed default to True
    # (the legacy 30-day behaviour) so refreshing them doesn't
    # accidentally shorten an active session.
    remember = bool(payload.get("remember", True))
    # Preserve the original session's jti so the same user_sessions row
    # is extended in place — refresh is "same browser, fresh token",
    # not a new session.  Legacy tokens (minted before user_sessions
    # existed) won't have a jti; we generate one and backfill a row.
    old_jti = str(payload.get("jti") or "").strip()
    new_jti = old_jti or secrets.token_urlsafe(16)
    new_token = create_jwt(
        user.telegram_id, user.account_id, user.role.value,
        remember_me=remember, jti=new_jti,
        user_id=user.id,
        # Re-read from DB so a manager-tier / owner-tier / role change
        # propagates to enforcement on the next token refresh.
        is_manager=user.is_manager,
        is_primary_owner=user.is_primary_owner,
        # Scope survives refresh: dropping it here would silently widen a
        # truck-list key into an account key every eight hours.
        aud=payload.get("aud"), scope=payload.get("scope"),
    )
    try:
        from datetime import datetime, timezone
        now_dt = datetime.now(timezone.utc)
        exp_ttl = JWT_EXPIRY_LONG_SECONDS if remember else JWT_EXPIRY_SHORT_SECONDS
        new_expires_iso = datetime.fromtimestamp(
            int(now_dt.timestamp()) + exp_ttl, tz=timezone.utc
        ).isoformat()
        if old_jti:
            await db.update_user_session_on_refresh(
                old_jti, new_expires_iso, now_dt.isoformat(),
            )
        else:
            # Legacy token without jti — backfill a session row so the
            # user shows up in the Active sessions panel from now on.
            ua = (request.headers.get("user-agent") or "")[:500]
            await db.create_user_session(
                user_id=user.id, jti=new_jti,
                device_label=_parse_user_agent(ua), user_agent=ua,
                ip=_client_ip(request),
                created_at=now_dt.isoformat(), last_seen=now_dt.isoformat(),
                expires_at=new_expires_iso,
            )
    except Exception as e:
        logging.getLogger("api.auth").warning(
            "session refresh bookkeeping failed for user=%s: %s",
            user.telegram_id, e,
        )
    # A scoped token stays a Bearer credential.  Setting the .4truck.us
    # cookie from it would overwrite the dashboard's session with a
    # two-permission key — or hand a lifted panel token a cookie.
    if not payload.get("aud"):
        _set_auth_cookie(response, new_token, remember_me=remember)
    return AuthResponse(
        access_token=new_token,
        user={
            "telegram_id": user.telegram_id,
            "name": user.display_name or "",
            "role": user.role.value,
            "account_id": user.account_id,
        },
    )


@router.post("/telegram", response_model=AuthResponse)
@limiter.limit("30/minute")
async def auth_telegram(request: Request, response: Response, body: AuthRequest):
    """Authenticate via Telegram Mini App initData.

    Supports per-account bot tokens: parses the user ID from initData first,
    looks up which account they belong to, then validates the HMAC with that
    account's bot token.  Falls back to the global TELEGRAM_TOKEN for legacy
    single-bot setups.
    """
    from infra.platform import get_platform_db
    db = get_platform_db()
    from infra.crypto import decrypt

    # Pre-parse user ID from initData (before HMAC validation)
    parsed = parse_qs(body.init_data, keep_blank_values=True)
    user_json = parsed.get("user", [None])[0]
    if not user_json:
        raise HTTPException(status_code=401, detail="Missing user in initData")
    try:
        tg_user_pre = json.loads(unquote(user_json))
    except (json.JSONDecodeError, TypeError):
        raise HTTPException(status_code=401, detail="Invalid user data")

    telegram_id = tg_user_pre.get("id")
    if not telegram_id:
        raise HTTPException(status_code=401, detail="Invalid user data")

    # Determine which bot token to validate against.  Per-account bot
    # wins when set (a tenant with their own branded bot signs their
    # Mini App initData with that token).  Otherwise the fallback is
    # the LOGIN bot, not the system one — Mini App callers are always
    # customers.
    user = await db.get_user_by_telegram_id(telegram_id)
    bot_token = TELEGRAM_LOGIN_BOT_TOKEN or ""

    if user:
        account = await db.get_account(user.account_id)
        if account and account.bot_token_encrypted:
            bot_token = decrypt(account.bot_token_encrypted)

    try:
        tg_user = validate_telegram_init_data(body.init_data, bot_token)
    except ValueError as e:
        raise HTTPException(status_code=401, detail=str(e))

    if not user:
        raise HTTPException(
            status_code=403,
            detail="User not registered. Use the Telegram bot to register first.",
        )

    if body.client == EXTENSION_AUDIENCE:
        # The panel does not sign in with a password any more: an
        # extension token is minted ONLY by POST /extension/connect, after
        # the person confirms on the dashboard.  An old panel asking here
        # gets a plain answer, never an unscoped token.
        raise HTTPException(
            status_code=400,
            detail="Connect the browser extension from your 4truck dashboard.",
        )
    token = await mint_session_token(
        db, request,
        user_id=user.id, telegram_id=user.telegram_id,
        account_id=user.account_id, role=user.role.value,
        is_manager=user.is_manager,
        is_primary_owner=user.is_primary_owner,
        remember_me=body.remember_me,
    )
    _set_auth_cookie(response, token, remember_me=body.remember_me)
    return AuthResponse(
        access_token=token,
        user={
            "telegram_id": user.telegram_id,
            "name": tg_user.get("first_name", ""),
            "role": user.role.value,
            "account_id": user.account_id,
        },
    )


class LoginWidgetRequest(BaseModel):
    id: int
    first_name: str = ""
    last_name: str = ""
    username: str = ""
    photo_url: str = ""
    auth_date: int
    hash: str
    # NOT part of the signed Telegram payload — sent alongside it by
    # the dashboard.  Excluded from the hash check via the same empty-
    # value filter applied to optional Telegram fields, so the widget
    # signature stays intact.  Defaults to False so callers must
    # explicitly opt-in to 30-day tokens (the dashboard always sends
    # an explicit value from the "Remember me" checkbox).
    remember_me: bool = False
    # Which client is signing in: the Chrome extension sends
    # ``"extension"`` (and gets a scoped token); the mini app and the
    # scripted callers send nothing.  Optional, because the handler
    # reads it unconditionally and a missing field is not an error —
    # it raised AttributeError on every mini-app login instead.
    client: str | None = None
def validate_telegram_login_widget(data: dict, bot_token: str) -> None:
    """Validate Telegram Login Widget data using SHA256 HMAC.

    The Login Widget uses a different key derivation than Mini Apps:
    secret = SHA256(bot_token) instead of HMAC("WebAppData", bot_token).
    """
    received_hash = data.get("hash", "")
    check_pairs = []
    for key in sorted(data.keys()):
        if key == "hash":
            continue
        check_pairs.append(f"{key}={data[key]}")
    check_string = "\n".join(check_pairs)

    secret_key = hashlib.sha256(bot_token.encode()).digest()
    computed = hmac.new(
        secret_key, check_string.encode(), hashlib.sha256
    ).hexdigest()

    if not hmac.compare_digest(computed, received_hash):
        raise ValueError("Invalid login widget signature")

    auth_date = data.get("auth_date", 0)
    if isinstance(auth_date, str):
        auth_date = int(auth_date)
    if time.time() - auth_date > 86400:
        raise ValueError("Login data expired")


@router.post("/telegram-login", response_model=AuthResponse)
@limiter.limit("30/minute")
async def auth_telegram_login(request: Request, response: Response, body: LoginWidgetRequest):
    """Authenticate via Telegram Login Widget (desktop dashboard).

    Supports per-account bot tokens: looks up user → account → bot token
    before validating the login widget hash.
    """
    from infra.platform import get_platform_db
    db = get_platform_db()
    from infra.crypto import decrypt

    user = await db.get_user_by_telegram_id(body.id)

    # The widget payload is signed by whichever bot RENDERED it.  The
    # login page renders the platform LOGIN bot (see ``auth_config``);
    # a page cached from the account-branded era is signed by the
    # user's account bot.  Accept either — never the system bot; this
    # endpoint is for customer dashboards only.  The operator console
    # at system.4truck.us hits ``/auth/system-telegram-login`` instead.
    candidate_tokens = [t for t in (TELEGRAM_LOGIN_BOT_TOKEN or "",) if t]
    if user:
        account = await db.get_account(user.account_id)
        if account and account.bot_token_encrypted:
            try:
                candidate_tokens.append(decrypt(account.bot_token_encrypted))
            except Exception as e:
                logging.getLogger("api.auth").debug(
                    "Could not decrypt account bot token: %s", e)

    # model_dump() includes all fields (even empty defaults like
    # last_name="", photo_url="").  Telegram only signs the fields
    # that were actually present in the widget callback, so we must
    # exclude keys whose value is an empty string.
    # ``remember_me`` is OUR field (not signed by Telegram), drop
    # it before hashing or the signature check fails.
    raw = {
        k: v for k, v in body.model_dump().items()
        if v != "" and k != "remember_me"
    }
    last_error: ValueError | None = ValueError("no bot token configured")
    for bot_token in candidate_tokens:
        try:
            validate_telegram_login_widget(raw, bot_token)
            last_error = None
            break
        except ValueError as e:
            last_error = e
    if last_error is not None:
        raise HTTPException(status_code=401, detail=str(last_error))

    if not user:
        raise HTTPException(
            status_code=403,
            detail="User not registered. Use the Telegram bot to register first.",
        )

    if body.client == EXTENSION_AUDIENCE:
        # The panel does not sign in with a password any more: an
        # extension token is minted ONLY by POST /extension/connect, after
        # the person confirms on the dashboard.  An old panel asking here
        # gets a plain answer, never an unscoped token.
        raise HTTPException(
            status_code=400,
            detail="Connect the browser extension from your 4truck dashboard.",
        )
    token = await mint_session_token(
        db, request,
        user_id=user.id, telegram_id=user.telegram_id,
        account_id=user.account_id, role=user.role.value,
        is_manager=user.is_manager,
        is_primary_owner=user.is_primary_owner,
        remember_me=body.remember_me,
    )
    _set_auth_cookie(response, token, remember_me=body.remember_me)
    return AuthResponse(
        access_token=token,
        user={
            "telegram_id": user.telegram_id,
            "name": body.first_name,
            "role": user.role.value,
            "account_id": user.account_id,
        },
    )


# ── Email / password auth ────────────────────────────────────

_EMAIL_RE = re.compile(r"^[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}$")


def _signup_base_url() -> str:
    """The apex origin where the public ``/signup/<code>`` route lives.

    Honors ``AUTH_BASE_URL`` (preferred) → ``DASHBOARD_BASE_URL`` (legacy)
    → ``https://4truck.us`` (default).  Same env precedence the email
    sender uses, so invite emails AND clipboard URLs always point at
    the apex where Login.tsx is reachable — never at a persona
    subdomain (``dash./app./api./bot.``) which would 404 the unauth
    visitor.  Frontend reads this from ``/auth/config`` to build
    the URL-channel clipboard string.
    """
    return (
        os.getenv("AUTH_BASE_URL")
        or os.getenv("DASHBOARD_BASE_URL")
        or "https://4truck.us"
    ).rstrip("/")


@router.get("/config")
async def auth_config(request: Request):
    """Return public auth config (bot username for Telegram Login Widget
    + signup_base_url for the URL-channel invite clipboard).

    The widget bot is ALWAYS the platform's LOGIN bot.  It used to be
    "the first configured account bot", which put a TENANT's bot on the
    platform's public login page — meaning login for everyone depended
    on that account owner running /setdomain on their own bot in
    BotFather (owner's ruling 2026-08-19: a tenant bot is the tenant's
    to manage; the platform must not require anything of it).  Deep
    links (invites, bot-login) still use branded account bots — those
    are t.me links and never need a domain; only this web widget does.
    Never the SYSTEM bot here — that one is for platform operators.

    Login verification accepts a widget payload signed by EITHER the
    login bot or the user's account bot (see ``auth_telegram_login``),
    so pages cached from before this change keep working.

    ``signup_base_url`` is the apex origin where /signup/<code> works —
    used by the dashboard's invite "Copy URL" / "URL link channel"
    to build a recipient-facing link that lands on Login.tsx (which
    lives on the apex, NOT on dash./app./api. subdomains).
    """
    from interfaces.bot.config import bot_username as global_bot_username
    signup_base = _signup_base_url()

    # Resolve the username via getMe on TELEGRAM_LOGIN_BOT_TOKEN
    # (cached) rather than reading the shared ``bot.config.bot_username``
    # global — that global collapses to the system bot when
    # LOGIN_BOT_TOKEN is unset, which used to silently bypass the
    # "never return SYSTEM" promise.
    _bot_id = ""
    if TELEGRAM_LOGIN_BOT_TOKEN and ":" in TELEGRAM_LOGIN_BOT_TOKEN:
        _bot_id = TELEGRAM_LOGIN_BOT_TOKEN.split(":", 1)[0]
    login_username = await _login_bot_username()
    return {
        "bot_username": login_username or global_bot_username or "4truckBot",
        "bot_id": _bot_id,
        "signup_base_url": signup_base,
        "turnstile_site_key": (os.getenv("TURNSTILE_SITE_KEY") or "").strip(),
    }


# ── System-operator auth (system.4truck.us) ──────────────────
#
# The operator console at system.4truck.us uses these two endpoints
# instead of the customer-side /auth/config + /auth/telegram-login
# pair.  Both validate against TELEGRAM_SYSTEM_BOT_TOKEN — never the
# login bot, never a per-account bot — and the login endpoint
# additionally gates by SYSTEM_OWNER_IDS so a stranger who somehow
# gets a valid Telegram login on the system bot still can't get a
# JWT.

_SYSTEM_BOT_USERNAME_CACHE: dict[str, str] = {}


async def _system_bot_username() -> str:
    """Resolve the system bot's @username via Telegram getMe, cached.

    The customer-side bot daemon caches its own username on post_init,
    but the system bot doesn't run as a daemon — it's just a Telegram
    API client.  So we call getMe lazily here and cache the result for
    the lifetime of the process.  Returns ``""`` on any failure so the
    UI can render a generic "Telegram operator login" message instead
    of crashing.
    """
    if "username" in _SYSTEM_BOT_USERNAME_CACHE:
        return _SYSTEM_BOT_USERNAME_CACHE["username"]
    if not TELEGRAM_SYSTEM_BOT_TOKEN:
        return ""
    try:
        import telegram as _telegram
        bot = _telegram.Bot(token=TELEGRAM_SYSTEM_BOT_TOKEN)
        me = await bot.get_me()
        username = me.username or ""
    except Exception:
        logging.getLogger("api.auth").exception("system bot getMe failed")
        username = ""
    _SYSTEM_BOT_USERNAME_CACHE["username"] = username
    return username


_LOGIN_BOT_USERNAME_CACHE: dict[str, str] = {}


async def _login_bot_username() -> str:
    """Resolve the customer LOGIN bot's @username via Telegram getMe, cached.

    Source-of-truth for the customer apex ``/login`` page and the
    bot-login deep-link generator.  Does NOT read
    ``interfaces.bot.config.bot_username`` — that global is whatever the
    daemon's ``post_init`` happened to set last, which collapses to the
    system bot when ``TELEGRAM_LOGIN_BOT_TOKEN`` is unset (the daemon
    falls back to the system token).  That fallback was masking the bug
    where the customer apex login deep-linked into the operator bot.

    Returns ``""`` on any failure so the caller can render a generic
    fallback rather than crashing.
    """
    if "username" in _LOGIN_BOT_USERNAME_CACHE:
        return _LOGIN_BOT_USERNAME_CACHE["username"]
    if not TELEGRAM_LOGIN_BOT_TOKEN:
        return ""
    try:
        import telegram as _telegram
        bot = _telegram.Bot(token=TELEGRAM_LOGIN_BOT_TOKEN)
        me = await bot.get_me()
        username = me.username or ""
    except Exception:
        logging.getLogger("api.auth").exception("login bot getMe failed")
        username = ""
    _LOGIN_BOT_USERNAME_CACHE["username"] = username
    return username


@router.get("/system-config")
async def auth_system_config():
    """Public config the system.4truck.us login page needs to render its widget.

    Returns the SYSTEM bot's username + numeric id (not the login bot,
    not any per-account bot).  Safe to call unauthenticated — the
    bot's @username is already public anyway.
    """
    username = await _system_bot_username()
    bot_id = ""
    if TELEGRAM_SYSTEM_BOT_TOKEN and ":" in TELEGRAM_SYSTEM_BOT_TOKEN:
        bot_id = TELEGRAM_SYSTEM_BOT_TOKEN.split(":", 1)[0]
    return {"bot_username": username or "", "bot_id": bot_id}


@router.post("/system-telegram-login", response_model=AuthResponse)
@limiter.limit("30/minute")
async def auth_system_telegram_login(
    request: Request, response: Response, body: LoginWidgetRequest,
):
    """Operator-only Telegram login for system.4truck.us.

    Two differences from ``/auth/telegram-login``:
      1. HMAC is validated against ``TELEGRAM_SYSTEM_BOT_TOKEN`` only —
         no per-account fallback (operators don't belong to a tenant
         account).
      2. After signature verification, we additionally check that the
         telegram_id is in ``SYSTEM_OWNER_IDS``.  A valid Telegram login
         from a stranger still 403s here.

    No cookie is set — the operator console uses Bearer header + an
    origin-isolated localStorage entry, not the apex SSO cookie.
    """
    if not TELEGRAM_SYSTEM_BOT_TOKEN:
        raise HTTPException(
            status_code=503,
            detail="System bot not configured — TELEGRAM_BOT_TOKEN is empty.",
        )
    try:
        raw = {
            k: v for k, v in body.model_dump().items()
            if v != "" and k != "remember_me"
        }
        validate_telegram_login_widget(raw, TELEGRAM_SYSTEM_BOT_TOKEN)
    except ValueError as e:
        raise HTTPException(status_code=401, detail=str(e))

    from capabilities.permissions.roles import is_system_owner
    if not is_system_owner(body.id):
        raise HTTPException(
            status_code=403,
            detail="Restricted to platform operators (SYSTEM_OWNER_IDS).",
        )

    # Operator user may or may not have a row in our ``users`` table.
    # If they do, mirror their role + account_id in the JWT for
    # consistency; if they don't, mint a JWT with a placeholder
    # account_id=0 and role='owner' (the SYSTEM_OWNER_IDS check is
    # what actually gates /system/*).
    from infra.platform import get_platform_db
    db = get_platform_db()
    user = await db.get_user_by_telegram_id(body.id)
    if user:
        token = await mint_session_token(
            db, request,
            user_id=user.id, telegram_id=user.telegram_id,
            account_id=user.account_id, role=user.role.value,
            is_manager=user.is_manager,
            is_primary_owner=user.is_primary_owner,
            remember_me=body.remember_me,
        )
        user_payload = {
            "telegram_id": user.telegram_id,
            "name": body.first_name,
            "role": user.role.value,
            "account_id": user.account_id,
        }
    else:
        # Operator without a backing users row — no session bookkeeping
        # (nothing to attach it to).  Still mint the token; the
        # SYSTEM_OWNER_IDS gate is what actually authorises /system/*.
        token = create_jwt(
            body.id, 0, "owner",
            remember_me=body.remember_me,
        )
        user_payload = {
            "telegram_id": body.id,
            "name": body.first_name,
            "role": "owner",
            "account_id": 0,
        }
    return AuthResponse(access_token=token, user=user_payload)


@router.post("/system-telegram-init", response_model=AuthResponse)
@limiter.limit("30/minute")
async def auth_system_telegram_init(request: Request, body: AuthRequest):
    """Operator login via Telegram **Mini App** initData (system.4truck.us).

    This is the preferred operator-login path: when system.4truck.us is
    opened from the system bot's Menu Button inside Telegram, the
    WebApp SDK provides ``initData`` signed by the bot token.  We
    validate it against ``TELEGRAM_SYSTEM_BOT_TOKEN`` and check
    ``SYSTEM_OWNER_IDS`` — no Login Widget, no BotFather ``/setdomain``,
    so it sidesteps the widget-domain propagation entirely.

    No cookie is set — the operator console uses Bearer + an
    origin-isolated localStorage entry.
    """
    if not TELEGRAM_SYSTEM_BOT_TOKEN:
        raise HTTPException(
            status_code=503,
            detail="System bot not configured — TELEGRAM_SYSTEM_BOT_TOKEN is empty.",
        )
    try:
        tg_user = validate_telegram_init_data(body.init_data, TELEGRAM_SYSTEM_BOT_TOKEN)
    except ValueError as e:
        raise HTTPException(status_code=401, detail=str(e))

    tg_id = tg_user.get("id")
    if not tg_id:
        raise HTTPException(status_code=401, detail="Invalid user data")

    from capabilities.permissions.roles import is_system_owner
    if not is_system_owner(int(tg_id)):
        raise HTTPException(
            status_code=403,
            detail="Restricted to platform operators (SYSTEM_OWNER_IDS).",
        )

    # Mirror the operator's real role/account if they have a row;
    # otherwise mint an owner token with account_id=0 (the
    # SYSTEM_OWNER_IDS check is the real gate for /system/*).
    from infra.platform import get_platform_db
    db = get_platform_db()
    user = await db.get_user_by_telegram_id(int(tg_id))
    if user:
        token = await mint_session_token(
            db, request,
            user_id=user.id, telegram_id=user.telegram_id,
            account_id=user.account_id, role=user.role.value,
            is_manager=user.is_manager,
            is_primary_owner=user.is_primary_owner,
            remember_me=True,
        )
        user_payload = {
            "telegram_id": user.telegram_id,
            "name": tg_user.get("first_name", ""),
            "role": user.role.value,
            "account_id": user.account_id,
        }
    else:
        # Same rationale as system-telegram-login: no users row → no
        # session row.  Token still valid; SYSTEM_OWNER_IDS gates access.
        token = create_jwt(int(tg_id), 0, "owner", remember_me=True)
        user_payload = {
            "telegram_id": int(tg_id),
            "name": tg_user.get("first_name", ""),
            "role": "owner",
            "account_id": 0,
        }
    return AuthResponse(access_token=token, user=user_payload)


# ── Bot-login: one-time deep-link auth via system bot ─────────

BOT_LOGIN_TTL = 300  # 5 minutes
BOT_LOGIN_PREFIX = "bot_login:"


@router.post("/bot-login/init")
@limiter.limit("10/minute")
async def bot_login_init(request: Request):
    """Generate a one-time login token and return a deep link to the **login** bot.

    The user clicks the link, which opens the customer login bot
    (``TELEGRAM_LOGIN_BOT_TOKEN`` → e.g. ``@login_4truck_bot``) with
    /start login_TOKEN.  The bot's ``register_login_handlers`` /start
    handler verifies the user and writes approval into Redis; the
    frontend polls /bot-login/check/{token} until approved or expired.

    Must resolve the LOGIN bot specifically — NOT the system bot.  The
    earlier implementation read ``interfaces.bot.config.bot_username``
    which silently collapsed to the system bot whenever
    ``TELEGRAM_LOGIN_BOT_TOKEN`` was unset (the bot.config TOKEN falls
    back to the system token, the daemon runs that, and its post_init
    writes the system bot's username into the shared global).  When
    that happened, customer logins deep-linked into the operator bot
    and were greeted with "this bot is for platform operators only".
    """
    from infra.cache import cache_set as redis_set

    token = secrets.token_urlsafe(32)
    await redis_set(
        f"{BOT_LOGIN_PREFIX}{token}",
        {"status": "pending"},
        ttl=BOT_LOGIN_TTL,
    )

    username = await _login_bot_username()
    if not username:
        # No LOGIN bot configured.  We deliberately do NOT fall back to
        # the system bot — that's the bug we just fixed.  Tell the
        # caller the flow is unavailable so the dashboard can show a
        # clean error instead of routing the user to a dead end.
        raise HTTPException(
            status_code=503,
            detail=(
                "Bot-login is unavailable: TELEGRAM_LOGIN_BOT_TOKEN is "
                "not configured on the API.  Use email + password or the "
                "Telegram Login Widget instead."
            ),
        )
    deep_link = f"https://t.me/{username}?start=login_{token}"
    return {"token": token, "deep_link": deep_link, "ttl": BOT_LOGIN_TTL}


@router.get("/bot-login/check/{token}")
@limiter.limit("60/minute")
async def bot_login_check(request: Request, response: Response, token: str):
    """Poll for the result of a bot-login attempt.

    Returns:
      - {"status": "pending"} — still waiting
      - {"status": "approved", "access_token": "...", "user": {...}} — success
      - {"status": "rejected", "reason": "..."} — user not registered
      - {"status": "expired"} — token gone from Redis
    """
    from infra.cache import get as redis_get

    if not token or len(token) > 64:
        raise HTTPException(status_code=400, detail="Invalid token")

    data = await redis_get(f"{BOT_LOGIN_PREFIX}{token}")
    if data is None:
        return {"status": "expired"}

    if data.get("status") == "approved":
        # Clean up the token — one-time use
        from infra.cache import delete as redis_del
        await redis_del(f"{BOT_LOGIN_PREFIX}{token}")
        # Set the cross-subdomain auth cookie so persona subdomains
        # trust this session.  Without it the frontend would store the
        # token in apex localStorage only (per-host), and any role-
        # based forward to fleet./dispatch./safety. would land on a
        # host with no session — bouncing the user back into a login
        # loop.  Read ``remember`` from the JWT itself so the cookie
        # lifetime matches what the bot decided when it minted the
        # token; falls back to True (long session, the bot-login
        # default per ``registration.start_bot_login_approval``).
        access_token = data.get("access_token", "")
        if access_token:
            try:
                claims = decode_jwt(access_token)
                remember = bool(claims.get("remember", True))
            except Exception:
                remember = True
            _set_auth_cookie(response, access_token, remember_me=remember)
        return data

    if data.get("status") == "rejected":
        from infra.cache import delete as redis_del
        await redis_del(f"{BOT_LOGIN_PREFIX}{token}")
        return {"status": "rejected", "reason": data.get("reason", "Not authorized")}

    return {"status": "pending"}


def _hash_password(password: str) -> str:
    return bcrypt.hashpw(password.encode(), bcrypt.gensalt()).decode()


def _verify_password(password: str, hashed: str) -> bool:
    return bcrypt.checkpw(password.encode(), hashed.encode())


class EmailLoginRequest(BaseModel):
    email: str
    password: str
    # "Remember me" decides the JWT lifetime (long = 30 days vs short
    # = 8 hours).  See ``create_jwt`` for the rationale.
    remember_me: bool = False
    # The browser extension no longer signs in here: an old panel sending
    # "extension" is refused with the instruction to connect from the
    # dashboard (POST /extension/connect is the only mint of a scoped
    # token).  Any other value is ignored rather than trusted.
    client: str | None = None


class EmailRegisterRequest(BaseModel):
    email: str
    password: str
    display_name: str = ""
    invite_code: str = ""
    # Defaults to False so a forgotten ``remember_me`` field doesn't
    # silently mint a 30-day token.  The dashboard register flow sends
    # ``remember_me: true`` explicitly when it wants the long session;
    # other clients have to opt-in too.
    remember_me: bool = False
    # Cloudflare Turnstile widget token (see RegisterAccountRequest for
    # the dev/test bypass rationale).
    turnstile_token: str | None = None


def _validate_password_strength(password: str) -> None:
    """Enforce the policy: at least 8 characters with at least one
    letter and one digit.

    Raises 422 with a clear message — surfaced verbatim on the
    register / set-password / reset-password screens so the user
    knows exactly what to fix.  Kept here (not in a pydantic
    validator) so all entry points share one source of truth and
    future rule changes touch one function.
    """
    if not password or len(password) < 8:
        raise HTTPException(
            status_code=422,
            detail="Password must be at least 8 characters.",
        )
    has_letter = any(c.isalpha() for c in password)
    has_digit = any(c.isdigit() for c in password)
    if not (has_letter and has_digit):
        raise HTTPException(
            status_code=422,
            detail="Password must include at least one letter and one digit.",
        )


@router.post("/login", response_model=AuthResponse)
@limiter.limit("10/minute")
async def auth_email_login(request: Request, response: Response, body: EmailLoginRequest):
    """Authenticate via email + password.

    Adds three checks beyond "password matches":

    1. **Account lockout** — refuses login while ``locked_until`` is in
       the future.  The error explicitly says when the lock expires so
       the user can come back later (or use "forgot password" to bypass
       the wait by resetting from a fresh device).

    2. **Failed-attempt tracking** — every wrong password increments
       a counter on the user row.  At 5 failures the account is locked
       for 15 minutes and the legitimate owner gets an email heads-up
       (best-effort; SMTP no-op is fine).

    3. **Email-verified gate** — refuses login if the user hasn't
       redeemed their verification token yet.  The response surfaces a
       structured detail so the dashboard can show a "resend" button
       instead of a generic 401.

    Audit-trailing: a placeholder; once ``add_platform_audit_log``
    lands it should be called here for both success + failure with the
    request IP.  For now the standard rate-limiter + lockout cover
    the security side; the audit dim is "nice to have."
    """
    from infra.platform import get_platform_db
    db = get_platform_db()

    # Collect request metadata up front — used by every audit-log
    # branch below so the same IP / user-agent labels appear whether
    # we're recording a success, a wrong password, a lockout, or an
    # unverified-email refusal.
    _ip = _client_ip(request)
    _ua = (request.headers.get("user-agent") or "")[:500]

    user = await db.get_user_by_email(body.email)
    if not user or not user.password_hash:
        # Don't reveal whether the email exists.  A probe attacker
        # learns nothing from the timing because we never reach the
        # password verify path.
        await db.record_login_attempt(
            user_id=None, email=body.email, success=False,
            failure_reason="no_such_email",
            ip_address=_ip, user_agent=_ua,
        )
        raise HTTPException(status_code=401, detail="Invalid email or password")

    # Lockout check happens BEFORE password verification so a brute
    # force can't even time-probe valid emails.
    locked_until = await db.is_user_locked(user.id)
    if locked_until:
        await db.record_login_attempt(
            user_id=user.id, email=body.email, success=False,
            failure_reason="account_locked",
            ip_address=_ip, user_agent=_ua,
        )
        raise HTTPException(
            status_code=423,  # Locked
            detail={
                "message": (
                    "Account temporarily locked after too many failed "
                    "login attempts.  Try again after the lockout window "
                    "expires, or use 'Forgot password' to reset."
                ),
                "error_code": "account_locked",
                "locked_until": locked_until,
            },
        )

    if not _verify_password(body.password, user.password_hash):
        # Record the miss + lock if threshold hit.  We don't await
        # the email here so a slow SMTP relay doesn't slow the 401.
        lock_state = await db.record_failed_login(user.id)
        await db.record_login_attempt(
            user_id=user.id, email=body.email, success=False,
            failure_reason=(
                "lockout_triggered" if lock_state.get("locked_until")
                else "bad_password"
            ),
            ip_address=_ip, user_agent=_ua,
        )
        if lock_state.get("locked_until"):
            # Best-effort heads-up to the legitimate user.
            try:
                from capabilities.email.auth_emails import (
                    send_lockout_notice,
                )
                client_ip = _client_ip(request)
                send_lockout_notice(
                    to=user.email or "", recipient_name=user.display_name,
                    ip=client_ip,
                )
            except Exception as e:
                logging.getLogger("api.auth").debug(
                    "lockout notice for %s failed: %s", user.email, e,
                )
        raise HTTPException(status_code=401, detail="Invalid email or password")

    # Successful auth — clear the lockout counter.
    await db.clear_failed_logins(user.id)

    # Email-verified gate.  Users created BEFORE verification existed
    # are backfilled to verified=1 by the schema migration, so we don't
    # accidentally lock out historical accounts.
    if not await db.is_email_verified(user.id):
        await db.record_login_attempt(
            user_id=user.id, email=body.email, success=False,
            failure_reason="email_not_verified",
            ip_address=_ip, user_agent=_ua,
        )
        raise HTTPException(
            status_code=403,
            detail={
                "message": (
                    "Please verify your email before signing in.  Check "
                    "your inbox for the verification link, or request a "
                    "new one from the login screen."
                ),
                "error_code": "email_not_verified",
                "email": user.email,
            },
        )

    await db.record_login_attempt(
        user_id=user.id, email=body.email, success=True,
        ip_address=_ip, user_agent=_ua,
    )

    if body.client == EXTENSION_AUDIENCE:
        # The panel does not sign in with a password any more: an
        # extension token is minted ONLY by POST /extension/connect, after
        # the person confirms on the dashboard.  An old panel asking here
        # gets a plain answer, never an unscoped token.
        raise HTTPException(
            status_code=400,
            detail="Connect the browser extension from your 4truck dashboard.",
        )
    token = await mint_session_token(
        db, request,
        user_id=user.id, telegram_id=user.telegram_id,
        account_id=user.account_id, role=user.role.value,
        is_manager=user.is_manager,
        is_primary_owner=user.is_primary_owner,
        remember_me=body.remember_me,
    )
    _set_auth_cookie(response, token, remember_me=body.remember_me)
    return AuthResponse(
        access_token=token,
        user={
            "telegram_id": user.telegram_id,
            "name": user.display_name or user.email or "",
            "role": user.role.value,
            "account_id": user.account_id,
        },
    )


@router.post("/register")
@limiter.limit("10/minute")
async def auth_email_register(request: Request, response: Response, body: EmailRegisterRequest):
    """Register a new user via email + password + invite code.

    Behavior change: registration NO LONGER auto-mints a session token.
    The user must verify their email first (we email them a link with a
    one-time token).  The response carries a structured message that the
    dashboard renders as "We sent a verification email — check your
    inbox" instead of trying to redirect them into the app.

    This matches the policy "block login until verified" — letting a
    fresh signup walk straight in would defeat the gate.
    """
    from infra.platform import get_platform_db
    db = get_platform_db()

    # Turnstile gate runs first so a bot can't even probe whether an
    # email or invite code is in the DB before solving the challenge.
    from infra.turnstile import verify_turnstile
    if not await verify_turnstile(
        body.turnstile_token,
        remote_ip=_client_ip(request),
    ):
        raise HTTPException(
            status_code=403,
            detail="Captcha verification failed. Please try again.",
        )

    # Validate email format
    if not _EMAIL_RE.match(body.email):
        raise HTTPException(status_code=422, detail="Invalid email address")

    # Shared password policy — same rule as /set-password and /reset-password.
    _validate_password_strength(body.password)

    # Check if email already taken
    existing = await db.get_user_by_email(body.email)
    if existing:
        raise HTTPException(status_code=409, detail="Email already registered")

    # If user provides invite code, join that account with the invite's role
    if body.invite_code:
        invite = await db.get_invite(body.invite_code.strip())
        if not invite:
            raise HTTPException(status_code=404, detail="Invalid invite code")
        if invite.is_expired:
            raise HTTPException(status_code=410, detail="Invite code expired")
        if invite.is_used:
            raise HTTPException(status_code=410, detail="Invite code already used")

        pw_hash = _hash_password(body.password)
        async with db.transaction():
            # Re-check inside transaction.  get_invite already filters
            # revoked_at IS NULL (see adapters/storage/invites.py:55-60),
            # so a code revoked by the operator AFTER the outer check
            # but BEFORE this re-check is correctly rejected with 410.
            invite = await db.get_invite(body.invite_code.strip())
            if not invite or invite.is_used:
                raise HTTPException(status_code=410, detail="Invite code already used")
            # Check user quota before creating
            from interfaces.api.deps import enforce_user_quota
            await enforce_user_quota(invite.account_id, platform_db=db)
            user = await db.create_user_with_email(
                email=body.email,
                password_hash=pw_hash,
                account_id=invite.account_id,
                role=database.Role.from_str(invite.role),
                display_name=body.display_name or body.email.split("@")[0],
            )
            # Mark invite as used.  TOCTOU defence (mirrors
            # ``redeem_invite`` in adapters/storage/invites.py:120-134):
            # an operator revoke that commits between the re-check
            # above and this UPDATE must NOT silently admit the user.
            # The WHERE clause guards both used_by IS NULL (a parallel
            # redeem won the race) AND revoked_at IS NULL (an admin
            # revoke won).  rowcount != 1 → raise to roll the whole
            # transaction back (including create_user_with_email).
            cur = await db._db.execute(
                "UPDATE invites SET used_by = ? "
                "WHERE id = ? AND used_by IS NULL AND revoked_at IS NULL",
                (user.id, invite.id),
            )
            if cur.rowcount != 1:
                raise HTTPException(
                    status_code=410,
                    detail="Invite code already used",
                )
    else:
        raise HTTPException(
            status_code=422,
            detail="Invite code is required for registration. "
            "Ask your company admin for an invite link.",
        )

    # Email verification: mint a token, ship the link.  Do NOT issue
    # an auth cookie — the user must redeem the verification token
    # first.  This blocks the "spam signup → use the app" path the
    # email-required policy is designed to prevent.
    try:
        verify_token = await db.create_email_verification_token(
            user.id, body.email,
        )
        from capabilities.email.auth_emails import (
            send_verification_email,
        )
        send_verification_email(
            to=body.email, token=verify_token,
            recipient_name=user.display_name or "",
        )
    except Exception as e:
        # Token mint or email send failed — log and surface a generic
        # error.  The user can hit "resend verification" to retry.
        logging.getLogger("api.auth").warning(
            "verification email mint/send failed for %s: %s",
            body.email, e,
        )

    return {
        "status": "registered",
        "verification_required": True,
        "email": body.email,
        "message": (
            "Account created.  Check your inbox for the verification "
            "link, then sign in."
        ),
    }


class ForgotPasswordRequest(BaseModel):
    email: str
    # Cloudflare Turnstile token — forgot-password sends email to any
    # address the caller types, so without a captcha a bot can burn our
    # sender reputation by spraying reset mails at strangers.  Optional
    # at the schema level; the verifier skips when TURNSTILE_SECRET_KEY
    # is unset (dev / self-host).
    turnstile_token: str | None = None


class ResetPasswordRequest(BaseModel):
    token: str
    password: str


class ResendVerificationRequest(BaseModel):
    email: str


@router.post("/forgot-password")
@limiter.limit("5/minute")
async def auth_forgot_password(request: Request, body: ForgotPasswordRequest):
    """Start the password-reset flow.

    Always responds 200 with the same generic body whether the email
    exists or not — that way a probing attacker can't enumerate which
    emails are registered by timing or response shape.  The reset
    email is sent (best-effort) only when the email IS registered;
    other requests are silent no-ops.
    """
    from infra.platform import get_platform_db
    db = get_platform_db()

    # Captcha gate ahead of any DB/email work — this endpoint mails
    # arbitrary addresses, so bots here cost us sender reputation, not
    # just compute.
    from infra.turnstile import verify_turnstile
    if not await verify_turnstile(
        body.turnstile_token,
        remote_ip=_client_ip(request),
    ):
        raise HTTPException(
            status_code=403,
            detail="Captcha verification failed. Please try again.",
        )

    email = (body.email or "").strip().lower()

    if email and _EMAIL_RE.match(email):
        user = await db.get_user_by_email(email)
        if user and user.password_hash:
            try:
                token = await db.create_password_reset_token(user.id)
                from capabilities.email.auth_emails import (
                    send_password_reset_email,
                )
                send_password_reset_email(
                    to=email, token=token,
                    recipient_name=user.display_name or "",
                )
            except Exception as e:
                logging.getLogger("api.auth").warning(
                    "reset email mint/send failed for %s: %s", email, e,
                )

    return {
        "status": "ok",
        "message": (
            "If an account exists for that email, a reset link is on "
            "its way.  Check your inbox (and spam folder)."
        ),
    }


@router.post("/reset-password")
@limiter.limit("10/minute")
async def auth_reset_password(request: Request, body: ResetPasswordRequest):
    """Complete the password reset.

    Token must be unredeemed and unexpired (1-hour window per the
    storage helper).  On success: set the new password hash, mark the
    token used, clear any lockout, stamp ``password_changed_at``.
    Does NOT auto-sign-in — the user is bounced to the login screen
    so they prove the new credentials work and pick remember-me on
    their own.
    """
    from infra.platform import get_platform_db
    db = get_platform_db()
    _validate_password_strength(body.password)

    user_id = await db.consume_password_reset_token(body.token)
    if not user_id:
        raise HTTPException(
            status_code=400,
            detail={
                "message": (
                    "Reset link is invalid, expired, or already used.  "
                    "Request a new one from the login page."
                ),
                "error_code": "reset_token_invalid",
            },
        )

    pw_hash = _hash_password(body.password)
    user = await db.get_user_by_id(user_id)
    if not user:
        raise HTTPException(status_code=404, detail="User not found")
    # set_user_email_password keeps the email the same when called
    # with the user's current email.
    await db.set_user_email_password(user_id, user.email or "", pw_hash)
    await db.clear_failed_logins(user_id)
    await db.mark_password_changed(user_id)

    return {
        "status": "ok",
        "message": "Password updated.  Sign in with your new password.",
    }


@router.get("/verify-email")
async def auth_verify_email(token: str):
    """Redeem an email-verification token.

    Returns a small JSON body the dashboard renders into a success
    page.  If you'd rather have a friendly redirect, the dashboard
    just calls this endpoint from its /verify-email route and shows
    its own success view.

    Re-redeeming a used token returns the same error as an unknown
    one — no information leakage about whether THIS token was ever
    valid.
    """
    from infra.platform import get_platform_db
    db = get_platform_db()
    user_id = await db.consume_email_verification_token(token)
    if not user_id:
        raise HTTPException(
            status_code=400,
            detail={
                "message": (
                    "Verification link is invalid, expired, or already "
                    "used.  Request a fresh one from the login page."
                ),
                "error_code": "verify_token_invalid",
            },
        )
    return {
        "status": "ok",
        "message": "Email verified.  You can sign in now.",
    }


@router.post("/resend-verification")
@limiter.limit("3/minute")
async def auth_resend_verification(
    request: Request, body: ResendVerificationRequest,
):
    """Re-send the verification email.

    Like ``forgot-password``: always responds 200 to avoid leaking
    which emails are registered.  Caps at 3 sends per minute per IP
    on top of the per-account token churn.
    """
    from infra.platform import get_platform_db
    db = get_platform_db()
    email = (body.email or "").strip().lower()
    if email and _EMAIL_RE.match(email):
        user = await db.get_user_by_email(email)
        # Only re-send when the account exists AND hasn't already been
        # verified.  Verified accounts don't need another link.
        if user and not await db.is_email_verified(user.id):
            try:
                token = await db.create_email_verification_token(
                    user.id, email,
                )
                from capabilities.email.auth_emails import (
                    send_verification_email,
                )
                send_verification_email(
                    to=email, token=token,
                    recipient_name=user.display_name or "",
                )
            except Exception as e:
                logging.getLogger("api.auth").warning(
                    "resend verification failed for %s: %s", email, e,
                )
    return {
        "status": "ok",
        "message": (
            "If an unverified account exists for that email, a fresh "
            "link has been sent.  Check your inbox."
        ),
    }


@router.post("/logout")
async def auth_logout(
    request: Request,
    response: Response,
    authorization: str | None = Header(default=None),
    auth_token: str | None = Cookie(default=None, alias=AUTH_COOKIE_NAME),
):
    """Clear the cross-subdomain auth cookie AND denylist the session jti.

    Idempotent — safe to call without an active session.  The Mini App
    and other Bearer-header clients should also drop their stored token.

    Two things happen so revocation is bullet-proof:
      1. The ``.4truck.us`` cookie is cleared via Set-Cookie Max-Age=0.
      2. If we can recover a jti from the request's Bearer header OR
         cookie, push it onto the Redis denylist + mark the
         ``user_sessions`` row revoked.  This closes the failure mode
         where the cookie clear didn't actually take effect (browser
         quirk, network race) — the JWT itself becomes unusable, so a
         subdomain that still sees the stale cookie still gets 401 from
         every authed endpoint and bounces the user cleanly to login
         instead of looping forever between apex and the persona host.

    Failures in step 2 are silently logged — the cookie clear in
    step 1 is the primary mechanism; the denylist is belt-and-braces.
    """
    _clear_auth_cookie(response)

    # Pull whichever token the caller presented.  Try Bearer first
    # (matches get_current_user's preference) then the cookie.  An old
    # localStorage token that no longer matches the cookie still gets
    # denylisted — that's the right call; the user wants OUT.
    token: str | None = None
    if authorization and authorization.startswith("Bearer "):
        token = authorization[7:]
    if not token and auth_token:
        token = auth_token

    if token:
        try:
            # decode_jwt, not a raw jwt.decode: the raw call refuses any
            # token that carries an ``aud`` ("Invalid audience"), so the
            # browser extension's logout used to log "already invalid?"
            # and answer ok — without revoking anything.
            payload = decode_jwt(token)
            jti = str(payload.get("jti") or "")
            if jti:
                # Mark the user_sessions row revoked (so the session
                # disappears from the user's "Active sessions" panel)
                # and push the jti onto the Redis denylist so future
                # requests with this JWT 401 immediately.
                try:
                    from infra.platform import get_platform_db
                    db = get_platform_db()
                    revoked = await db.revoke_user_session_by_jti(jti) \
                        if hasattr(db, "revoke_user_session_by_jti") else None
                    expires_at = (revoked or {}).get("expires_at") if revoked else None
                except Exception as e:
                    logging.getLogger("api.auth").debug(
                        "logout: session row revoke failed for jti=%s: %s", jti, e
                    )
                    expires_at = None
                await mark_jti_revoked(jti, expires_at)
        except JWTError as e:
            logging.getLogger("api.auth").debug(
                "logout: token decode failed (already invalid?): %s", e
            )
        except Exception as e:
            logging.getLogger("api.auth").warning(
                "logout: unexpected denylist failure: %s", e
            )

    return {"ok": True}


@router.post("/set-password")
async def auth_set_password(body: EmailLoginRequest):
    """Set email+password for the currently authenticated user.

    Requires valid JWT (user logged in via Telegram first).
    Lets them add email/password credentials for future dashboard logins.
    """
    # Stub: redirects to the canonical credentials route.  No DB access
    # needed — the import + assignment that used to live here were dead
    # weight, F841 once flake8 was wired up.
    raise HTTPException(
        status_code=501,
        detail="Use PUT /api/user/credentials instead",
    )


# ── Web-based account registration (4truck.us) ───────────────


class RegisterAccountRequest(BaseModel):
    """Create a new account + owner user via web (no Telegram required)."""
    company_name: str = Field(..., min_length=2, max_length=100)
    email: str
    password: str = Field(..., min_length=8, max_length=128)
    display_name: str = ""
    # Cloudflare Turnstile widget token.  Optional at the schema level
    # so dev / self-host environments without a Cloudflare account can
    # POST without a token; the server-side verifier is the gate, and
    # it returns True (skip) when ``TURNSTILE_SECRET_KEY`` is unset.
    turnstile_token: str | None = None


# Auto-trial length for self-serve company signups.  90 days for the
# operator-side soft-delete grace already encoded elsewhere; 14 days is
# the industry-standard self-trial window (Stripe, GitHub teams, etc.)
# and is short enough that an inactive signup falls off paid telemetry
# quickly but long enough for a fleet operator to evaluate the dashboard
# across a full driver-pay/dispatch cycle.
_AUTO_TRIAL_DAYS = 14


@router.post("/register-account")
@limiter.limit("5/minute")
async def auth_register_account(request: Request, body: RegisterAccountRequest):
    """Self-serve company signup via 4truck.us.

    The public counterpart to ``POST /system/accounts`` (operator-only).
    Creates an account + owner user, auto-grants a 14-day comp trial,
    and ships an email-verification link.  **No JWT is issued until the
    owner verifies their email** — same security posture as
    ``/auth/register`` (the invite-based flow), so the two public
    signup paths can't be played against each other to bypass
    verification.
    """
    from infra.platform import get_platform_db
    db = get_platform_db()

    # Turnstile first — bots that auto-fill the form should not even
    # reach the email-exists check (which leaks "email is registered"
    # via the 409 status code).
    from infra.turnstile import verify_turnstile
    if not await verify_turnstile(
        body.turnstile_token,
        remote_ip=_client_ip(request),
    ):
        raise HTTPException(
            status_code=403,
            detail="Captcha verification failed. Please try again.",
        )

    if not _EMAIL_RE.match(body.email):
        raise HTTPException(status_code=422, detail="Invalid email address")

    existing = await db.get_user_by_email(body.email)
    if existing:
        raise HTTPException(status_code=409, detail="Email already registered")

    try:
        account = await db.create_account(body.company_name)

        pw_hash = _hash_password(body.password)
        user = await db.create_user_with_email(
            email=body.email,
            password_hash=pw_hash,
            account_id=account.id,
            role=database.Role.OWNER,
            display_name=body.display_name or body.email.split("@")[0],
        )
    except ValueError as e:
        # Company-name collision — surfaced as a conflict the form can
        # render inline, not a generic 500.
        raise HTTPException(status_code=409, detail=str(e))
    except Exception as e:
        logging.getLogger("api.auth").error(
            "Account registration failed: %s", e, exc_info=True,
        )
        raise HTTPException(
            status_code=500,
            detail="Registration failed. Please try again.",
        )

    try:
        await db.add_platform_audit(
            "account_created",
            account_id=account.id,
            actor="self-serve",
            details=f"name={account.name!r} owner={body.email} ip={_client_ip(request)}",
        )
    except Exception:
        logging.getLogger("api.auth").exception(
            "platform audit write failed for account %s", account.id,
        )

    # 14-day Pro trial — a REAL trial, not a comp: tier='pro' +
    # status='trialing' + trial_ends_at so the owner gets full Pro
    # features (and the operator console shows "Pro · trialing", not a
    # confusing "free").  The daily ``expire_due_trials`` job downgrades
    # to free when the window elapses.  Non-fatal on failure — the
    # account still exists; an operator can start the trial manually.
    trial_expires_iso: str | None = None
    try:
        trial_expires_iso = await db.start_trial(
            account.id, tier="pro", days=_AUTO_TRIAL_DAYS,
        )
    except Exception as e:
        trial_expires_iso = None
        logging.getLogger("api.auth").warning(
            "Auto-trial start failed for new account %s: %s",
            account.id, e,
        )

    # Email verification gate — same as /auth/register (the invite path).
    # No JWT minted, no cookie set; the user MUST click the link before
    # they can sign in.  Blocks the "spam signup → use the app" path.
    try:
        verify_token = await db.create_email_verification_token(
            user.id, body.email,
        )
        from capabilities.email.auth_emails import (
            send_verification_email,
        )
        send_verification_email(
            to=body.email, token=verify_token,
            recipient_name=user.display_name or "",
        )
    except Exception as e:
        logging.getLogger("api.auth").warning(
            "verification email mint/send failed for %s: %s",
            body.email, e,
        )

    return {
        "status": "registered",
        "verification_required": True,
        "email": body.email,
        "account_id": account.id,
        "trial": {
            "days": _AUTO_TRIAL_DAYS,
            "expires_at": trial_expires_iso,
        } if trial_expires_iso else None,
        "message": (
            "Account created. Check your inbox for the verification link, "
            f"then sign in. Your {_AUTO_TRIAL_DAYS}-day trial is already running."
        ),
    }


# ══════════════════════════════════════════════════════════════════
# Invite preview + decline (public, unauthenticated)
# ══════════════════════════════════════════════════════════════════
#
# Two endpoints supporting the email-channel invite flow:
#
# 1.  GET /auth/invite-preview?code=XXXX
#       Returns the safe-to-show metadata of an invite WITHOUT
#       consuming it.  The Login page renders this as a "you're being
#       invited to ACME as Driver by Alice" callout above the password
#       field, so a phishing-aware recipient can verify the invite
#       looks legitimate BEFORE burning the single-use code.  Returns
#       uniform 404 for missing/expired/used/revoked so the endpoint
#       can't be used as a code-enumeration oracle.
#
# 2.  POST /auth/invite/decline?token=XXXX
#       Backs the List-Unsubscribe header.  An invitee who hits
#       "Unsubscribe" in Gmail's inbox toolbar (or follows the
#       footer link) ends up here; we revoke the invite without
#       needing any authentication.  Rate-limited per IP because the
#       token IS the auth — guessing a valid token is the only
#       attack vector.

@router.get("/invite-preview")
@limiter.limit("10/minute")
async def auth_invite_preview(request: Request, code: str):
    """Preview the invite without consuming it (public, unauth).

    Returns minimal trust-affirming metadata.  Uniform 404 for
    every not-available branch so this endpoint isn't a code-
    enumeration oracle.  Rate-limited per IP to cap brute-force.
    """
    if not code:
        raise HTTPException(status_code=404, detail="Invite not found")
    # Cleanup the same way redeem_invite normalises it
    norm = code.upper().strip()
    from infra.platform import get_platform_db
    db = get_platform_db()
    if db is None:
        raise HTTPException(status_code=500, detail="Database unavailable")
    invite = await db.get_invite(norm)
    # get_invite already filters revoked + missing.  Used / expired
    # need explicit checks so we don't preview a dead invite.
    if not invite or invite.is_used or invite.is_expired:
        raise HTTPException(status_code=404, detail="Invite not found")
    # Resolve human-readable account + inviter names so the recipient
    # sees "ACME Trucking" not "account_id=42".  Tolerate missing
    # rows gracefully — if the inviter user was deleted between
    # invite-create and now, return a generic label rather than 500.
    account = await db.get_account(invite.account_id)
    inviter = None
    try:
        inviter = await db.get_user_by_id(invite.created_by)
    except Exception:
        pass
    return {
        "account_name": (account.name if account else "") or "your new team",
        "role_label": invite.role.capitalize(),
        "truck_num": invite.truck_num,
        "expires_at": invite.expires_at,
        "inviter_display_name": (
            (inviter.display_name if inviter else "")
            or "your inviter"
        ),
    }


async def _decline_invite_impl(request: Request, token: str) -> None:
    """Shared revocation logic for the POST (List-Unsubscribe
    One-Click, RFC 8058) and GET (footer link / human click) handlers.

    Writes a tenant-scoped audit row with NULL actor so the operator
    can distinguish "recipient declined via email" from "operator
    revoked in dashboard" — and so distributed-scan / DoS-via-token-
    guessing has a forensic trail.  Source IP captured in details.
    Uniform no-op for missing/used/expired/revoked codes — no
    enumeration oracle, no info leak.
    """
    if not token:
        return
    norm = token.upper().strip()
    from infra.platform import get_platform_db
    db = get_platform_db()
    if db is None:
        return
    invite = await db.get_invite(norm)
    if not invite or invite.is_used or invite.is_expired:
        return
    await db.revoke_invite(invite.account_id, invite.id)
    # Tenant-scoped audit row.  Actor is None — system-driven decline,
    # no JWT context.  AuditLog.tsx ACTION_LABEL renders this as
    # "Invite declined by recipient" so the operator sees the cause.
    try:
        from infra.platform import get_tenant_db as _get_tenant_db
        tenant = await _get_tenant_db(invite.account_id)
        source_ip = (
            request.client.host if request.client else "unknown"
        )
        details = (
            f"Role: {invite.role}, "
            f"channel: {invite.channel}, source_ip: {source_ip}"
        )[:500]
        from capabilities.activity_trail import record_simple
        await record_simple(
            tenant, invite.account_id, None,
            "invite_declined", "invite", invite.id,
            # No platform user exists for the anonymous recipient — the
            # trail's people-only rule requires actorless events to
            # declare themselves.
            context={"system": "public: invite recipient declined"},
            note=details,
        )
    except Exception as e:
        logging.getLogger("api.auth").warning(
            "audit_log on invite_declined failed: %s", e,
        )


@router.post("/invite/decline")
@limiter.limit("10/minute")
async def auth_invite_decline_post(request: Request, token: str):
    """Revoke an invite from Gmail/Yahoo's One-Click List-Unsubscribe
    POST (RFC 8058).  The token IS the authorisation — inboxes can't
    authenticate the recipient.  Per-IP rate limit caps brute-force;
    tenant audit row gives the operator visibility on declines."""
    await _decline_invite_impl(request, token)
    return {"ok": True}


@router.get("/invite/decline")
@limiter.limit("10/minute")
async def auth_invite_decline_get(request: Request, token: str):
    """Human-click decline handler — the in-body 'Click here to decline'
    link in the invite email issues a GET.  Returns a small confirmation
    page rather than 405-ing the recipient.  Same revocation + audit
    logic as the POST handler."""
    from fastapi.responses import HTMLResponse
    await _decline_invite_impl(request, token)
    return HTMLResponse(
        """<!doctype html>
<html lang="en"><head><meta charset="utf-8"><title>Invite declined</title></head>
<body style="font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',sans-serif;max-width:480px;margin:48px auto;padding:24px;color:#1f2937">
<h1 style="font-size:18px">Invite declined</h1>
<p style="font-size:14px;color:#6b7280">Thanks — we've removed your invite.  You can close this window.</p>
<p style="font-size:13px;color:#6b7280;margin-top:32px">If this was a mistake, contact the person who invited you and ask for a new invite.</p>
</body></html>""",
    )
