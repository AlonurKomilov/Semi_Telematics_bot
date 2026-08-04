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
from capabilities.activity_trail import record_simple
from capabilities.permissions.roles import validate_role_change, role_rank
from features.carrier_directory.service import MAX_SENDER_NAME, clean_sender_name

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
    """Update a single account setting — the key's OWN owner must allow it.

    ``can_manage_account`` is the General settings feature's Manage action.
    ``account_settings`` is shared: Storage and Integrations are PEER
    features in the same Administration band, and the config family keeps
    its account-scope members there too.  Accepting any key meant one
    feature's Manage wrote two siblings' configuration plus everything
    gated on can_manage_config_all — the storage backend, a live Drive
    OAuth token, KPI thresholds, the DQF passphrase.  Nobody grants that
    when they tick "General settings".

    So the dependency above only gets the caller THROUGH the door; the
    key's declared owner decides whether this particular write is theirs.
    An undeclared key is refused, never written — defaulting it to the
    caller's permission is how the sprawl happened.
    """
    from capabilities.permissions.roles import can, is_system_owner
    from capabilities.settings_registry import SELF_ONLY, SYSTEM_ONLY, owner_for

    rule = owner_for(body.key)
    if rule is None:
        raise HTTPException(
            status_code=422,
            detail=(
                f"Unknown setting '{body.key}'. Settings must be declared in "
                f"capabilities/settings_registry.py with the permission that "
                f"owns them."
            ),
        )
    if rule.permission == SYSTEM_ONLY:
        # Platform infrastructure — AI model pinning and friends. No
        # account-level action exists for these by design.
        if not is_system_owner(int(user.get("sub") or 0)):
            raise HTTPException(
                status_code=403,
                detail=f"'{body.key}' is managed by the platform, not the account.",
            )
    elif rule.permission == SELF_ONLY:
        # Per-user preference. This endpoint is account-scoped and cannot
        # establish that the caller owns the key's user id.
        raise HTTPException(
            status_code=403,
            detail=(
                f"'{body.key}' is a per-user preference — set it from the "
                f"surface that owns it, not the account settings endpoint."
            ),
        )
    elif not can(user["role"], rule.permission):
        raise HTTPException(
            status_code=403,
            detail=(
                f"'{body.key}' belongs to {rule.feature} and requires "
                f"{rule.permission}."
            ),
        )

    await tenant_db.set_account_setting(user["account_id"], body.key, body.value)
    await record_simple(
        tenant_db, user["account_id"], await resolve_user_id(user),
        "setting_update", "setting", body.key,
        changes={body.key: {"from": None, "to": body.value[:200]}},
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
