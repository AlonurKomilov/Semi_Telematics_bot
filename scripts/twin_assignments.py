"""Count same-number vehicle twins and the assignments that hit them.

Read-only.  A unit number is a reusable LABEL — two live trucks can share
"103" across a customer's companies — but a Vehicle-Access assignment in
``driver_trucks`` is a bare ``truck_num`` string, so an assignment of
"103" cannot say which one and every twin joins the scope.  Before the
assignment gains a ``registry_id`` this reports how big that is: which
accounts have twins, and which existing assignments are ambiguous (the
ones a backfill must leave NULL for a human to pick).

Usage::

    python3 -m scripts.twin_assignments --account 10000001
    python3 -m scripts.twin_assignments --all
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

logger = logging.getLogger("scope.twin_assignments")


async def _account_ids() -> list[int]:
    pdb = get_platform_db()
    cur = await pdb._db.execute("SELECT id FROM accounts ORDER BY id")
    return [int(r[0]) for r in await cur.fetchall()]


async def report(account_id: int) -> tuple[int, int, int]:
    tenant = await get_tenant_db(account_id)
    if tenant is None:
        logger.warning("account %s: no tenant database", account_id)
        return 0, 0, 0
    cur = await tenant._db.execute(
        "SELECT lower(unit_number), company_code, id, is_active FROM vehicles "
        "WHERE account_id = ? AND COALESCE(unit_number, '') <> ''",
        (account_id,),
    )
    by_unit: dict[str, list[tuple[str, int, bool]]] = defaultdict(list)
    for unit, co, vid, active in await cur.fetchall():
        by_unit[str(unit)].append((str(co or ""), int(vid), bool(active)))
    twins_any = {u: rows for u, rows in by_unit.items() if len(rows) > 1}
    twins_live = {u: [r for r in rows if r[2]] for u, rows in twins_any.items()
                  if sum(1 for r in rows if r[2]) > 1}

    pdb = get_platform_db()
    cur = await pdb._db.execute(
        "SELECT user_id, truck_num FROM driver_trucks WHERE account_id = ?",
        (account_id,),
    )
    assignments = [(int(u), str(t or "").strip().lower()) for u, t in await cur.fetchall()]
    ambiguous = [(u, t) for u, t in assignments if t in twins_any]

    logger.info("account %s: %d units, %d twin units (%d with 2+ LIVE), "
                "%d assignments, %d ambiguous",
                account_id, len(by_unit), len(twins_any), len(twins_live),
                len(assignments), len(ambiguous))
    for u, rows in sorted(twins_any.items()):
        logger.info("  unit %-10s -> %s", u,
                    ", ".join(f"{co or '(no co)'}#{vid}{'' if act else '†'}" for co, vid, act in rows))
    for u, t in ambiguous:
        logger.info("  AMBIGUOUS assignment user=%s truck=%s", u, t)
    return len(twins_any), len(twins_live), len(ambiguous)


async def main(argv: list[str]) -> int:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--account", type=int)
    p.add_argument("--all", action="store_true")
    args = p.parse_args(argv)
    if not args.account and not args.all:
        p.error("--account N or --all")
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
    await init_services()
    ids = [args.account] if args.account else await _account_ids()
    tot = [0, 0, 0]
    for aid in ids:
        r = await report(aid)
        for i in range(3):
            tot[i] += r[i]
    logger.info("TOTAL: %d twin units, %d with 2+ live, %d ambiguous assignments across %d accounts",
                tot[0], tot[1], tot[2], len(ids))
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main(sys.argv[1:])))
