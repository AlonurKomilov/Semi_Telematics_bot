"""Periodic health check for active telematics integrations.

Runs every 15 minutes via the bot scheduler.  For each active account
× each connected integration row, calls the provider's
``test_connection`` and records the result on
``account_integrations.last_health_at`` / ``last_health_error``.

What this enables
-----------------
* The dashboard badge stays accurate without owners having to click
  "Test connection" manually — a token rotated upstream surfaces as
  an Error badge within 15 minutes.
* The operator console (M8's other deliverable) renders a live
  per-account health view from the same column the dashboard reads.
* Audit-log writes capture every status transition so support can
  reconstruct "when did this account's integration break?" without
  trawling logs.

Boundedness
-----------
* Bounded by the existing per-account semaphore in
  ``_for_each_active_account`` (default 5) so a fleet-wide rollout
  doesn't burst Samsara.
* Per-account health checks have a 12-second timeout (the
  ``test_connection`` cap) so one slow provider can't block the
  others.
* No write happens when the state is unchanged — Postgres write
  amplification stays at zero for healthy steady-state fleets.
"""

from __future__ import annotations

import asyncio
import logging

from infra import observability as _obs
from infra.platform import get_platform_db
from infra.services import get_telematics_client

logger = logging.getLogger(__name__)

_HEALTH_CHECK_TIMEOUT_SEC = 12.0


async def run_integration_health_checks() -> int:
    """Probe ``test_connection`` for every active account's connected
    integrations.  Updates the integration row's health columns when
    the status flips; logs a single INFO summary line per cycle.

    Returns the number of probes attempted.
    """
    db = get_platform_db()
    try:
        accounts = await db.list_accounts(active_only=True)
    except Exception:
        logger.exception("integration_health_checks: list_accounts failed")
        return 0

    probes = 0
    flipped = 0

    async def _probe(account_id: int) -> None:
        nonlocal probes, flipped
        try:
            # include_error=True: error rows MUST keep getting probed so a
            # transient upstream failure can heal back to "connected" once
            # test_connection passes again — otherwise the auto-flip is a
            # one-way trap that silently pauses the account's ingests
            # forever (ingest itself still skips non-connected rows).
            integrations = await db.list_enabled_account_integrations(
                account_id, include_error=True,
            )
        except Exception:
            logger.exception(
                "integration_health_checks: list failed acct=%d", account_id,
            )
            return
        for ai in integrations:
            probes += 1
            prev_status = ai.status
            try:
                provider = await get_telematics_client(
                    account_id, ai.provider_id,
                )
                status = await asyncio.wait_for(
                    provider.test_connection(ai.credentials),
                    timeout=_HEALTH_CHECK_TIMEOUT_SEC,
                )
            except asyncio.TimeoutError:
                await db.record_integration_health_check(
                    account_id, ai.provider_id,
                    ok=False, message="timeout after 12 s",
                )
                _obs.record_integration_health_check(
                    ai.provider_id, "timeout",
                )
                if prev_status != "error":
                    flipped += 1
                continue
            except KeyError:
                # Provider somehow lost its registration; skip
                # cleanly without flipping the row to error.
                continue
            except Exception as e:
                await db.record_integration_health_check(
                    account_id, ai.provider_id,
                    ok=False, message=str(e)[:300],
                )
                _obs.record_integration_health_check(
                    ai.provider_id, "error",
                )
                if prev_status != "error":
                    flipped += 1
                continue

            await db.record_integration_health_check(
                account_id, ai.provider_id,
                ok=status.ok, message=status.message,
            )
            _obs.record_integration_health_check(
                ai.provider_id, "ok" if status.ok else "error",
            )
            new_status = "connected" if status.ok else "error"
            if prev_status != new_status:
                flipped += 1

    await asyncio.gather(*(_probe(a.id) for a in accounts))
    logger.info(
        "integration_health_checks accounts=%d probes=%d status_flips=%d",
        len(accounts), probes, flipped,
    )
    return probes


async def job_run_integration_health_checks(_app=None) -> None:
    """APScheduler entry point."""
    await run_integration_health_checks()
