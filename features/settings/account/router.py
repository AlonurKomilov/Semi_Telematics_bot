"""Settings · Account Settings — general settings, timezone, Telegram bot config, per-role AI guidance.

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
    require_permission, get_current_user, get_current_db_user, get_tenant_db,
    get_platform_db, paginate, resolve_user_id,
)
from adapters.storage.models import Role
from capabilities.permissions.roles import validate_role_change, role_rank

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/admin", tags=["settings"])


# ── Account Settings ──────────────────────────────────────────

class SettingUpdate(BaseModel):
    key: str = Field(..., min_length=1, max_length=50)
    value: str


@router.get("/settings")
async def get_settings(
    user: dict = Depends(require_permission("can_manage_account")),
    platform_db=Depends(get_platform_db),
    tenant_db=Depends(get_tenant_db),
):
    """Get all account settings + AI usage stats."""
    # Fetch common settings
    settings: dict[str, str] = {}
    for key in ("account_name", "alert_defaults", "timezone", "language",
                "digest_hour", "scorecard_default_subject"):
        val = await tenant_db.get_account_setting(user["account_id"], key)
        if val:
            settings[key] = val

    # Account info
    account = await platform_db.get_account(user["account_id"])

    # AI usage
    ai_stats = await platform_db.get_ai_usage_stats(user["account_id"], days=30)

    # ``schedules`` field removed from this response: the only consumer
    # was the Working Hours section in the Settings page, which was
    # consolidated into Team Management → Working Hours tab.  That tab
    # fetches schedules from the dedicated ``GET /admin/work-hours``
    # endpoint, so keeping a second copy in this payload was a wasted
    # DB read + bandwidth per Settings page load.  Endpoint still
    # exists; only the duplicate fetch path is gone.

    return {
        "account": {
            "id": account.id if account else None,
            "name": account.name if account else "",
            "tier": account.tier if account else "free",
            "is_active": account.is_active if account else False,
        },
        "settings": settings,
        "ai_usage": ai_stats,
    }


@router.put("/settings")
async def update_setting(
    body: SettingUpdate,
    user: dict = Depends(require_permission("can_manage_account")),
    tenant_db=Depends(get_tenant_db),
):
    """Update a single account setting."""
    await tenant_db.set_account_setting(user["account_id"], body.key, body.value)
    await tenant_db.add_audit_log(
        user["account_id"], int(user["sub"]),
        "setting_update",
        target_type="setting", target_id=body.key,
        details=f"Set to: {body.value[:100]}",
    )
    return {"ok": True}

# ── Bot configuration (owner-only) ───────────────────────────


class BotConfigRequest(BaseModel):
    bot_token: str = Field(..., min_length=30, max_length=100,
                           pattern=r"^\d+:[A-Za-z0-9_-]+$")


@router.put("/bot-config")
async def update_bot_config(
    body: BotConfigRequest,
    user: dict = Depends(require_permission("can_manage_account")),
    platform_db=Depends(get_platform_db),
    tenant_db=Depends(get_tenant_db),
):
    """Configure Telegram bot token for this account (owner-only).

    Validates the token via Telegram getMe(), encrypts it, and stores it.
    If the account already has a running bot, it will be restarted.
    """
    if user["role"] != "owner":
        raise HTTPException(status_code=403, detail="Only the account owner can configure the bot")

    import aiohttp
    from infra.crypto import encrypt

    # Validate token with Telegram API
    try:
        async with aiohttp.ClientSession() as session:
            async with session.get(
                f"https://api.telegram.org/bot{body.bot_token}/getMe",
                timeout=aiohttp.ClientTimeout(total=10),
            ) as resp:
                data = await resp.json()
                if not data.get("ok"):
                    raise HTTPException(
                        status_code=422,
                        detail=f"Invalid bot token: {data.get('description', 'unknown error')}",
                    )
                bot_info = data["result"]
    except aiohttp.ClientError as e:
        raise HTTPException(status_code=502, detail=f"Failed to reach Telegram API: {e}")

    bot_username = bot_info.get("username", "")
    encrypted_token = encrypt(body.bot_token)

    await platform_db.update_account(
        user["account_id"],
        bot_token_encrypted=encrypted_token,
        bot_username=bot_username,
    )

    await tenant_db.add_audit_log(
        user["account_id"], int(user["sub"]),
        "bot_config_update",
        details=f"Bot configured: @{bot_username}",
    )

    # Hot-reload: start or restart per-account bot
    try:
        from infra.bot_registry import get_registry
        registry = get_registry()
        if registry:
            await registry.restart_bot(
                account_id=user["account_id"],
                encrypted_token=encrypted_token,
            )
    except Exception as e:
        logger.warning("Bot hot-reload failed for account %d: %s", user["account_id"], e)

    return {
        "ok": True,
        "bot_username": bot_username,
        "bot_id": bot_info.get("id"),
    }


@router.delete("/bot-config")
async def delete_bot_config(
    user: dict = Depends(require_permission("can_manage_account")),
    platform_db=Depends(get_platform_db),
    tenant_db=Depends(get_tenant_db),
):
    """Disconnect the Telegram bot for this account (owner-only)."""
    if user["role"] != "owner":
        raise HTTPException(status_code=403, detail="Only the account owner can configure the bot")

    account = await platform_db.get_account(user["account_id"])
    if not account or not account.bot_token_encrypted:
        raise HTTPException(status_code=404, detail="No bot configured for this account")

    await platform_db.update_account(
        user["account_id"],
        bot_token_encrypted=None,
        bot_username="",
        webhook_secret="",
    )

    await tenant_db.add_audit_log(
        user["account_id"], int(user["sub"]),
        "bot_config_delete",
        details="Bot disconnected",
    )

    # Hot-reload: stop per-account bot
    try:
        from infra.bot_registry import get_registry
        registry = get_registry()
        if registry:
            await registry.stop_bot(user["account_id"])
    except Exception as e:
        logger.warning("Bot stop failed for account %d: %s", user["account_id"], e)

    return {"ok": True}


@router.get("/bot-config")
async def get_bot_config(
    # Read open to any staff member — role managers render the Main
    # row of the bot roster from this (username/running only; the token
    # never leaves the server).  Writes below stay owner-gated.
    user: dict = Depends(get_current_user),
    platform_db=Depends(get_platform_db),
):
    """Get bot configuration status for this account."""
    account = await platform_db.get_account(user["account_id"])
    if not account:
        raise HTTPException(status_code=404, detail="Account not found")

    if not account.bot_token_encrypted:
        return {"has_bot": False, "bot_username": ""}

    result: dict = {
        "has_bot": True,
        "bot_username": account.bot_username or "",
    }

    # Cross-process liveness check.  In split-service deployments the
    # API process never starts bots (ENABLE_BOT=0 on the API systemd
    # unit), so its in-memory registry is permanently empty.  The bot
    # service writes a short-TTL Redis key on start + refreshes it
    # every 30 s; we read that key here.  Local/dev runs (where API +
    # bot share a process) also honour this — the same key gets
    # written from the same process.  See infra/bot_registry.py.
    try:
        from infra.bot_registry import is_bot_alive
        result["is_running"] = await is_bot_alive(user["account_id"])
    except Exception:
        result["is_running"] = False

    # Fetch live bot info from Telegram
    try:
        from infra.crypto import decrypt
        import aiohttp

        token = decrypt(account.bot_token_encrypted)
        async with aiohttp.ClientSession() as session:
            async with session.get(
                f"https://api.telegram.org/bot{token}/getMe",
                timeout=aiohttp.ClientTimeout(total=5),
            ) as resp:
                data = await resp.json()
                if data.get("ok"):
                    info = data["result"]
                    result["bot_id"] = info.get("id")
                    result["first_name"] = info.get("first_name", "")
    except Exception as e:
        logger.debug("Telegram getMe failed (partial result returned): %s", e)

    return result


# ── Alert routing (owner/admin self-serve) ────────────────────────────
# The customer configures where their bot posts alerts: one forum group
# for everything (single_group) or a flat group per role
# (per_persona_groups).  Chat bindings are validated live against
# Telegram's getChat with the ACCOUNT's own bot token — a typo'd chat id
# or a group the bot was never added to gets a 422 here instead of
# silently eating alerts later.

# Mirrors capabilities.alerting.persona_mapping.PERSONAS — kept as a
# local constant so a bad slug 400s at the validator (same rationale as
# the operator console's copy in interfaces/api/routes/system.py).
_VALID_PERSONAS = ("owner_admin", "dispatcher", "safety", "fleet", "hr",
                   "accounting", "recruiter")
_ALERT_ROUTING_MODES = ("single_group", "per_persona_groups")

# The nudge threshold: below this fleet size one group comfortably fits
# the alert volume and the suggestion would be noise.  Advisory only —
# nothing is gated on it.
ALERT_ROUTING_NUDGE_VEHICLES = 30


class AlertRoutingModeRequest(BaseModel):
    mode: str = Field(..., pattern="^(single_group|per_persona_groups)$")


class PersonaGroupBindRequest(BaseModel):
    persona: str = Field(..., pattern="^(owner_admin|dispatcher|safety|fleet|hr|accounting|recruiter)$")
    chat_id: int = Field(..., description="Telegram chat id — negative for groups")


@router.get("/alert-routing")
async def get_alert_routing_settings(
    # Read is open to any staff member: role MANAGERS need this to see
    # their own row on Settings → Telegram Bot (writes stay gated
    # below — mode is owner/admin, bindings are per-persona).
    user: dict = Depends(get_current_user),
    platform_db=Depends(get_platform_db),
    tenant_db=Depends(get_tenant_db),
):
    """Current routing mode + per-persona group bindings + the fleet
    size the dashboard uses for the 'consider per-role groups'
    nudge."""
    account = await platform_db.get_account(user["account_id"])
    if not account:
        raise HTTPException(status_code=404, detail="Account not found")

    rows = await platform_db.list_persona_groups(user["account_id"])
    personas: dict = {p: None for p in _VALID_PERSONAS}
    for r in rows:
        personas[r.persona] = {
            "chat_id": r.chat_id,
            "chat_title": r.chat_title,
            "is_active": bool(r.is_active),
        }

    try:
        vehicle_count = await tenant_db.count_vehicles(user["account_id"])
    except Exception:
        vehicle_count = 0

    return {
        "mode": getattr(account, "alert_routing_mode", "single_group") or "single_group",
        "personas": personas,
        "vehicle_count": vehicle_count,
        "nudge_threshold": ALERT_ROUTING_NUDGE_VEHICLES,
        "bot_configured": bool(account.bot_token_encrypted),
    }


@router.put("/alert-routing")
async def set_alert_routing_settings(
    body: AlertRoutingModeRequest,
    user: dict = Depends(require_permission("can_manage_account")),
    platform_db=Depends(get_platform_db),
    tenant_db=Depends(get_tenant_db),
):
    """Switch between single-group and per-role alert routing.

    Safe in both directions: the resolver falls back to the legacy
    single-group lookup for any persona without a binding, so flipping
    the mode before every group is bound never drops alerts."""
    ok = await platform_db.set_alert_routing_mode(user["account_id"], body.mode)
    if not ok:
        raise HTTPException(status_code=404, detail="Account not found")
    await tenant_db.add_audit_log(
        user["account_id"], int(user["sub"]),
        "alert_routing_mode_set", details=body.mode,
    )
    return {"ok": True, "mode": body.mode}


@router.post("/alert-routing/persona-groups")
async def bind_persona_group(
    body: PersonaGroupBindRequest,
    user: dict = Depends(get_current_user),
    platform_db=Depends(get_platform_db),
    tenant_db=Depends(get_tenant_db),
):
    """Bind one role to a Telegram group (validated via getChat).

    Owner/admin bind any role; a role MANAGER binds exactly their own
    (the owner's decision: managers own their role's bot, group, and
    topics)."""
    if not _may_manage_persona_bot(user, body.persona):
        raise HTTPException(
            status_code=403,
            detail="Only the owner/admin or this role's manager can bind its group.",
        )
    account = await platform_db.get_account(user["account_id"])
    if not account or not account.bot_token_encrypted:
        raise HTTPException(
            status_code=400,
            detail="Configure the account's Telegram bot before binding alert groups.",
        )

    import aiohttp
    from infra.crypto import decrypt

    chat_title = ""
    try:
        token = decrypt(account.bot_token_encrypted)
        async with aiohttp.ClientSession() as session:
            async with session.get(
                f"https://api.telegram.org/bot{token}/getChat",
                params={"chat_id": body.chat_id},
                timeout=aiohttp.ClientTimeout(total=10),
            ) as resp:
                data = await resp.json()
        if not data.get("ok"):
            # Telegram's own wording ("chat not found") is the clearest
            # explanation the user can get — pass it through.
            raise HTTPException(
                status_code=422,
                detail="Telegram rejected this chat id: "
                       f"{data.get('description', 'unknown error')}. "
                       "Add the bot to the group, then use /chatid there.",
            )
        chat_title = (data.get("result") or {}).get("title", "") or ""
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=502, detail=f"Failed to reach Telegram API: {e}")

    row = await platform_db.upsert_persona_group(
        account_id=user["account_id"], persona=body.persona,
        chat_id=body.chat_id, chat_title=chat_title,
    )
    await tenant_db.add_audit_log(
        user["account_id"], int(user["sub"]),
        "alert_persona_group_bound",
        details=f"{body.persona} → {chat_title or body.chat_id}",
    )
    return {
        "ok": True,
        "persona": row.persona,
        "chat_id": row.chat_id,
        "chat_title": row.chat_title,
    }


@router.delete("/alert-routing/persona-groups/{persona}")
async def unbind_persona_group(
    persona: str,
    user: dict = Depends(get_current_user),
    platform_db=Depends(get_platform_db),
    tenant_db=Depends(get_tenant_db),
):
    """Unbind a role's group — its alerts fall back to the legacy
    single-group route (never dropped)."""
    if persona not in _VALID_PERSONAS:
        raise HTTPException(status_code=400, detail="Unknown persona")
    if not _may_manage_persona_bot(user, persona):
        raise HTTPException(
            status_code=403,
            detail="Only the owner/admin or this role's manager can unbind its group.",
        )
    await platform_db.delete_persona_group(user["account_id"], persona)
    await tenant_db.add_audit_log(
        user["account_id"], int(user["sub"]),
        "alert_persona_group_unbound", details=persona,
    )
    return {"ok": True}


# ── Role Sub bots (sender-only) ─────────────────────────────────
# In Sub-bot mode a role's alert group receives posts from the
# role's OWN bot.  Owner/admin manage all of them; a role MANAGER
# (users.is_manager on the matching base role) manages exactly their
# own role's bot — a safety manager cannot touch dispatch's.
# Sender-only contract: identity (registration, login, commands) stays
# on the primary bot; delivery falls back to the primary whenever a
# sub-bot is missing or down.


def _may_manage_persona_bot(user: dict, persona: str) -> bool:
    role = user.get("role", "")
    if role in ("owner", "admin"):
        return True
    if persona == "owner_admin":
        return False  # the aggregate is owner/admin territory
    return bool(user.get("is_manager")) and role == persona


class SubBotAttachRequest(BaseModel):
    persona: str = Field(..., pattern="^(owner_admin|dispatcher|safety|fleet|hr|accounting|recruiter)$")
    bot_token: str = Field(..., min_length=30, max_length=100,
                           pattern=r"^\d+:[A-Za-z0-9_-]+$")


@router.get("/bot-instances")
async def list_sub_bots(
    user: dict = Depends(get_current_user),
    platform_db=Depends(get_platform_db),
):
    """Per-persona Sub bot status.  Every staff member may LOOK (the
    rows also tell a manager which personas they can manage); writes
    are gated per persona."""
    from infra.bot_registry import is_sub_bot_alive
    rows = await platform_db.list_bot_instances(user["account_id"])
    by_persona: dict = {p: None for p in _VALID_PERSONAS}
    for r in rows:
        if not r.is_active:
            continue
        by_persona[r.persona] = {
            "persona": r.persona,
            "bot_username": r.bot_username,
            "is_running": await is_sub_bot_alive(user["account_id"], r.persona),
        }
    return {
        "personas": by_persona,
        "manageable": [p for p in _VALID_PERSONAS if _may_manage_persona_bot(user, p)],
    }


@router.post("/bot-instances")
async def attach_sub_bot(
    body: SubBotAttachRequest,
    user: dict = Depends(get_current_user),
    platform_db=Depends(get_platform_db),
    tenant_db=Depends(get_tenant_db),
):
    """Attach (or replace) a role's sender bot — validated via
    Telegram getMe, token stored encrypted like the primary's."""
    if not _may_manage_persona_bot(user, body.persona):
        raise HTTPException(
            status_code=403,
            detail="Only the owner/admin or this role's manager can attach its Sub bot.",
        )
    account = await platform_db.get_account(user["account_id"])
    if not account or not account.bot_token_encrypted:
        raise HTTPException(
            status_code=400,
            detail="Configure the account's primary Telegram bot before attaching Sub bots.",
        )

    import secrets as _secrets
    import aiohttp
    from infra.crypto import encrypt

    try:
        async with aiohttp.ClientSession() as session:
            async with session.get(
                f"https://api.telegram.org/bot{body.bot_token}/getMe",
                timeout=aiohttp.ClientTimeout(total=10),
            ) as resp:
                data = await resp.json()
        if not data.get("ok"):
            raise HTTPException(
                status_code=422,
                detail=f"Invalid bot token: {data.get('description', 'unknown error')}",
            )
        bot_info = data["result"]
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=502, detail=f"Failed to reach Telegram API: {e}")

    bot_username = bot_info.get("username", "")
    if bot_username and bot_username == (account.bot_username or ""):
        raise HTTPException(
            status_code=422,
            detail="That's the account's primary bot — a Sub bot needs its own token.",
        )

    row = await platform_db.upsert_bot_instance(
        account_id=user["account_id"],
        persona=body.persona,
        token_encrypted=encrypt(body.bot_token),
        bot_username=bot_username,
        webhook_secret=_secrets.token_urlsafe(24),
    )
    await tenant_db.add_audit_log(
        user["account_id"], int(user["sub"]),
        "sub_bot_attached", details=f"{body.persona} → @{bot_username}",
    )

    # Hot-start where a registry runs in-process (bot service / dev).
    # On the split API process this is a no-op; the bot service picks
    # the instance up on its next start_all.
    try:
        from infra.bot_registry import get_registry
        registry = get_registry()
        if registry:
            await registry.start_sub_bot(
                account_id=user["account_id"], persona=body.persona,
                encrypted_token=row.token_encrypted,
                webhook_secret=row.webhook_secret,
            )
    except Exception as e:
        logger.warning("Sub bot hot-start failed %d/%s: %s",
                       user["account_id"], body.persona, e)

    return {"ok": True, "persona": body.persona, "bot_username": bot_username}


@router.delete("/bot-instances/{persona}")
async def detach_sub_bot(
    persona: str,
    user: dict = Depends(get_current_user),
    platform_db=Depends(get_platform_db),
    tenant_db=Depends(get_tenant_db),
):
    """Detach a role's sender bot — its alerts return to the
    primary bot on the next send (never dropped)."""
    if persona not in _VALID_PERSONAS:
        raise HTTPException(status_code=400, detail="Unknown persona")
    if not _may_manage_persona_bot(user, persona):
        raise HTTPException(
            status_code=403,
            detail="Only the owner/admin or this role's manager can detach its Sub bot.",
        )
    try:
        from infra.bot_registry import get_registry
        registry = get_registry()
        if registry:
            await registry.stop_sub_bot(user["account_id"], persona)
    except Exception as e:
        logger.warning("Sub bot stop failed %d/%s: %s", user["account_id"], persona, e)
    await platform_db.delete_bot_instance(user["account_id"], persona)
    await tenant_db.add_audit_log(
        user["account_id"], int(user["sub"]),
        "sub_bot_detached", details=persona,
    )
    return {"ok": True}


# ── Per-role topic settings (route on/off + AI inclusion) ─────────────
# The role-group equivalent of the single-forum topic table: each role's
# group gets per-alert-type controls, editable by that role's manager,
# and each role only ever sees ITS OWN alert types (the persona mapping
# is the routing SSOT, so a Recruiter never sees Maintenance toggles).
#
# Storage is account_settings — the same mechanism the single-forum AI
# toggle already uses:
#   forum_ai.{key}       "1"/"0"  AI-analysis inclusion (SHARED with
#                                 single-group mode by design: "include
#                                 AI for Faults" is a per-type editorial
#                                 choice, not a per-mode one)
#   persona_route.{key}  "1"/"0"  role-group routing on/off (persona
#                                 mode only; single-group keeps its own
#                                 alert_routing.is_active)


class PersonaTopicToggle(BaseModel):
    field: str = Field(..., pattern="^(enabled|ai)$")
    value: bool


async def _topics_for(account_id: int, persona: str) -> list[str]:
    """A row's alert types.  Role rows derive from the PERMISSION
    matrix (the SSOT — granting Fleet the Safety-Events feature adds
    events to Fleet's topics automatically); the Main/owner_admin row
    keeps its static admin set."""
    from capabilities.alerting.persona_mapping import (
        canonical_types_for_persona, types_for_role,
    )
    if persona == "owner_admin":
        return canonical_types_for_persona("owner_admin")
    return await types_for_role(account_id, persona)


@router.get("/alert-routing/persona-topics")
async def list_persona_topics(
    user: dict = Depends(get_current_user),
    tenant_db=Depends(get_tenant_db),
):
    """Per-role topic settings, grouped by role — the ▸ topics &
    settings expander's data, PERMISSION-FILTERED per role.
    ``manageable`` mirrors the sub-bot contract so the UI enables
    toggles only where the server would accept the write."""
    account_id = user["account_id"]
    personas: dict = {}
    for persona in _VALID_PERSONAS:
        rows = []
        for key in await _topics_for(account_id, persona):
            enabled = await tenant_db.get_account_setting(
                account_id, f"persona_route.{persona}.{key}", default="",
            )
            if not enabled:  # legacy account-wide key, pre-per-role
                enabled = await tenant_db.get_account_setting(
                    account_id, f"persona_route.{key}", default="1",
                )
            # Shared-AI contract: ONE generated answer per alert; the
            # per-role flag only controls inclusion in that role's post.
            ai_default = await tenant_db.get_account_setting(
                account_id, f"forum_ai.{key}", default="1",
            )
            if persona == "owner_admin":
                ai = ai_default
            else:
                ai = await tenant_db.get_account_setting(
                    account_id, f"persona_ai.{persona}.{key}", default="",
                ) or ai_default
            row = {
                "alert_type": key,
                "enabled": enabled != "0",
                "ai": ai != "0",
            }
            # Sub-category selection, for types with a real vocabulary
            # (events today).  selected=None ⇒ ALL (the default).
            from capabilities.alerting.persona_mapping import ALERT_SUBTYPES
            vocab = ALERT_SUBTYPES.get(key)
            if vocab:
                sel = await tenant_db.get_account_setting(
                    account_id, f"persona_subtypes.{persona}.{key}", default="",
                )
                row["subtypes"] = {
                    "all": list(vocab),
                    "selected": sel.split(",") if sel else None,
                }
            rows.append(row)
        personas[persona] = rows
    return {
        "personas": personas,
        "manageable": [p for p in _VALID_PERSONAS if _may_manage_persona_bot(user, p)],
    }


@router.put("/alert-routing/persona-topics/{persona}/{alert_type}")
async def toggle_persona_topic(
    persona: str,
    alert_type: str,
    body: PersonaTopicToggle,
    user: dict = Depends(get_current_user),
    tenant_db=Depends(get_tenant_db),
):
    """Flip one alert type's routing (per ROLE) or AI inclusion
    (per type, shared editorial).  A manager may only touch their own
    role's row, and only types the role's PERMISSIONS grant it."""
    from adapters.storage.models import ALERT_TYPE_KEYS

    if persona not in _VALID_PERSONAS:
        raise HTTPException(status_code=400, detail="Unknown persona")
    if alert_type not in ALERT_TYPE_KEYS:
        raise HTTPException(status_code=422, detail=f"Unknown alert_type: {alert_type}")
    if not _may_manage_persona_bot(user, persona):
        raise HTTPException(
            status_code=403,
            detail="Only the owner/admin or this role's manager can change its topics.",
        )
    if alert_type not in await _topics_for(user["account_id"], persona):
        raise HTTPException(
            status_code=403,
            detail="This role's permissions don't include that alert type.",
        )

    if body.field == "enabled":
        setting_key = f"persona_route.{persona}.{alert_type}"
    elif persona == "owner_admin":
        # The Main row edits the account-wide default (single-mode's
        # forum panel writes the same key).
        setting_key = f"forum_ai.{alert_type}"
    else:
        # Per-role INCLUSION of the one shared AI answer — never a
        # second generation (owner decision).
        setting_key = f"persona_ai.{persona}.{alert_type}"
    await tenant_db.set_account_setting(
        user["account_id"], setting_key, "1" if body.value else "0",
    )
    await tenant_db.add_audit_log(
        user["account_id"], int(user["sub"]),
        "persona_topic_toggle",
        target_type="alert_type", target_id=alert_type,
        details=f"{body.field}={body.value} ({persona})",
    )
    return {"persona": persona, "alert_type": alert_type, body.field: body.value}


# ── Custom topics (user-defined, per role group) ─────────────────────
# A named routing rule: one alert type, optionally narrowed to sub-
# categories, optionally posted into its own Telegram forum thread.
# Matching custom topics REPLACE the role's default flat post (resolver
# contract).  Deleting one removes only the rule — the Telegram thread
# and its history stay.


class CustomTopicIn(BaseModel):
    persona: str = Field(..., pattern="^(dispatcher|safety|fleet|hr|accounting|recruiter)$")
    name: str = Field(..., min_length=1, max_length=60)
    alert_type: str = Field(..., min_length=1, max_length=32)
    subtypes: list[str] = Field(default_factory=list, max_length=32)


@router.get("/alert-routing/custom-topics")
async def list_custom_topics(
    user: dict = Depends(get_current_user),
    platform_db=Depends(get_platform_db),
):
    """Custom topics grouped by role (read open to staff — the roster
    renders them; writes are gated per persona)."""
    rows = await platform_db.list_alert_topics(user["account_id"])
    by_persona: dict[str, list] = {}
    for r in rows:
        # Sentinel-scoped rows (single_group mode's custom topics, keyed
        # by a "__…" persona) are managed on the single-bot surface, not
        # the per-role roster — keep them out of this persona view.
        if r.persona.startswith("__"):
            continue
        by_persona.setdefault(r.persona, []).append({
            "id": r.id,
            "name": r.name,
            "alert_type": r.alert_type,
            "subtypes": r.subtypes.split(",") if r.subtypes else [],
            "has_thread": r.thread_id is not None,
        })
    return {"personas": by_persona}


@router.post("/alert-routing/custom-topics")
async def create_custom_topic(
    body: CustomTopicIn,
    user: dict = Depends(get_current_user),
    platform_db=Depends(get_platform_db),
    tenant_db=Depends(get_tenant_db),
):
    """Create a custom topic in the role's group.  Tries to create a
    real Telegram forum thread (bot must be group admin with topic
    rights); on failure the topic still works, posting flat."""
    from capabilities.alerting.persona_mapping import ALERT_SUBTYPES

    if not _may_manage_persona_bot(user, body.persona):
        raise HTTPException(
            status_code=403,
            detail="Only the owner/admin or this role's manager can manage its topics.",
        )
    if body.alert_type not in await _topics_for(user["account_id"], body.persona):
        raise HTTPException(
            status_code=403,
            detail="This role's permissions don't include that alert type.",
        )
    vocab = ALERT_SUBTYPES.get(body.alert_type)
    if body.subtypes:
        if not vocab:
            raise HTTPException(
                status_code=422,
                detail=f"'{body.alert_type}' has no sub-categories.",
            )
        unknown = [s for s in body.subtypes if s not in vocab]
        if unknown:
            raise HTTPException(status_code=422, detail=f"Unknown sub-categories: {unknown}")

    grp = await platform_db.get_persona_group(user["account_id"], body.persona)
    if grp is None:
        raise HTTPException(
            status_code=400,
            detail="Bind this role's group before adding custom topics.",
        )

    # Try to create a real forum thread via the role's SENDER bot
    # (Sub bot when attached, else primary) — plain Bot-API HTTP, same
    # pattern as getMe/getChat.  Fail-open: no thread ⇒ flat posts.
    thread_id = None
    try:
        import aiohttp
        from infra.crypto import decrypt
        token = None
        inst = await platform_db.get_bot_instance(user["account_id"], body.persona)
        if inst is not None:
            token = decrypt(inst.token_encrypted)
        else:
            account = await platform_db.get_account(user["account_id"])
            if account and account.bot_token_encrypted:
                token = decrypt(account.bot_token_encrypted)
        if token:
            async with aiohttp.ClientSession() as session:
                async with session.get(
                    f"https://api.telegram.org/bot{token}/createForumTopic",
                    params={"chat_id": grp.chat_id, "name": body.name[:128]},
                    timeout=aiohttp.ClientTimeout(total=10),
                ) as resp:
                    data = await resp.json()
            if data.get("ok"):
                thread_id = (data.get("result") or {}).get("message_thread_id")
    except Exception as e:
        logger.debug("createForumTopic failed acct=%d persona=%s: %s",
                     user["account_id"], body.persona, e)

    # Store the selection in vocabulary order (or '' = every sub-category).
    subtypes_csv = ""
    if vocab and body.subtypes and len(set(body.subtypes)) < len(vocab):
        subtypes_csv = ",".join(s for s in vocab if s in set(body.subtypes))
    row = await platform_db.create_alert_topic(
        account_id=user["account_id"], persona=body.persona,
        name=body.name.strip(), alert_type=body.alert_type,
        subtypes=subtypes_csv, thread_id=thread_id,
    )
    await tenant_db.add_audit_log(
        user["account_id"], int(user["sub"]),
        "custom_topic_created",
        target_type="alert_topic", target_id=str(row.id),
        details=f"{body.persona}/{body.alert_type}: {row.name} "
                f"({subtypes_csv or 'all'}; thread={'yes' if thread_id else 'no'})",
    )
    return {
        "id": row.id,
        "name": row.name,
        "alert_type": row.alert_type,
        "subtypes": row.subtypes.split(",") if row.subtypes else [],
        "has_thread": thread_id is not None,
    }


@router.delete("/alert-routing/custom-topics/{topic_id}")
async def delete_custom_topic(
    topic_id: int,
    user: dict = Depends(get_current_user),
    platform_db=Depends(get_platform_db),
    tenant_db=Depends(get_tenant_db),
):
    """Remove the routing rule.  The Telegram thread (and its message
    history) is deliberately left in place — alerts just stop landing
    there and return to the role's default routing."""
    row = await platform_db.get_alert_topic(user["account_id"], topic_id)
    # Sentinel-scoped ("__…") rows belong to the single-bot forum surface
    # and are managed via /forum-routing — this per-role endpoint must not
    # touch them (keeps the scope guard symmetric with list_custom_topics).
    if row is None or row.persona.startswith("__"):
        raise HTTPException(status_code=404, detail="Topic not found")
    if not _may_manage_persona_bot(user, row.persona):
        raise HTTPException(
            status_code=403,
            detail="Only the owner/admin or this role's manager can manage its topics.",
        )
    await platform_db.delete_alert_topic(user["account_id"], topic_id)
    await tenant_db.add_audit_log(
        user["account_id"], int(user["sub"]),
        "custom_topic_deleted",
        target_type="alert_topic", target_id=str(topic_id),
        details=f"{row.persona}/{row.alert_type}: {row.name}",
    )
    return {"ok": True}


class SubtypeSelection(BaseModel):
    # The sub-categories this role's group should receive for one alert
    # type.  Empty list (or the full vocabulary) = ALL — stored as the
    # cleared default so future vocabulary additions flow automatically.
    selected: list[str] = Field(default_factory=list, max_length=32)


@router.put("/alert-routing/persona-topics/{persona}/{alert_type}/subtypes")
async def set_persona_topic_subtypes(
    persona: str,
    alert_type: str,
    body: SubtypeSelection,
    user: dict = Depends(get_current_user),
    tenant_db=Depends(get_tenant_db),
):
    """Select which sub-categories of one alert type route to this
    role's group (e.g. Safety Events: only crash + braking).  Same
    permission gate as the other row controls."""
    from capabilities.alerting.persona_mapping import ALERT_SUBTYPES

    if persona not in _VALID_PERSONAS:
        raise HTTPException(status_code=400, detail="Unknown persona")
    if not _may_manage_persona_bot(user, persona):
        raise HTTPException(
            status_code=403,
            detail="Only the owner/admin or this role's manager can change its topics.",
        )
    vocab = ALERT_SUBTYPES.get(alert_type)
    if not vocab:
        raise HTTPException(
            status_code=422,
            detail=f"'{alert_type}' has no sub-categories.",
        )
    if alert_type not in await _topics_for(user["account_id"], persona):
        raise HTTPException(
            status_code=403,
            detail="This role's permissions don't include that alert type.",
        )
    unknown = [s for s in body.selected if s not in vocab]
    if unknown:
        raise HTTPException(status_code=422, detail=f"Unknown sub-categories: {unknown}")

    # Full selection == ALL == cleared default (future vocab additions
    # then flow to this role automatically instead of being silently
    # excluded by a frozen snapshot).
    selected = [s for s in vocab if s in set(body.selected)]
    value = "" if (not selected or len(selected) == len(vocab)) else ",".join(selected)
    await tenant_db.set_account_setting(
        user["account_id"], f"persona_subtypes.{persona}.{alert_type}", value,
    )
    await tenant_db.add_audit_log(
        user["account_id"], int(user["sub"]),
        "persona_topic_subtypes",
        target_type="alert_type", target_id=alert_type,
        details=f"{persona}: {value or 'all'}",
    )
    return {
        "persona": persona,
        "alert_type": alert_type,
        "selected": selected if value else None,
    }


# ── Role AI guidance ──────────────────────────────────────────────────────────

class RoleGuidanceIn(BaseModel):
    guidance: str = Field(..., min_length=10, max_length=4000)


@router.get("/role-guidance")
async def get_all_role_guidance(
    user: dict = Depends(require_permission("can_manage_account")),
    platform_db=Depends(get_platform_db),
):
    """Return all per-account AI guidance overrides.

    Each entry describes how the AI should behave for a given role within
    this account.  Missing roles fall back to the built-in defaults.
    """
    account_id = user["account_id"]
    overrides = await platform_db.get_all_role_ai_guidance(account_id)

    # Return the hardcoded defaults merged with any overrides so callers
    # can see every role with its active guidance in one response.
    from capabilities.permissions.roles import Role as RoleEnum, build_role_guidance
    result = {}
    for role in RoleEnum:
        result[role.value] = {
            "guidance": overrides.get(role.value) or build_role_guidance(role.value),
            "is_custom": role.value in overrides,
        }
    return result


@router.put("/role-guidance/{role}")
async def set_role_guidance(
    role: str,
    body: RoleGuidanceIn,
    user: dict = Depends(require_permission("can_manage_account")),
    platform_db=Depends(get_platform_db),
):
    """Set a custom AI guidance override for a role in this account."""
    from capabilities.permissions.roles import Role as RoleEnum
    valid_roles = {r.value for r in RoleEnum}
    if role not in valid_roles:
        raise HTTPException(status_code=422, detail=f"Unknown role '{role}'. Valid: {sorted(valid_roles)}")

    account_id = user["account_id"]
    await platform_db.set_role_ai_guidance(account_id, role, body.guidance)
    return {"status": "ok", "role": role, "guidance": body.guidance}


@router.delete("/role-guidance/{role}")
async def delete_role_guidance(
    role: str,
    user: dict = Depends(require_permission("can_manage_account")),
    platform_db=Depends(get_platform_db),
):
    """Remove a custom AI guidance override for a role (reverts to built-in default)."""
    account_id = user["account_id"]
    deleted = await platform_db.delete_role_ai_guidance(account_id, role)
    if not deleted:
        raise HTTPException(status_code=404, detail=f"No custom guidance found for role '{role}'")
    return {"status": "ok", "role": role}


# ── Account timezone (single source of truth for cron + display) ───
#
# Per-user override lives on ``users.timezone`` and is set via the
# Profile page.  This admin endpoint sets the account-wide default —
# every user without an override inherits it, every cron job uses it
# to decide "is it 09:00 here yet?", every formatter renders against it.

class AccountTimezoneUpdate(BaseModel):
    timezone: str = Field(..., min_length=1)


@router.get("/timezone")
async def get_account_timezone(
    user: dict = Depends(require_permission("can_manage_account")),
    platform_db=Depends(get_platform_db),
):
    """Return the account's default timezone plus the supported list."""
    from capabilities.localization.tz import IANA_OPTIONS, DEFAULT_TIMEZONE
    acct = await platform_db.get_account(user["account_id"])
    return {
        "timezone": getattr(acct, "timezone", DEFAULT_TIMEZONE) or DEFAULT_TIMEZONE,
        "options": list(IANA_OPTIONS),
    }


@router.put("/timezone")
async def set_account_timezone(
    body: AccountTimezoneUpdate,
    user: dict = Depends(require_permission("can_manage_account")),
    platform_db=Depends(get_platform_db),
    tenant_db=Depends(get_tenant_db),
):
    """Set the account's default timezone.  Validates against the
    canonical IANA-options list so an admin can't enter ``"EST"``
    (deprecated alias) or a typo."""
    from capabilities.localization.tz import IANA_OPTIONS
    tz_val = body.timezone.strip()
    if tz_val not in IANA_OPTIONS:
        raise HTTPException(
            status_code=422,
            detail=f"Unsupported timezone. Valid values: {', '.join(IANA_OPTIONS)}",
        )
    ok = await platform_db.update_account(user["account_id"], timezone=tz_val)
    if not ok:
        raise HTTPException(status_code=404, detail="Account not found")
    await tenant_db.add_audit_log(
        user["account_id"], int(user["sub"]),
        "timezone_update",
        target_type="account", target_id=str(user["account_id"]),
        details=f"Set account timezone to {tz_val}",
    )
    return {"timezone": tz_val}


# ── Department modules (account-level on/off) ─────────────────────
#
# The backend half of the Permissions page's section-header switches.
# ``accounts.disabled_modules`` (CSV of disabled ids) is the storage;
# a disabled module force-masks its permission flags account-wide (see
# capabilities/permissions/modules.py), so "module off" is a real API
# switch, not just hidden nav.  Driver Pay folded in here 2026-07-08,
# replacing the legacy one-off ``payroll_enabled`` flag + its bespoke
# Settings card.


class ModulesUpdate(BaseModel):
    enabled: list[str]


@router.get("/account/modules")
async def get_account_modules(
    user: dict = Depends(require_permission("can_manage_account")),
    platform_db=Depends(get_platform_db),
):
    from capabilities.permissions.modules import (
        TOGGLEABLE_MODULES, enabled_modules,
    )
    acct = await platform_db.get_account(user["account_id"])
    return {
        "enabled": enabled_modules(getattr(acct, "disabled_modules", "")),
        "all": list(TOGGLEABLE_MODULES),
    }


@router.put("/account/modules")
async def set_account_modules(
    body: ModulesUpdate,
    user: dict = Depends(require_permission("can_manage_account")),
    platform_db=Depends(get_platform_db),
    tenant_db=Depends(get_tenant_db),
):
    """Persist the enabled-module list.  Unknown ids are ignored (the
    CSV codec only stores registry ids), and the permissions cache is
    invalidated so the mask applies on the next request."""
    from capabilities.permissions.modules import (
        enabled_modules, to_disabled_csv,
    )
    from capabilities.permissions.roles import invalidate_permissions_cache
    csv = to_disabled_csv(body.enabled)
    ok = await platform_db.update_account(
        user["account_id"], disabled_modules=csv,
    )
    if not ok:
        raise HTTPException(status_code=404, detail="Account not found")
    invalidate_permissions_cache(user["account_id"])
    await tenant_db.add_audit_log(
        user["account_id"], int(user["sub"]),
        "account_modules_update",
        target_type="account", target_id=str(user["account_id"]),
        details=f"Disabled modules: {csv or '(none)'}",
    )
    return {"enabled": enabled_modules(csv)}
