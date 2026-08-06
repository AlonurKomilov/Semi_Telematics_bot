"""Stamp ``registry_id`` onto existing warehouse history.

Migration 177 added the column; the ingest stamps it on everything
written from now on.  This walks what was written BEFORE — one
set-based UPDATE per table, driven from the registry's
``telematics_ref`` link, so each table is one statement rather than a
loop that re-scans for stragglers it can never resolve (rows whose
external id the registry simply does not know stay NULL by design and
are the orphan quarantine's business, not this script's).

Row-level UPDATEs block no readers and no concurrent ingest INSERTs;
run it any time.  Finish with VACUUM (ANALYZE) per table — never
VACUUM FULL, which takes an exclusive lock.

Usage::

    python3 -m scripts.backfill_registry_id --account 10000001 --dry-run
    python3 -m scripts.backfill_registry_id --account 10000001
"""

from __future__ import annotations

import argparse
import asyncio
import logging
import sys

from dotenv import load_dotenv

load_dotenv()

from infra.services import get_tenant_db  # noqa: E402
from infra.startup import initialize as init_services  # noqa: E402


logger = logging.getLogger("warehouse.backfill_registry_id")

# Every table migration 177 gave the column to.  vehicle_state is
# included for completeness even though the next ingest tick would
# stamp it anyway.
_TABLES = (
    "vehicle_state_live",
    "vehicle_state_minute",
    "vehicle_state_hour",
    "vehicle_state_day",
    "vehicle_state_week",
    "safety_event_log",
    "vehicle_health_snapshot",
    "vehicle_fault_snapshot",
    "vehicle_fault_detail",
    "weather_snapshot",
)


async def main(argv: list[str]) -> int:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--account", type=int, required=True)
    p.add_argument("--dry-run", action="store_true",
                   help="Report how many rows each table would gain; write nothing.")
    args = p.parse_args(argv)

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    await init_services()
    tenant = await get_tenant_db(args.account)
    if tenant is None:
        logger.error("account %d has no tenant database", args.account)
        return 1

    for table in _TABLES:
        if args.dry_run:
            cur = await tenant._db.execute(
                f"SELECT COUNT(*) FROM {table} t "
                f"WHERE t.account_id = ? AND t.registry_id IS NULL "
                f"AND EXISTS (SELECT 1 FROM vehicles v "
                f"            WHERE v.account_id = t.account_id "
                f"              AND v.telematics_ref = t.vehicle_id "
                f"              AND v.telematics_ref <> '')",
                (args.account,),
            )
            n = (await cur.fetchone())[0]
            logger.info("%-28s would resolve %s rows", table, n)
            continue

        cur = await tenant._db.execute(
            f"UPDATE {table} t SET registry_id = v.id "
            f"FROM vehicles v "
            f"WHERE t.account_id = ? AND t.registry_id IS NULL "
            f"  AND v.account_id = t.account_id "
            f"  AND v.telematics_ref = t.vehicle_id "
            f"  AND v.telematics_ref <> ''",
            (args.account,),
        )
        await tenant._db.commit()
        logger.info("%-28s resolved %s rows",
                    table, getattr(cur, "rowcount", "?"))

    if not args.dry_run:
        # Reclaim the churn the UPDATEs produced.  Plain ANALYZE-style
        # vacuum only — FULL would take an exclusive lock on hot tables.
        for table in _TABLES:
            try:
                await tenant._db.execute(f"VACUUM (ANALYZE) {table}")
            except Exception as e:
                logger.warning("VACUUM %s skipped: %s", table, e)
        logger.info("Done.")
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main(sys.argv[1:])))
