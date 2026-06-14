"""Settings · Forum Routing — Telegram group-topic alert routing config.

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


# ── Forum routing (Telegram group topics) ─────────────────────

@router.get("/forum-routing")
async def get_forum_routing(
    user: dict = Depends(require_permission("can_manage_account")),
    platform_db=Depends(get_platform_db),
):
    """Return the current forum-routing state for the account.

    Powers the inline "Alert Routing" section in the Telegram Bot
    admin card.  When no group is bound the response signals the
    setup wizard should be rendered; when bound it lists each alert
    type, the topic it maps to, and whether the route is active.
    """
    from capabilities.alerting.forum_topics import FORUM_TOPIC_SPEC

    account_id = user["account_id"]
    group = await platform_db.get_forum_group(account_id)

    # Render the topic catalog regardless of bound state — the
    # dashboard uses it to show "what will be created" in the
    # not-yet-connected state.
    catalog = [
        {
            "alert_type":  spec.key,
            "name":        spec.name,
            "icon_emoji":  spec.icon_emoji,
            "description": spec.description,
            "pinned":      spec.pinned,
        }
        for spec in FORUM_TOPIC_SPEC
    ]

    if group is None:
        return {
            "connected": False,
            "catalog": catalog,
            "routes": [],
        }

    routes = await platform_db.list_alert_routes(account_id)
    by_key = {r.alert_type: r for r in routes}
    route_rows = []
    for spec in FORUM_TOPIC_SPEC:
        r = by_key.get(spec.key)
        route_rows.append({
            "alert_type":          spec.key,
            "name":                spec.name,
            "icon_emoji":          spec.icon_emoji,
            "description":         spec.description,
            "pinned":              spec.pinned,
            "is_mapped":           r is not None,
            "is_active":           bool(r and r.is_active),
            "message_thread_id":   r.message_thread_id if r else None,
            "topic_name_snapshot": r.topic_name_snapshot if r else "",
            # Per-topic "🟢 RESOLVED" receipt toggle (migration 079).
            # When false the auto-resolve pipeline still flips the
            # underlying alert_history row but skips the chat post.
            # Defaults to True on legacy rows; admin flips via the
            # ForumRoutingSection on the dashboard.
            "send_resolve_receipt": bool(r.send_resolve_receipt) if r else True,
        })

    # Account-level group-routing settings.  Per-alert-type AI
    # toggles let admins enable AI for some categories (e.g. Parking
    # AI is useful; Health AI is noise) without an all-or-nothing
    # global switch.  Only the alert types that actually generate AI
    # content are exposed; the rest stay quiet.
    _AI_CAPABLE = ("faults", "health", "parking", "camera")
    ai_per_type: dict[str, bool] = {}
    for key in _AI_CAPABLE:
        val = await platform_db.get_account_setting(
            account_id, f"forum_ai.{key}", default="1",
        )
        ai_per_type[key] = val != "0"

    return {
        "connected":      True,
        "chat_id":        group.chat_id,
        "chat_title":     group.chat_title,
        "setup_status":   group.setup_status,
        "last_setup_at":  group.last_setup_at,
        "last_repair_at": group.last_repair_at,
        "catalog":        catalog,
        "routes":         route_rows,
        "settings": {
            "ai_per_type": ai_per_type,
        },
    }


class ForumRouteToggle(BaseModel):
    is_active: bool


class ForumSettingsUpdate(BaseModel):
    # Map of alert_type → bool.  Only alert types in _AI_CAPABLE are
    # honoured server-side; unknown keys are ignored so the API stays
    # tolerant if the dashboard sends extras.
    ai_per_type: Optional[dict[str, bool]] = None


@router.put("/forum-routing/settings")
async def update_forum_settings(
    body: ForumSettingsUpdate,
    user: dict = Depends(require_permission("can_manage_account")),
    platform_db=Depends(get_platform_db),
    tenant_db=Depends(get_tenant_db),
):
    """Update per-alert-type AI toggles for the group routing.

    Each key in ``ai_per_type`` is a canonical alert key (``faults``,
    ``health``, ``parking`` today — the only types with AI content).
    Setting any of them to False makes future alerts of that type
    post to the topic *without* the AI section; DM fallback (for
    CRITICAL mirrors and non-routed accounts) still respects each
    subscriber's per-user ``ai_*`` preference.
    """
    account_id = user["account_id"]
    _AI_CAPABLE = ("faults", "health", "parking", "camera")
    changed: list[str] = []
    if body.ai_per_type:
        for alert_type, enabled in body.ai_per_type.items():
            if alert_type not in _AI_CAPABLE:
                continue
            await platform_db.set_account_setting(
                account_id, f"forum_ai.{alert_type}",
                "1" if enabled else "0",
            )
            changed.append(f"{alert_type}={'on' if enabled else 'off'}")

    if changed:
        await tenant_db.add_audit_log(
            account_id, int(user["sub"]),
            "forum_settings_update",
            target_type="account", target_id=str(account_id),
            details="ai_per_type: " + ", ".join(changed),
        )
    return {"ok": True, "changed": changed}


@router.put("/forum-routing/{alert_type}")
async def toggle_forum_route(
    alert_type: str,
    body: ForumRouteToggle,
    user: dict = Depends(require_permission("can_manage_account")),
    platform_db=Depends(get_platform_db),
    tenant_db=Depends(get_tenant_db),
):
    """Soft-toggle a single alert→topic route.

    Disabling sends future alerts of that type back to the per-user
    DM path (subscribers respect their personal mute toggles again).
    Re-enabling restores group routing.  The Telegram topic itself
    is never touched — only the database row.
    """
    from adapters.storage.models import ALERT_TYPE_KEYS

    if alert_type not in ALERT_TYPE_KEYS:
        raise HTTPException(status_code=422, detail=f"Unknown alert_type: {alert_type}")

    account_id = user["account_id"]
    route = await platform_db.get_alert_route(account_id, alert_type)
    if route is None:
        # Soft-toggle only works for an existing (possibly inactive)
        # row.  If the row doesn't exist at all the admin needs to
        # run /setupforum or /repairforum first.
        raise HTTPException(
            status_code=404,
            detail=f"No route exists for '{alert_type}'. Run /setupforum or /repairforum first.",
        )

    await platform_db.set_alert_route_active(
        account_id, alert_type, body.is_active,
    )
    await tenant_db.add_audit_log(
        account_id, int(user["sub"]),
        "forum_route_toggle",
        target_type="alert_type", target_id=alert_type,
        details=f"is_active={body.is_active}",
    )
    return {"alert_type": alert_type, "is_active": body.is_active}


# ── Per-topic "🟢 RESOLVED" receipt toggle ──────────────────────────
# Migration 079 added ``alert_routing.send_resolve_receipt``.  This
# endpoint lets admins flip it per route from the ForumRoutingSection
# checkbox.  Defaults to True on existing rows — turning it off
# suppresses the chat receipt only; the underlying alert_history row
# still flips to resolved, so the dashboard monitoring view stays
# accurate.


class ForumRouteReceiptToggle(BaseModel):
    send_resolve_receipt: bool


@router.put("/forum-routing/routes/{alert_type}/receipt")
async def toggle_forum_route_receipt(
    alert_type: str,
    body: ForumRouteReceiptToggle,
    user: dict = Depends(require_permission("can_manage_account")),
    platform_db=Depends(get_platform_db),
    tenant_db=Depends(get_tenant_db),
):
    """Enable or disable the '🟢 RESOLVED' chat receipt for one topic."""
    from adapters.storage.models import ALERT_TYPE_KEYS

    if alert_type not in ALERT_TYPE_KEYS:
        raise HTTPException(status_code=422, detail=f"Unknown alert_type: {alert_type}")

    account_id = user["account_id"]
    route = await platform_db.get_alert_route(account_id, alert_type)
    if route is None:
        raise HTTPException(
            status_code=404,
            detail=f"No route exists for '{alert_type}'. Run /setupforum or /repairforum first.",
        )
    ok = await platform_db.set_alert_route_send_resolve_receipt(
        account_id, alert_type, body.send_resolve_receipt,
    )
    await tenant_db.add_audit_log(
        account_id, int(user["sub"]),
        "forum_route_receipt_toggle",
        target_type="alert_type", target_id=alert_type,
        details=f"send_resolve_receipt={body.send_resolve_receipt}",
    )
    return {
        "alert_type": alert_type,
        "send_resolve_receipt": body.send_resolve_receipt,
        "ok": ok,
    }


@router.post("/forum-routing/disconnect")
async def disconnect_forum_routing(
    user: dict = Depends(require_permission("can_manage_account")),
    platform_db=Depends(get_platform_db),
    tenant_db=Depends(get_tenant_db),
):
    """Unbind the forum group from the account.

    Removes the ``forum_groups`` row and every ``alert_routing`` row
    in one shot — subsequent alerts fall straight back to per-user
    DM delivery.  Topics themselves are NOT deleted from Telegram;
    admins can clean those up via /resetforum in the group when they
    want a clean slate.
    """
    account_id = user["account_id"]
    group = await platform_db.get_forum_group(account_id)
    if group is None:
        return {"ok": True, "was_connected": False}

    await platform_db.delete_forum_group(account_id)
    await tenant_db.add_audit_log(
        account_id, int(user["sub"]),
        "forum_routing_disconnect",
        target_type="account", target_id=str(account_id),
        details=f"chat_id={group.chat_id}",
    )
    return {"ok": True, "was_connected": True, "chat_id": group.chat_id}
