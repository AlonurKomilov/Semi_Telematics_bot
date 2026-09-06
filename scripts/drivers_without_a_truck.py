"""Who is about to go blind — read-only, run before the fail-closed deploy.

``deps.get_user_vehicle_scope`` used to answer "unrestricted" for a
driver holding no truck assignment, so such a person saw every vehicle
in the account.  That is now an EMPTY scope: they see nothing.

The change is correct and it is also visible, so it must not be a
surprise.  This lists exactly the people it lands on, per account, and
writes nothing.

    python3 -m scripts.drivers_without_a_truck                # every account
    python3 -m scripts.drivers_without_a_truck --account 10000001

An empty report means the deploy is a no-op for live users.  A non-empty
one is a decision: assign each person a truck, or accept that they open
an empty app until somebody does.

A driver counts as ASSIGNED when either source of truth names a truck —
a ``driver_trucks`` row, or the legacy ``users.truck_num`` column that
invite redemption still writes before the junction row exists.  Both are
checked because ``deps.get_user_vehicle_nums`` checks both; a report
that consulted only one would understate the blast radius in the
direction that matters.
"""

from __future__ import annotations

import argparse
import asyncio
import os
import sys

import asyncpg
from dotenv import load_dotenv

load_dotenv()

QUERY = """
SELECT u.id,
       u.account_id,
       a.name                        AS account_name,
       COALESCE(u.display_name, '')  AS display_name,
       COALESCE(u.email, '')         AS email,
       COALESCE(u.is_active, TRUE)   AS is_active
  FROM users u
  LEFT JOIN accounts a ON a.id = u.account_id
 WHERE u.role = 'driver'
   AND COALESCE(u.truck_num, '') = ''
   AND NOT EXISTS (SELECT 1 FROM driver_trucks dt WHERE dt.user_id = u.id)
   {account_clause}
 ORDER BY u.account_id, u.id
"""


async def run(account: int | None) -> int:
    db_url = os.environ.get("DATABASE_URL", "").strip()
    if not db_url.startswith(("postgresql://", "postgres://")):
        sys.stderr.write("ERROR: DATABASE_URL is not a Postgres DSN.  Set it in .env first.\n")
        return 2

    conn = await asyncpg.connect(db_url)
    try:
        if account is None:
            rows = await conn.fetch(QUERY.format(account_clause=""))
        else:
            rows = await conn.fetch(
                QUERY.format(account_clause="AND u.account_id = $1"), account)
    finally:
        await conn.close()

    print("READ-ONLY.  Nothing was written.\n")
    if not rows:
        where = "any account" if account is None else f"account {account}"
        print(f"No driver in {where} is missing a truck assignment.")
        print("The fail-closed change lands on nobody — deploy is a no-op for live users.")
        return 0

    active = [r for r in rows if r["is_active"]]
    print(f"{len(rows)} driver(s) with NO truck assignment "
          f"({len(active)} active).  Each currently sees EVERY vehicle in their")
    print("account and will see NONE after the change.\n")
    header = f"{'user id':>8}  {'account':>9}  {'active':>6}  name / email"
    print(header)
    print("-" * len(header))
    for r in rows:
        who = r["display_name"] or r["email"] or "(no name)"
        print(f"{r['id']:>8}  {r['account_id']:>9}  "
              f"{'yes' if r['is_active'] else 'no':>6}  {who}"
              f"{'  [' + (r['account_name'] or '') + ']' if r['account_name'] else ''}")
    print("\nFor each ACTIVE one, either assign a truck (Drivers → the person →")
    print("trucks) or accept the empty app.  An inactive one cannot sign in and")
    print("needs no decision.")
    return 0


def main() -> int:
    p = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--account", type=int,
                   help="one account id (PTG is 10000001); omit for every account")
    args = p.parse_args()
    return asyncio.run(run(args.account))


if __name__ == "__main__":
    raise SystemExit(main())
