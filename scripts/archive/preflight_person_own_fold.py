"""Pre-flight for the own→view fold of the person-subject flags.

Report only — nothing is written.  Run BEFORE the fold changes any
reader, seed or stored row:

    python3 -m scripts.preflight_person_own_fold [--account N]

Two findings, both intent the fold must not guess at:

  staff_own_only    a non-driver grant row holds a `*_own` flag and NOT
                    the feature's wide verb.  The fold turns own into
                    view, and staff width is 'all' — that row WIDENS.
  driver_holds_wide a driver grant row holds the wide verb.  Today the
                    loads router reads "holds view" as account-wide;
                    after the fold width is the role's, so that row
                    NARROWS to the driver's own rows.

Zero rows: the fold is intent-neutral, proceed.  Any row: decide it
with the owner first.
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

from infra.startup import initialize as init_services  # noqa: E402

logger = logging.getLogger("permissions.preflight_person_own_fold")


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
    p.add_argument("--account", type=int, default=None, help="Only this account id.")
    args = p.parse_args(argv)

    from capabilities.permissions.fold import plan_own_preflight

    await init_services()
    from infra.platform import get_platform_db
    pdb = get_platform_db()

    accounts = await pdb.list_accounts(active_only=False)
    if args.account is not None:
        accounts = [a for a in accounts if int(a.id) == args.account]

    rows_seen, findings = 0, 0
    print(f"{'account':>9}  {'key':<20} {'company':<8} {'flag':<26} finding")
    for acct in accounts:
        for key, company_id, stored in await _rows_for(pdb, int(acct.id)):
            rows_seen += 1
            for flag, kind in plan_own_preflight(key, stored):
                findings += 1
                print(f"{acct.id:>9}  {key:<20} {str(company_id or '-'):<8} {flag:<26} {kind}")

    print(f"\n{rows_seen} stored row(s) read, {findings} finding(s).")
    print("Report only — nothing written." if findings else
          "Report only — nothing written.  Zero findings: the fold is intent-neutral.")
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main(sys.argv[1:])))
