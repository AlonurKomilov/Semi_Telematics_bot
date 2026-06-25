"""One-shot historical backfill triggered when a Samsara company is connected.

Existing ingestors run on intervals (1 min – 1 hour) and only fetch their
default windows (1-7 days), so a freshly-connected account has nothing
in the warehouse for the first few days/weeks.  This module backfills
historical data on connect / token-rotation so the dashboard's 30/90-day
windows aren't empty out of the gate.

Idempotency / dedup guarantees:
    * ``safety_event_log`` has ``UNIQUE(samsara_event_id)`` and the
      writer uses ``INSERT OR IGNORE`` — re-running skips duplicates.
    * ``driver_efficiency_daily`` is keyed on ``(account, driver, day)``
      and the writer uses ``ON CONFLICT … DO UPDATE`` — re-running
      refreshes the row, never creates a second one.
    * ``vehicle_state`` / ``vehicle_health_snapshot`` /
      ``vehicle_fault_snapshot`` / ``aggregate_weather_snapshot`` are
      keyed on ``vehicle_id`` and ``ON CONFLICT DO UPDATE`` —
      snapshot-style, latest value wins.

Gap detection avoids burning Samsara API calls when the warehouse
already covers the requested window:
    * Safety events: skip the fetch when the row count in the trailing
      window is already healthy.
    * Driver efficiency: same — skip when at least *N* days are
      already present.

Snapshot sources (vehicle_state / health / faults / weather) always
re-fetch — they're a current snapshot, not historical, and the
UPSERT semantics make re-runs cheap.
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any

from infra.services import get_tenant_db
from capabilities.integrations.samsara.sync import (
    ingest_vehicle_state,
    ingest_vehicle_health,
    ingest_vehicle_faults,
    ingest_fleet_weather,
    ingest_safety_events,
    ingest_driver_efficiency_daily,
    ingest_fleet_efficiency,
    ingest_geofence_definitions,
)

logger = logging.getLogger(__name__)


# Backfill window — wide enough to fill the dashboard's 30-day scoreboard
# *and* feed the 90-day risk-summary report.  Samsara retains safety
# events ≥ 90 days for most plans; older windows return less data.
_BACKFILL_DAYS = 90

# Heuristics for "warehouse already covers this period — skip Samsara".
# Tuned for typical fleet sizes; conservative enough that a healthy
# warehouse is detected, lenient enough that a partially-populated
# one still triggers the backfill.
_EVENT_SUFFICIENT_ROWS = 50          # ~ a few events per day for a small fleet
_EFFICIENCY_SUFFICIENT_ROWS = 30     # ~ a full month of daily rows — matches
                                     #   the dashboard's default 30-day window


async def backfill_account_initial(account_id: int, *, days: int = _BACKFILL_DAYS) -> dict[str, Any]:
    """Pull historical + current state for a freshly-connected account.

    Safe to re-run — every ingest path uses UPSERT/UNIQUE so duplicates
    are silently merged.  Gap-aware — skips the Samsara fetch for
    sources that already have sufficient coverage in the window.

    Returns a per-source result dict so callers can log / surface a
    summary to the admin who triggered the connect.

    Designed to be fire-and-forget — caller wraps in
    ``asyncio.create_task`` so the API response doesn't block on
    Samsara round-trips (~10-30s for a 90-day pull on a large fleet).
    """
    result: dict[str, Any] = {"days": days, "sources": {}}
    tenant = await get_tenant_db(account_id)
    if tenant is None:
        result["error"] = "no_tenant_db"
        return result

    # ── Snapshot sources — always re-pull (current state, no history). ──
    # Each one is independent so we don't bail on the first failure.
    snapshot_jobs: list[tuple[str, Any]] = [
        ("vehicle_state",      ingest_vehicle_state(account_id)),
        ("vehicle_health",     ingest_vehicle_health(account_id)),
        ("vehicle_faults",     ingest_vehicle_faults(account_id)),
        ("fleet_weather",      ingest_fleet_weather(account_id)),
        ("geofence_definitions", ingest_geofence_definitions(account_id)),
    ]
    for name, coro in snapshot_jobs:
        try:
            persisted = await coro
            result["sources"][name] = {"persisted": persisted, "skipped": False}
        except Exception as e:
            logger.exception("backfill: %s failed for acct=%d", name, account_id)
            result["sources"][name] = {"error": str(e), "skipped": False}

    # ── Historical: safety events ──
    # Check coverage first; skip the 90-day Samsara round-trip when the
    # warehouse already has data for this period.  The UNIQUE constraint
    # on samsara_event_id would silently skip duplicates anyway, but
    # avoiding the API call keeps onboarding fast.
    try:
        have = await tenant.count_safety_events_in_window(account_id, days=days)
    except Exception:
        have = 0
    if have >= _EVENT_SUFFICIENT_ROWS:
        result["sources"]["safety_events"] = {
            "skipped": True, "reason": f"already have {have} events in window",
        }
    else:
        try:
            persisted = await ingest_safety_events(account_id, days=days)
            result["sources"]["safety_events"] = {
                "persisted": persisted, "skipped": False,
            }
        except Exception as e:
            logger.exception("backfill: safety_events failed for acct=%d", account_id)
            result["sources"]["safety_events"] = {"error": str(e), "skipped": False}

    # ── Historical: driver efficiency ──
    # Samsara's efficiency endpoint returns an aggregated window (one
    # record per driver covering the requested ``days``), not a per-day
    # breakdown.  We can't reconstruct per-day rows from the aggregate,
    # so the best we can do on first connect is seed today's row with
    # the 90-day baseline; the hourly ingestor takes over from there.
    try:
        have = await tenant.count_driver_efficiency_in_window(account_id, days=days)
    except Exception:
        have = 0
    if have >= _EFFICIENCY_SUFFICIENT_ROWS:
        result["sources"]["driver_efficiency"] = {
            "skipped": True, "reason": f"already have {have} daily rows in window",
        }
    else:
        try:
            persisted = await ingest_driver_efficiency_daily(account_id, days=days)
            result["sources"]["driver_efficiency"] = {
                "persisted": persisted, "skipped": False,
            }
        except Exception as e:
            logger.exception("backfill: driver_efficiency failed for acct=%d", account_id)
            result["sources"]["driver_efficiency"] = {"error": str(e), "skipped": False}

    # ── Historical: fleet efficiency (aggregated) ──
    try:
        persisted = await ingest_fleet_efficiency(account_id, days=days)
        result["sources"]["fleet_efficiency"] = {
            "persisted": persisted, "skipped": False,
        }
    except Exception as e:
        logger.exception("backfill: fleet_efficiency failed for acct=%d", account_id)
        result["sources"]["fleet_efficiency"] = {"error": str(e), "skipped": False}

    logger.info(
        "backfill_account_initial acct=%d days=%d result=%s",
        account_id, days, result["sources"],
    )
    return result


def schedule_backfill(account_id: int, *, days: int = _BACKFILL_DAYS) -> asyncio.Task:
    """Fire-and-forget wrapper for use inside FastAPI handlers.

    Returns the ``asyncio.Task`` so the caller can optionally await it
    in tests; production code just lets it run in the background.
    """
    return asyncio.create_task(
        backfill_account_initial(account_id, days=days),
        name=f"backfill_account_{account_id}",
    )
