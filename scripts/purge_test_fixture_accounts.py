#!/usr/bin/env python3
"""One-time operator script: hard-delete the six empty test-fixture
accounts (S4/Req/Theme/HdrBg/Legal/Surf Co — June 2026 recruiter-brand
test leftovers) via the SAME purge the 90-day retention scheduler runs.

Safety rails:
  • hard-coded id list — nothing else is touchable
  • refuses any account that has users (a fixture has none)
  • prints per-table row counts for the audit trail

Run:  python3 scripts/purge_test_fixture_accounts.py --yes
"""

import asyncio
import sys

from dotenv import load_dotenv

load_dotenv()

FIXTURES = [10000010, 10000011, 10000012, 10000013, 10000014, 10000015]


async def main() -> None:
    from adapters.storage import Database
    db = Database()
    await db.initialize()
    try:
        for acct_id in FIXTURES:
            row = await (await db.execute(
                "SELECT name FROM accounts WHERE id = ?", (acct_id,))
            ).fetchone()
            if row is None:
                print(f"skip {acct_id}: already gone")
                continue
            cur = await db.execute(
                "SELECT count(*) FROM users WHERE account_id = ?", (acct_id,))
            n_users = (await cur.fetchone())[0]
            if n_users != 0:
                print(f"SKIP {acct_id} ({row[0]}): has {n_users} users — "
                      "not a fixture!")
                continue
            deleted = await db.purge_account_data(acct_id)
            print(f"purged {acct_id} ({row[0]}): {deleted}")
    finally:
        await db.close()


if __name__ == "__main__":
    if "--yes" not in sys.argv:
        print(__doc__)
        print("Refusing without --yes (irreversible).")
        sys.exit(1)
    asyncio.run(main())
