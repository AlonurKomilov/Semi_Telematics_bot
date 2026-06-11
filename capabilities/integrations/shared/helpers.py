"""Provider-agnostic helpers used by every integration sub-router.

Background task tracking, request models, audit log writes, provider
validation, the encryption-on guard, and the catalog/integration
serializers.  Everything here is intentionally provider-neutral —
each sub-router (samsara, datatruck, ...) imports from here so the
on-the-wire shape and the audit / encryption / validation behaviour
stay identical across providers.

The HTTP routes that consume these helpers live in:
  * ``capabilities/integrations/shared/router.py``   — generic
  * ``capabilities/integrations/samsara/router.py``  — telematics
  * ``capabilities/integrations/datatruck/router.py`` — TMS
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any

from fastapi import HTTPException
from pydantic import BaseModel, Field

from adapters.telematics import (
    PROVIDER_CATALOG,
    ProviderStatus,
    is_registered,
)
from infra.platform import get_tenant_db

logger = logging.getLogger(__name__)


# ── Background task lifecycle (GC-safe) ───────────────────────────
#
# asyncio holds only weak references to tasks created via
# ``create_task`` — a GC sweep mid-flight kills a still-running
# backfill with "Task was destroyed but it is pending!".  We hold a
# strong ref in this set until the task finishes, then drop it via
# the done callback.  ONE set is shared platform-wide; there's no
# isolation requirement (tasks are already account-scoped via their
# coro), and a shared set means every router gets the same lifecycle
# guarantee.

_BACKGROUND_TASKS: set = set()


def spawn_background(coro) -> None:
    """Fire-and-forget a coroutine with GC-safe task lifecycle.

    Use this instead of bare ``asyncio.create_task`` for any task
    that needs to outlive the HTTP request that spawned it.
    """
    task = asyncio.create_task(coro)
    _BACKGROUND_TASKS.add(task)
    task.add_done_callback(_BACKGROUND_TASKS.discard)


# ── Request models ────────────────────────────────────────────────


class ConnectRequest(BaseModel):
    """Body for the connect action.  Credentials are provider-shaped
    JSON — Samsara is ``{"api_token": "..."}``; Datatruck is
    ``{"api_token": "...", "company_subdomain": "..."}``; future OAuth2
    providers will be ``{"access_token": "...", "refresh_token": "..."}``.
    The handler doesn't introspect — it just passes the dict to the
    provider's ``build_for_test`` and ``test_connection`` methods, and
    persists on success."""

    credentials: dict = Field(default_factory=dict)


class ToggleUpdateRequest(BaseModel):
    """Body for the toggle update — replaces the feature_toggles map
    wholesale.  The dashboard sends the full map every time so the
    server doesn't have to reconcile partial diffs.

    Inner value types are intentionally ``Any`` rather than ``dict``
    so a legacy or partially-migrated row whose value is ``null`` /
    bool can still be round-tripped through the dashboard without a
    422.  The handler normalises the values before persisting, so
    only valid shapes ever reach storage.
    """

    feature_toggles: dict[str, Any] = Field(default_factory=dict)
    cadence_overrides: dict[str, Any] | None = None


class BackfillHistoryRequest(BaseModel):
    """Body for the backfill trigger.  Days defaults to 30 (matches
    the velocity window the maintenance projection uses); range
    bounded to avoid pathological values.  Used only by telematics
    providers — TMS providers have no backfill concept."""

    days: int = Field(30, ge=1, le=90)


# ── Audit / validation / encryption guard ─────────────────────────


async def audit(
    account_id: int,
    user_id: int,
    action: str,
    provider_id: str,
    details: str = "",
) -> None:
    """Best-effort audit log write.  Wraps in try/except so audit
    failures never block the user-visible action — operators see
    them in logs but the API still returns success."""
    try:
        tenant = await get_tenant_db(account_id)
        if tenant is None:
            return
        await tenant.add_audit_log(
            account_id, user_id,
            action=action,
            target_type="integration",
            target_id=provider_id,
            details=details,
        )
    except Exception as e:
        logger.warning(
            "audit log write failed acct=%d action=%s: %s",
            account_id, action, e,
        )


def validate_provider(provider_id: str) -> None:
    """Reject unknown provider ids cleanly.  AVAILABLE providers have
    a registered implementation; COMING_SOON providers exist in the
    catalog but have no working backend.  Both are visible in the
    catalog endpoint; only AVAILABLE accept actions."""
    entry = PROVIDER_CATALOG.get(provider_id)
    if entry is None:
        raise HTTPException(404, f"provider {provider_id!r} not in catalog")
    if entry.status != ProviderStatus.AVAILABLE:
        raise HTTPException(
            409,
            f"provider {provider_id!r} is {entry.status.value} — "
            "not available for actions yet",
        )


def guard_credential_encryption() -> None:
    """Refuse to persist a fresh integration secret while encryption is off.

    ``infra.crypto`` is a deliberate plaintext pass-through when
    ``ENCRYPTION_KEY`` is unset — a migration affordance for the legacy
    single-key column.  That is fine for reading or clearing a value, but
    an operator connecting a new integration (or rotating a token) through
    the API would otherwise land a live third-party API key in the DB in
    cleartext.  Fail closed with a 503 so the dashboard surfaces a clear
    "configure ENCRYPTION_KEY" message instead of silently storing it.
    """
    from infra import crypto
    if not crypto.is_enabled():
        raise HTTPException(
            503,
            "ENCRYPTION_KEY is not configured, so the integration's API "
            "token would be stored in plaintext. Set ENCRYPTION_KEY and "
            "restart the service before connecting an integration.",
        )


# ── Serializers (catalog entry, account integration row) ──────────


def serialize_catalog_entry(entry: Any) -> dict:
    """Render a catalog entry as a JSON-shaped dict for the dashboard.

    The dashboard never imports the catalog directly — it consumes
    this serialized form via ``GET /integrations`` so adding a new
    provider becomes a backend-only change for the catalog metadata.
    """
    return {
        "provider_id":      entry.provider_id,
        "display_name":     entry.display_name,
        "tagline":          entry.tagline,
        "description":      entry.description,
        "capabilities":     sorted(entry.capabilities),
        "auth_kind":        entry.auth_kind,
        "kind":             entry.kind.value,
        "docs_url":         entry.docs_url,
        "icon":             entry.icon,
        "status":           entry.status.value,
        "feature_defaults": entry.feature_defaults,
        "is_registered":    is_registered(entry.provider_id),
    }


def serialize_integration(ai: Any) -> dict:
    """Render an ``AccountIntegration`` for the dashboard.  Never
    exposes the raw credentials blob — only ``has_credentials`` so
    the UI can render "connected" vs "needs reconnect"."""
    creds = ai.credentials or {}
    return {
        "account_id":         ai.account_id,
        "provider_id":        ai.provider_id,
        "status":             ai.status,
        "connected_at":       ai.connected_at,
        "has_credentials":    bool(creds),
        "feature_toggles":    ai.feature_toggles,
        "cadence_overrides":  ai.cadence_overrides,
        "last_health_at":     ai.last_health_at,
        "last_health_error":  ai.last_health_error,
        "last_backfill_at":   ai.last_backfill_at,
        "created_by":         ai.created_by,
        "created_at":         ai.created_at,
        "updated_at":         ai.updated_at,
    }
