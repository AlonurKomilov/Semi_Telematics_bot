"""Alerts config — Group delivery's per-type settings.

ONE config endpoint per feature at ``/<feature>/config``, in a file named
``config.py``.  Interface-layer, exactly as ``router.py``/``forum.py`` are.

ALERTS, not notifications, and the distinction cost a commit to learn.
The BACKEND placement is right and unchanged — ``capabilities/alerting``
is the alert DOMAIN, ``capabilities/notifications`` is the DELIVERY
capability, and a CI rule keeps telegram out of alerting; forum topics are
delivery.  But the surface a user opens is Group delivery, a tab of
``/alerts``, and ``/notifications/*`` is already the Notification Centre's
live namespace (inbox, push, preferences).  Naming the URL after the
module put a Group-delivery config in the middle of the bell's routes.

ONLY the two CONFIG writes live here.  ``forum.py`` keeps the operations —
linking the group, creating topics, testing delivery, unlinking — because
that is running the integration, not setting the values it runs on.

TWO ROUTERS: the primary is ``/alerts/config``; the deprecated aliases are
under ``/admin/forum-routing/*``, a different prefix.
"""

from __future__ import annotations

import logging
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from capabilities.activity_trail import record_simple
from interfaces.api.deps import (
    get_platform_db, get_tenant_db, require_permission, resolve_user_id,
)

logger = logging.getLogger(__name__)

# Legacy aliases sit under /admin; the primary under /alerts.
router = APIRouter(prefix="/admin", tags=["settings"])
config_router = APIRouter(prefix="/alerts", tags=["alerts"])


class ForumSettingsUpdate(BaseModel):
    # Map of alert_type → bool.  Only alert types in _AI_CAPABLE are
    # honoured server-side; unknown keys are ignored so the API stays
    # tolerant if the dashboard sends extras.
    ai_per_type: Optional[dict[str, bool]] = None


@config_router.put("/config")
@router.put("/forum-routing/settings", deprecated=True)
async def put_config(
    body: ForumSettingsUpdate,
    # CONFIG, not Manage.  Writes ``forum_ai.<type>`` account_settings
    # rows, which the config family owns.  The rest of forum routing —
    # linking the group, creating topics, testing delivery — stays on
    # can_manage_account, because that is operating the integration
    # rather than setting a value every future alert is rendered through.
    user: dict = Depends(require_permission("can_manage_config_all")),
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
        await record_simple(
            tenant_db, account_id, await resolve_user_id(user),
            "forum_settings_update", "account", account_id,
            note="ai_per_type: " + ", ".join(changed),
        )
    return {"ok": True, "changed": changed}



class ForumSubtypeSelection(BaseModel):
    selected: list[str]


@config_router.put("/config/{alert_type}/subtypes")
@router.put("/forum-routing/{alert_type}/subtypes", deprecated=True)
async def set_forum_subtypes(
    alert_type: str,
    body: ForumSubtypeSelection,
    # CONFIG, not Manage — writes ``forum_subtypes.<type>``, an
    # account_settings row owned by the config family.  It decides which
    # sub-categories reach the group, so every future alert of that type
    # is filtered through it.
    user: dict = Depends(require_permission("can_manage_config_all")),
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
    await record_simple(
        tenant_db, user["account_id"], await resolve_user_id(user),
        "forum_subtypes_set", "alert_type", alert_type,
        note=f"selected={csv or 'all'}",
    )
    return {"alert_type": alert_type, "selected": (csv.split(",") if csv else None)}
