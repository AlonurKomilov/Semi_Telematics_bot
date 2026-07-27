"""Settings · Forum Routing — Telegram group-topic alert routing config.

router.py is interface-layer code co-located with its feature
(docs/FEATURES.md): ONLY router.py may import interfaces.api.deps.
Keeps the historical ``/admin`` URL prefix.
"""
import logging

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from typing import Optional

from interfaces.api.deps import (
    require_permission, get_tenant_db,
    get_platform_db,
)

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
    from capabilities.notifications.forum_topics import FORUM_TOPIC_SPEC

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

    from capabilities.alerting.persona_mapping import ALERT_SUBTYPES, FORUM_SENTINEL

    routes = await platform_db.list_alert_routes(account_id)
    by_key = {r.alert_type: r for r in routes}
    route_rows = []
    for spec in FORUM_TOPIC_SPEC:
        r = by_key.get(spec.key)
        row = {
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
        }
        # Sub-category selection (only types with a vocabulary, e.g.
        # events).  ``selected=None`` ⇒ ALL kinds (the default).
        vocab = ALERT_SUBTYPES.get(spec.key)
        if vocab:
            sel = await platform_db.get_account_setting(
                account_id, f"forum_subtypes.{spec.key}", default="",
            )
            row["subtypes"] = {
                "all": list(vocab),
                "selected": (sel.split(",") if sel else None),
            }
        route_rows.append(row)

    # Custom topics for single mode live under the FORUM_SENTINEL persona.
    custom_topics = [
        {
            "id": tp.id, "name": tp.name, "alert_type": tp.alert_type,
            "subtypes": tp.subtypes.split(",") if tp.subtypes else [],
            "has_thread": tp.thread_id is not None,
        }
        for tp in await platform_db.list_alert_topics(account_id, FORUM_SENTINEL)
    ]

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
        "custom_topics":  custom_topics,
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
    # Existence check must see INACTIVE rows too — otherwise re-enabling
    # a route the admin just turned off 404s (get_alert_route hides
    # is_active=0 rows for the pipeline's benefit, not this endpoint's).
    route = await platform_db.get_alert_route_any(account_id, alert_type)
    if route is None:
        # If the row doesn't exist at all the admin needs to run
        # /setupforum or /repairforum first.
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
    # See toggle_forum_route: an inactive route still has a receipt
    # setting to flip, so the existence check must include is_active=0.
    route = await platform_db.get_alert_route_any(account_id, alert_type)
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


# ── Sub-category (kind) selection — single-mode parity with role mode ─
# Stored as account_setting ``forum_subtypes.{key}`` (csv, ""=ALL); the
# resolver's single-group path gates on it.  Owner/admin only.


class ForumSubtypeSelection(BaseModel):
    selected: list[str]


@router.put("/forum-routing/{alert_type}/subtypes")
async def set_forum_subtypes(
    alert_type: str,
    body: ForumSubtypeSelection,
    user: dict = Depends(require_permission("can_manage_account")),
    platform_db=Depends(get_platform_db),
    tenant_db=Depends(get_tenant_db),
):
    """Narrow which sub-categories of an alert type reach the group
    (e.g. Safety Events → only crash + braking).  A full or empty
    selection normalizes to ALL (stored as "")."""
    from capabilities.alerting.persona_mapping import ALERT_SUBTYPES

    vocab = ALERT_SUBTYPES.get(alert_type)
    if not vocab:
        raise HTTPException(status_code=422, detail=f"'{alert_type}' has no sub-categories.")
    unknown = [s for s in body.selected if s not in vocab]
    if unknown:
        raise HTTPException(status_code=422, detail=f"Unknown sub-categories: {unknown}")

    sel = set(body.selected)
    csv = "" if (not sel or len(sel) >= len(vocab)) else ",".join(s for s in vocab if s in sel)
    await tenant_db.set_account_setting(user["account_id"], f"forum_subtypes.{alert_type}", csv)
    await tenant_db.add_audit_log(
        user["account_id"], int(user["sub"]),
        "forum_subtypes_set",
        target_type="alert_type", target_id=alert_type,
        details=f"selected={csv or 'all'}",
    )
    return {"alert_type": alert_type, "selected": (csv.split(",") if csv else None)}


# ── Custom topics (single mode) — sentinel-keyed, PRIMARY-bot thread ──


class ForumCustomTopicIn(BaseModel):
    # No persona field: single mode is role-less, the server keys these
    # under FORUM_SENTINEL.  The gate is can_manage_account (owner/admin).
    name: str = Field(min_length=1, max_length=128)
    alert_type: str
    subtypes: list[str] = Field(default_factory=list)


@router.post("/forum-routing/custom-topics")
async def create_forum_custom_topic(
    body: ForumCustomTopicIn,
    user: dict = Depends(require_permission("can_manage_account")),
    platform_db=Depends(get_platform_db),
    tenant_db=Depends(get_tenant_db),
):
    """Create a custom topic in the single-bot forum group: a named
    Telegram thread that a chosen alert type (optionally narrowed by
    sub-category) posts to, replacing its default type-topic."""
    from adapters.storage.models import ALERT_TYPE_KEYS
    from capabilities.alerting.persona_mapping import ALERT_SUBTYPES, FORUM_SENTINEL

    if body.alert_type not in ALERT_TYPE_KEYS:
        raise HTTPException(status_code=422, detail=f"Unknown alert_type: {body.alert_type}")
    vocab = ALERT_SUBTYPES.get(body.alert_type)
    if body.subtypes:
        if not vocab:
            raise HTTPException(status_code=422, detail=f"'{body.alert_type}' has no sub-categories.")
        unknown = [s for s in body.subtypes if s not in vocab]
        if unknown:
            raise HTTPException(status_code=422, detail=f"Unknown sub-categories: {unknown}")

    group = await platform_db.get_forum_group(user["account_id"])
    if group is None:
        raise HTTPException(status_code=400, detail="Connect a forum group before adding custom topics.")

    account = await platform_db.get_account(user["account_id"])
    if not (account and account.bot_token_encrypted):
        raise HTTPException(status_code=400, detail="No bot is configured for this account.")
    from infra.crypto import decrypt
    token = decrypt(account.bot_token_encrypted)

    # Create the real forum thread via the PRIMARY bot.  DELIBERATE
    # divergence from the per-role contract: in the single forum a
    # threadless topic would REPLACE the type-topic and dump into
    # General (silent downgrade), so we FAIL rather than persist a flat
    # row — the row is only written once the thread exists.
    thread_id = None
    try:
        import aiohttp
        async with aiohttp.ClientSession() as session:
            async with session.get(
                f"https://api.telegram.org/bot{token}/createForumTopic",
                params={"chat_id": group.chat_id, "name": body.name[:128]},
                timeout=aiohttp.ClientTimeout(total=10),
            ) as resp:
                data = await resp.json()
        if data.get("ok"):
            thread_id = (data.get("result") or {}).get("message_thread_id")
    except Exception as e:
        # Log the error TYPE, never the raw exception — this block holds
        # the decrypted bot token; a stringified error must not risk it.
        logger.warning("createForumTopic failed acct=%d: %s",
                       user["account_id"], type(e).__name__)
    if thread_id is None:
        raise HTTPException(
            status_code=502,
            detail="Couldn't create the Telegram topic — the bot needs admin rights with "
                   "'Manage Topics' in the group. Fix that and try again.",
        )

    subtypes_csv = ""
    if vocab and body.subtypes and len(set(body.subtypes)) < len(vocab):
        subtypes_csv = ",".join(s for s in vocab if s in set(body.subtypes))
    row = await platform_db.create_alert_topic(
        account_id=user["account_id"], persona=FORUM_SENTINEL,
        name=body.name.strip(), alert_type=body.alert_type,
        subtypes=subtypes_csv, thread_id=thread_id,
    )
    await tenant_db.add_audit_log(
        user["account_id"], int(user["sub"]),
        "forum_custom_topic_created",
        target_type="alert_topic", target_id=str(row.id),
        details=f"{body.alert_type}: {row.name} ({subtypes_csv or 'all'}; thread={thread_id})",
    )
    return {
        "id": row.id, "name": row.name, "alert_type": row.alert_type,
        "subtypes": row.subtypes.split(",") if row.subtypes else [],
        "has_thread": True,
    }


@router.delete("/forum-routing/custom-topics/{topic_id}")
async def delete_forum_custom_topic(
    topic_id: int,
    user: dict = Depends(require_permission("can_manage_account")),
    platform_db=Depends(get_platform_db),
    tenant_db=Depends(get_tenant_db),
):
    """Remove the routing rule.  The Telegram thread and its history are
    deliberately left in place — alerts return to the default type-topic."""
    from capabilities.alerting.persona_mapping import FORUM_SENTINEL

    row = await platform_db.get_alert_topic(user["account_id"], topic_id)
    # Guard the sentinel: a per-role topic must never be deletable here.
    if row is None or row.persona != FORUM_SENTINEL:
        raise HTTPException(status_code=404, detail="Topic not found")
    await platform_db.delete_alert_topic(user["account_id"], topic_id)
    await tenant_db.add_audit_log(
        user["account_id"], int(user["sub"]),
        "forum_custom_topic_deleted",
        target_type="alert_topic", target_id=str(topic_id),
        details=f"{row.alert_type}: {row.name}",
    )
    return {"ok": True}


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
