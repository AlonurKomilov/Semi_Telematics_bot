"""Provider-agnostic integration routes.

Every provider gets these for free:
  * ``GET    /integrations``                          — catalog + per-account list
  * ``POST   /integrations/{provider_id}/connect``    — verify creds, persist
  * ``DELETE /integrations/{provider_id}``            — disconnect
  * ``PUT    /integrations/{provider_id}/toggles``    — update feature toggles
  * ``POST   /integrations/{provider_id}/actions/test-connection``
  * ``GET    /integrations/{provider_id}/cadences``   — effective cadence read

Provider-specific routes (per-company keys, history backfill, sync
endpoints) live in sibling sub-routers under
``capabilities.integrations.<provider>.router``.

The connect route is generic by delegating to the provider class's
``build_for_test(creds)`` classmethod — every provider knows how to
construct itself from raw credentials without an integration row
existing yet.  That keeps the shared route free of
``if provider_id == "x"`` branches as new providers ship.
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any

from fastapi import APIRouter, Depends, HTTPException

from adapters.telematics import (
    PROVIDER_CATALOG,
    Capability,
    resolve_capability_cadence,
)
from capabilities.telemetry.history_backfill import (
    backfill_vehicle_history,
    get_backfill_status,
)
from infra.platform import get_platform_db
from infra.services import get_telematics_client, invalidate_client
from interfaces.api.deps import require_permission

from .helpers import (
    ConnectRequest,
    ToggleUpdateRequest,
    audit,
    guard_credential_encryption,
    serialize_catalog_entry,
    serialize_integration,
    spawn_background,
    validate_provider,
)

logger = logging.getLogger(__name__)


router = APIRouter(prefix="/integrations", tags=["integrations"])

_owner_only = require_permission("can_manage_integrations")


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
        serialize_catalog_entry(PROVIDER_CATALOG[pid])
        for pid in sorted(PROVIDER_CATALOG.keys())
    ]
    return {
        "catalog":      catalog,
        "integrations": [serialize_integration(ai) for ai in rows],
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
      1. Build the provider via its ``build_for_test`` classmethod
         (the provider class knows how to construct itself from raw
         creds — Samsara wraps a cached MultiCompanyClient, Datatruck
         builds a bare HTTPS client), call ``test_connection`` against
         the new credentials.
      2. If OK, upsert the row with status=connected and the
         catalog's default feature_toggles.

    On failure the row is NOT created; the dashboard sees the error
    message verbatim and prompts the owner to retry.
    """
    validate_provider(provider_id)
    account_id = int(user["account_id"])
    triggered_by = int(user.get("id") or 0)

    # Fail fast before even touching the provider: if we can't encrypt
    # the token, don't connect at all rather than store it in cleartext.
    if body.credentials:
        guard_credential_encryption()

    # Test connection BEFORE persisting anything so bad credentials
    # surface immediately and the integration row never enters a
    # half-configured "stored but unusable" state.
    #
    # Every provider class implements ``build_for_test(account_id,
    # creds)`` — Samsara's resolves through the cached pool; Datatruck's
    # builds a bare HTTPS client from the subdomain+token.  The shared
    # route doesn't care which mechanism — it just gets back a provider
    # instance ready to probe.
    from adapters.telematics.registry import get_provider as _get_provider_cls
    try:
        provider_cls = _get_provider_cls(provider_id)
    except KeyError as e:
        raise HTTPException(503, f"provider not registered: {e}")
    try:
        provider = await provider_cls.build_for_test(
            account_id, body.credentials,
        )
    except ValueError as e:
        raise HTTPException(400, str(e))
    try:
        try:
            status = await provider.test_connection(body.credentials)
        finally:
            # ``build_for_test`` may have constructed a single-use
            # client (Datatruck does this — there's no integration row
            # yet so the cached resolver path doesn't apply).  Close
            # it so the aiohttp session doesn't leak.  Providers whose
            # build_for_test returns the cached instance treat close
            # as a no-op.
            try:
                await provider.close_if_owned_by_test()
            except AttributeError:
                # Older providers may not expose this hook yet —
                # safe to skip; their underlying clients live in the
                # cache and will close at process shutdown.
                pass
    except HTTPException:
        raise
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
    await audit(
        account_id, triggered_by, "integration.connect", provider_id,
        details=status.message[:200],
    )

    # Auto-trigger 30-day history backfill on first connect for
    # providers whose catalog declares HISTORY_BACKFILL with
    # enabled=True (Samsara today, future telematics providers if
    # they ship the same shape).  TMS providers (Datatruck) don't
    # declare this capability and the block silently skips.
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
            spawn_background(_run_backfill())
            logger.info(
                "auto-backfill queued on first connect acct=%d provider=%s days=30",
                account_id, provider_id,
            )

    ai = await db.get_account_integration(account_id, provider_id)
    return serialize_integration(ai)


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
    validate_provider(provider_id)
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
    await audit(
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

    Returns the updated integration row in the same shape as
    ``GET /integrations`` so the dashboard can refresh state from
    the response.
    """
    validate_provider(provider_id)
    account_id = int(user["account_id"])
    db = get_platform_db()

    existing = await db.get_account_integration(account_id, provider_id)
    if existing is None:
        raise HTTPException(404, "integration not configured")

    # Normalise feature_toggles — round-tripping through the dashboard
    # can preserve legacy non-dict values from older rows.  Coerce to
    # the shape downstream code expects.
    raw_toggles = body.feature_toggles or {}
    sanitized_toggles: dict[str, dict] = {}
    for cap, value in raw_toggles.items():
        if isinstance(value, dict):
            sanitized_toggles[cap] = value
        elif isinstance(value, bool):
            sanitized_toggles[cap] = {"enabled": value}
        else:
            sanitized_toggles[cap] = {"enabled": True}

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
    await audit(
        account_id, int(user.get("id") or 0),
        "integration.toggles", provider_id,
        details=", ".join(diff)[:300] or "(no changes)",
    )
    return serialize_integration(ai)


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
    validate_provider(provider_id)
    account_id = int(user["account_id"])
    db = get_platform_db()
    ai = await db.get_account_integration(account_id, provider_id)
    if ai is None:
        raise HTTPException(404, "integration not configured")

    # Hard 12-second total deadline — wraps provider resolution AND
    # the test probe.  Without this the resolution path is uncapped
    # and the user gets the bare "timed out after 30s" frontend banner
    # instead of a clean 504.
    async def _probe() -> Any:
        import time as _time
        t_start = _time.time()
        provider = await get_telematics_client(
            account_id, provider_id, prefetch=False,
        )
        t_built = _time.time()
        logger.info(
            "[test_conn] acct=%d provider=%s phase=provider_build elapsed_ms=%d",
            account_id, provider_id, int((t_built - t_start) * 1000),
        )
        status = await provider.test_connection(ai.credentials)
        logger.info(
            "[test_conn] acct=%d provider=%s phase=probe ok=%s elapsed_ms=%d msg=%s",
            account_id, provider_id, status.ok,
            int((_time.time() - t_built) * 1000),
            (status.message or "")[:80],
        )
        return status

    try:
        status = await asyncio.wait_for(_probe(), timeout=12.0)
    except asyncio.TimeoutError:
        logger.warning(
            "[test_conn] acct=%d provider=%s phase=ROUTE_TIMEOUT — check "
            "earlier log lines for provider_build/probe timings",
            account_id, provider_id,
        )
        await db.record_integration_health_check(
            account_id, provider_id, ok=False,
            message="upstream slow — provider may be rate-limited",
        )
        entry = PROVIDER_CATALOG.get(provider_id)
        provider_name = entry.display_name if entry else provider_id
        raise HTTPException(
            504,
            f"{provider_name} is slow to respond — a backfill or sync "
            "may be running. Try again in a few minutes.",
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
    validate_provider(provider_id)
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
