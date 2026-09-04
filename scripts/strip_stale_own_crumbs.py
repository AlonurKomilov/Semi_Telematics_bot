#!/usr/bin/env python3
"""Hygiene before the person fold: sweep stale ``*_own`` crumbs out of
stored grant rows.

The same residue the pair sweep removed, one family later.  The
recruiter seed carried the own-record baseline — own risk summary, own
paystubs, own coaching, own documents — until 327bf160 (2026-07-27)
turned them off in the SAME commit that turned off the ``*_vehicle``
crumbs.  The seed changed; the ``role_permissions`` rows materialised
from the old seed never did.  They have kept granting what the seed no
longer does, invisibly, ever since.

Why it matters now: the person fold turns an own flag into the
feature's VIEW verb, and staff width is 'all'.  Left in place, this
residue would hand every recruiter row an account-wide view of
paystubs, driver documents, coaching and risk summaries — as if an
owner had chosen it.

The decision (``capabilities/permissions/fold.stale_own_crumbs``) is
deliberately narrow: an own key stored True, for a feature the CURRENT
seed grants neither half of, on a row with no wide grant for it.  A
seed that still grants the own half — the fleet role's risk summary —
is a live default and is never touched.

DRY RUN (default) prints, per (account, storage key), the keys that
would be removed.  ``--apply`` removes them (the row falls back to the
seed for those keys), trail-logs each row on its account, and leaves
every other key exactly as stored.  ``--role`` defaults to recruiter —
the diagnosed case; widen it only after a dry-run of that role reads
like residue too.  ``--account N`` restricts either mode.

Permission caches in running API workers expire by TTL; a restart is
not required.

    python3 -m scripts.strip_stale_own_crumbs             # dry run
    python3 -m scripts.strip_stale_own_crumbs --apply     # after review
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

logger = logging.getLogger("permissions.strip_stale_own_crumbs")


async def _rows_for(platform_db, account_id: int, role: str):
    """Stored rows for one base role: the role itself and its tiers."""
    async with platform_db.acquire() as conn:
        cur = await conn.execute(
            "SELECT role, company_id, permissions FROM role_permissions "
            "WHERE account_id = ? ORDER BY role, company_id",
            (account_id,),
        )
        rows = await cur.fetchall()
    out = []
    for r in rows:
        key = r[0]
        if key != role and not key.startswith(role + "__"):
            continue
        try:
            out.append((key, r[1], json.loads(r[2] or "{}")))
        except Exception:
            logger.warning("account %s key %s: unreadable grant JSON, skipped", account_id, key)
    return out


async def _trail(account_id: int, key: str, company_id, removed: list[str], ctx) -> None:
    from capabilities.activity_trail.recorder import record_simple
    tdb = await get_tenant_db(account_id)
    await record_simple(
        tdb, account_id, None, "stale_grant_crumbs_swept", "role", key,
        changes={"removed_keys": removed},
        context=ctx("person fold hygiene (scripts/strip_stale_own_crumbs.py)",
                    company_id=company_id),
        note="person fold hygiene: *_own crumbs the seed no longer grants",
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
    args = p.parse_args(argv)

    from capabilities.permissions.fold import stale_own_crumbs, system_trail_context

    await init_services()
    from infra.platform import get_platform_db
    pdb = get_platform_db()

    accounts = await pdb.list_accounts(active_only=False)
    if args.account is not None:
        accounts = [a for a in accounts if int(a.id) == args.account]

    plan = []
    print(f"{'account':>9}  {'key':<20} {'company':<8} remove")
    for acct in accounts:
        for key, company_id, stored in await _rows_for(pdb, int(acct.id), args.role):
            crumbs = stale_own_crumbs(key, stored)
            if not crumbs:
                continue
            cleaned = {k: v for k, v in stored.items() if k not in crumbs}
            plan.append((int(acct.id), key, company_id, cleaned, crumbs))
            print(f"{acct.id:>9}  {key:<20} {str(company_id or '-'):<8} {', '.join(crumbs)}")

    print(f"\n{len(plan)} row(s) would be swept.")
    if not args.apply:
        print("Dry run — nothing written.  Re-run with --apply after review.")
        return 0

    swept, trail_failed = 0, 0
    for account_id, key, company_id, cleaned, crumbs in plan:
        await pdb.set_role_permissions(account_id, key, cleaned, updated_by=0,
                                       company_id=company_id)
        swept += 1
        try:
            await _trail(account_id, key, company_id, crumbs, system_trail_context)
        except Exception:
            trail_failed += 1
            logger.warning("account %s key %s: swept, trail failed", account_id, key, exc_info=True)

    print(f"Applied: {swept} row(s) swept.")
    if trail_failed:
        print(f"WARNING: {trail_failed} trail event(s) failed — the grants ARE clean; "
              f"re-run is safe (it plans nothing).")
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main(sys.argv[1:])))
