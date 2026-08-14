"""Settings · Account Settings — general settings, timezone, Telegram bot config, per-role AI guidance.

router.py is interface-layer code co-located with its feature
(docs/FEATURES.md): router.py and config.py are the interface-layer pair — those two may
# import interfaces.api.deps; nothing else in the feature may.
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
from capabilities.activity_trail import record_simple
from capabilities.permissions.roles import validate_role_change, role_rank
from features.carrier_directory.service import MAX_SENDER_NAME, clean_sender_name

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/admin", tags=["settings"])

# The feature-prefixed config surface: ``/settings/config``.
#
# General settings is a feature like any other, so its account_settings
# rows reach the same place by the same shape.  Only the CONFIG verbs move
# — bot config, forum routing, modules, public identity, role guidance and
# the danger zone stay on ``/admin/*`` with can_manage_account, because
# those are the page's operations rather than its settings.
config_router = APIRouter(prefix="/settings", tags=["settings"])



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

    await record_simple(
        tenant_db, user["account_id"], await resolve_user_id(user),
        "bot_config_update", "account", user["account_id"],
        note=f"Bot configured: @{bot_username}",
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

    # Health right after connect: the card renders per-surface results
    # (webhook clean? avatar? bindings reachable?) without the owner
    # pressing anything.  Fire-and-forget — the connect response must
    # not wait on a dozen Telegram probes.
    try:
        from capabilities.notifications.bot_health import run_bot_health
        asyncio.create_task(
            run_bot_health(user["account_id"], platform_db=platform_db),
            name=f"bot_health_{user['account_id']}",
        )
    except Exception:
        logger.exception("post-connect bot health scheduling failed")

    return {
        "ok": True,
        "bot_username": bot_username,
        "bot_id": bot_info.get("id"),
    }


@router.get("/bot-health")
async def get_bot_health(
    user: dict = Depends(require_permission("can_manage_account")),
):
    """Last stored bot-health report (Redis, 7-day TTL) — instant read
    for the Settings card.  ``null`` report means never checked."""
    from capabilities.notifications.bot_health import get_bot_health_report
    return {"report": await get_bot_health_report(user["account_id"])}


@router.post("/bot-health/check")
async def run_bot_health_check(
    user: dict = Depends(require_permission("can_manage_account")),
    platform_db=Depends(get_platform_db),
):
    """Probe the account bot's real delivery surface NOW (token,
    webhook hygiene, group membership, forum topics, sub bots) and
    return the fresh report.  Bounded by per-call Telegram timeouts."""
    from capabilities.notifications.bot_health import run_bot_health
    report = await run_bot_health(user["account_id"], platform_db=platform_db)
    return {"report": report}


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

    await record_simple(
        tenant_db, user["account_id"], await resolve_user_id(user),
        "bot_config_delete", "account", user["account_id"],
        note="Bot disconnected",
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
    await record_simple(
        tenant_db, user["account_id"], await resolve_user_id(user),
        "timezone_update", "account", user["account_id"],
        changes={"timezone": {"from": None, "to": tz_val}},
    )
    return {"timezone": tz_val}


# ── Public identity (what outsiders see) ──────────────────────────
#
# ``accounts.name`` is the tenant's registered name and the label every
# INTERNAL surface uses.  Outward, token-gated surfaces read by people
# with no relationship to this account — today the carrier self-fill
# page and its invite email — show this instead.  Empty is a real
# setting meaning "show no company name at all"; those surfaces fall
# back to neutral wording, never to ``name``.
#
# Account-wide, so it sits behind the account-wide permission.  A
# recruiting manager overrides it per invite link without needing this
# right (features/carrier_directory/router.py).


class PublicIdentityUpdate(BaseModel):
    # Shares the carrier-directory cap — this value and the per-link
    # override land in the same header/page slots.
    public_display_name: str = Field("", max_length=MAX_SENDER_NAME)


@router.get("/account/public-identity")
async def get_account_public_identity(
    user: dict = Depends(require_permission("can_manage_account")),
    platform_db=Depends(get_platform_db),
):
    acct = await platform_db.get_account(user["account_id"])
    if not acct:
        raise HTTPException(status_code=404, detail="Account not found")
    return {
        "public_display_name": getattr(acct, "public_display_name", "") or "",
        # Shown read-only beside the field so an owner can see exactly
        # what they are replacing.
        "registered_name": getattr(acct, "name", "") or "",
    }


@router.put("/account/public-identity")
async def set_account_public_identity(
    body: PublicIdentityUpdate,
    user: dict = Depends(require_permission("can_manage_account")),
    platform_db=Depends(get_platform_db),
    tenant_db=Depends(get_tenant_db),
):
    """Set (or clear) the outward-facing name.  Clearing is deliberate —
    it makes public surfaces anonymous rather than reverting to the
    registered name."""
    value = clean_sender_name(body.public_display_name)
    ok = await platform_db.update_account(
        user["account_id"], public_display_name=value,
    )
    if not ok:
        raise HTTPException(status_code=404, detail="Account not found")
    await record_simple(
        tenant_db, user["account_id"], await resolve_user_id(user),
        "public_identity_update", "account", user["account_id"],
        changes={"public_display_name": {"from": None, "to": value or None}},
        note=("" if value else
              "cleared — outside surfaces show no company name"),
    )
    return {"public_display_name": value}


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
    await record_simple(
        tenant_db, user["account_id"], await resolve_user_id(user),
        "account_modules_update", "account", user["account_id"],
        changes={"disabled_modules": {"from": None, "to": csv or ""}},
    )
    return {"enabled": enabled_modules(csv)}
