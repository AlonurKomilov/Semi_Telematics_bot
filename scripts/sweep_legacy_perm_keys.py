#!/usr/bin/env python3
"""Rewrite stored role_permissions rows from legacy keys to canonical.

The verb/scope migration's flip made the canonical grammar physical
and left every legacy name as an alias; rows written before the flip
still carry the old keys, which the resolver maps on every read.  This
sweep rewrites them once, so the alias layer can be deleted.

DRY RUN (default) prints one line per (account, storage key, company)
that still carries a legacy key: how many keys leave, and any
COLLISION — a legacy key and its canonical key both present with
different values, which the OR rule resolved (the effective grant does
not change; reads already applied the same rule).  Writes nothing.

    --apply     rewrites the rows (idempotent: a canonical row is
                skipped), trail-logs each rewrite on its account.
    --account N restricts either mode.

    python3 -m scripts.sweep_legacy_perm_keys            # dry run
    python3 -m scripts.sweep_legacy_perm_keys --apply    # after review
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

logger = logging.getLogger("permissions.sweep_legacy_perm_keys")


async def _rows_for(platform_db, account_id: int):
    async with platform_db.acquire() as conn:
        cur = await conn.execute(
            "SELECT role, company_id, permissions FROM role_permissions "
            "WHERE account_id = ? ORDER BY role, company_id",
            (account_id,),
        )
        rows = await cur.fetchall()
    out = []
    for r in rows:
        try:
            out.append((r[0], r[1], json.loads(r[2] or "{}")))
        except Exception:
            logger.warning("account %s key %s: unreadable grant JSON, skipped", account_id, r[0])
    return out


async def main(argv: list[str]) -> int:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--apply", action="store_true", help="Rewrite the rows (default: report only).")
    p.add_argument("--account", type=int, default=None, help="Only this account id.")
    args = p.parse_args(argv)

    from capabilities.permissions.fold import plan_row_sweep, system_trail_context

    await init_services()
    from infra.platform import get_platform_db
    pdb = get_platform_db()

    accounts = await pdb.list_accounts(active_only=False)
    if args.account is not None:
        accounts = [a for a in accounts if int(a.id) == args.account]

    plan = []
    print(f"{'account':>9}  {'key':<20} {'company':<8} {'legacy keys':>11}  collisions (OR-resolved)")
    for acct in accounts:
        for key, company_id, stored in await _rows_for(pdb, int(acct.id)):
            canonical, removed, collisions = plan_row_sweep(stored)
            if not removed:
                continue
            print(f"{acct.id:>9}  {key:<20} {str(company_id or '-'):<8} {len(removed):>11}  {', '.join(collisions) or '—'}")
            plan.append((int(acct.id), key, company_id, canonical, removed, collisions))

    print(f"\n{len(plan)} row(s) would be rewritten.")
    if not args.apply:
        print("Dry run — nothing written.  Re-run with --apply after review.")
        return 0

    n = 0
    for account_id, key, company_id, canonical, removed, collisions in plan:
        await pdb.set_role_permissions(account_id, key, canonical, updated_by=0, company_id=company_id)
        n += 1
        try:
            from capabilities.activity_trail.recorder import record_simple
            tdb = await get_tenant_db(account_id)
            await record_simple(
                tdb, account_id, None, "legacy_grant_keys_swept", "role", key,
                changes={"removed_keys": removed, "collisions_or_resolved": collisions},
                context=system_trail_context(
                    "verb/scope migration: stored grant keys rewritten to canonical "
                    "(scripts/sweep_legacy_perm_keys.py)", company_id=company_id),
                note="verb/scope migration: legacy permission keys rewritten to canonical",
            )
            if hasattr(tdb, "commit"):
                await tdb.commit()
        except Exception:
            logger.warning("account %s key %s: rewritten, trail failed", account_id, key, exc_info=True)
    try:
        from capabilities.permissions.roles import invalidate_permissions_cache
        invalidate_permissions_cache()
    except Exception:
        pass
    print(f"Applied: {n} row(s) rewritten.")
    return 0


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
    raise SystemExit(asyncio.run(main(sys.argv[1:])))
