"""Pin existing Vehicle-Access assignments to ONE registry truck — where
that is not a guess.

``driver_trucks.registry_id`` is new and NULL on every existing row.
NULL means exactly what the row meant yesterday: the name, every truck
answering to it.  This sets the id only where the name resolves to
exactly ONE registry row (archived included — a scope is a permission,
and someone scoped to a retired truck must keep reaching its history).
A name with twins is left NULL for a human to pick in Team Management;
a name the registry does not know stays NULL as a name-only rung.
Nothing here ever chooses between twins.

Usage::

    python3 -m scripts.backfill_driver_trucks_registry_id --account 10000001 --dry-run
    python3 -m scripts.backfill_driver_trucks_registry_id --all
"""

from __future__ import annotations

import argparse
import asyncio
import logging
import sys
from collections import defaultdict

from dotenv import load_dotenv

load_dotenv()

from infra.platform import get_platform_db  # noqa: E402
from infra.services import get_tenant_db  # noqa: E402
from infra.startup import initialize as init_services  # noqa: E402

logger = logging.getLogger("scope.backfill_driver_trucks_registry_id")


async def run(account_id: int, dry_run: bool) -> tuple[int, int, int]:
    tenant = await get_tenant_db(account_id)
    pdb = get_platform_db()
    if tenant is None:
        return 0, 0, 0
    cur = await tenant._db.execute(
        "SELECT lower(unit_number), id FROM vehicles "
        "WHERE account_id = ? AND COALESCE(unit_number, '') <> ''",
        (account_id,),
    )
    by_unit: dict[str, list[int]] = defaultdict(list)
    for unit, vid in await cur.fetchall():
        by_unit[str(unit)].append(int(vid))
    cur = await pdb._db.execute(
        "SELECT id, user_id, truck_num FROM driver_trucks "
        "WHERE account_id = ? AND registry_id IS NULL",
        (account_id,),
    )
    pinned = twins = unknown = 0
    for row_id, user_id, truck in await cur.fetchall():
        ids = by_unit.get(str(truck or "").strip().lower(), [])
        if len(ids) == 1:
            pinned += 1
            logger.info("  %s user=%s %-10s -> registry #%s", "would pin" if dry_run else "pin",
                        user_id, truck, ids[0])
            if not dry_run:
                await pdb._db.execute(
                    "UPDATE driver_trucks SET registry_id = ? WHERE id = ? AND registry_id IS NULL",
                    (ids[0], row_id),
                )
        elif len(ids) > 1:
            twins += 1
            logger.info("  LEAVE   user=%s %-10s — %d twins, a human picks in Team Management",
                        user_id, truck, len(ids))
        else:
            unknown += 1
            logger.info("  LEAVE   user=%s %-10s — not in the registry (name-only rung)", user_id, truck)
    if not dry_run and pinned:
        await pdb._db.commit()
    logger.info("account %s: %d pinned, %d twins left, %d unknown left%s",
                account_id, pinned, twins, unknown, " (dry run)" if dry_run else "")
    return pinned, twins, unknown


async def main(argv: list[str]) -> int:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--account", type=int)
    p.add_argument("--all", action="store_true")
    p.add_argument("--dry-run", action="store_true")
    args = p.parse_args(argv)
    if not args.account and not args.all:
        p.error("--account N or --all")
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
    await init_services()
    if args.account:
        ids = [args.account]
    else:
        cur = await get_platform_db()._db.execute("SELECT id FROM accounts ORDER BY id")
        ids = [int(r[0]) for r in await cur.fetchall()]
    for aid in ids:
        await run(aid, args.dry_run)
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main(sys.argv[1:])))
