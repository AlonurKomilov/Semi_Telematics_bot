"""Per-user UI preference endpoints — the backend half of the
preferences capability.

An opaque key-value store scoped to ONE user, used by the dashboard's
preferences service (``interfaces/dashboard/src/preferences/``) so an
operator's UI state follows them across devices instead of being trapped
in one browser's localStorage.

WHAT BELONGS HERE vs a feature table — the rule the dashboard applies
too (see ``interfaces/dashboard/src/preferences/CLAUDE.md``):

  * If any BACKEND path reads the value to act on it (DND gating alerts,
    timezone in bot messages, language in emails), or it affects anyone
    but the current user  ->  typed column / feature table.
  * If it only changes how THIS user's screen renders  ->  here.

That's why ``PUT /user/preferences`` (language / timezone / DND) stays a
typed endpoint in ``interfaces/api/routes/user.py``: the bot and the
notification router consume those values.  This store is never read by
backend logic — only handed back to the browser that wrote it.

Tenant safety: every handler resolves the user from the JWT and scopes
the query by ``user_id``.  No path is constructible to read or write
another user's preferences.

URLs are unchanged from when these lived in ``routes/user.py`` — the
prefix below reproduces ``/user/preferences/...`` exactly, and
``capabilities/preferences/tests/test_preferences_routes.py`` pins them.
"""

from __future__ import annotations

from typing import Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from interfaces.api.deps import (
    get_current_db_user,
    get_current_user,
    get_platform_db,
)

from .keys import VALUE_MAX, validate_key, validate_prefix

router = APIRouter(prefix="/user/preferences", tags=["preferences"])


class UiPrefBody(BaseModel):
    value: str = Field("", max_length=VALUE_MAX)


async def _require_user(user: dict, platform_db):
    """Resolve the JWT's user row, or 404.  Every handler scopes by the
    id this returns — never by anything client-supplied."""
    db_user = await get_current_db_user(user, platform_db)
    if not db_user:
        raise HTTPException(status_code=404, detail="User not found")
    return db_user


@router.get("/ui")
async def list_ui_preferences(
    prefix: Optional[str] = None,
    user: dict = Depends(get_current_user),
    platform_db=Depends(get_platform_db),
):
    """Bulk-load every UI preference for the requesting user.

    ``prefix`` narrows to keys starting with that string (e.g.
    ``table.``).  This is the read path the dashboard's preferences
    service uses on login — ONE round-trip for every key instead of one
    request per key.
    """
    validate_prefix(prefix)
    db_user = await _require_user(user, platform_db)
    items = await platform_db.list_user_preferences(db_user.id, prefix=prefix)
    return {"items": items, "count": len(items)}


@router.get("/ui/{key}")
async def get_ui_preference(
    key: str,
    user: dict = Depends(get_current_user),
    platform_db=Depends(get_platform_db),
):
    """Read a single UI preference scoped to the requesting user.

    Returns ``{"value": "..."}`` (empty string when the key is unset)
    rather than 404 so the frontend treats first-read-of-a-new-key the
    same as fresh-default — no special error path needed in callers.
    """
    validate_key(key)
    db_user = await _require_user(user, platform_db)
    value = await platform_db.get_user_preference(db_user.id, key)
    return {"key": key, "value": value}


@router.put("/ui/{key}")
async def set_ui_preference(
    key: str,
    body: UiPrefBody,
    user: dict = Depends(get_current_user),
    platform_db=Depends(get_platform_db),
):
    """Upsert a single UI preference for the requesting user."""
    validate_key(key)
    db_user = await _require_user(user, platform_db)
    await platform_db.set_user_preference(db_user.id, key, body.value)
    return {"ok": True, "key": key}


@router.delete("/ui/{key}")
async def delete_ui_preference(
    key: str,
    user: dict = Depends(get_current_user),
    platform_db=Depends(get_platform_db),
):
    """Delete a single UI preference — used by the dashboard's
    "Reset to defaults" so cleared state stops syncing back from the
    last device that wrote it."""
    validate_key(key)
    db_user = await _require_user(user, platform_db)
    await platform_db.delete_user_preference(db_user.id, key)
    return {"ok": True, "key": key}
