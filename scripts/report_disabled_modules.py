"""Which accounts have a department switched off — and which.

Report only; nothing is written.  Run before the restart that makes
the department mask real for the seven flags it never carried (loads,
carrier directory, parts, service tasks, truck anatomy, risk summary,
cost reports): every account listed here is one where those features'
API opens today and closes after — exactly what the switch promised.
An empty list means nobody notices.

    python3 -m scripts.report_disabled_modules
"""

from __future__ import annotations

import asyncio
import os
import sys
from collections import Counter

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from dotenv import load_dotenv  # noqa: E402

load_dotenv()

from infra.startup import initialize as init_services  # noqa: E402


async def main() -> int:
    from capabilities.permissions.modules import parse_disabled, TOGGLEABLE_MODULES

    await init_services()
    from infra.platform import get_platform_db
    pdb = get_platform_db()

    accounts = await pdb.list_accounts(active_only=False)
    per_module: Counter = Counter()
    rows = []
    for acct in accounts:
        off = parse_disabled(getattr(acct, "disabled_modules", "") or "")
        if not off:
            continue
        rows.append((int(acct.id), "active" if getattr(acct, "is_active", True) else "inactive", sorted(off)))
        for m in off:
            per_module[m] += 1

    print(f"{'account':>9}  {'state':<9} departments OFF")
    for aid, state, off in sorted(rows):
        print(f"{aid:>9}  {state:<9} {', '.join(off)}")
    print(f"\n{len(accounts)} account(s) read, {len(rows)} with a department off.")
    if rows:
        print("Per department: " + ", ".join(f"{m}={per_module[m]}" for m in TOGGLEABLE_MODULES if per_module[m]))
        # what a department switch closes — from the registry, so this
        # line cannot go stale when the mask changes again
        from capabilities.permissions.registry import ENTRIES
        for m in TOGGLEABLE_MODULES:
            if not per_module[m]:
                continue
            closes = sorted(e.id for e in ENTRIES if e.maskable and e.modules == {m})
            print(f"  {m} off closes (only-{m} features): {', '.join(closes) or '-'}")
    print("Report only — nothing written.")
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
