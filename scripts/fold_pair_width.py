#!/usr/bin/env python3
"""Pre-flight for pair death: where does each role's WIDTH go?

The ``*_all`` / ``*_vehicle`` permission pairs carry, per role and per
feature, whether that role sees ALL units or only ASSIGNED trucks.
Stage E of the verb/scope migration deletes those pairs.  Before it
may, every width an owner set through the Permissions matrix must
already live in ``role_vehicle_scope`` — otherwise a role narrowed to
its own trucks silently widens to the whole account on the day of the
flip.  That is a disclosure, and this script exists so it cannot
happen quietly.

DRY RUN (default) — reads every account's stored grants, resolves the
EFFECTIVE permission set per storage key (seed ⊕ stored, exactly as
the resolver does), classifies each of the ten paired features as
wide / narrow / none, folds per role (narrowest wins), and prints one
line per (account, role) that is NOT already the built-in default:

    CONSISTENT  → will write 'assigned'; lossless
    MIXED       → will write 'assigned'; names the features whose
                  account-wide width is LOST (the new model holds one
                  width per role) — the owner decides whether to give
                  it back per member afterwards

Writes nothing.  Run it, read it, then:

    --apply     writes the rows (idempotent: an existing equal row is
                skipped), trail-logs each write on the account.
    --account N restricts either mode to one account.

Owners are never scoped and are skipped.  Tier keys (``fleet__manager``)
and company-specific rows fold INTO their base role — role_vehicle_scope
has neither dimension — and a disagreement between them is reported
as MIXED.

Run from the repo root, either form:

    python3 -m scripts.fold_pair_width            # dry run
    python3 scripts/fold_pair_width.py --apply    # after review
"""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
import os
import sys
from dataclasses import asdict as _asdict

# Project root on sys.path, so ``python3 scripts/<name>.py`` works as
# well as ``python3 -m scripts.<name>`` — the house's two conventions.
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from dotenv import load_dotenv  # noqa: E402

load_dotenv()

from infra.services import get_tenant_db  # noqa: E402
from infra.startup import initialize as init_services  # noqa: E402

logger = logging.getLogger("permissions.fold_pair_width")


def _effective(seed, stored: dict):
    """seed ⊕ stored, the resolver's own merge (canonical keys mapped
    onto legacy fields BEFORE the unknown-key filter)."""
    from dataclasses import fields
    from capabilities.permissions.roles import FeatureSet, normalize_stored_perm_keys
    known = {f.name for f in fields(FeatureSet)}
    merged = {**_asdict(seed), **{k: v for k, v in
                                  normalize_stored_perm_keys(stored).items()
                                  if k in known}}
    return FeatureSet(**merged)


async def _rows_for(platform_db, account_id: int) -> list[tuple[str, int | None, dict]]:
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
            logger.warning("account %s key %s: unreadable grant JSON, skipped",
                           account_id, r[0])
    return out


async def _decide_account(platform_db, account_id: int):
    from capabilities.permissions.fold import classify_pairs, fold, merge_keys, seed_for_key
    per_role: dict[str, list] = {}
    for key, _company_id, stored in await _rows_for(platform_db, account_id):
        seed = seed_for_key(key)
        if seed is None:
            continue
        base = key.split("__", 1)[0]
        per_role.setdefault(base, []).append(
            fold(base, classify_pairs(_effective(seed, stored))))
    return {role: merge_keys(ds) for role, ds in per_role.items()}


async def main(argv: list[str]) -> int:
    p = argparse.ArgumentParser(description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--apply", action="store_true",
                   help="Write the folded widths (default: report only).")
    p.add_argument("--account", type=int, default=None,
                   help="Only this account id.")
    args = p.parse_args(argv)

    await init_services()
    from infra.platform import get_platform_db
    pdb = get_platform_db()

    accounts = await pdb.list_accounts(active_only=False)
    if args.account is not None:
        accounts = [a for a in accounts if int(a.id) == args.account]

    to_write: list[tuple[int, str, str, str, tuple]] = []
    print(f"{'account':>9}  {'role':<11} {'shape':<11} {'write':<9} {'narrow on':<34} lost width on")
    for acct in accounts:
        decisions = await _decide_account(pdb, int(acct.id))
        for role, d in sorted(decisions.items()):
            if d.write is None and d.shape in ("default", "no-access"):
                continue
            lost = ", ".join(d.lost) if d.lost else "—"
            narrow = ", ".join(d.narrow) if d.narrow else "—"
            print(f"{acct.id:>9}  {role:<11} {d.shape:<11} {d.write or '(no row)':<9} {narrow:<34} {lost}")
            if d.write is not None:
                to_write.append((int(acct.id), role, d.write, d.shape, d.lost))

    print(f"\n{len(to_write)} row(s) would be written.")
    if not args.apply:
        print("Dry run — nothing written.  Re-run with --apply after review.")
        return 0

    written = skipped = 0
    for account_id, role, width, shape, lost in to_write:
        if await pdb.get_role_vehicle_scope(account_id, role) == width:
            skipped += 1
            continue
        await pdb.set_role_vehicle_scope(account_id, role, width, updated_by=0)
        written += 1
        from capabilities.permissions.scope import invalidate_role_scope_cache
        invalidate_role_scope_cache(account_id)
        try:
            from capabilities.activity_trail.recorder import record_simple
            from capabilities.permissions.fold import system_trail_context
            tdb = await get_tenant_db(account_id)
            await record_simple(
                tdb, account_id, None, "role_vehicle_scope_fold", "role", role,
                changes={"role_vehicle_scope": {"from": None, "to": width}},
                context=system_trail_context(
                    "verb/scope migration: pair-width fold (scripts/fold_pair_width.py)",
                    shape=shape, lost_width_on=list(lost)),
                note="verb/scope migration: width folded out of the permission pairs",
            )
            # append_activity_events rides the caller's transaction and
            # never commits; a script has no request to ride.
            if hasattr(tdb, "commit"):
                await tdb.commit()
        except Exception:
            logger.warning("account %s role %s: written, trail failed", account_id, role,
                           exc_info=True)
    print(f"Applied: {written} written, {skipped} already equal (skipped).")
    return 0


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
    raise SystemExit(asyncio.run(main(sys.argv[1:])))
