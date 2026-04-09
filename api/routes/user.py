"""User profile API endpoint."""

import re

import bcrypt
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from api.deps import get_current_user, get_platform_db
from permissions import get_permissions
from database import Role

router = APIRouter(prefix="/user", tags=["user"])

_EMAIL_RE = re.compile(r"^[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}$")


@router.get("/me")
async def user_me(
    user: dict = Depends(get_current_user),
    platform_db=Depends(get_platform_db),
):
    """Return the current user's profile and permissions."""
    db_user = await platform_db.get_user_by_telegram_id(int(user["sub"]))
    if not db_user:
        return {"error": "User not found"}

    role_enum = Role(user["role"])
    perms = get_permissions(role_enum)
    perm_dict = {
        field: getattr(perms, field)
        for field in perms.__dataclass_fields__
    }

    return {
        "telegram_id": db_user.telegram_id,
        "display_name": db_user.display_name,
        "role": user["role"],
        "department": db_user.department,
        "account_id": user["account_id"],
        "truck_num": db_user.truck_num,
        "language": db_user.language,
        "timezone": db_user.timezone,
        "email": db_user.email,
        "has_password": db_user.password_hash is not None,
        "permissions": perm_dict,
    }


class SetCredentialsRequest(BaseModel):
    email: str
    password: str


@router.put("/credentials")
async def set_credentials(
    body: SetCredentialsRequest,
    user: dict = Depends(get_current_user),
    platform_db=Depends(get_platform_db),
):
    """Set email+password for the current user.

    Allows Telegram-authenticated users to add email/password
    login credentials for the dashboard.
    """
    if not _EMAIL_RE.match(body.email):
        raise HTTPException(status_code=422, detail="Invalid email address")
    if len(body.password) < 8:
        raise HTTPException(
            status_code=422, detail="Password must be at least 8 characters"
        )

    # Check email not already taken by another user
    existing = await platform_db.get_user_by_email(body.email)
    db_user = await platform_db.get_user_by_telegram_id(int(user["sub"]))
    if not db_user:
        raise HTTPException(status_code=404, detail="User not found")
    if existing and existing.id != db_user.id:
        raise HTTPException(status_code=409, detail="Email already in use")

    pw_hash = bcrypt.hashpw(body.password.encode(), bcrypt.gensalt()).decode()
    await platform_db.set_user_email_password(db_user.id, body.email, pw_hash)
    return {"detail": "Credentials saved"}
