"""Settings · Audit Log — read-only governance audit trail.

router.py is interface-layer code co-located with its feature
(docs/FEATURES.md): ONLY router.py may import interfaces.api.deps.
Keeps the historical ``/admin`` URL prefix.
"""
import asyncio
import logging
import os
import time

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field, model_validator
from typing import Optional

from interfaces.api.deps import (
    require_permission, get_current_db_user, get_tenant_db,
    get_platform_db, paginate, resolve_user_id,
)
from adapters.storage.models import Role
from capabilities.permissions.roles import validate_role_change, role_rank

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/admin", tags=["settings"])


# ── Audit Log ─────────────────────────────────────────────────

@router.get("/audit-log")
async def get_audit_log(
    limit: int = Query(100, ge=1, le=500),
    user: dict = Depends(require_permission("can_manage_users")),
    tenant_db=Depends(get_tenant_db),
):
    """Get the audit log for the account."""
    entries = await tenant_db.get_audit_log(user["account_id"], limit=limit)
    return {"entries": entries, "count": len(entries)}


# ── Activity (the unified who-did-what reader) ────────────────

async def _resolve_actor_names(
    tenant_db, account_id: int, events: list[dict],
) -> None:
    """Stamp ``actor_name`` on every wire event.  Two id spaces: the
    trails store platform ``users.id``; the frozen audit_log stored
    telegram ids — one account-users fetch builds both maps."""
    users = await tenant_db.list_account_users(account_id)
    by_id = {}
    by_tg = {}
    for u in users:
        name = ((getattr(u, "display_name", "") or "").strip()
                or f"#{u.id}")
        by_id[u.id] = name
        if getattr(u, "telegram_id", None):
            by_tg[u.telegram_id] = name
    for e in events:
        uid = e.get("actor_user_id")
        if uid is None:
            e["actor_name"] = ""
            continue
        space = by_tg if e.get("actor_space") == "telegram" else by_id
        e["actor_name"] = space.get(uid, f"#{uid}")


async def _viewer_flags(user: dict) -> dict:
    """Which entity-types THIS viewer may read values for — driven by
    each feature's own declaration (registry view_permissions), so the
    trail can't become a side door around per-feature gates."""
    from capabilities.activity_trail.registry import (
        _ENTITIES, ensure_declarations_loaded,
    )
    from capabilities.permissions.roles import Role, get_user_permissions
    ensure_declarations_loaded()
    perms = await get_user_permissions(
        Role(user["role"]), user["account_id"],
        is_manager=bool(user.get("is_manager")),
        is_primary_owner=bool(user.get("is_primary_owner")),
    )
    return {
        et: any(getattr(perms, p, False) for p in d.view_permissions)
        for et, d in _ENTITIES.items()
    }


@router.get("/activity")
async def get_activity(
    limit: int = Query(100, ge=1, le=200),
    before: Optional[str] = Query(None, description="ISO cursor: rows older than this"),
    entity_type: Optional[str] = Query(None),
    user: dict = Depends(require_permission("can_manage_users")),
    tenant_db=Depends(get_tenant_db),
):
    """The unified account-wide activity feed: the activity_events
    trail + the loads/inventory rich trails + the frozen thin log's
    human rows, one shape, newest first, bulk actions collapsed into
    groups (expand via ``/admin/activity/group/{group_id}``)."""
    from capabilities.activity_trail.facade import account_activity
    events = await account_activity(
        tenant_db, user["account_id"],
        limit=limit, before_ts=before, entity_type=entity_type,
        viewer_can_see=await _viewer_flags(user),
    )
    await _resolve_actor_names(tenant_db, user["account_id"], events)
    return {"events": events, "count": len(events)}


@router.get("/activity/group/{group_id}")
async def get_activity_group(
    group_id: str,
    user: dict = Depends(require_permission("can_manage_users")),
    tenant_db=Depends(get_tenant_db),
):
    """Every member event of one bulk action — full changes each (the
    per-entity recovery record; never truncated)."""
    from capabilities.activity_trail.facade import normalize_trail
    from capabilities.activity_trail.sensitive import mask_changes
    rows = await tenant_db.list_activity_events(
        user["account_id"], group_id=group_id, limit=500,
    )
    events = [normalize_trail(e) for e in rows]
    flags = await _viewer_flags(user)
    for e in events:
        e["changes"] = mask_changes(
            e["entity_type"], e["changes"],
            viewer_can_see=flags.get(e["entity_type"], False),
        )
    await _resolve_actor_names(tenant_db, user["account_id"], events)
    return {"events": events, "count": len(events)}
