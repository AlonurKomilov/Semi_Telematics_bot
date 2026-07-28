"""Per-company integration health — Redis-backed.

Each company (CFT, G1, OSY, …) under a provider integration carries
its own ``/me`` probe result so the dashboard can render per-row
diagnostics ("OSY: timeout after 3s — key may be revoked") instead
of a binary account-level connected/error.

Stored as one Redis key per ``(account_id, provider_id, company_code)``
with a 7-day TTL.  No DB schema change required — health is a UX
hint, not source of truth.  TTL lets a long-idle entry naturally
disappear instead of going stale-forever.

Read paths:
  * ``GET /integrations/{provider}/companies`` enriches each row.
  * The Run-now preflight asks "is at least one company healthy?"
    to avoid blocking the whole account when 4 of 5 are fine.

Write paths:
  * ``POST /integrations/{provider}/companies/{code}/actions/test``
    runs the lean probe and stores the result.
  * The account-level ``POST /actions/test-connection`` also writes
    each company's individual result so an account-wide test surfaces
    the broken company immediately.
"""

from __future__ import annotations

import logging
import time
from typing import Any, Optional

from infra import cache as _redis

logger = logging.getLogger(__name__)

HEALTH_TTL_SEC = 7 * 24 * 60 * 60  # 7 days


def _key(account_id: int, provider_id: str, company_code: str) -> str:
    return f"integration_health:{account_id}:{provider_id}:{company_code}"


def _list_pattern(account_id: int, provider_id: str) -> str:
    return f"integration_health:{account_id}:{provider_id}:*"


async def set_company_health(
    account_id: int,
    provider_id: str,
    company_code: str,
    *,
    ok: bool,
    message: str = "",
    elapsed_ms: Optional[int] = None,
) -> None:
    """Stamp the latest probe result for ONE company.

    ``elapsed_ms`` is optional — the dashboard renders "reachable in
    187ms" when present, falling back to just "✓ reachable" when
    absent.  Soft-fails on Redis unavailability — the probe still
    returned its result to the caller; we just can't persist it.
    """
    if not _redis.is_available():
        return
    try:
        payload = {
            "ok":         bool(ok),
            "message":    str(message)[:500],
            "checked_at": time.time(),
        }
        if elapsed_ms is not None:
            payload["elapsed_ms"] = int(elapsed_ms)
        await _redis.cache_set(
            _key(account_id, provider_id, company_code),
            payload,
            ttl=HEALTH_TTL_SEC,
        )
    except Exception as e:
        logger.debug(
            "company health publish failed acct=%d code=%s: %s",
            account_id, company_code, e,
        )


async def get_company_health(
    account_id: int,
    provider_id: str,
    company_code: str,
) -> Optional[dict[str, Any]]:
    """Return the latest probe result for ONE company, or None."""
    if not _redis.is_available():
        return None
    try:
        return await _redis.get(
            _key(account_id, provider_id, company_code),
        )
    except Exception as e:
        logger.debug(
            "company health read failed acct=%d code=%s: %s",
            account_id, company_code, e,
        )
        return None


async def list_company_health(
    account_id: int,
    provider_id: str,
) -> dict[str, dict[str, Any]]:
    """Return ``{company_code: health_dict}`` for every company that
    has a stored health record.  Companies with no record (never
    tested, or record expired) are absent from the result — caller
    treats missing as "unknown" rather than "unhealthy"."""
    if not _redis.is_available():
        return {}
    try:
        keys = await _redis.scan_keys(_list_pattern(account_id, provider_id))
    except Exception as e:
        logger.debug("company health scan failed acct=%d: %s", account_id, e)
        return {}
    out: dict[str, dict[str, Any]] = {}
    prefix = f"integration_health:{account_id}:{provider_id}:"
    for key in keys:
        if not key.startswith(prefix):
            continue
        code = key[len(prefix):]
        try:
            payload = await _redis.get(key)
        except Exception:
            continue
        if isinstance(payload, dict):
            out[code] = payload
    return out


def any_company_healthy(health_map: dict[str, dict[str, Any]]) -> bool:
    """Lightweight predicate the Run-now preflight uses.

    Accept the trigger if AT LEAST ONE company has been tested
    recently and reported ok.  An account with no test records at
    all is treated as "unknown but allowed" — the operator may just
    not have tested since reconnect; better to let the backfill try
    and fail loudly than silently block.
    """
    if not health_map:
        return True  # no records → unknown, don't block
    return any(h.get("ok") for h in health_map.values())


def summarise_health(
    health_map: dict[str, dict[str, Any]],
    total_companies: int,
) -> dict[str, Any]:
    """Compute the integration-level "N of M healthy" badge data.

    Returns ``{healthy: int, total: int, untested: int, status: str}``
    where ``status`` is one of:
      * ``"unknown"``  — no health records at all
      * ``"healthy"``  — every recorded company is ok
      * ``"degraded"`` — some ok, some not
      * ``"error"``    — every recorded company is failing
    """
    tested = len(health_map)
    if tested == 0:
        return {
            "healthy":  0,
            "total":    total_companies,
            "untested": total_companies,
            "status":   "unknown",
        }
    healthy = sum(1 for h in health_map.values() if h.get("ok"))
    if healthy == 0:
        status = "error"
    elif healthy == tested:
        status = "healthy"
    else:
        status = "degraded"
    return {
        "healthy":  healthy,
        "total":    total_companies,
        "untested": max(0, total_companies - tested),
        "status":   status,
    }
