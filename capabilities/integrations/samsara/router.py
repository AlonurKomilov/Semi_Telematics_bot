"""Samsara-specific integration routes.

Everything that's Samsara-shaped — per-company API keys, history
backfill (account-wide + per-company), snapshot coverage preview,
aggregate health reconciler — lives here.  All paths are hardcoded
to ``/samsara/...`` rather than ``/{provider_id}/...`` so the URL
surface accurately reflects which provider the route applies to.

Generic routes (connect, disconnect, toggles, test-connection,
cadences) live in ``capabilities.integrations.shared.router`` and
keep the ``/{provider_id}/...`` placeholder.

This file is mounted by the top-level aggregator at
``capabilities.integrations.router`` alongside the shared router.
"""
# router.py is interface-layer code co-located with its hub/domain
# (docs/FEATURES.md): router.py and config.py are the interface-layer pair — those two may
# import interfaces.api.deps; nothing else in the feature may.


from __future__ import annotations

import asyncio
import logging
from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from adapters.telematics import PROVIDER_CATALOG
from capabilities.integrations.shared.history_backfill import (
    backfill_vehicle_history,
    get_backfill_status,
    reset_backfill_status,
)
from infra.platform import get_platform_db, get_tenant_db
from infra.services import get_telematics_client, invalidate_client
from interfaces.api.deps import require_permission

from ..shared.helpers import (
    BackfillHistoryRequest,
    audit,
    guard_credential_encryption,
    serialize_integration,
    spawn_background,
    validate_provider,
)

logger = logging.getLogger(__name__)


router = APIRouter(prefix="/integrations", tags=["integrations"])

_owner_only = require_permission("can_manage_integrations")

# Constant for clarity — keeps every Samsara-only route honest about
# the fact that ``provider_id`` is fixed; no need for an enum.
_PROVIDER_ID = "samsara"


# ── Account-wide history backfill ────────────────────────────────


@router.post("/samsara/actions/backfill-history")
async def trigger_backfill_history(
    body: BackfillHistoryRequest,
    user: dict = Depends(_owner_only),
):
    """Queue an account-wide backfill for Samsara.

    Fire-and-forget — the endpoint returns immediately with the
    initial state.  The actual fetch + persist work happens in a
    background task scheduled by ``asyncio.create_task`` so the HTTP
    handler doesn't block on the multi-minute backfill.

    Idempotent: if a backfill is already running for this account,
    the new request returns 409 rather than queueing a parallel
    duplicate.  The cross-account serial lock further ensures only
    one backfill runs platform-wide at a time, but that's enforced
    inside the capability — this endpoint's check is a fast preflight
    against the same-account case.
    """
    validate_provider(_PROVIDER_ID)
    account_id = int(user["account_id"])

    # Preflight: if there's a status row showing 'running' or
    # 'queued', refuse to queue a duplicate.  Including 'queued'
    # closes the TOCTOU window between accepting a request and the
    # task actually flipping state to 'running'.
    status = await get_backfill_status(account_id, _PROVIDER_ID)
    if status and status.get("state") in ("running", "queued"):
        raise HTTPException(
            409,
            "a backfill is already running or queued for this account — "
            "wait for it to complete or check the status endpoint",
        )

    db = get_platform_db()
    ai = await db.get_account_integration(account_id, _PROVIDER_ID)
    if ai is None:
        raise HTTPException(
            404,
            f"no {_PROVIDER_ID} integration configured for this account",
        )
    # Loosened preflight — allow the trigger when the aggregate status
    # is ``connected`` OR when at least one company has been tested
    # recently and reports healthy.  This stops a stuck "error" status
    # (from an account-level test that ran into one bad company) from
    # blocking the backfill for the other companies.
    if ai.status != "connected":
        from capabilities.integrations.company_health import (
            list_company_health, any_company_healthy,
        )
        tenant_for_codes = await get_tenant_db(account_id)
        if tenant_for_codes is not None:
            current_companies = await tenant_for_codes.get_account_companies(
                account_id,
            )
            current_codes = {co.code for co in current_companies}
            raw = await list_company_health(account_id, _PROVIDER_ID)
            health_map = {
                code: h for code, h in raw.items() if code in current_codes
            }
        else:
            health_map = await list_company_health(account_id, _PROVIDER_ID)
        if not any_company_healthy(health_map):
            raise HTTPException(
                409,
                f"integration status is {ai.status!r}, not connected — "
                "click 'Test' on individual companies to identify the "
                "failing one, then rotate its key or remove it",
            )

    triggered_by = int(user.get("id") or 0)
    days = int(body.days)

    async def _run() -> None:
        try:
            await backfill_vehicle_history(
                account_id,
                days=days,
                provider_id=_PROVIDER_ID,
                triggered_by=triggered_by,
            )
        except Exception:
            logger.exception(
                "backfill background task crashed acct=%d", account_id,
            )

    spawn_background(_run())
    logger.info(
        "backfill queued acct=%d days=%d by user=%d",
        account_id, days, triggered_by,
    )
    await audit(
        account_id, triggered_by, "integration.backfill_trigger",
        _PROVIDER_ID, details=f"days={days}",
    )
    return {
        "state": "queued",
        "account_id": account_id,
        "provider_id": _PROVIDER_ID,
        "days": days,
        "triggered_by": triggered_by,
    }


@router.get("/samsara/actions/backfill-history/status")
async def backfill_history_status(user: dict = Depends(_owner_only)):
    """Read the latest backfill state from Redis.

    Returns ``{"state": "idle"}`` when no run has been recorded — the
    dashboard renders that as "no backfill running" rather than a 404
    so the polling UI doesn't have to special-case errors.
    """
    validate_provider(_PROVIDER_ID)
    account_id = int(user["account_id"])
    status = await get_backfill_status(account_id, _PROVIDER_ID)
    if not status:
        return {
            "state": "idle",
            "account_id": account_id,
            "provider_id": _PROVIDER_ID,
        }
    return status


@router.post("/samsara/actions/backfill-history/reset")
async def backfill_history_reset(user: dict = Depends(_owner_only)):
    """Owner-only escape hatch: forcibly clear the Redis state for a
    stuck backfill.  Heartbeat staleness coercion handles most
    SIGTERM / OOM cases automatically; this is for the operator who
    wants the badge gone immediately."""
    validate_provider(_PROVIDER_ID)
    account_id = int(user["account_id"])
    triggered_by = int(user.get("id") or 0)

    cleared = await reset_backfill_status(account_id, _PROVIDER_ID)
    await audit(
        account_id, triggered_by, "integration.backfill_reset",
        _PROVIDER_ID, details=f"cleared={cleared}",
    )
    return {"cleared": bool(cleared)}


@router.get("/samsara/snapshot-coverage")
async def snapshot_coverage(
    days: int = 30,
    user: dict = Depends(_owner_only),
):
    """Per-day backfill coverage, newest first.

    Shows which days a "Run backfill" would actually fetch vs skip —
    judged the same way the backfill's own day cursor judges them
    (``_day_is_covered``), NOT by the minute tier alone.  The minute
    tier only retains ~7 days, so a minute-only view permanently
    renders every older day as "missing" even when its history is
    safe in the hour/day roll-ups — inviting pointless re-runs.

    Each entry carries ``minute_rows`` (raw capture, inside minute
    retention), ``hour_rows`` (the durable roll-up), and ``covered``.
    ``row_count`` mirrors ``minute_rows`` for the original response
    shape.
    """
    validate_provider(_PROVIDER_ID)
    account_id = int(user["account_id"])
    if days < 1 or days > 90:
        raise HTTPException(400, "days must be between 1 and 90")
    tenant = await get_tenant_db(account_id)
    if tenant is None:
        raise HTTPException(503, "tenant DB unavailable")
    from datetime import datetime as _dt, timedelta as _td, timezone as _tz

    from capabilities.integrations.shared.history_backfill import (
        _day_is_covered,
    )

    per_day = await tenant.vehicle_state_backfill_day_coverage(
        account_id, days_back=days,
    )
    max_minute = max(
        (c.get("minute_rows", 0) for c in per_day.values()), default=0,
    )
    max_hour = max(
        (c.get("hour_rows", 0) for c in per_day.values()), default=0,
    )
    today = _dt.now(_tz.utc).date()
    coverage = []
    for offset in range(days):
        d = (today - _td(days=offset)).isoformat()
        c = per_day.get(d, {})
        minute_rows = c.get("minute_rows", 0)
        hour_rows = c.get("hour_rows", 0)
        coverage.append({
            "day_utc": d,
            "row_count": minute_rows,
            "minute_rows": minute_rows,
            "hour_rows": hour_rows,
            "covered": _day_is_covered(
                minute_rows, hour_rows, max_minute, max_hour,
            ),
        })
    entry = PROVIDER_CATALOG[_PROVIDER_ID]
    return {
        "account_id": account_id,
        "provider_id": _PROVIDER_ID,
        "provider_display_name": entry.display_name,
        "days": days,
        "coverage": coverage,
    }


# NOTE: the "Synced data" feeds endpoint is now generic + catalog-
# driven — see ``GET /{provider_id}/feeds`` in the shared router.  Samsara
# declares its feeds via ``feeds=_SAMSARA_FEED_SPECS`` in the catalog, so
# there's no provider-specific endpoint here anymore.


# ── Per-company credentials (canonical key management) ───────────


class CompanyCredentialUpsert(BaseModel):
    """Body for setting one company's API token inside the
    integration credentials map."""
    api_token: str = Field(..., min_length=1, max_length=512)


@router.get("/samsara/companies")
async def list_provider_companies(user: dict = Depends(_owner_only)):
    """Per-company key-status overview for the Integration card.

    Returns one entry per company on the account with the latest
    Redis-stored health from per-company ``/me`` probes.  Companies
    that have never been tested (or whose 7-day TTL expired) have
    ``health: null``; the dashboard renders these as "untested"
    rather than "down".

    No raw tokens are returned — only the boolean.  Drives the
    "Connected companies (N)" section on the Integration card.
    """
    validate_provider(_PROVIDER_ID)
    account_id = int(user["account_id"])
    from capabilities.integrations.company_health import (
        list_company_health, summarise_health,
    )
    platform_db = get_platform_db()
    integ = await platform_db.get_account_integration(account_id, _PROVIDER_ID)
    creds_map: dict = {}
    if integ and integ.credentials:
        creds_map = integ.credentials.get("companies") or {}

    tenant = await get_tenant_db(account_id)
    if tenant is None:
        raise HTTPException(503, "tenant DB unavailable")
    companies = await tenant.get_account_companies(account_id)
    raw_health_map = await list_company_health(account_id, _PROVIDER_ID)
    # Filter stale records — a removed company leaves its Redis health
    # record live for the 7-day TTL.  Without this filter,
    # ``summarise_health`` could report "7 of 5 healthy" (5 current
    # + 2 stale) and ``any_company_healthy`` could falsely pass the
    # Run-now preflight on the strength of a company that no longer
    # exists.
    current_codes = {co.code for co in companies}
    health_map = {
        code: h for code, h in raw_health_map.items() if code in current_codes
    }
    return {
        "account_id":  account_id,
        "provider_id": _PROVIDER_ID,
        "health_summary": summarise_health(health_map, len(companies)),
        "companies": [
            {
                "code":         co.code,
                "display_name": co.display_name,
                "has_key":      bool(creds_map.get(co.code) or co.samsara_api_key),
                "active_days":  co.active_days,
                "health":       health_map.get(co.code),
            }
            for co in companies
        ],
    }


@router.post("/samsara/companies/{company_code}/actions/test")
async def test_company_connection_action(
    company_code: str,
    user: dict = Depends(_owner_only),
):
    """Run the lean /me probe against ONE company.

    Same shape as the account-level ``/actions/test-connection`` but
    scoped to a single company so the operator can isolate exactly
    which company is failing.  Persists the result in Redis (7-day
    TTL); updates the integration's aggregate ``status`` column when
    one company flipping fixes or breaks the aggregate.
    """
    validate_provider(_PROVIDER_ID)
    account_id = int(user["account_id"])
    db = get_platform_db()
    ai = await db.get_account_integration(account_id, _PROVIDER_ID)
    if ai is None:
        raise HTTPException(404, "integration not configured")

    async def _probe_one() -> Any:
        from capabilities.integrations.company_health import (
            set_company_health,
        )
        import time as _time
        t_start = _time.time()
        provider = await get_telematics_client(
            account_id, _PROVIDER_ID, prefetch=False,
        )
        t_built = _time.time()
        logger.info(
            "[test_company] acct=%d code=%s phase=provider_build elapsed_ms=%d",
            account_id, company_code, int((t_built - t_start) * 1000),
        )
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
            account_id, _PROVIDER_ID, company_code,
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
            account_id, _PROVIDER_ID, company_code,
            ok=False, message="route timed out (provider construction slow)",
        )
        raise HTTPException(504, "provider construction timed out")
    except KeyError:
        raise HTTPException(
            503,
            f"provider {_PROVIDER_ID!r} is not registered on this deploy",
        )
    except NotImplementedError as e:
        raise HTTPException(503, str(e))
    except Exception as e:
        raise HTTPException(502, f"per-company test threw: {e}")

    status, elapsed_ms = result
    if status is None:
        raise HTTPException(501, str(elapsed_ms))

    await _refresh_aggregate_status(account_id, _PROVIDER_ID)

    return {
        "code":        company_code,
        "ok":          status.ok,
        "message":     status.message,
        "elapsed_ms":  elapsed_ms,
        "checked_at":  None,
    }


@router.post(
    "/samsara/companies/{company_code}/actions/backfill-history",
)
async def trigger_company_backfill_history(
    company_code: str,
    body: BackfillHistoryRequest,
    user: dict = Depends(_owner_only),
):
    """Per-company "Refresh history" trigger.

    Same semantics as the account-wide backfill but scoped to one
    company.  Useful when the operator just rotated a single company's
    key and wants to backfill only that company's history without re-
    fetching the others.

    Progress lives under a per-company Redis key so this can co-exist
    with an account-wide "Run all".  The preflight refuses if another
    backfill (account-wide OR same-company) is currently running.
    """
    validate_provider(_PROVIDER_ID)
    account_id = int(user["account_id"])

    db = get_platform_db()
    ai = await db.get_account_integration(account_id, _PROVIDER_ID)
    if ai is None:
        raise HTTPException(
            404,
            f"no {_PROVIDER_ID} integration configured for this account",
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

    company_status = await get_backfill_status(
        account_id, _PROVIDER_ID, company_code,
    )
    if company_status and company_status.get("state") in ("running", "queued"):
        raise HTTPException(
            409,
            f"a refresh is already running for company {company_code!r} — "
            "wait for it to complete",
        )
    account_status = await get_backfill_status(account_id, _PROVIDER_ID)
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
                provider_id=_PROVIDER_ID,
                triggered_by=triggered_by,
                company_code=company_code,
            )
        except Exception:
            logger.exception(
                "per-company backfill task crashed acct=%d code=%s",
                account_id, company_code,
            )

    spawn_background(_run())
    logger.info(
        "per-company backfill queued acct=%d code=%s days=%d by user=%d",
        account_id, company_code, days, triggered_by,
    )
    await audit(
        account_id, triggered_by,
        "integration.company_backfill_trigger",
        f"{_PROVIDER_ID}:{company_code}",
        details=f"days={days}",
    )
    return {
        "state": "queued",
        "account_id": account_id,
        "provider_id": _PROVIDER_ID,
        "company_code": company_code,
        "days": days,
        "triggered_by": triggered_by,
    }


@router.get(
    "/samsara/companies/{company_code}/actions/backfill-history/status",
)
async def company_backfill_history_status(
    company_code: str,
    user: dict = Depends(_owner_only),
):
    """Read the latest per-company backfill state from Redis.
    Returns ``{"state": "idle"}`` when no run has been recorded for
    this company."""
    validate_provider(_PROVIDER_ID)
    account_id = int(user["account_id"])
    status = await get_backfill_status(account_id, _PROVIDER_ID, company_code)
    if not status:
        return {
            "state": "idle",
            "account_id": account_id,
            "provider_id": _PROVIDER_ID,
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
    that haven't migrated to per-company yet.

    Preserves a useful ``last_health_error`` even when flipping back
    to ``connected`` — if 4 of 5 companies are healthy but one is
    still failing, the banner still shows the failing one.
    """
    from capabilities.integrations.company_health import (
        list_company_health,
    )
    db = get_platform_db()
    raw = await list_company_health(account_id, provider_id)
    if not raw:
        return
    tenant = await get_tenant_db(account_id)
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

    if not failing:
        message = ""
    else:
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


@router.put("/samsara/companies/{company_code}/credentials")
async def set_company_credential_endpoint(
    company_code: str,
    body: CompanyCredentialUpsert,
    user: dict = Depends(_owner_only),
):
    """Set or rotate ONE company's API token for the Samsara integration.

    Dual-write: updates the canonical integration creds map AND the
    legacy ``companies.samsara_api_key`` column so the legacy read
    sites keep working until they're refactored.
    """
    validate_provider(_PROVIDER_ID)
    account_id = int(user["account_id"])
    triggered_by = int(user.get("id") or 0)

    guard_credential_encryption()

    platform_db = get_platform_db()
    integ = await platform_db.get_account_integration(account_id, _PROVIDER_ID)
    if integ is None:
        raise HTTPException(404, "integration not configured")

    tenant = await get_tenant_db(account_id)
    if tenant is None:
        raise HTTPException(503, "tenant DB unavailable")
    co = await tenant.get_company_by_code(account_id, company_code)
    if co is None:
        raise HTTPException(404, f"company not found: {company_code}")

    updated = await platform_db.set_company_credential(
        account_id, _PROVIDER_ID, company_code, body.api_token,
    )
    await tenant.update_company(
        co.id, account_id=account_id, samsara_api_key=body.api_token,
    )
    await invalidate_client(account_id)

    await audit(
        account_id, triggered_by, "integration.set_company_credential",
        f"{_PROVIDER_ID}:{company_code}",
        details=f"key set for {company_code}",
    )
    return serialize_integration(updated)


@router.delete("/samsara/companies/{company_code}/credentials")
async def remove_company_credential_endpoint(
    company_code: str,
    user: dict = Depends(_owner_only),
):
    """Remove ONE company's API token.

    The company row itself stays — only the key is cleared.  The
    company will appear in the dashboard list with ``has_key=false``
    and a "Set key" affordance.
    """
    validate_provider(_PROVIDER_ID)
    account_id = int(user["account_id"])
    triggered_by = int(user.get("id") or 0)

    platform_db = get_platform_db()
    integ = await platform_db.get_account_integration(account_id, _PROVIDER_ID)
    if integ is None:
        raise HTTPException(404, "integration not configured")

    tenant = await get_tenant_db(account_id)
    if tenant is None:
        raise HTTPException(503, "tenant DB unavailable")
    co = await tenant.get_company_by_code(account_id, company_code)
    if co is None:
        raise HTTPException(404, f"company not found: {company_code}")

    updated = await platform_db.set_company_credential(
        account_id, _PROVIDER_ID, company_code, None,
    )
    await tenant.update_company(
        co.id, account_id=account_id, samsara_api_key="",
    )
    await invalidate_client(account_id)

    await audit(
        account_id, triggered_by, "integration.remove_company_credential",
        f"{_PROVIDER_ID}:{company_code}",
        details=f"key cleared for {company_code}",
    )
    return serialize_integration(updated)
