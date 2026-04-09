"""FastAPI dependencies: auth, database, role checks."""

from fastapi import Depends, HTTPException, Header

from jose import JWTError

from api.auth import decode_jwt
from bot.state import router as db_router
from permissions import can


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


def require_permission(feature: str):
    """Dependency factory: check the user's role has a specific permission."""
    async def _check(user: dict = Depends(get_current_user)):
        if not can(user["role"], feature):
            raise HTTPException(status_code=403, detail="Insufficient permissions")
        return user
    return _check


async def get_tenant_db(user: dict = Depends(get_current_user)):
    """Get the tenant database for the current user's account."""
    return await db_router.get_tenant(user["account_id"])


async def get_platform_db():
    """Get the platform database."""
    return db_router.platform
