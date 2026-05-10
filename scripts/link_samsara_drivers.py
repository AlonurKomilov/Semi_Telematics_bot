#!/usr/bin/env python3
"""Admin helper — link Telegram users (role=driver) to their Samsara
driver_id so the new /api/coaching/me and /api/payroll/me endpoints
can return per-driver data safely.

Why this script exists
----------------------
Before the Phase 3 refactor the backend inferred a Telegram user's
Samsara driver from the most-recent safety event on whatever truck
they were assigned to. That heuristic leaked data after a vehicle
re-assignment (driver A's payroll could surface for driver B).

The fix made `users.samsara_driver_id` a hard prerequisite — those
two endpoints now return empty when the field is NULL. Existing rows
were not back-filled by the migration; this script does the back-fill.

Usage
-----
    # 1. Inspect: list mapped + unmapped drivers and Samsara candidates
    python3 scripts/link_samsara_drivers.py --account-id 1

    # 2. Apply ad-hoc mappings (user_id=samsara_driver_id pairs)
    python3 scripts/link_samsara_drivers.py --account-id 1 \\
        --apply 42=8123456789,43=8123456790

    # 3. Bulk apply from CSV (header row required: user_id,samsara_driver_id)
    python3 scripts/link_samsara_drivers.py --account-id 1 \\
        --from-csv /tmp/driver_links.csv

    # 4. Auto-link by exact case-insensitive name match (dry-run by default;
    #    pass --apply with no value to commit auto-matches)
    python3 scripts/link_samsara_drivers.py --account-id 1 --auto

Safety
------
* No connections to Samsara unless the account actually needs it.
* `--apply` and `--from-csv` are no-ops without `--commit`. Without
  `--commit` the script prints the SQL that *would* run.
* Refuses to overwrite an already-set `samsara_driver_id` unless
  `--force` is passed.
"""

from __future__ import annotations

import argparse
import asyncio
import csv
import logging
import os
import sys
from typing import Optional

# Project root on sys.path
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from infra.services import get_client, get_platform_db
from infra.startup import initialize as init_services


logger = logging.getLogger("link_samsara_drivers")


def _norm(s: str) -> str:
    """Normalize a name for fuzzy matching: lowercase, collapse spaces."""
    return " ".join((s or "").strip().lower().split())


async def _fetch_samsara_drivers(account_id: int) -> list[dict]:
    """Pull every Samsara driver across every company in the account.

    Each entry gets a `_company_code` field so the operator can tell
    multi-company tenants apart visually.
    """
    multi = await get_client(account_id)
    out: list[dict] = []
    for code, c in multi.clients.items():
        try:
            drivers = await c.get_drivers()
        except Exception as e:
            logger.warning("get_drivers failed for company %s: %s", code, e)
            continue
        for d in drivers:
            d["_company_code"] = code
            out.append(d)
    return out


async def _do_inspect(account_id: int, *, samsara_drivers: list[dict]) -> None:
    """Print a side-by-side report: DB users vs Samsara drivers, with
    auto-suggested matches by exact case-insensitive name."""
    db = get_platform_db()
    users = await db.list_account_users(account_id)
    drivers = [u for u in users if getattr(u.role, "value", str(u.role)) == "driver"]

    by_norm = {_norm(d.get("name", "")): d for d in samsara_drivers}

    print(f"\n── Account {account_id} drivers (role=driver) ──")
    print(f"  DB drivers:     {len(drivers)}")
    print(f"  Samsara drivers: {len(samsara_drivers)}")
    print()
    print(f"{'user_id':>7}  {'tg_id':>11}  {'name':<28}  {'samsara_driver_id':<22}  status")
    print("-" * 95)

    unmapped = []
    for u in drivers:
        name = (u.display_name or u.full_name or "").strip()
        sid = u.samsara_driver_id or ""
        if sid:
            status = "linked"
        else:
            match = by_norm.get(_norm(name))
            if match:
                status = f"→ suggest {match['id']} ({match.get('_company_code', '')})"
                unmapped.append((u.id, match["id"]))
            else:
                status = "UNMAPPED — no name match"
        print(f"{u.id:>7}  {u.telegram_id:>11}  {name[:28]:<28}  {sid:<22}  {status}")

    if unmapped:
        print()
        print("Auto-link suggestions (run with --auto --commit to apply):")
        print("  " + ",".join(f"{uid}={sid}" for uid, sid in unmapped))

    print()
    print("── Samsara drivers in this account (id  company  name) ──")
    for d in sorted(samsara_drivers, key=lambda x: (x.get("_company_code", ""), x.get("name", ""))):
        print(f"  {str(d.get('id', '')):<14}  {d.get('_company_code', ''):<10}  {d.get('name', '')}")
    print()


async def _apply_mappings(
    account_id: int,
    pairs: list[tuple[int, str]],
    *,
    commit: bool,
    force: bool,
) -> int:
    """Apply user_id → samsara_driver_id pairs.  Returns count applied.

    Refuses to clobber an existing samsara_driver_id unless force=True.
    Refuses to touch a user that doesn't belong to *account_id*.
    """
    db = get_platform_db()
    applied = 0
    for user_id, samsara_id in pairs:
        u = await db.get_user(user_id)
        if not u:
            print(f"  ! user {user_id}: not found — skipping")
            continue
        if u.account_id != account_id:
            print(f"  ! user {user_id}: belongs to account {u.account_id}, not {account_id} — skipping")
            continue
        if u.samsara_driver_id and not force:
            print(
                f"  ~ user {user_id}: already linked to {u.samsara_driver_id} "
                f"(use --force to overwrite)"
            )
            continue
        action = "WOULD update" if not commit else "Updating"
        print(
            f"  {action} user {user_id} ({u.full_name or u.display_name}): "
            f"samsara_driver_id = {samsara_id}"
        )
        if commit:
            await db.update_user(u.id, samsara_driver_id=samsara_id)
            applied += 1
    return applied


def _parse_apply_arg(s: str) -> list[tuple[int, str]]:
    """Parse `--apply '42=8123,43=8124'` into [(42, '8123'), (43, '8124')]."""
    if not s:
        return []
    pairs: list[tuple[int, str]] = []
    for chunk in s.split(","):
        chunk = chunk.strip()
        if not chunk:
            continue
        if "=" not in chunk:
            raise SystemExit(f"--apply: malformed pair {chunk!r} (expected user_id=samsara_id)")
        uid, sid = chunk.split("=", 1)
        pairs.append((int(uid.strip()), sid.strip()))
    return pairs


def _parse_csv(path: str) -> list[tuple[int, str]]:
    """Parse a CSV with header row `user_id,samsara_driver_id`."""
    pairs: list[tuple[int, str]] = []
    with open(path, newline="") as f:
        reader = csv.DictReader(f)
        if not reader.fieldnames or "user_id" not in reader.fieldnames or "samsara_driver_id" not in reader.fieldnames:
            raise SystemExit(
                f"--from-csv: {path} must have header 'user_id,samsara_driver_id'"
            )
        for row in reader:
            try:
                pairs.append((int(row["user_id"]), str(row["samsara_driver_id"]).strip()))
            except (ValueError, KeyError):
                logger.warning("skipping malformed row: %r", row)
    return pairs


async def main_async(args: argparse.Namespace) -> int:
    await init_services()

    samsara_drivers: list[dict] = []
    needs_samsara = args.auto or not (args.apply or args.from_csv)
    if needs_samsara:
        samsara_drivers = await _fetch_samsara_drivers(args.account_id)

    # Default: inspect mode
    if not args.apply and not args.from_csv and not args.auto:
        await _do_inspect(args.account_id, samsara_drivers=samsara_drivers)
        return 0

    # Build the pair list from whichever flag(s) the operator passed.
    pairs: list[tuple[int, str]] = []
    if args.apply:
        pairs.extend(_parse_apply_arg(args.apply))
    if args.from_csv:
        pairs.extend(_parse_csv(args.from_csv))
    if args.auto:
        # Auto-match by exact case-insensitive name.
        db = get_platform_db()
        users = await db.list_account_users(args.account_id)
        drivers = [u for u in users if getattr(u.role, "value", str(u.role)) == "driver"]
        by_norm = {_norm(d.get("name", "")): d for d in samsara_drivers}
        for u in drivers:
            if u.samsara_driver_id and not args.force:
                continue
            name = (u.display_name or u.full_name or "").strip()
            match = by_norm.get(_norm(name))
            if match:
                pairs.append((u.id, str(match["id"])))

    if not pairs:
        print("No mappings to apply.")
        return 0

    print(f"\n── {'COMMIT' if args.commit else 'DRY-RUN'}: {len(pairs)} mapping(s) for account {args.account_id} ──")
    n = await _apply_mappings(
        args.account_id, pairs, commit=args.commit, force=args.force
    )
    print()
    if args.commit:
        print(f"Applied {n} mapping(s).")
    else:
        print(f"Dry-run: {n} mapping(s) would be applied.  Re-run with --commit to apply.")
    return 0


def main() -> None:
    p = argparse.ArgumentParser(
        description="Link Telegram users to Samsara driver IDs",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    p.add_argument("--account-id", type=int, required=True, help="Account to operate on")
    p.add_argument("--apply", help="Inline mappings: 42=8123,43=8124")
    p.add_argument("--from-csv", help="CSV file with header user_id,samsara_driver_id")
    p.add_argument(
        "--auto", action="store_true",
        help="Auto-link by exact case-insensitive name match",
    )
    p.add_argument(
        "--force", action="store_true",
        help="Overwrite an already-set samsara_driver_id",
    )
    p.add_argument(
        "--commit", action="store_true",
        help="Actually write to the DB (default is dry-run)",
    )
    p.add_argument("-v", "--verbose", action="store_true", help="Debug logging")
    args = p.parse_args()

    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s | %(message)s",
    )

    sys.exit(asyncio.run(main_async(args)))


if __name__ == "__main__":
    main()
