"""One-shot purge: delete test-suite droppings from the tenant file tree.

Why this exists
---------------
``tests/conftest.py`` did not override ``OBJECT_STORE_ROOT``, and
``accounts.id`` starts at 10000001 by schema design — so the first
account a fresh test database created shared a folder with the first
REAL account, and every suite that stored a file wrote it into a live
customer's tree.  It ran that way for months.  The leak is fixed at the
source; this removes what it left behind.

What makes a file deletable here
--------------------------------
Three conditions, ALL required.  Any one of them alone would be a bad
rule, and together they are what makes deleting from a customer's data
directory defensible:

1. **Unreferenced.**  No row in any stored-path column of the schema
   mentions it.  The reference set is built by querying every such
   column, not by assuming which tables matter.
2. **Stub-sized.**  Under ``MAX_STUB_BYTES``.  The fixtures write 44-byte
   placeholders; a real CDL photo is 120-200 KB and a real banner is
   megabytes.  Size is the single most reliable separator here, and the
   cap is a hard refusal — this script will not delete a large file even
   if the other two conditions hold.
3. **Inside a known-polluted area.**  Application folders and the
   placeholder company folders.  Never a real company's folder, never
   work orders, never anything else.

Condition 2 is relaxed by a DIRECTORY rule, because on its own it left
the tree still looking dirty: a fixture application folder holds three
44-byte stubs and one 48 KB generated signature, so the stubs went and
the signature stayed, and the folder survived looking almost real.  When
NOTHING in a folder is referenced and NOTHING in it reaches
``REAL_FILE_BYTES``, the whole record is test output and goes together.
A single genuine file anywhere in the folder protects all of it.

Anything failing a condition is REPORTED and kept.  A file we are unsure
about is worth more than the disk it occupies.

Run the relocation FIRST
------------------------
``scripts/relocate_userdata_layout.py --apply`` moves the real files out
of these directories.  Running the purge before it would find real files
still sitting among the stubs; they would survive (the size cap), but
the report would be noisier and harder to trust.

Usage
-----
Reads DATABASE_URL from .env, like every other script here — no
environment prefix needed::

    python3 -m scripts.purge_test_artifacts            # dry-run report
    python3 -m scripts.purge_test_artifacts --apply    # delete
"""

from __future__ import annotations

import argparse
import asyncio
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from dotenv import load_dotenv  # noqa: E402

# The project keeps DATABASE_URL in .env, and every other script here
# loads it the same way.  Requiring it on the command line instead
# invites pasting a placeholder, which is exactly what happened once —
# the run died on an invalid DSN before reaching a single file.
load_dotenv()

try:
    import asyncpg
except ImportError:  # pragma: no cover
    sys.stderr.write("ERROR: asyncpg not installed.\n")
    sys.exit(1)

from features.work_orders.storage import GENERIC_COMPANY_FOLDER  # noqa: E402

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
USERDATA = os.path.join(PROJECT_ROOT, "data", "userdata")

# Above this, a file is never deleted regardless of everything else.
# The fixtures write 44 bytes; the smallest real upload seen in the live
# tree is ~3 KB (a generated sidecar PDF) and real photos are 100 KB+.
MAX_STUB_BYTES = 1000

# A file this size or larger is treated as genuine content — a scan, a
# photo, a map render.  Used by the directory rule below, never as a
# licence to delete on its own.
REAL_FILE_BYTES = 50_000

# Directories under an account that the test suite wrote into.  A real
# company's folder is deliberately NOT here: nothing in this script may
# touch "PREMIER TRUCKING GROUP INC/" and friends.
def _polluted_dirs(account_dir: str) -> list[str]:
    out = [
        os.path.join(account_dir, "applications"),
        os.path.join(account_dir, "unnamed-company"),
        os.path.join(account_dir, GENERIC_COMPANY_FOLDER),
        os.path.join(account_dir, "company-logos"),
        os.path.join(account_dir, "company-banners"),
    ]
    # Folders named after a company that does not exist in this account
    # are fixture leftovers ("Doc Carrier", "TESTCO", an old display
    # name).  The caller supplies the real names; see build_targets.
    return [d for d in out if os.path.isdir(d)]


def _tail(path: str) -> str:
    """A file's identity for reference matching: its path WITHIN its
    account, lowercased — e.g. ``premier trucking group inc/branding/
    logo-1.jpg``.

    Two narrower rules were tried and both were wrong.  The basename
    alone protects everything: every application folder on disk holds
    ``cdlFront.jpg`` / ``cdlBack.jpg`` / ``medical.pdf``, so three real
    applications made 939 stubs look referenced.  The last two
    components fix that but still collapse company folders together —
    ``Premier Trucking Group/branding/logo-1.jpg`` (a 44-byte stub under
    a dead folder name) and ``PREMIER TRUCKING GROUP INC/branding/
    logo-1.jpg`` (the real logo) share a tail, so the stub inherited the
    real file's protection.

    The company segment is precisely what must not be discarded, since
    telling a real company's folder from a fixture's is the whole job.
    A stored path from before the per-account layout has no
    ``account-N`` component; those fall back to the last two components,
    which is the conservative direction for a form that no longer occurs
    after the relocation has run.
    """
    parts = [q for q in path.replace("\\", "/").split("/") if q]
    for i, part in enumerate(parts):
        if part.startswith("account-"):
            return "/".join(parts[i + 1:]).lower()
    return "/".join(parts[-2:]).lower()


async def _referenced_paths(conn) -> set[str]:
    """Every stored path the database still points at, as ``_tail``s."""
    cols = await conn.fetch(
        "SELECT table_name, column_name FROM information_schema.columns "
        "WHERE table_schema='public' AND ("
        "  column_name ILIKE '%object_id%' OR column_name ILIKE '%image_path%'"
        "  OR column_name ILIKE '%file_path%' OR column_name ILIKE '%_path'"
        "  OR column_name ILIKE '%docs_json%')"
    )
    seen: set[str] = set()
    for c in cols:
        t, col = c["table_name"], c["column_name"]
        try:
            rows = await conn.fetch(
                f"SELECT {col}::text AS v FROM {t} "
                f"WHERE COALESCE({col}::text,'') <> ''"
            )
        except Exception:
            continue
        for r in rows:
            for chunk in str(r["v"]).replace('"', " ").replace(",", " ").split():
                if "/" in chunk:
                    seen.add(_tail(chunk.strip().rstrip("}").rstrip("/")))
    return seen


async def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--apply", action="store_true",
                    help="actually delete (default: dry-run report)")
    args = ap.parse_args()

    db_url = os.environ.get("DATABASE_URL", "").strip()
    if not db_url.startswith(("postgresql://", "postgres://")):
        sys.stderr.write(
            f"ERROR: DATABASE_URL is not a Postgres DSN (got {db_url[:40]!r}).\n"
            "Set it in .env, or export it before running.\n")
        return 2

    conn = await asyncpg.connect(db_url)
    try:
        referenced = await _referenced_paths(conn)
        companies: dict[int, set[str]] = {}
        for r in await conn.fetch(
                "SELECT account_id, display_name, code FROM companies"):
            from features.work_orders.storage import sanitize_company_folder
            companies.setdefault(r["account_id"], set()).add(
                sanitize_company_folder(r["display_name"] or r["code"] or ""))
    finally:
        await conn.close()

    print(f"database references {len(referenced)} distinct stored filenames\n")

    deleted = kept_ref = kept_big = 0
    freed = 0
    kept_notes: list[str] = []

    for acct_dir_name in sorted(os.listdir(USERDATA)):
        acct_path = os.path.join(USERDATA, acct_dir_name)
        if not acct_dir_name.startswith("account-") or not os.path.isdir(acct_path):
            continue
        try:
            acct = int(acct_dir_name.split("-", 1)[1])
        except ValueError:
            continue
        real = companies.get(acct, set())

        targets = _polluted_dirs(acct_path)
        # plus any top-level folder that is not one of this account's
        # real companies and not a known system folder
        known = real | {"applications", "unnamed-company", GENERIC_COMPANY_FOLDER,
                        "company-logos", "company-banners"}
        for name in sorted(os.listdir(acct_path)):
            full = os.path.join(acct_path, name)
            if os.path.isdir(full) and name not in known:
                targets.append(full)

        for target in targets:
            rel_t = os.path.relpath(target, PROJECT_ROOT)
            for root, _dirs, files in os.walk(target):
                paths = [os.path.join(root, f) for f in sorted(files)]
                if not paths:
                    continue
                # DIRECTORY RULE.  A per-file size test alone leaves the
                # mess behind: a fixture application folder holds three
                # 44-byte stubs AND a 48 KB generated signature, so the
                # stubs go and the signature stays, and the folder
                # survives looking almost real.  Judge the folder: if no
                # row references anything in it and nothing in it is
                # genuine content, the whole record is test output.
                dir_referenced = any(_tail(f) in referenced for f in paths)
                dir_has_real = any(
                    os.path.getsize(f) >= REAL_FILE_BYTES for f in paths)
                whole_dir_is_junk = not dir_referenced and not dir_has_real

                for full in paths:
                    size = os.path.getsize(full)
                    if _tail(full) in referenced:
                        kept_ref += 1
                        kept_notes.append(
                            f"  KEPT (referenced) {size:>9,} B  "
                            f"{os.path.relpath(full, PROJECT_ROOT)}")
                        continue
                    if size > MAX_STUB_BYTES and not whole_dir_is_junk:
                        kept_big += 1
                        kept_notes.append(
                            f"  KEPT (too large)  {size:>9,} B  "
                            f"{os.path.relpath(full, PROJECT_ROOT)}")
                        continue
                    # Defence in depth, not redundancy: the rules above
                    # decide, this refuses.  Any future rule that gets
                    # the reasoning wrong still cannot destroy genuine
                    # content — it fails loudly here instead.
                    if size >= REAL_FILE_BYTES:
                        raise AssertionError(
                            f"refusing to delete {size:,}-byte file, at or "
                            f"over the {REAL_FILE_BYTES:,} genuine-content "
                            f"threshold: {full}")
                    deleted += 1
                    freed += size
                    if args.apply:
                        os.remove(full)
            if args.apply:
                # Prune directories the deletions emptied, deepest first.
                for root, dirs, files in os.walk(target, topdown=False):
                    if not os.listdir(root):
                        os.rmdir(root)
            print(f"  swept {rel_t}")

    if kept_notes:
        print("\nKEPT — every file this run refused to delete:")
        print("\n".join(kept_notes))

    verb = "deleted" if args.apply else "would delete"
    print(f"\n{'APPLIED' if args.apply else 'DRY-RUN (nothing changed)'}: "
          f"{verb} {deleted:,} stub file(s), {freed:,} bytes; "
          f"kept {kept_ref} referenced, {kept_big} too-large")
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
