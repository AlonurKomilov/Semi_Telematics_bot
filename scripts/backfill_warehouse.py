"""Phase C — one-shot warehouse backfill.

Runs every ingestor for every active account once, then exits.  Used
during the cutover window per plan.md C7:

  1. Deploy Phase C with ``WAREHOUSE_READS_ENABLED=0`` (default).
  2. Run this script in production to populate the warehouse from
     Samsara.
  3. Let the APScheduler ingestor run for ~24 h to verify shadow reads
     match Samsara within tolerance.
  4. Set ``WAREHOUSE_READS_ENABLED=1`` and restart the API service.

Usage::

    python -m scripts.backfill_warehouse              # all accounts
    python -m scripts.backfill_warehouse --account 7  # one account
    python -m scripts.backfill_warehouse --skip-events  # vehicle_state only

Idempotent: re-running just refreshes the data; safety events dedupe
on samsara_event_id, others upsert.
"""

from __future__ import annotations

import argparse
import asyncio
import logging
import sys

from capabilities.telemetry.ingestor import (
    aggregate_telemetry_hourly,
    ingest_driver_efficiency_daily,
    ingest_fleet_efficiency,
    ingest_fleet_weather,
    ingest_geofence_definitions,
    ingest_safety_events,
    ingest_vehicle_faults,
    ingest_vehicle_health,
    ingest_vehicle_state,
)
from infra.services import get_platform_db
from infra.startup import initialize as init_services


logger = logging.getLogger("warehouse.backfill")


async def _run_for(account_id: int, *, skip_events: bool, skip_efficiency: bool) -> None:
    """Drive every ingestor once for a single account, logging per-step
    counts so ops can eyeball that the data actually showed up.

    All 9 ingestors run by default \u2014 covers every warehouse-backed
    reader. ``--skip-events`` and ``--skip-efficiency`` skip the two
    slowest (paginated Samsara endpoints) for quick smoke-tests.
    """
    logger.info("\u2500\u2500 backfill account %d \u2500\u2500", account_id)

    # State (60s in prod) \u2014 vehicle_state table powers /vehicles + map.
    n = await ingest_vehicle_state(account_id)
    logger.info("  vehicle_state            %d rows", n)

    # Health (5min in prod) \u2014 vehicle_health_snapshot powers Reports.
    try:
        n = await ingest_vehicle_health(account_id)
        logger.info("  vehicle_health_snapshot  %d rows", n)
    except Exception:
        logger.exception("  vehicle_health_snapshot  FAILED \u2014 continuing")

    # Faults (2min in prod) \u2014 vehicle_fault_snapshot powers fault badges.
    try:
        n = await ingest_vehicle_faults(account_id)
        logger.info("  vehicle_fault_snapshot   %d rows", n)
    except Exception:
        logger.exception("  vehicle_fault_snapshot   FAILED \u2014 continuing")

    # Weather (10min in prod) \u2014 fleet_weather_snapshot powers Weather page.
    try:
        n = await ingest_fleet_weather(account_id)
        logger.info("  fleet_weather_snapshot   %d rows", n)
    except Exception:
        logger.exception("  fleet_weather_snapshot   FAILED \u2014 continuing")

    # Fleet efficiency (30min in prod) \u2014 fleet_efficiency_snapshot
    # powers Cost-per-Mile + Scorecards.
    try:
        n = await ingest_fleet_efficiency(account_id, days=7)
        logger.info("  fleet_efficiency_snapshot %d rows", n)
    except Exception:
        logger.exception("  fleet_efficiency_snapshot FAILED \u2014 continuing")

    # Geofences (1h in prod) \u2014 geofence_definitions powers map overlays.
    try:
        n = await ingest_geofence_definitions(account_id)
        logger.info("  geofence_definitions     %d rows", n)
    except Exception:
        logger.exception("  geofence_definitions     FAILED \u2014 continuing")

    if not skip_events:
        n = await ingest_safety_events(account_id, days=7)
        logger.info("  safety_event_log         %d new", n)

    if not skip_efficiency:
        n = await ingest_driver_efficiency_daily(account_id, days=7)
        logger.info("  driver_efficiency_daily  %d rows", n)

    n = await aggregate_telemetry_hourly(account_id)
    logger.info("  vehicle_telemetry_hourly %d rows", n)


async def main(argv: list[str]) -> int:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--account", type=int, help="Backfill a single account only.")
    p.add_argument("--skip-events", action="store_true",
                   help="Skip safety_event_log (slow on first run).")
    p.add_argument("--skip-efficiency", action="store_true",
                   help="Skip driver_efficiency_daily (slow on first run).")
    args = p.parse_args(argv)

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )

    await init_services()

    if args.account is not None:
        await _run_for(
            args.account,
            skip_events=args.skip_events,
            skip_efficiency=args.skip_efficiency,
        )
        return 0

    accounts = await get_platform_db().list_accounts(active_only=True)
    logger.info("Backfilling %d active accounts", len(accounts))
    for acc in accounts:
        try:
            await _run_for(
                acc.id,
                skip_events=args.skip_events,
                skip_efficiency=args.skip_efficiency,
            )
        except Exception:
            logger.exception("backfill failed for account %d \u2014 continuing", acc.id)
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main(sys.argv[1:])))
