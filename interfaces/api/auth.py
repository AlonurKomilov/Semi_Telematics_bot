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
from fastapi import APIRouter, HTTPException, Request, Response
from pydantic import BaseModel, Field

from jose import jwt
from interfaces.api.rate_limit import limiter

import adapters.storage as database
import logging

from infra.config import TELEGRAM_TOKEN  # noqa: F401  (kept for other auth helpers below)

# JWT settings — JWT_SECRET is REQUIRED.  Previously this code fell back
# to TELEGRAM_TOKEN, but that meant any bot-token rotation silently
# invalidated every issued JWT and logged out the whole fleet.  The
# fail-fast at startup forces operators to set a stable, dedicated
# secret.  Tests provide one via tests/conftest.py.
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


def create_jwt(
    telegram_id: int,
    account_id: int,
    role: str,
    *,
    remember_me: bool = False,
) -> str:
    """Create a JWT token for an authenticated user.

    ``remember_me=True``  → 30-day TTL (long session).
    ``remember_me=False`` → 8-hour TTL (short session, default).

    The ``remember`` claim is embedded in the payload so the refresh
    endpoint can keep issuing tokens of the same flavour without the
    client having to re-supply the flag.
    """
    ttl = JWT_EXPIRY_LONG_SECONDS if remember_me else JWT_EXPIRY_SHORT_SECONDS
    now = int(time.time())
    payload = {
        "sub": str(telegram_id),
        "account_id": account_id,
        "role": role,
        "remember": bool(remember_me),
        "exp": now + ttl,
        "iat": now,
    }
    return jwt.encode(payload, JWT_SECRET, algorithm=JWT_ALGORITHM)


def decode_jwt(token: str) -> dict:
    """Decode and validate a JWT token. Raises JWTError on failure."""
    return jwt.decode(token, JWT_SECRET, algorithms=[JWT_ALGORITHM])


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
    new_token = create_jwt(
        user.telegram_id, user.account_id, user.role.value,
        remember_me=remember,
    )
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

    # Determine which bot token to validate against
    user = await db.get_user_by_telegram_id(telegram_id)
    bot_token = TELEGRAM_TOKEN or ""

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

    token = create_jwt(
        user.telegram_id, user.account_id, user.role.value,
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

    # Determine which bot token to validate against
    bot_token = TELEGRAM_TOKEN or ""
    if user:
        account = await db.get_account(user.account_id)
        if account and account.bot_token_encrypted:
            bot_token = decrypt(account.bot_token_encrypted)

    try:
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
        validate_telegram_login_widget(raw, bot_token)
    except ValueError as e:
        raise HTTPException(status_code=401, detail=str(e))

    if not user:
        raise HTTPException(
            status_code=403,
            detail="User not registered. Use the Telegram bot to register first.",
        )

    token = create_jwt(
        user.telegram_id, user.account_id, user.role.value,
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


@router.get("/config")
async def auth_config(request: Request):
    """Return public auth config (bot username for Telegram Login Widget).

    If the request carries a valid JWT, returns the per-account bot_username.
    Otherwise returns the first configured account bot (for the login widget),
    falling back to the global system bot.
    """
    from interfaces.bot.config import bot_username as global_bot_username

    # Try to extract account-specific bot_username from JWT
    auth_header = request.headers.get("authorization", "")
    if auth_header.startswith("Bearer "):
        try:
            payload = decode_jwt(auth_header[7:])
            account_id = payload.get("account_id")
            if account_id:
                from infra.platform import get_platform_db
                db = get_platform_db()
                from infra.crypto import decrypt
                account = await db.get_account(account_id)
                if account and account.bot_username:
                    acct_bot_id = ""
                    if account.bot_token_encrypted:
                        try:
                            tok = decrypt(account.bot_token_encrypted)
                            if ":" in tok:
                                acct_bot_id = tok.split(":", 1)[0]
                        except Exception as e:
                            logging.getLogger("api.auth").debug("Could not decrypt bot token: %s", e)
                    return {"bot_username": account.bot_username, "bot_id": acct_bot_id}
        except Exception as e:
            logging.getLogger("api.auth").debug("JWT account lookup failed, falling through: %s", e)
    # (account bots handle user auth; system bot is for platform admin only)
    try:
        from infra.platform import get_platform_db
        db = get_platform_db()
        from infra.crypto import decrypt
        accounts = await db.list_accounts()
        for acct in accounts:
            if acct.bot_token_encrypted and acct.bot_username:
                acct_bot_id = ""
                try:
                    tok = decrypt(acct.bot_token_encrypted)
                    if ":" in tok:
                        acct_bot_id = tok.split(":", 1)[0]
                except Exception as e:
                    logging.getLogger("api.auth").debug("Could not decrypt bot token: %s", e)
                return {"bot_username": acct.bot_username, "bot_id": acct_bot_id}
    except Exception as e:
        logging.getLogger("api.auth").debug("Could not look up fallback account bot: %s", e)
    _bot_id = ""
    if TELEGRAM_TOKEN and ":" in TELEGRAM_TOKEN:
        _bot_id = TELEGRAM_TOKEN.split(":", 1)[0]
    return {"bot_username": global_bot_username or "4truckBot", "bot_id": _bot_id}


# ── Bot-login: one-time deep-link auth via system bot ─────────

BOT_LOGIN_TTL = 300  # 5 minutes
BOT_LOGIN_PREFIX = "bot_login:"


@router.post("/bot-login/init")
@limiter.limit("10/minute")
async def bot_login_init(request: Request):
    """Generate a one-time login token and return a deep link to the system bot.

    The user clicks the link, which opens @app_4truck_bot with /start login_TOKEN.
    The bot verifies the user and writes approval into Redis.
    The frontend polls /bot-login/check/{token} until approved or expired.
    """
    from interfaces.bot.config import bot_username as sys_bot_username
    from infra.cache import cache_set as redis_set

    token = secrets.token_urlsafe(32)
    await redis_set(
        f"{BOT_LOGIN_PREFIX}{token}",
        {"status": "pending"},
        ttl=BOT_LOGIN_TTL,
    )

    username = sys_bot_username or "app_4truck_bot"
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


@router.post("/login", response_model=AuthResponse)
@limiter.limit("10/minute")
async def auth_email_login(request: Request, response: Response, body: EmailLoginRequest):
    """Authenticate via email + password."""
    from infra.platform import get_platform_db
    db = get_platform_db()

    user = await db.get_user_by_email(body.email)
    if not user or not user.password_hash:
        raise HTTPException(status_code=401, detail="Invalid email or password")

    if not _verify_password(body.password, user.password_hash):
        raise HTTPException(status_code=401, detail="Invalid email or password")

    token = create_jwt(
        user.telegram_id, user.account_id, user.role.value,
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


@router.post("/register", response_model=AuthResponse)
@limiter.limit("10/minute")
async def auth_email_register(request: Request, response: Response, body: EmailRegisterRequest):
    """Register a new user via email + password + invite code."""
    from infra.platform import get_platform_db
    db = get_platform_db()

    # Validate email format
    if not _EMAIL_RE.match(body.email):
        raise HTTPException(status_code=422, detail="Invalid email address")

    # Validate password strength
    if len(body.password) < 8:
        raise HTTPException(
            status_code=422, detail="Password must be at least 8 characters"
        )

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
            # Re-check inside transaction to prevent race conditions
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
                department=invite.department,
                display_name=body.display_name or body.email.split("@")[0],
            )
            # Mark invite as used
            await db._db.execute(
                "UPDATE invites SET used_by = ? WHERE id = ?",
                (user.id, invite.id),
            )
    else:
        raise HTTPException(
            status_code=422,
            detail="Invite code is required for registration. "
            "Ask your company admin for an invite link.",
        )

    token = create_jwt(
        user.telegram_id, user.account_id, user.role.value,
        remember_me=body.remember_me,
    )
    _set_auth_cookie(response, token, remember_me=body.remember_me)
    return AuthResponse(
        access_token=token,
        user={
            "telegram_id": user.telegram_id,
            "name": user.display_name,
            "role": user.role.value,
            "account_id": user.account_id,
        },
    )


@router.post("/logout")
async def auth_logout(response: Response):
    """Clear the cross-subdomain auth cookie.

    Idempotent — safe to call without an active session.  The Mini App and
    other Bearer-header clients should also drop their stored token; this
    endpoint only invalidates the cookie side of the session.  The JWT
    itself remains valid until ``exp``; we don't maintain a server-side
    revocation list (no use case has justified that complexity yet).
    """
    _clear_auth_cookie(response)
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


@router.post("/register-account", response_model=AuthResponse)
@limiter.limit("5/minute")
async def auth_register_account(request: Request, response: Response, body: RegisterAccountRequest):
    """Register a new company account via web at 4truck.us.

    Creates an account and an owner user with email/password credentials.
    The owner can then configure their Telegram bot in admin settings.
    No Telegram interaction required for initial registration.
    """
    from infra.platform import get_platform_db
    db = get_platform_db()

    # Validate email format
    if not _EMAIL_RE.match(body.email):
        raise HTTPException(status_code=422, detail="Invalid email address")

    # Check if email already taken
    existing = await db.get_user_by_email(body.email)
    if existing:
        raise HTTPException(status_code=409, detail="Email already registered")

    try:
        # Create account
        account = await db.create_account(body.company_name)

        # Create owner user with email+password (telegram_id=0 for web-only users)
        pw_hash = _hash_password(body.password)
        user = await db.create_user_with_email(
            email=body.email,
            password_hash=pw_hash,
            account_id=account.id,
            role=database.Role.OWNER,
            department="management",
            display_name=body.display_name or body.email.split("@")[0],
        )

        token = create_jwt(
            user.telegram_id, user.account_id, user.role.value,
            remember_me=True,  # account owners always get the long session
        )
        _set_auth_cookie(response, token, remember_me=True)
        return AuthResponse(
            access_token=token,
            user={
                "telegram_id": user.telegram_id,
                "name": user.display_name or body.email.split("@")[0],
                "role": user.role.value,
                "account_id": user.account_id,
            },
        )
    except Exception as e:
        logging.getLogger("api.auth").error("Account registration failed: %s", e, exc_info=True)
        raise HTTPException(status_code=500, detail="Registration failed. Please try again.")
