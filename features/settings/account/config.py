"""General settings config — the account-wide values.

ONE config endpoint per feature at ``/<feature>/config``, in a file named
``config.py``.  Interface-layer, exactly as ``router.py`` is.

General settings is a feature like any other, so its ``account_settings``
rows reach the same place by the same shape.  Only the CONFIG verbs live
here — ``router.py`` keeps the page's OPERATIONS: bot config, forum
routing, department modules, public identity, role guidance, the danger
zone.  On this page the distinction is hardest to see, because everything
on it is colloquially "a setting"; the line is that these rows are
``account_settings`` and the config family owns them.

TWO ROUTERS, because the deprecated alias is on a different PREFIX.  The
primary is ``/settings/config``; the legacy path is ``/admin/settings``.
Both are exported and both are mounted — a config.py whose alias shares
its feature prefix needs only one router (see features/kpi/config.py).
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from adapters.storage.models import Role
from capabilities.activity_trail import record_simple
from interfaces.api.deps import (
    get_platform_db, get_tenant_db, require_permission, resolve_user_id,
)

# The legacy alias lives under /admin; the primary under /settings.
router = APIRouter(prefix="/admin", tags=["settings"])
config_router = APIRouter(prefix="/settings", tags=["settings"])

# ── Account Settings ──────────────────────────────────────────

class SettingUpdate(BaseModel):
    key: str = Field(..., min_length=1, max_length=50)
    value: str


@config_router.get("/config")
@router.get("/settings", deprecated=True)
async def get_config(
    user: dict = Depends(require_permission("can_manage_account")),
    platform_db=Depends(get_platform_db),
    tenant_db=Depends(get_tenant_db),
):
    """Get all account settings + AI usage stats.

    ONE payload, TWO gates — because it carries two different things.

    ``account`` and ``ai_usage`` are the Settings PAGE's own data, so the
    page's Manage action (can_manage_account) admits the caller.  The
    ``settings`` dict is something else: every key in it is an
    ``account_settings`` row, which capabilities/config/account.py
    puts behind ``can_manage_config_all`` to WRITE.  A read that returns
    the same values on a weaker permission would make the write gate
    decorative — you could see every value and simply not save.

    So the dict is populated only for callers who could also write it,
    and the endpoint stays reachable for those who cannot.  The
    alternative — gating the whole endpoint on the config flag — would
    403 the page for a General-settings holder who has legitimate
    business on it (bot config, forum routing, modules, danger zone).
    """
    # Fetch common settings — config values, so only for config holders.
    #
    # Resolved the SAME way require_permission does, deliberately.  The
    # sync ``can()`` helper is the bot's: it reads per-account overrides
    # only from a contextvar the bot primes and nothing in interfaces/api
    # ever sets, and it applies no is_manager / is_primary_owner overlay.
    # Inside a FastAPI handler it therefore always answers from the
    # hardcoded role defaults — so a Safety MANAGER (who gets
    # can_manage_config_all from MANAGER_GRANTS) or any role an owner
    # delegated it to would have been able to SAVE these values through
    # PUT below while this GET returned an empty dict forever.
    from capabilities.permissions.roles import get_user_permissions
    _perms = await get_user_permissions(
        Role(user["role"]), user["account_id"],
        is_manager=bool(user.get("is_manager")),
        is_primary_owner=bool(user.get("is_primary_owner")),
    )
    settings: dict[str, str] = {}
    if getattr(_perms, "can_manage_config_all", False):
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


@config_router.put("/config")
@router.put("/settings", deprecated=True)
async def put_config(
    body: SettingUpdate,
    user: dict = Depends(require_permission("can_manage_config_all")),
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
    from capabilities.permissions.roles import (
        get_user_permissions, is_system_owner,
    )
    from capabilities.config import SELF_ONLY, SYSTEM_ONLY, owner_for

    rule = owner_for(body.key)
    if rule is None:
        raise HTTPException(
            status_code=422,
            detail=(
                f"Unknown setting '{body.key}'. Settings must be declared in "
                f"capabilities/config/account.py with the permission that "
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
    elif not getattr(
        await get_user_permissions(
            Role(user["role"]), user["account_id"],
            is_manager=bool(user.get("is_manager")),
            is_primary_owner=bool(user.get("is_primary_owner")),
        ),
        rule.permission, False,
    ):
        # Same resolution as the dependency above — NOT the sync can(),
        # which ignores per-account delegation and the manager overlay in
        # an API context and would 403 a caller the dependency just let in.
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
