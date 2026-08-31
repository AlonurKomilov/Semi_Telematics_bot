"""One-shot relocation: put everything about a truck under that truck.

    {COMPANY}/vehicles/110/          →  {COMPANY}/vehicles/110/documents/
    {COMPANY}/work-orders/2026/…/    →  {COMPANY}/vehicles/110/work-orders/…

New writes already land in the new shape; this moves what is already on
disk and rewrites the rows that point at it, so the two never disagree.

DRY-RUN BY DEFAULT.  Nothing moves until ``--apply``:

    python3 -m scripts.relocate_vehicle_folders           # show the plan
    python3 -m scripts.relocate_vehicle_folders --apply   # move + update rows

Order matters and is not negotiable: the FILE moves first, the ROW is
rewritten second.  A row pointing at a file that has not moved yet is a
download that 404s until the next line runs; a row still pointing at the
old path after the file moved is a download that 404s FOREVER, and
nothing in the product would tell you.  Each pair is committed
individually for the same reason — a crash halfway leaves a consistent
prefix, not a torn set.

Idempotent: a row already carrying the new path is skipped, so re-running
after an interruption resumes rather than double-moving.

SCOPE: vehicle DOCUMENTS only.  Work orders written from now on land
under their truck, but the existing invoice folders are not moved here.
They are years of historical records addressed by opaque stored
locators (``work_order_attachments.file_path`` is whatever the store
returned — a relative path on disk, a file id on Drive), so relocating
them is a different and riskier operation that deserves its own pass
once this one has been run and checked.  Saying so beats a script that
quietly does half of what its name promises.

Work orders with NO vehicle keep the dated tree in any case — that tree
is now what they are FOR.
"""

from __future__ import annotations

import argparse
import asyncio
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from dotenv import load_dotenv  # noqa: E402

load_dotenv()

from adapters.storage.object_storage import (  # noqa: E402
    get_object_storage_for_account,
)
from infra.services import get_tenant_db  # noqa: E402
from infra.startup import initialize as init_services  # noqa: E402
from capabilities.object_storage.paths import (  # noqa: E402
    resolve_company_folder,
)
from features.vehicles.documents.paths import vehicle_docs_bucket  # noqa: E402


class Plan:
    """What we intend to do, printed before anything is done."""

    def __init__(self) -> None:
        # (doc_id, vehicle_id, src, dst)
        self.doc_moves: list[tuple[int, int, str, str]] = []
        self.skipped: list[str] = []

    def render(self) -> None:
        print(f"\n  vehicle documents to relocate : {len(self.doc_moves)}")
        for _id, _vid, src, dst in self.doc_moves[:20]:
            print(f"    {src}\n      → {dst}")
        if len(self.doc_moves) > 20:
            print(f"    … and {len(self.doc_moves) - 20} more")
        if self.skipped:
            print(f"\n  skipped ({len(self.skipped)}):")
            for s in self.skipped[:20]:
                print(f"    {s}")


async def build_plan(db, account_id: int) -> Plan:
    plan = Plan()

    # ── vehicle documents ──────────────────────────────────────────
    rows = await db.list_account_vehicle_documents(account_id)
    for r in rows:
        company = r.get("company_code") or ""
        unit = r.get("unit_number") or ""
        if not unit:
            plan.skipped.append(f"doc {r['id']}: no unit number")
            continue
        try:
            folder = await resolve_company_folder(db, account_id, company)
        except Exception as e:
            plan.skipped.append(f"doc {r['id']}: company unresolved ({e})")
            continue
        want = vehicle_docs_bucket(folder, unit)
        have = r.get("bucket") or ""
        if have == want:
            continue                       # already relocated
        if not have:
            plan.skipped.append(f"doc {r['id']}: no stored bucket")
            continue
        plan.doc_moves.append(
            (int(r["id"]), int(r["vehicle_id"]), have, want))

    return plan


async def apply_plan(db, account_id: int, plan: Plan) -> None:
    store = await get_object_storage_for_account(account_id, db)
    moved = failed = 0
    # One (src, dst) can cover several documents — the whole folder
    # moves once, then every row pointing at it is rewritten.
    by_folder: dict[tuple[str, str], list[tuple[int, int]]] = {}
    for doc_id, vehicle_id, src, dst in plan.doc_moves:
        by_folder.setdefault((src, dst), []).append((doc_id, vehicle_id))

    for (src, dst), docs in by_folder.items():
        try:
            # FILE first, ROW second — see the module docstring.
            if not store.move_folder(src, dst):
                print(f"    · nothing at {src} (already moved?) — rows left alone")
                continue
            for _doc_id, vehicle_id in docs:
                await db.move_vehicle_documents_bucket(
                    account_id, vehicle_id, src, dst)
            moved += 1
        except Exception as e:                       # noqa: BLE001
            failed += 1
            print(f"    ! {src}: {e}")
    print(f"\n  moved {moved} folder(s), {failed} failed")


async def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--account", type=int, required=True,
                        help="account id to relocate")
    parser.add_argument("--apply", action="store_true",
                        help="actually move files and rewrite rows")
    args = parser.parse_args()

    # The same way every per-account script opens the tenant DB: bring
    # the services up, then ask for THIS account's handle — a raw
    # Database() would miss the pool, the RLS account pin and the
    # object-store wiring the move depends on.
    await init_services()
    db = await get_tenant_db(args.account)
    if db is None:
        raise SystemExit(f"account {args.account}: no tenant DB")

    plan = await build_plan(db, args.account)
    plan.render()
    mode = ("APPLIED" if args.apply
            else "DRY-RUN (nothing changed; re-run with --apply)")
    print(f"\n  {mode}\n")
    if args.apply:
        await apply_plan(db, args.account, plan)


if __name__ == "__main__":
    asyncio.run(main())
