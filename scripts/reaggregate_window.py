"""Rebuild the warehouse hourly + daily tiers over an explicit day range.

The operator-facing repair tool: point it at the days that are missing or
wrong and it re-derives them from the data still on hand (5-min snapshots
for the hourly tier, hourly buckets for the daily one).  Unlike the
30-day ``backfill_aggregations`` sweep, the window is stated, so nothing
outside it is touched.

Both tiers upsert, so re-running is safe and converges.  What no longer
has a source is left as-is rather than zeroed: hours whose snapshots have
aged out (7-day retention) produce no row, and previously banked
end-of-day readings survive.

Usage::

    python -m scripts.reaggregate_window --account 10000001 \\
        --start 2026-07-28 --end 2026-07-28
    python -m scripts.reaggregate_window --account 10000001 \\
        --start 2026-07-28 --end 2026-07-28 --dry-run

``--dry-run`` reports the coverage the rebuild would draw on — snapshots
per day and the tier rows that exist now — without writing anything.
"""

from __future__ import annotations

import argparse
import asyncio
import logging
import sys
from datetime import datetime, timedelta, timezone

from dotenv import load_dotenv

load_dotenv()

from features.vehicles.warehouse.aggregator import reaggregate_window  # noqa: E402
from infra.services import get_tenant_db  # noqa: E402
from infra.startup import initialize as init_services  # noqa: E402


logger = logging.getLogger("warehouse.reaggregate")


def _parse_day(value: str) -> datetime:
    try:
        return datetime.strptime(value, "%Y-%m-%d").replace(tzinfo=timezone.utc)
    except ValueError as exc:  # argparse renders this as a usage error
        raise argparse.ArgumentTypeError(
            f"{value!r} is not a YYYY-MM-DD date"
        ) from exc


async def _report_coverage(account_id: int, first: datetime, last: datetime) -> None:
    """Print what each day in the window has to rebuild FROM."""
    tenant = await get_tenant_db(account_id)
    if tenant is None:
        logger.error("account %d has no tenant database", account_id)
        return

    day = first
    while day <= last:
        label = day.strftime("%Y-%m-%d")
        nxt = (day + timedelta(days=1)).strftime("%Y-%m-%d")
        cur = await tenant._db.execute(
            "SELECT COUNT(DISTINCT vehicle_id), COUNT(*) "
            "FROM vehicle_state_minute "
            "WHERE account_id = ? AND captured_at >= ? AND captured_at < ?",
            (account_id, label, nxt),
        )
        snap_vehicles, snap_rows = (await cur.fetchone()) or (0, 0)

        counts: dict[str, int] = {}
        for grain, table in (("hourly", "vehicle_state_hour"),
                             ("daily", "vehicle_state_day")):
            cur = await tenant._db.execute(
                f"SELECT COUNT(DISTINCT vehicle_id) FROM {table} "
                "WHERE account_id = ? "
                "AND bucket_start >= ? AND bucket_start < ?",
                (account_id, label, nxt),
            )
            counts[grain] = ((await cur.fetchone()) or (0,))[0]

        logger.info(
            "%s  snapshots=%d rows/%d vehicles  ->  hourly=%d vehicles, "
            "daily=%d vehicles",
            label, snap_rows, snap_vehicles, counts["hourly"], counts["daily"],
        )
        day += timedelta(days=1)


async def main(argv: list[str]) -> int:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--account", type=int, required=True)
    p.add_argument("--start", type=_parse_day, required=True,
                   help="First UTC day to rebuild (YYYY-MM-DD, inclusive).")
    p.add_argument("--end", type=_parse_day,
                   help="Last UTC day to rebuild (inclusive). Defaults to --start.")
    p.add_argument("--dry-run", action="store_true",
                   help="Report available source data; write nothing.")
    p.add_argument("--rebuild-unbacked-days", action="store_true",
                   help="Also re-sum days the 5-min tier no longer covers. "
                        "Only after a history backfill has restored their "
                        "hours — otherwise this republishes the hourly tier "
                        "over a daily row that may be the better copy.")
    args = p.parse_args(argv)

    first, last = args.start, args.end or args.start
    if last < first:
        p.error("--end is before --start")

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    await init_services()

    span = (last - first).days + 1
    if args.dry_run:
        logger.info("DRY RUN — account %d, %d day(s)", args.account, span)
        await _report_coverage(args.account, first, last)
        return 0

    logger.info(
        "Rebuilding account %d: %s .. %s (%d day(s))",
        args.account, first.strftime("%Y-%m-%d"), last.strftime("%Y-%m-%d"), span,
    )
    result = await reaggregate_window(
        args.account, first_day=first, last_day=last,
        rebuild_unbacked_days=args.rebuild_unbacked_days,
    )
    logger.info(
        "Done — %d hours -> %d hourly rows, %d days -> %d daily rows",
        result["hours"], result["hourly_rows"],
        result["days"], result["daily_rows"],
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main(sys.argv[1:])))
