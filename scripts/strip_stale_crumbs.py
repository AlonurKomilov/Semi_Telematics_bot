#!/usr/bin/env python3
"""Hygiene before pair death: sweep stale ``*_vehicle`` crumbs out of
stored grant rows.

The fold pre-flight found every account carrying the same recruiter
row — narrow on all ten paired features — while the recruiter seed
opens none of them.  History explains it: the seed carried
``*_vehicle=True`` baseline crumbs until 327bf160, the cleanup
removed them from the SEED, and no migration ever swept the
``role_permissions`` rows materialised from the old seed.  Those rows
have kept granting what the seed no longer does, invisibly, ever
since — and the fold would have enshrined that residue as if an owner
had chosen it.

The decision (capabilities/permissions/fold.stale_narrow_crumbs) is
deliberately narrow: a narrow-half key stored True, for a pair the
CURRENT seed grants neither half of, with no wide grant on the row.  A
wide grant is someone's choice and is never touched.

DRY RUN (default) prints, per (account, storage key), the keys that
would be removed.  ``--apply`` removes them (the row falls back to the
seed for those keys), trail-logs each row on its account, and leaves
every other key exactly as stored.  ``--role`` defaults to recruiter —
the diagnosed case; widen it only after a dry-run of that role reads
like residue too.  ``--account N`` restricts either mode.

Permission caches in running API workers expire by TTL; a restart is
not required.

    python3 -m scripts.strip_stale_crumbs                 # dry run
    python3 -m scripts.strip_stale_crumbs --apply         # after review
"""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from dotenv import load_dotenv  # noqa: E402

load_dotenv()

from infra.services import get_tenant_db  # noqa: E402
from infra.startup import initialize as init_services  # noqa: E402

logger = logging.getLogger("permissions.strip_stale_crumbs")


async def _rows_for(platform_db, account_id: int, role: str):
    async with platform_db.acquire() as conn:
        cur = await conn.execute(
            "SELECT role, company_id, permissions FROM role_permissions "
            "WHERE account_id = ? AND (role = ? OR role LIKE ?) "
            "ORDER BY role, company_id",
            (account_id, role, f"{role}__%"),
        )
        rows = await cur.fetchall()
    out = []
    for r in rows:
        try:
            out.append((r[0], r[1], json.loads(r[2] or "{}")))
        except Exception:
            logger.warning("account %s key %s: unreadable grant JSON, skipped",
                           account_id, r[0])
    return out


async def _trail(account_id: int, key: str, company_id, removed: list[str], ctx) -> None:
    from capabilities.activity_trail.recorder import record_simple
    tdb = await get_tenant_db(account_id)
    await record_simple(
        tdb, account_id, None, "stale_grant_crumbs_swept", "role", key,
        changes={"removed_keys": removed},
        context=ctx("verb/scope migration hygiene (scripts/strip_stale_crumbs.py)",
                    company_id=company_id),
        note="verb/scope migration hygiene: *_vehicle crumbs the seed no longer grants",
    )
    # append_activity_events rides the caller's transaction and never
    # commits; a script has no request to ride.
    if hasattr(tdb, "commit"):
        await tdb.commit()


async def main(argv: list[str]) -> int:
    p = argparse.ArgumentParser(description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--apply", action="store_true", help="Remove the keys (default: report only).")
    p.add_argument("--account", type=int, default=None, help="Only this account id.")
    p.add_argument("--role", default="recruiter", help="Base role whose rows to sweep (default: recruiter).")
    p.add_argument("--trail-backfill", metavar="FILE", default=None,
                   help="Write ONLY the trail events for sweeps already applied, from a JSON "
                        "list of {account_id, key, company_id, removed_keys}.  Changes no grant.")
    args = p.parse_args(argv)

    from capabilities.permissions.fold import (
        seed_for_key, stale_narrow_crumbs, system_trail_context,
    )

    await init_services()
    from infra.platform import get_platform_db
    pdb = get_platform_db()

    if args.trail_backfill:
        # The first --apply swept its rows and then raised on every
        # trail write (no ``system`` context).  The grants are already
        # clean, so a re-run plans nothing; this records what happened
        # from the run's own printed plan.
        with open(args.trail_backfill, encoding="utf-8") as fh:
            items = json.load(fh)
        n = 0
        for it in items:
            await _trail(int(it["account_id"]), str(it["key"]), it.get("company_id"),
                         [str(k) for k in it["removed_keys"]], system_trail_context)
            n += 1
        print(f"Trail backfill: {n} event(s) written.")
        return 0

    accounts = await pdb.list_accounts(active_only=False)
    if args.account is not None:
        accounts = [a for a in accounts if int(a.id) == args.account]

    plan: list[tuple[int, str, int | None, dict, list[str]]] = []
    print(f"{'account':>9}  {'key':<20} {'company':<8} remove")
    for acct in accounts:
        for key, company_id, stored in await _rows_for(pdb, int(acct.id), args.role):
            seed = seed_for_key(key)
            if seed is None:
                continue
            crumbs = stale_narrow_crumbs(seed, stored)
            if not crumbs:
                continue
            print(f"{acct.id:>9}  {key:<20} {str(company_id or '-'):<8} {', '.join(crumbs)}")
            plan.append((int(acct.id), key, company_id, stored, crumbs))

    print(f"\n{len(plan)} row(s) would be swept.")
    if not args.apply:
        print("Dry run — nothing written.  Re-run with --apply after review.")
        return 0

    swept = 0
    for account_id, key, company_id, stored, crumbs in plan:
        cleaned = {k: v for k, v in stored.items() if k not in crumbs}
        await pdb.set_role_permissions(account_id, key, cleaned, updated_by=0,
                                       company_id=company_id)
        swept += 1
        try:
            await _trail(account_id, key, company_id, crumbs, system_trail_context)
        except Exception:
            logger.warning("account %s key %s: swept, trail failed", account_id, key,
                           exc_info=True)
    try:
        from capabilities.permissions.roles import invalidate_permissions_cache
        invalidate_permissions_cache()
    except Exception:
        pass
    print(f"Applied: {swept} row(s) swept.")
    return 0


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
    raise SystemExit(asyncio.run(main(sys.argv[1:])))
