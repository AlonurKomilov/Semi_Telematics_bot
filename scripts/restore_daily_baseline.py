"""Put daily-tier miles back to a captured baseline.

A re-aggregation rebuilds each daily row by summing the hourly rows
underneath it.  That is right whenever those hours are still backed by
5-min snapshots — and wrong once they are not: hours that have aged past
snapshot retention can no longer be recomputed, so if anything ever
damaged them, re-summing copies the damage upward into a daily row that
was still holding the better number.

This restores `miles` (and any end-of-day odometer the baseline had) for
an explicit day range from a CSV captured BEFORE such a run.  Expected
CSV columns: vehicle_id, bucket_start, miles, odometer_eod.

Usage::

    python -m scripts.restore_daily_baseline --account 10000001 \\
        --csv /path/before.csv --start 2026-07-17 --end 2026-07-23 --dry-run
    python -m scripts.restore_daily_baseline --account 10000001 \\
        --csv /path/before.csv --start 2026-07-17 --end 2026-07-23
"""

from __future__ import annotations

import argparse
import asyncio
import csv
import logging
import sys

from dotenv import load_dotenv

load_dotenv()

from infra.services import get_tenant_db  # noqa: E402
from infra.startup import initialize as init_services  # noqa: E402


logger = logging.getLogger("warehouse.restore")


def _load(path: str, first: str, last: str) -> dict[tuple[str, str], dict]:
    rows: dict[tuple[str, str], dict] = {}
    with open(path, newline="") as fh:
        for r in csv.DictReader(fh):
            day = (r.get("bucket_start") or "").strip()
            vid = (r.get("vehicle_id") or "").strip()
            if not vid or not (first <= day <= last):
                continue
            rows[(vid, day)] = r
    return rows


async def main(argv: list[str]) -> int:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--account", type=int, required=True)
    p.add_argument("--csv", required=True, help="Baseline CSV captured before the run.")
    p.add_argument("--start", required=True, help="First UTC day to restore (YYYY-MM-DD).")
    p.add_argument("--end", required=True, help="Last UTC day to restore (inclusive).")
    p.add_argument("--dry-run", action="store_true",
                   help="Report what would change; write nothing.")
    args = p.parse_args(argv)

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    await init_services()

    baseline = _load(args.csv, args.start, args.end)
    if not baseline:
        logger.error("no baseline rows in %s within %s..%s",
                     args.csv, args.start, args.end)
        return 1

    tenant = await get_tenant_db(args.account)
    if tenant is None:
        logger.error("account %d has no tenant database", args.account)
        return 1

    cur = await tenant._db.execute(
        "SELECT vehicle_id, bucket_start, miles FROM vehicle_state_day "
        "WHERE account_id = ? "
        "AND bucket_start >= ? AND bucket_start <= ?",
        (args.account, args.start, args.end),
    )
    live = {
        (str(d["vehicle_id"]), str(d["bucket_start"])): float(d["miles"] or 0)
        for d in (dict(zip(("vehicle_id", "bucket_start", "miles"), r))
                  for r in await cur.fetchall())
    }

    changes: list[tuple[str, str, float, float, float | None]] = []
    for key, row in baseline.items():
        if key not in live:
            continue
        want = float(row.get("miles") or 0)
        have = live[key]
        if abs(want - have) <= 0.05:
            continue
        odo = row.get("odometer_eod")
        changes.append((key[0], key[1], have, want,
                        float(odo) if odo not in (None, "") else None))

    delta = sum(w - h for _, _, h, w, _ in changes)
    logger.info(
        "%d row(s) differ from baseline in %s..%s (net %+.1f miles)",
        len(changes), args.start, args.end, delta,
    )
    if args.dry_run:
        for vid, day, have, want, _ in changes[:15]:
            logger.info("  %s %s: %.1f -> %.1f", vid, day, have, want)
        if len(changes) > 15:
            logger.info("  … and %d more", len(changes) - 15)
        return 0
    if not changes:
        return 0

    for vid, day, _have, want, odo in changes:
        if odo is not None:
            await tenant._db.execute(
                "UPDATE vehicle_state_day SET miles = ?, odometer_eod = ? "
                "WHERE account_id = ? "
                "AND vehicle_id = ? AND bucket_start = ?",
                (want, odo, args.account, vid, day),
            )
        else:
            await tenant._db.execute(
                "UPDATE vehicle_state_day SET miles = ? "
                "WHERE account_id = ? "
                "AND vehicle_id = ? AND bucket_start = ?",
                (want, args.account, vid, day),
            )
    await tenant._db.commit()
    logger.info("Restored %d row(s).", len(changes))
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main(sys.argv[1:])))
