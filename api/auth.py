"""Telegram initData validation and JWT authentication."""

import hashlib
import hmac
import json
import os
import time
from urllib.parse import parse_qs, unquote

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from jose import jwt

from bot.config import TELEGRAM_TOKEN

# JWT settings
JWT_SECRET = os.getenv("JWT_SECRET", TELEGRAM_TOKEN or "change-me")
JWT_ALGORITHM = "HS256"
JWT_EXPIRY_SECONDS = 24 * 60 * 60  # 24 hours

router = APIRouter(prefix="/auth", tags=["auth"])


class AuthRequest(BaseModel):
    init_data: str


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


def create_jwt(telegram_id: int, account_id: int, role: str) -> str:
    """Create a JWT token for an authenticated user."""
    payload = {
        "sub": str(telegram_id),
        "account_id": account_id,
        "role": role,
        "exp": int(time.time()) + JWT_EXPIRY_SECONDS,
        "iat": int(time.time()),
    }
    return jwt.encode(payload, JWT_SECRET, algorithm=JWT_ALGORITHM)


def decode_jwt(token: str) -> dict:
    """Decode and validate a JWT token. Raises JWTError on failure."""
    return jwt.decode(token, JWT_SECRET, algorithms=[JWT_ALGORITHM])


@router.post("/telegram", response_model=AuthResponse)
async def auth_telegram(body: AuthRequest):
    """Authenticate via Telegram Mini App initData."""
    from bot.state import db

    try:
        tg_user = validate_telegram_init_data(body.init_data, TELEGRAM_TOKEN or "")
    except ValueError as e:
        raise HTTPException(status_code=401, detail=str(e))

    telegram_id = tg_user.get("id")
    if not telegram_id:
        raise HTTPException(status_code=401, detail="Invalid user data")

    # Look up in our DB
    user = await db.get_user_by_telegram_id(telegram_id)
    if not user:
        raise HTTPException(
            status_code=403,
            detail="User not registered. Use the Telegram bot to register first.",
        )

    token = create_jwt(user.telegram_id, user.account_id, user.role.value)
    return AuthResponse(
        access_token=token,
        user={
            "telegram_id": user.telegram_id,
            "name": tg_user.get("first_name", ""),
            "role": user.role.value,
            "account_id": user.account_id,
        },
    )
