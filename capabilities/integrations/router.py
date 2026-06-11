"""Telematics integration management endpoints.

Owner / admin self-service surface for the M1 ``account_integrations``
table.  M5's job is the backfill trigger + status polling; the full
connect / configure / disconnect flow lands in M6 alongside the
dashboard UI.

Routes
------
* ``POST /api/integrations/{provider_id}/actions/backfill-history``
  Fire-and-forget trigger that runs ``backfill_vehicle_history`` in
  a background task.  Returns immediately with the started state;
  the caller polls the status endpoint to watch progress.

* ``GET  /api/integrations/{provider_id}/actions/backfill-history/status``
  Latest known state from Redis.  Returns ``{"state": "idle"}``
  shape when there's no recorded run rather than 404 so callers can
  render the empty state consistently.

* ``GET  /api/integrations/{provider_id}/snapshot-coverage``
  Per-day row count for the recent window — backs the dashboard's
  backfill progress preview ("days 1-7 already populated, days 8-30
  empty").  Read-only.

Per-account isolation
---------------------
Every endpoint reads ``user["account_id"]`` and operates only on the
caller's own data.  Backfills consume the caller's own Samsara
quota, write to the caller's own snapshot rows, and surface through
the caller's own Redis status key.  Account A's owner cannot trigger
or observe Account B's backfill.

Permission
----------
All routes require ``can_manage_account`` (owner / admin).  The
dashboard surface in M6 will gate the UI by the same permission so
the API and UI agree on who can act.
"""
# router.py is interface-layer code co-located with its hub/domain
# (docs/FEATURES.md): ONLY router.py may import interfaces.api.deps.


from __future__ import annotations

import asyncio
import logging
from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from adapters.telematics import (
    PROVIDER_CATALOG,
    ProviderStatus,
    is_registered,
    resolve_capability_cadence,
)
from adapters.telematics.protocol import Capability
from capabilities.telemetry.history_backfill import (
    backfill_vehicle_history,
    get_backfill_status,
    reset_backfill_status,
)
from infra.platform import get_platform_db, get_tenant_db
from infra.services import get_telematics_client, invalidate_client
from interfaces.api.deps import require_permission

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/integrations", tags=["integrations"])

_owner_only = require_permission("can_manage_account")

# Strong references to fire-and-forget background tasks.  asyncio only
# keeps WEAK references to tasks created via ``create_task`` — if the
# garbage collector runs while a 45-minute backfill is in flight, the
# task gets dropped with "Task was destroyed but it is pending!".
# Holding a strong reference until the task completes (then dropping
# via add_done_callback) makes the lifecycle deterministic.
_BACKGROUND_TASKS: set = set()


def _spawn_background(coro) -> None:
    """Fire-and-forget a coroutine with GC-safe task lifecycle.

    Use this instead of bare ``asyncio.create_task`` for any task
    that needs to outlive the HTTP request that spawned it (e.g. a
    multi-minute backfill chain).
    """
    task = asyncio.create_task(coro)
    _BACKGROUND_TASKS.add(task)
    task.add_done_callback(_BACKGROUND_TASKS.discard)


class BackfillHistoryRequest(BaseModel):
    """Body for the backfill trigger.  Days defaults to 30 (matches
    the velocity window the maintenance projection uses); range
    bounded to avoid pathological values."""

    days: int = Field(30, ge=1, le=90)


class ConnectRequest(BaseModel):
    """Body for the connect action.  Credentials are provider-shaped
    JSON — Samsara is ``{"api_token": "..."}``; future OAuth2 providers
    will be ``{"access_token": "...", "refresh_token": "..."}``.  The
    handler doesn't introspect — it just passes the dict to the
    provider's ``test_connection`` and persists on success."""

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


async def _audit(
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


def _validate_provider(provider_id: str) -> None:
    """Reject unknown provider ids cleanly.  AVAILABLE providers
    have a registered implementation; COMING_SOON providers exist in
    the catalog but have no working backend.  Both are visible in
    the catalog endpoint; only AVAILABLE accept actions."""
    entry = PROVIDER_CATALOG.get(provider_id)
    if entry is None:
        raise HTTPException(404, f"provider {provider_id!r} not in catalog")
    if entry.status != ProviderStatus.AVAILABLE:
        raise HTTPException(
            409,
            f"provider {provider_id!r} is {entry.status.value} — "
            "not available for actions yet",
        )


def _guard_credential_encryption() -> None:
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


def _serialize_catalog_entry(entry: Any) -> dict:
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
        "docs_url":         entry.docs_url,
        "icon":             entry.icon,
        "status":           entry.status.value,
        "feature_defaults": entry.feature_defaults,
        "is_registered":    is_registered(entry.provider_id),
    }


def _serialize_integration(ai: Any) -> dict:
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


# ── Catalog + per-account list ───────────────────────────────────


@router.get("")
async def list_integrations(user: dict = Depends(_owner_only)):
    """List the full provider catalog + the caller's connected
    integrations.

    Backs the dashboard's Integrations page.  Returned shape:

      {
        "catalog":      [<provider catalog entry>, ...],
        "integrations": [<account integration row>, ...],
      }

    The catalog includes COMING_SOON providers so the dashboard can
    render them as inert preview cards.  Integrations only include
    the caller's account_id — no cross-account leakage.
    """
    account_id = int(user["account_id"])
    db = get_platform_db()
    rows = await db.list_account_integrations(account_id)
    catalog = [
        _serialize_catalog_entry(PROVIDER_CATALOG[pid])
        for pid in sorted(PROVIDER_CATALOG.keys())
    ]
    return {
        "catalog":      catalog,
        "integrations": [_serialize_integration(ai) for ai in rows],
    }


# ── Connect / disconnect ─────────────────────────────────────────


@router.post("/{provider_id}/connect")
async def connect_integration(
    provider_id: str,
    body: ConnectRequest,
    user: dict = Depends(_owner_only),
):
    """Validate credentials with the provider and upsert the
    integration row on success.

    Two-step:
      1. Build the provider, call ``test_connection`` with the new
         credentials.
      2. If OK, upsert the row with status=connected and the
         catalog's default feature_toggles.

    On failure the row is NOT created; the dashboard sees the error
    message verbatim and prompts the owner to retry.
    """
    _validate_provider(provider_id)
    account_id = int(user["account_id"])
    triggered_by = int(user.get("id") or 0)

    # Fail fast before even touching the provider: if we can't encrypt
    # the token, don't connect at all rather than store it in cleartext.
    if body.credentials:
        _guard_credential_encryption()

    # Test connection BEFORE persisting anything so bad credentials
    # surface immediately and the integration row never enters a
    # half-configured "stored but unusable" state.
    try:
        provider = await get_telematics_client(account_id, provider_id)
    except KeyError as e:
        raise HTTPException(503, f"provider not registered: {e}")
    try:
        status = await provider.test_connection(body.credentials)
    except Exception as e:
        raise HTTPException(502, f"connection test failed: {e}")
    if not status.ok:
        raise HTTPException(
            400, f"credentials rejected: {status.message or 'unknown error'}",
        )

    db = get_platform_db()
    entry = PROVIDER_CATALOG[provider_id]
    # Detect whether this is a FIRST-time connect or a re-connect /
    # credential rotation.  Two behaviours diverge here:
    #   * First connect: write catalog default feature_toggles AND
    #     auto-trigger the history backfill (~45 min Samsara fetch
    #     followed by the aggregation chain) so the calendar works.
    #   * Re-connect: preserve the user's existing toggle map
    #     (passing ``feature_toggles=None`` to the upsert) and skip
    #     the backfill — the snapshot table already has data.
    existing_before = await db.get_account_integration(account_id, provider_id)
    is_first_connect = existing_before is None

    ai = await db.upsert_account_integration(
        account_id, provider_id,
        credentials=body.credentials,
        # First connect installs catalog defaults; reconnect leaves
        # the user's customizations alone.  Without this guard a
        # credential rotation would silently reset every per-account
        # toggle the operator had configured.
        feature_toggles=entry.feature_defaults if is_first_connect else None,
        status="connected",
        created_by=triggered_by,
    )
    await db.record_integration_health_check(
        account_id, provider_id, ok=True, message=status.message,
    )
    logger.info(
        "integration connected acct=%d provider=%s by user=%d first=%s",
        account_id, provider_id, triggered_by, is_first_connect,
    )
    await _audit(
        account_id, triggered_by, "integration.connect", provider_id,
        details=status.message[:200],
    )

    # Auto-trigger 30-day Samsara history backfill on first connect
    # so the calendar projection + utilization reports work the
    # moment the backfill completes (~45 min) rather than after a
    # week of natural warmup.  Fire-and-forget via the GC-safe
    # spawner; the user can keep working and watch the progress
    # badge on the Integration card.
    backfill_default_on = entry.feature_defaults.get(
        Capability.HISTORY_BACKFILL, {},
    ).get("enabled")
    if is_first_connect and backfill_default_on:
        # TOCTOU guard: a duplicate POST /connect (double-click, retry
        # from a flaky client, two dashboard tabs) could both see
        # ``existing_before is None`` before the upsert lands.  The
        # second request would then queue a redundant backfill that
        # walks the same 720 aggregation hours.  Redis-backed status
        # check makes the trigger idempotent across requests.
        running = await get_backfill_status(account_id, provider_id)
        if running and running.get("state") in ("running", "queued"):
            logger.info(
                "auto-backfill skipped — already running acct=%d provider=%s",
                account_id, provider_id,
            )
        else:
            async def _run_backfill() -> None:
                try:
                    await backfill_vehicle_history(
                        account_id,
                        days=30,
                        provider_id=provider_id,
                        triggered_by=triggered_by,
                    )
                except Exception:
                    logger.exception(
                        "auto-backfill on connect failed acct=%d provider=%s",
                        account_id, provider_id,
                    )
            _spawn_background(_run_backfill())
            logger.info(
                "auto-backfill queued on first connect acct=%d provider=%s days=30",
                account_id, provider_id,
            )

    ai = await db.get_account_integration(account_id, provider_id)
    return _serialize_integration(ai)


@router.delete("/{provider_id}")
async def disconnect_integration(
    provider_id: str,
    user: dict = Depends(_owner_only),
):
    """Disconnect (delete the row).  Drops the cached client so the
    next ingest call rebuilds from scratch.

    Companies + Samsara API keys in the ``companies`` table are NOT
    touched here — see the docstring on
    ``delete_account_integration``.  Disconnecting only pauses
    ingest; reconnect restores it without re-entering credentials
    UNLESS the owner explicitly clears credentials first.
    """
    _validate_provider(provider_id)
    account_id = int(user["account_id"])
    db = get_platform_db()
    removed = await db.delete_account_integration(account_id, provider_id)
    if not removed:
        raise HTTPException(404, "integration not configured")
    await invalidate_client(account_id)
    logger.info(
        "integration disconnected acct=%d provider=%s",
        account_id, provider_id,
    )
    await _audit(
        account_id, int(user.get("id") or 0),
        "integration.disconnect", provider_id,
    )
    return {"state": "disconnected", "provider_id": provider_id}


# ── Toggle updates ───────────────────────────────────────────────


@router.put("/{provider_id}/toggles")
async def update_toggles(
    provider_id: str,
    body: ToggleUpdateRequest,
    user: dict = Depends(_owner_only),
):
    """Replace the feature_toggles + cadence_overrides maps.

    Defensively merges with the catalog's defaults so toggles for
    capabilities the dashboard didn't send still default to enabled.
    This prevents a buggy client from accidentally disabling
    everything by sending an empty map.

    Returns the updated integration row in the same shape as
    ``GET /integrations`` so the dashboard can refresh state from
    the response.
    """
    _validate_provider(provider_id)
    account_id = int(user["account_id"])
    db = get_platform_db()

    existing = await db.get_account_integration(account_id, provider_id)
    if existing is None:
        raise HTTPException(404, "integration not configured")

    # Normalise feature_toggles — round-tripping through the dashboard
    # can preserve legacy non-dict values from older rows (the spread
    # ``{...toggles, [cap]: ...}`` doesn't drop them).  Coerce
    # non-dict values to a sane default so storage only sees the
    # shape downstream code expects.  Boolean → ``{enabled: bool}``;
    # anything else (null / number / string) → ``{enabled: True}``
    # which matches the default-on rollout semantics.
    raw_toggles = body.feature_toggles or {}
    sanitized_toggles: dict[str, dict] = {}
    for cap, value in raw_toggles.items():
        if isinstance(value, dict):
            sanitized_toggles[cap] = value
        elif isinstance(value, bool):
            sanitized_toggles[cap] = {"enabled": value}
        else:
            sanitized_toggles[cap] = {"enabled": True}

    # Cap any cadence override that's faster than the catalog default
    # at the default itself — see ``resolve_capability_cadence``'s
    # docstring for the why.  We don't outright reject; we cap and
    # surface the actual effective value in the response.
    incoming_cadences = body.cadence_overrides or {}
    sanitized_cadences: dict[str, dict] = {}
    for cap, override in incoming_cadences.items():
        if not isinstance(override, dict):
            continue
        sanitized_cadences[cap] = override

    ai = await db.upsert_account_integration(
        account_id, provider_id,
        feature_toggles=sanitized_toggles,
        cadence_overrides=sanitized_cadences,
        status=existing.status,
    )
    logger.info(
        "integration toggles updated acct=%d provider=%s",
        account_id, provider_id,
    )
    # Compute a compact diff for the audit detail so the operator
    # can reconstruct which toggle was flipped without dumping the
    # whole feature_toggles JSON.  Defensive against non-dict values
    # in either side (legacy rows + raw incoming) — fall back to
    # ``True`` for anything we can't introspect.
    def _is_enabled(entry: Any) -> bool:
        if isinstance(entry, dict):
            return bool(entry.get("enabled", True))
        if isinstance(entry, bool):
            return entry
        return True

    diff: list[str] = []
    old_toggles = existing.feature_toggles or {}
    new_toggles = sanitized_toggles
    for cap in set(old_toggles) | set(new_toggles):
        old_on = _is_enabled(old_toggles.get(cap))
        new_on = _is_enabled(new_toggles.get(cap))
        if old_on != new_on:
            diff.append(f"{cap}: {'on' if old_on else 'off'}→{'on' if new_on else 'off'}")
    await _audit(
        account_id, int(user.get("id") or 0),
        "integration.toggles", provider_id,
        details=", ".join(diff)[:300] or "(no changes)",
    )
    return _serialize_integration(ai)


# ── Health check ─────────────────────────────────────────────────


@router.post("/{provider_id}/actions/test-connection")
async def test_connection_action(
    provider_id: str,
    user: dict = Depends(_owner_only),
):
    """Run the provider's ``test_connection`` against current stored
    credentials.  Updates the integration row's
    ``last_health_at`` / ``last_health_error`` columns so the
    dashboard badge reflects the latest probe."""
    _validate_provider(provider_id)
    account_id = int(user["account_id"])
    db = get_platform_db()
    ai = await db.get_account_integration(account_id, provider_id)
    if ai is None:
        raise HTTPException(404, "integration not configured")
    # Hard 12-second TOTAL deadline.  Wraps BOTH the provider
    # resolution AND the test call — without this the resolution path
    # is uncapped.  ``get_telematics_client`` does a first-call
    # ``prefetch_org_ids`` which fans out a /me request per company
    # WITHOUT ``force=True`` (so it hits the per-call retry/backoff
    # path); during an active backfill that's already burning the
    # API key's rate limit on /me, prefetch alone can take 20s+ —
    # then the test_connection's own 12s would put total over the
    # frontend's 30s REQUEST_TIMEOUT_MS and the user gets the bare
    # "timed out after 30s" banner instead of a clean 504.
    async def _probe() -> Any:
        # prefetch=False skips the legacy ``prefetch_org_ids`` call
        # path which routes through ``_get_raw`` → 5s timeout × 3
        # retries × N companies and would alone consume the 12s
        # budget before our lean probe even started.  The probe
        # itself hits /me directly via aiohttp (3s ceiling each, N
        # in parallel) so we don't need warm org IDs upfront.
        import time as _time
        t_start = _time.time()
        provider = await get_telematics_client(
            account_id, provider_id, prefetch=False,
        )
        t_built = _time.time()
        logger.info(
            "[test_conn] acct=%d phase=provider_build elapsed_ms=%d",
            account_id, int((t_built - t_start) * 1000),
        )
        status = await provider.test_connection(ai.credentials)
        logger.info(
            "[test_conn] acct=%d phase=probe ok=%s elapsed_ms=%d msg=%s",
            account_id, status.ok,
            int((_time.time() - t_built) * 1000),
            (status.message or "")[:80],
        )
        return status
    try:
        status = await asyncio.wait_for(_probe(), timeout=12.0)
    except asyncio.TimeoutError:
        logger.warning(
            "[test_conn] acct=%d phase=ROUTE_TIMEOUT — check earlier "
            "log lines for provider_build/probe timings",
            account_id,
        )
        await db.record_integration_health_check(
            account_id, provider_id, ok=False,
            message="upstream slow — backfill or rate limit in progress",
        )
        raise HTTPException(
            504,
            "Samsara is slow to respond — a backfill may be running. "
            "Try again in a few minutes.",
        )
    except KeyError as e:
        raise HTTPException(503, f"provider not registered: {e}")
    except Exception as e:
        await db.record_integration_health_check(
            account_id, provider_id, ok=False, message=str(e)[:300],
        )
        raise HTTPException(502, f"connection test threw: {e}")
    await db.record_integration_health_check(
        account_id, provider_id, ok=status.ok, message=status.message,
    )
    return {
        "ok":                  status.ok,
        "message":             status.message,
        "provider_account_id": status.provider_account_id,
    }


# ── Effective cadence (read-only) ────────────────────────────────


@router.get("/{provider_id}/cadences")
async def effective_cadences(
    provider_id: str,
    user: dict = Depends(_owner_only),
):
    """Per-capability effective cadence for the caller's integration.

    Returns the cadence the scheduler will actually honour for each
    capability — the override if set, the catalog default otherwise.
    The dashboard renders this in the toggle row's "every X" label
    so owners always see the value that will be applied.
    """
    _validate_provider(provider_id)
    account_id = int(user["account_id"])
    db = get_platform_db()
    ai = await db.get_account_integration(account_id, provider_id)
    if ai is None:
        raise HTTPException(404, "integration not configured")
    out: dict[str, dict] = {}
    for cap in sorted(PROVIDER_CATALOG[provider_id].capabilities):
        out[cap] = resolve_capability_cadence(
            provider_id, cap, ai.cadence_overrides,
        )
    return {"provider_id": provider_id, "cadences": out}


@router.post("/{provider_id}/actions/backfill-history")
async def trigger_backfill_history(
    provider_id: str,
    body: BackfillHistoryRequest,
    user: dict = Depends(_owner_only),
):
    """Queue a backfill for the caller's own account.

    Fire-and-forget — the endpoint returns immediately with the
    initial state.  The actual fetch + persist work happens in a
    background task scheduled by ``asyncio.create_task`` so the HTTP
    handler doesn't block on the multi-minute backfill.

    Idempotent: if a backfill is already running for this account,
    the new request returns 409 rather than queueing a parallel
    duplicate.  The cross-account serial lock (M4) further ensures
    only one backfill runs platform-wide at a time, but that's
    enforced inside the capability — this endpoint's check is a
    fast preflight against the same-account case.
    """
    _validate_provider(provider_id)
    account_id = int(user["account_id"])

    # Preflight: if there's a status row showing 'running' or
    # 'queued', refuse to queue a duplicate.  Including 'queued'
    # closes the TOCTOU window between accepting a request and the
    # task actually flipping state to 'running' — without that, a
    # double-click could pass the preflight twice, both spawn tasks,
    # and the second one would lose the lock race and write 'skipped'
    # to the same Redis key (clobbering the first task's progress).
    # Redis-backed so multi-worker deploys share the check.
    status = await get_backfill_status(account_id, provider_id)
    if status and status.get("state") in ("running", "queued"):
        raise HTTPException(
            409,
            "a backfill is already running or queued for this account — "
            "wait for it to complete or check the status endpoint",
        )

    # Verify the integration exists + is connected before queueing
    # so the caller gets immediate feedback instead of polling
    # status only to discover the run was skipped.
    db = get_platform_db()
    ai = await db.get_account_integration(account_id, provider_id)
    if ai is None:
        raise HTTPException(
            404,
            f"no {provider_id} integration configured for this account",
        )
    # Run-now preflight: allow the trigger when the aggregate status
    # is ``connected`` OR when at least one company has been tested
    # recently and reports healthy.  This stops a stuck "error"
    # status (from an account-level test that ran into one bad
    # company) from blocking the backfill for the 4/5 companies that
    # ARE working.
    if ai.status != "connected":
        from capabilities.integrations.company_health import (
            list_company_health, any_company_healthy,
        )
        # Filter stale records — a deleted company's healthy result
        # could otherwise falsely satisfy ``any_company_healthy`` and
        # let a doomed backfill through (it would just hit the same
        # error inside the task).  Mirrors the filter in
        # ``list_provider_companies``.
        from infra.platform import get_tenant_db as _get_tenant_db
        tenant_for_codes = await _get_tenant_db(account_id)
        if tenant_for_codes is not None:
            current_companies = await tenant_for_codes.get_account_companies(
                account_id,
            )
            current_codes = {co.code for co in current_companies}
            raw = await list_company_health(account_id, provider_id)
            health_map = {
                code: h for code, h in raw.items() if code in current_codes
            }
        else:
            health_map = await list_company_health(account_id, provider_id)
        if not any_company_healthy(health_map):
            raise HTTPException(
                409,
                f"integration status is {ai.status!r}, not connected — "
                "click 'Test' on individual companies to identify the "
                "failing one, then rotate its key or remove it",
            )

    # Schedule the run.  ``asyncio.create_task`` returns immediately;
    # the task survives the request lifecycle because we hold no
    # reference to the handler's response.  Errors inside the task
    # log + write the failed status to Redis but do NOT propagate
    # back to the client (which has long since gotten its 202).
    triggered_by = int(user.get("id") or 0)
    days = int(body.days)

    async def _run() -> None:
        try:
            await backfill_vehicle_history(
                account_id,
                days=days,
                provider_id=provider_id,
                triggered_by=triggered_by,
            )
        except Exception:
            logger.exception(
                "backfill background task crashed acct=%d provider=%s",
                account_id, provider_id,
            )

    _spawn_background(_run())
    logger.info(
        "backfill queued acct=%d provider=%s days=%d by user=%d",
        account_id, provider_id, days, triggered_by,
    )
    await _audit(
        account_id, triggered_by, "integration.backfill_trigger", provider_id,
        details=f"days={days}",
    )
    return {
        "state": "queued",
        "account_id": account_id,
        "provider_id": provider_id,
        "days": days,
        "triggered_by": triggered_by,
    }


@router.get("/{provider_id}/actions/backfill-history/status")
async def backfill_history_status(
    provider_id: str,
    user: dict = Depends(_owner_only),
):
    """Read the latest backfill state from Redis.

    Shape: the result of ``capabilities.telemetry.history_backfill
    .BackfillResult`` published during the run.  Returns
    ``{"state": "idle"}`` when no run has been recorded — the
    dashboard renders that as "no backfill running" rather than a
    404 so the polling UI doesn't have to special-case errors.

    ``get_backfill_status`` applies heartbeat staleness coercion —
    a record stuck at ``running`` for >5min without progress is
    returned as ``state="failed"`` so the dashboard badge clears
    and the operator can re-trigger.
    """
    _validate_provider(provider_id)
    account_id = int(user["account_id"])
    status = await get_backfill_status(account_id, provider_id)
    if not status:
        return {"state": "idle", "account_id": account_id, "provider_id": provider_id}
    return status


@router.post("/{provider_id}/actions/backfill-history/reset")
async def backfill_history_reset(
    provider_id: str,
    user: dict = Depends(_owner_only),
):
    """Owner-only escape hatch: forcibly clear the Redis state for a
    stuck backfill.

    Use case: the heartbeat staleness coercion (5min) fixes most
    SIGTERM / OOM cases automatically, but an operator may want the
    badge gone immediately rather than waiting for the next status
    poll to coerce.  Also useful if a developer has been poking at
    the system and wants a clean slate.

    Safe even when no backfill is in progress — returns
    ``{"cleared": false}`` if the Redis record was already absent.
    Snapshot data already persisted is NOT touched; only the
    progress badge state is cleared.  Re-running the backfill will
    skip the already-populated days via the existing day-skip cursor.
    """
    _validate_provider(provider_id)
    account_id = int(user["account_id"])
    triggered_by = int(user.get("id") or 0)

    cleared = await reset_backfill_status(account_id, provider_id)
    await _audit(
        account_id, triggered_by, "integration.backfill_reset", provider_id,
        details=f"cleared={cleared}",
    )
    return {"cleared": bool(cleared)}


@router.get("/{provider_id}/snapshot-coverage")
async def snapshot_coverage(
    provider_id: str,
    days: int = 30,
    user: dict = Depends(_owner_only),
):
    """Per-day row count for the snapshot table, recent window first.

    Used by the dashboard's backfill preview card to show which
    days are populated and which are empty before the owner clicks
    "Run backfill".  Provider-agnostic (the snapshot table is one
    table per account regardless of provider), but the route is
    namespaced under ``/integrations/{provider_id}/`` so the
    dashboard's URL structure stays consistent.
    """
    _validate_provider(provider_id)
    account_id = int(user["account_id"])
    if days < 1 or days > 90:
        raise HTTPException(400, "days must be between 1 and 90")
    from infra.platform import get_tenant_db
    tenant = await get_tenant_db(account_id)
    if tenant is None:
        raise HTTPException(503, "tenant DB unavailable")
    coverage = await tenant.vehicle_state_snapshot_day_summary(
        account_id, days_back=days,
    )
    # Pull provider catalog metadata so the dashboard can render the
    # right "estimated wall clock" hint without a second roundtrip.
    entry = PROVIDER_CATALOG[provider_id]
    return {
        "account_id": account_id,
        "provider_id": provider_id,
        "provider_display_name": entry.display_name,
        "days": days,
        "coverage": coverage,
    }


# ── Per-company credentials (canonical key management) ───────────


class CompanyCredentialUpsert(BaseModel):
    """Body for setting one company's API token inside the
    integration credentials map.

    The token is encrypted in place via the integration's normal
    credentials_enc Fernet flow.  Operators set/rotate keys through
    this endpoint — the Companies page no longer exposes the field.
    """
    api_token: str = Field(..., min_length=1, max_length=512)


@router.get("/{provider_id}/companies")
async def list_provider_companies(
    provider_id: str,
    user: dict = Depends(_owner_only),
):
    """Per-company key-status overview for the Integration card.

    Returns one entry per company on the account with the latest
    Redis-stored health from per-company ``/me`` probes (set by
    ``POST /companies/{code}/actions/test`` or the account-level
    ``POST /actions/test-connection``).  Companies that have never
    been tested (or whose 7-day TTL expired) have ``health: null``;
    the dashboard renders these as "untested" rather than "down".

    No raw tokens are returned — only the boolean.  Drives the
    "Connected companies (N)" section on the Integration card.
    """
    _validate_provider(provider_id)
    account_id = int(user["account_id"])
    from infra.platform import get_platform_db, get_tenant_db
    from capabilities.integrations.company_health import (
        list_company_health, summarise_health,
    )
    platform_db = get_platform_db()
    integ = await platform_db.get_account_integration(account_id, provider_id)
    creds_map: dict = {}
    if integ and integ.credentials:
        creds_map = integ.credentials.get("companies") or {}

    tenant = await get_tenant_db(account_id)
    if tenant is None:
        raise HTTPException(503, "tenant DB unavailable")
    companies = await tenant.get_account_companies(account_id)
    raw_health_map = await list_company_health(account_id, provider_id)
    # Filter stale health records — a removed company leaves its
    # Redis health record live for the 7-day TTL.  Without this
    # filter, ``summarise_health`` could report "7 of 5 healthy"
    # (5 current + 2 stale) and ``any_company_healthy`` could
    # falsely pass the Run-now preflight on the strength of a
    # company that no longer exists.
    current_codes = {co.code for co in companies}
    health_map = {
        code: h for code, h in raw_health_map.items() if code in current_codes
    }
    return {
        "account_id":  account_id,
        "provider_id": provider_id,
        # Aggregate status the card header renders as "4 of 5 healthy"
        # — fed by per-company health, NOT the legacy ``ai.status``
        # column which is binary connected/error.
        "health_summary": summarise_health(health_map, len(companies)),
        "companies": [
            {
                "code":         co.code,
                "display_name": co.display_name,
                # has_key is True when either the canonical
                # integration map has an entry OR the legacy company
                # column does (dual-source during cutover).
                "has_key":      bool(creds_map.get(co.code) or co.samsara_api_key),
                "active_days":  co.active_days,
                # Per-company probe result.  None = never tested or
                # TTL expired; the dashboard distinguishes this from
                # ``{ok: false}`` (tested and failing).
                "health":       health_map.get(co.code),
            }
            for co in companies
        ],
    }


@router.post("/{provider_id}/companies/{company_code}/actions/test")
async def test_company_connection_action(
    provider_id: str,
    company_code: str,
    user: dict = Depends(_owner_only),
):
    """Run the lean /me probe against ONE company.

    Same shape as the account-level ``/actions/test-connection`` but
    scoped to a single company so the operator can isolate exactly
    which company is failing without re-testing the others.

    Persists the result in Redis (7-day TTL) so the next
    ``GET /companies`` reflects it; updates the company-level
    integration ``status`` column when one company flipping fixes
    or breaks the aggregate.
    """
    _validate_provider(provider_id)
    account_id = int(user["account_id"])
    db = get_platform_db()
    ai = await db.get_account_integration(account_id, provider_id)
    if ai is None:
        raise HTTPException(404, "integration not configured")

    # 8-second total cap — single-company probe is ~3s in the lean
    # path, but the budget includes provider construction (cold
    # cache builds the SamsaraClient + reads creds map).  Still well
    # under the dashboard's 30s timeout.
    #
    # Per-phase timings are emitted to logs so a future timeout
    # answers "what was the route actually doing" without re-
    # instrumenting.  Look for the ``[test_company]`` prefix.
    async def _probe_one() -> Any:
        from capabilities.integrations.company_health import (
            set_company_health,
        )
        import time as _time
        t_start = _time.time()
        provider = await get_telematics_client(
            account_id, provider_id, prefetch=False,
        )
        t_built = _time.time()
        logger.info(
            "[test_company] acct=%d code=%s phase=provider_build elapsed_ms=%d",
            account_id, company_code, int((t_built - t_start) * 1000),
        )
        # ``test_company`` only exists on SamsaraProvider for now —
        # other providers will get the same surface when they ship.
        if not hasattr(provider, "test_company"):
            return None, "provider doesn't support per-company tests"
        status = await provider.test_company(company_code)  # type: ignore[attr-defined]
        t_probed = _time.time()
        elapsed_ms = int((t_probed - t_built) * 1000)
        logger.info(
            "[test_company] acct=%d code=%s phase=probe ok=%s elapsed_ms=%d msg=%s",
            account_id, company_code, status.ok, elapsed_ms,
            status.message[:80],
        )
        await set_company_health(
            account_id, provider_id, company_code,
            ok=status.ok, message=status.message,
            elapsed_ms=elapsed_ms,
        )
        return status, elapsed_ms

    try:
        result = await asyncio.wait_for(_probe_one(), timeout=8.0)
    except asyncio.TimeoutError:
        from capabilities.integrations.company_health import (
            set_company_health,
        )
        logger.warning(
            "[test_company] acct=%d code=%s phase=ROUTE_TIMEOUT — "
            "check earlier log lines for provider_build/probe timings",
            account_id, company_code,
        )
        await set_company_health(
            account_id, provider_id, company_code,
            ok=False, message="route timed out (provider construction slow)",
        )
        raise HTTPException(504, "provider construction timed out")
    except KeyError:
        # ``get_telematics_client`` raises KeyError when the provider
        # isn't in the registry — surface as 503 (service unavailable)
        # rather than 502 (bad gateway) since the upstream wasn't even
        # contacted.  The KeyError repr is `'samsara'` (with quotes)
        # which would have made the user-facing error ugly anyway.
        raise HTTPException(
            503,
            f"provider {provider_id!r} is not registered on this deploy",
        )
    except NotImplementedError as e:
        # Provider IS registered but has no construction path in
        # get_telematics_client yet — same surface as KeyError from
        # the operator's perspective.
        raise HTTPException(503, str(e))
    except Exception as e:
        raise HTTPException(502, f"per-company test threw: {e}")

    status, elapsed_ms = result
    if status is None:
        raise HTTPException(501, str(elapsed_ms))

    # Update aggregate ``ai.status`` based on the new per-company
    # signal — any one company being healthy is enough to flip the
    # integration back to "connected".
    await _refresh_aggregate_status(account_id, provider_id)

    return {
        "code":        company_code,
        "ok":          status.ok,
        "message":     status.message,
        "elapsed_ms":  elapsed_ms,
        "checked_at":  None,  # set by set_company_health; client re-reads on next list
    }


@router.post(
    "/{provider_id}/companies/{company_code}/actions/backfill-history",
)
async def trigger_company_backfill_history(
    provider_id: str,
    company_code: str,
    body: BackfillHistoryRequest,
    user: dict = Depends(_owner_only),
):
    """Per-company "Refresh history" trigger.

    Same semantics as ``/actions/backfill-history`` but scoped to one
    company.  Useful when the operator just rotated a single company's
    key and wants to backfill only that company's history without re-
    fetching the others.

    Progress lives under a per-company Redis key so this can co-exist
    with an account-wide "Run all".  The preflight refuses if another
    backfill (account-wide OR same-company) is currently running.

    Returns the same ``{state: queued, …}`` shape as the account-level
    trigger so the dashboard polling code stays uniform.
    """
    _validate_provider(provider_id)
    account_id = int(user["account_id"])

    # Verify the integration exists and the company is configured on
    # it before queueing.  Status-record sanity is per-company so
    # we don't reject when account-level status is "error" — the
    # whole point of per-company refresh is to recover from that.
    db = get_platform_db()
    ai = await db.get_account_integration(account_id, provider_id)
    if ai is None:
        raise HTTPException(
            404,
            f"no {provider_id} integration configured for this account",
        )

    creds_map = (ai.credentials or {}).get("companies") or {}
    tenant = await get_tenant_db(account_id)
    if tenant is None:
        raise HTTPException(503, "tenant DB unavailable")
    co = await tenant.get_company_by_code(account_id, company_code)
    if co is None:
        raise HTTPException(404, f"company not found: {company_code}")
    if not (creds_map.get(company_code) or co.samsara_api_key):
        raise HTTPException(
            409,
            f"company {company_code!r} has no API key — set one first",
        )

    # Per-company TOCTOU: refuse to queue a duplicate per-company run.
    # An account-wide run is also rejected because it would compete
    # for the global backfill lock and the operator just kicked one
    # off — better to ask them to wait than queue a backfill that'll
    # be skipped.
    company_status = await get_backfill_status(
        account_id, provider_id, company_code,
    )
    if company_status and company_status.get("state") in ("running", "queued"):
        raise HTTPException(
            409,
            f"a refresh is already running for company {company_code!r} — "
            "wait for it to complete",
        )
    account_status = await get_backfill_status(account_id, provider_id)
    if account_status and account_status.get("state") in ("running", "queued"):
        raise HTTPException(
            409,
            "an account-wide backfill is currently running — wait for it "
            "to complete before refreshing a single company",
        )

    triggered_by = int(user.get("id") or 0)
    days = int(body.days)

    async def _run() -> None:
        try:
            await backfill_vehicle_history(
                account_id,
                days=days,
                provider_id=provider_id,
                triggered_by=triggered_by,
                company_code=company_code,
            )
        except Exception:
            logger.exception(
                "per-company backfill task crashed acct=%d provider=%s code=%s",
                account_id, provider_id, company_code,
            )

    _spawn_background(_run())
    logger.info(
        "per-company backfill queued acct=%d provider=%s code=%s days=%d by user=%d",
        account_id, provider_id, company_code, days, triggered_by,
    )
    await _audit(
        account_id, triggered_by,
        "integration.company_backfill_trigger",
        f"{provider_id}:{company_code}",
        details=f"days={days}",
    )
    return {
        "state": "queued",
        "account_id": account_id,
        "provider_id": provider_id,
        "company_code": company_code,
        "days": days,
        "triggered_by": triggered_by,
    }


@router.get(
    "/{provider_id}/companies/{company_code}/actions/backfill-history/status",
)
async def company_backfill_history_status(
    provider_id: str,
    company_code: str,
    user: dict = Depends(_owner_only),
):
    """Read the latest per-company backfill state from Redis.

    Same shape as the account-level status endpoint, scoped to one
    company.  Returns ``{"state": "idle"}`` when no run has been
    recorded for this company.
    """
    _validate_provider(provider_id)
    account_id = int(user["account_id"])
    status = await get_backfill_status(account_id, provider_id, company_code)
    if not status:
        return {
            "state": "idle",
            "account_id": account_id,
            "provider_id": provider_id,
            "company_code": company_code,
        }
    return status


async def _refresh_aggregate_status(
    account_id: int, provider_id: str,
) -> None:
    """Recompute the integration's account-level ``status`` column
    from the per-company health map.

    ``status = "connected"`` if ANY company is healthy, ``"error"``
    if ALL are failing, untouched if there's no health data at all.
    Keeps the legacy single-status column meaningful for callers
    that haven't migrated to per-company yet (the Run-now preflight
    is the main consumer).

    Preserves a useful ``last_health_error`` even when flipping back
    to ``connected`` — if 4 of 5 companies are healthy but one is
    still failing, we want the banner to keep showing "G1: HTTP 401"
    so the operator knows the work isn't done.  The previous
    implementation passed through ``record_integration_health_check``
    which forcibly cleared the error column on ok=True, losing this
    information.  We now write the UPDATE directly with a precise
    summary message.
    """
    from capabilities.integrations.company_health import (
        list_company_health,
    )
    from infra.platform import get_tenant_db as _get_tenant_db
    db = get_platform_db()
    # Stale-filter so removed companies' Redis records don't sway
    # the aggregate decision.
    raw = await list_company_health(account_id, provider_id)
    if not raw:
        return  # untested → don't override existing column
    tenant = await _get_tenant_db(account_id)
    if tenant is not None:
        current_codes = {
            co.code for co in await tenant.get_account_companies(account_id)
        }
        health_map = {c: h for c, h in raw.items() if c in current_codes}
        if not health_map:
            return
    else:
        health_map = raw

    healthy_codes = [c for c, h in health_map.items() if h.get("ok")]
    failing = [
        (c, h.get("message", ""))
        for c, h in health_map.items() if not h.get("ok")
    ]
    new_status = "connected" if healthy_codes else "error"

    # Build a useful message — preserves visibility of failing
    # companies even when the integration is otherwise "connected".
    if not failing:
        message = ""  # everything healthy
    else:
        # Show up to 3 failing companies so the banner stays readable.
        # Each entry is "G1: HTTP 401" — the company's own message
        # already includes the code prefix in most paths.
        head = ", ".join(
            msg if msg.startswith(f"{c}:") else f"{c}: {msg}"
            for c, msg in failing[:3]
        )
        extra = ""
        if len(failing) > 3:
            extra = f" (+{len(failing) - 3} more)"
        message = (
            f"{len(failing)} compan{'y' if len(failing) == 1 else 'ies'} "
            f"failing: {head}{extra}"
        )

    # Direct UPDATE so we control both columns exactly — going
    # through ``record_integration_health_check`` would zero the
    # error column on ok=True.
    from datetime import datetime, timezone
    now = datetime.now(timezone.utc).isoformat(timespec="seconds")
    await db._db.execute(
        """
        UPDATE account_integrations
           SET status            = ?,
               last_health_at    = ?,
               last_health_error = ?,
               updated_at        = ?
         WHERE account_id = ? AND provider_id = ?
        """,
        (new_status, now, message[:500], now, account_id, provider_id),
    )
    await db._db.commit()


@router.put("/{provider_id}/companies/{company_code}/credentials")
async def set_company_credential_endpoint(
    provider_id: str,
    company_code: str,
    body: CompanyCredentialUpsert,
    user: dict = Depends(_owner_only),
):
    """Set or rotate ONE company's API token for the integration.

    Dual-write: updates the canonical integration creds map AND the
    legacy ``companies.samsara_api_key`` column so the four legacy
    read sites (camera, media, ai_maintenance, formatting/admin)
    keep working until they're refactored.
    """
    _validate_provider(provider_id)
    account_id = int(user["account_id"])
    triggered_by = int(user.get("id") or 0)

    # api_token is min_length=1, so this PUT always writes a fresh secret
    # — refuse if we can only store it in cleartext.
    _guard_credential_encryption()

    from infra.platform import get_platform_db, get_tenant_db
    platform_db = get_platform_db()
    integ = await platform_db.get_account_integration(account_id, provider_id)
    if integ is None:
        raise HTTPException(404, "integration not configured")

    tenant = await get_tenant_db(account_id)
    if tenant is None:
        raise HTTPException(503, "tenant DB unavailable")
    co = await tenant.get_company_by_code(account_id, company_code)
    if co is None:
        raise HTTPException(404, f"company not found: {company_code}")

    # Canonical write — the integration credentials map.
    updated = await platform_db.set_company_credential(
        account_id, provider_id, company_code, body.api_token,
    )
    # Dual-write to the legacy column so the four legacy read sites
    # (camera/media/ai_maintenance/formatting/admin) keep working.
    # NB: signature is ``update_company(company_id, account_id, **kw)``
    # — keyword-pass account_id so a future arg-order change doesn't
    # silently break the dual-write into a single-write.
    await tenant.update_company(
        co.id, account_id=account_id, samsara_api_key=body.api_token,
    )
    # Drop the cached MultiCompanyClient so the new key is picked up
    # immediately — without this, the next request still uses the
    # old token until process restart.  ``invalidate_client`` is
    # async; missing the ``await`` here means the coroutine gets GC'd
    # without ever running and the cache stays warm with the stale
    # token (caught in adversarial review of this very change).
    await invalidate_client(account_id)

    await _audit(
        account_id, triggered_by, "integration.set_company_credential",
        f"{provider_id}:{company_code}",
        details=f"key set for {company_code}",
    )
    return _serialize_integration(updated)


@router.delete("/{provider_id}/companies/{company_code}/credentials")
async def remove_company_credential_endpoint(
    provider_id: str,
    company_code: str,
    user: dict = Depends(_owner_only),
):
    """Remove ONE company's API token.

    The company row itself stays — only the key is cleared.  The
    company will appear in the dashboard list with ``has_key=false``
    and a "Set key" affordance.
    """
    _validate_provider(provider_id)
    account_id = int(user["account_id"])
    triggered_by = int(user.get("id") or 0)

    from infra.platform import get_platform_db, get_tenant_db
    platform_db = get_platform_db()
    integ = await platform_db.get_account_integration(account_id, provider_id)
    if integ is None:
        raise HTTPException(404, "integration not configured")

    tenant = await get_tenant_db(account_id)
    if tenant is None:
        raise HTTPException(503, "tenant DB unavailable")
    co = await tenant.get_company_by_code(account_id, company_code)
    if co is None:
        raise HTTPException(404, f"company not found: {company_code}")

    updated = await platform_db.set_company_credential(
        account_id, provider_id, company_code, None,
    )
    # Dual-write: clear the legacy column too.  Schema is NOT NULL so
    # we write an empty string rather than NULL.  account_id passed
    # by keyword to keep the signature unambiguous.
    await tenant.update_company(
        co.id, account_id=account_id, samsara_api_key="",
    )
    await invalidate_client(account_id)

    await _audit(
        account_id, triggered_by, "integration.remove_company_credential",
        f"{provider_id}:{company_code}",
        details=f"key cleared for {company_code}",
    )
    return _serialize_integration(updated)
