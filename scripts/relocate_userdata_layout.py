"""One-shot relocation: move tenant files into the per-company folder layout.

Why this exists
---------------
The object-store tree nests company-scoped data under the company's
display-name folder (``{COMPANY}/work-orders/``, ``{COMPANY}/camera-images/``,
``{COMPANY}/parking-maps/``) — but four groups of files predate or ignored
that layout and sit at the account root instead:

1. ``company-logos/{id}/logo.ext``   → ``{COMPANY}/branding/logo-{id}.ext``
2. ``company-banners/{id}/banner.ext`` → ``{COMPANY}/branding/banner-{id}.ext``
3. ``applications/{REF}/…`` (company-branded links only)
                                     → ``{COMPANY}/applications/{REF}/…``
   (generic no-company links stay at the account-level ``applications/`` root
   — they have no company to belong to)
4. legacy ``camera_images/`` and ``parking_maps`` (underscore, pre-layout)
                                     → ``{COMPANY}/camera-images|parking-maps/``
   (company resolved via the row's vehicle → ``vehicle_state.company_code``)

Every one of these paths is referenced by a DB row (``companies.logo_object_id``
/ ``banner_object_id``, ``driver_applications.docs_json`` / ``sig_object_id``,
``camera_checks.image_path``, ``parking_events.map_image_path``), so each move
is FILE MOVE + ROW UPDATE together; the row is only updated after the file
move succeeded, and an existing target file is never clobbered (skip + warn).

Files with no DB reference are ORPHANS: reported, never moved (candidates for
retention review, not for guessing an owner).

Google Drive: this script touches OUR disk + DB only.  New uploads already
write the corrected tree to Drive (the bucket string flows verbatim through
the hybrid/GDrive stores), and Drive-connect pre-creates the new subfolders.
Old copies previously synced to Drive stay where they are — flagged in the
report so they can be tidied by hand if wanted (we never bulk-delete from a
customer's own cloud).

Usage
-----
    DATABASE_URL='postgresql://…' python3 -m scripts.relocate_userdata_layout            # dry-run (default)
    DATABASE_URL='postgresql://…' python3 -m scripts.relocate_userdata_layout --apply    # move + update rows
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import shutil
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from features.work_orders.storage import sanitize_company_folder  # noqa: E402

try:
    import asyncpg
except ImportError:  # pragma: no cover
    sys.stderr.write("ERROR: asyncpg not installed.\n")
    sys.exit(1)

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
USERDATA = os.path.join(PROJECT_ROOT, "data", "userdata")


def _legacy_candidates(stored: str, account_id: int) -> list[str]:
    """All plausible on-disk locations for a stored path, newest first."""
    rel = stored.lstrip("/")
    out = [os.path.join(PROJECT_ROOT, rel)]
    # pre-userdata form: data/<bucket>/<file> → data/userdata/account-N/<bucket>/<file>
    if rel.startswith("data/") and not rel.startswith("data/userdata/"):
        out.append(os.path.join(
            PROJECT_ROOT, "data", "userdata", f"account-{account_id}",
            rel[len("data/"):],
        ))
    return out


def _find_file(stored: str, account_id: int) -> str | None:
    for cand in _legacy_candidates(stored, account_id):
        if os.path.isfile(cand):
            return cand
    return None


# absolute source paths this run moved (or, in dry-run, would move) —
# the orphan report excludes them so its count means "truly unreferenced".
_MOVED_SRCS: set[str] = set()


def _move(src_abs: str, dst_rel: str, apply: bool, log: list[str]) -> bool:
    """Move src to PROJECT_ROOT/dst_rel.  Returns True if (would be) moved."""
    dst_abs = os.path.join(PROJECT_ROOT, dst_rel)
    if os.path.exists(dst_abs):
        log.append(f"    SKIP (target exists): {dst_rel}")
        return False
    if not apply:
        log.append(f"    would move → {dst_rel}")
        _MOVED_SRCS.add(src_abs)
        return True
    os.makedirs(os.path.dirname(dst_abs), exist_ok=True)
    shutil.move(src_abs, dst_abs)
    log.append(f"    moved → {dst_rel}")
    _MOVED_SRCS.add(src_abs)
    return True


async def _company_folder_by_id(conn, account_id: int, company_id: int) -> str | None:
    row = await conn.fetchrow(
        "SELECT display_name, code FROM companies WHERE account_id=$1 AND id=$2",
        account_id, company_id,
    )
    if not row:
        return None
    return sanitize_company_folder(row["display_name"] or row["code"] or "")


async def _company_folder_by_vehicle(conn, account_id: int, vehicle_id: str) -> str:
    """vehicle → company_code (vehicle_state) → display-name folder."""
    code = await conn.fetchval(
        "SELECT company_code FROM vehicle_state WHERE account_id=$1 AND vehicle_id=$2",
        account_id, str(vehicle_id),
    )
    if not code:
        return "unnamed-company"
    name = await conn.fetchval(
        "SELECT display_name FROM companies WHERE account_id=$1 AND code=$2",
        account_id, str(code).strip().upper(),
    )
    return sanitize_company_folder(name or code)


# ── Phase A: branding (logos + banners) ─────────────────────────────

async def relocate_branding(conn, apply: bool, log: list[str]) -> int:
    rows = await conn.fetch(
        "SELECT id, account_id, display_name, code, logo_object_id, banner_object_id "
        "FROM companies WHERE logo_object_id LIKE '%company-logos/%' "
        "   OR banner_object_id LIKE '%company-banners/%'"
    )
    moved = 0
    for r in rows:
        folder = sanitize_company_folder(r["display_name"] or r["code"] or "")
        acct = r["account_id"]
        for col, kind in (("logo_object_id", "logo"), ("banner_object_id", "banner")):
            stored = r[col] or ""
            if f"company-{kind}s/" not in stored:
                continue
            src = _find_file(stored, acct)
            if not src:
                log.append(f"  [branding] company={r['id']} {kind}: file missing for {stored} — row left as-is")
                continue
            ext = os.path.splitext(src)[1].lstrip(".") or "png"
            dst_rel = f"data/userdata/account-{acct}/{folder}/branding/{kind}-{r['id']}.{ext}"
            log.append(f"  [branding] company={r['id']} ({folder}) {kind}: {stored}")
            if _move(src, dst_rel, apply, log):
                moved += 1
                if apply:
                    await conn.execute(
                        f"UPDATE companies SET {col}=$1 WHERE id=$2 AND account_id=$3",
                        dst_rel, r["id"], acct,
                    )
    return moved


# ── Phase B: company-branded applications ───────────────────────────

async def relocate_applications(conn, apply: bool, log: list[str]) -> int:
    rows = await conn.fetch(
        "SELECT id, account_id, reference, company_id, docs_json, sig_object_id "
        "FROM driver_applications WHERE company_id IS NOT NULL "
        "AND (docs_json LIKE '%/applications/%' OR sig_object_id LIKE '%/applications/%')"
    )
    moved = 0
    for r in rows:
        acct, ref = r["account_id"], r["reference"]
        old_prefix = f"data/userdata/account-{acct}/applications/{ref}/"
        folder = await _company_folder_by_id(conn, acct, r["company_id"])
        if not folder:
            log.append(f"  [apps] {ref}: company row {r['company_id']} missing — left at account root")
            continue
        new_prefix = f"data/userdata/account-{acct}/{folder}/applications/{ref}/"

        docs = json.loads(r["docs_json"] or "{}")
        touched = False
        new_docs = {}
        for slot, path in docs.items():
            if isinstance(path, str) and path.startswith(old_prefix):
                src = _find_file(path, acct)
                dst_rel = new_prefix + os.path.basename(path)
                if src and _move(src, dst_rel, apply, log):
                    new_docs[slot] = dst_rel
                    touched = True
                    moved += 1
                else:
                    new_docs[slot] = path
                    if not src:
                        log.append(f"  [apps] {ref}/{slot}: file missing for {path}")
            else:
                new_docs[slot] = path

        sig = r["sig_object_id"] or ""
        new_sig = sig
        if sig.startswith(old_prefix):
            src = _find_file(sig, acct)
            dst_rel = new_prefix + os.path.basename(sig)
            if src and _move(src, dst_rel, apply, log):
                new_sig = dst_rel
                touched = True
                moved += 1

        if touched:
            log.append(f"  [apps] {ref} → {folder}/applications/{ref}/")
            if apply:
                await conn.execute(
                    "UPDATE driver_applications SET docs_json=$1, sig_object_id=$2 "
                    "WHERE id=$3 AND account_id=$4",
                    json.dumps(new_docs), new_sig, r["id"], acct,
                )
                # tidy the emptied per-application dir
                old_dir = os.path.join(PROJECT_ROOT, old_prefix.rstrip("/"))
                try:
                    if os.path.isdir(old_dir) and not os.listdir(old_dir):
                        os.rmdir(old_dir)
                except OSError:
                    pass
    return moved


# ── Phases C/D: legacy camera_images / parking_maps (underscore) ────

async def relocate_vehicle_media(
    conn, apply: bool, log: list[str],
    *, table: str, col: str, legacy_seg: str, new_seg: str,
) -> int:
    # strpos = LITERAL substring match — LIKE would treat the underscore in
    # ``camera_images`` as a single-char wildcard and swallow every correctly
    # placed ``camera-images`` row too.
    rows = await conn.fetch(
        f"SELECT id, account_id, vehicle_id, {col} AS p FROM {table} "
        f"WHERE strpos({col}, '{legacy_seg}/') > 0"
    )
    moved = 0
    rows_updated = 0
    # Many rows can reference ONE physical file (repeat parking events for
    # the same spot, repeat camera checks).  Move the file once, then point
    # every row that referenced the old path at the new location.
    remap: dict[tuple[int, str], str] = {}
    for r in rows:
        if f"{legacy_seg}/" not in (r["p"] or ""):  # defense-in-depth
            continue
        acct = r["account_id"]
        dst_rel = remap.get((acct, r["p"]))
        if dst_rel is None:
            src = _find_file(r["p"], acct)
            if not src:
                log.append(f"  [{new_seg}] {table}#{r['id']}: file missing for {r['p']} — row left as-is")
                continue
            # the same physical file can be stored under two path FORMS
            # (pre-073 ``data/<bucket>/…`` vs ``data/userdata/…``) — reuse
            # the destination if this run already handled the file.
            already = remap.get((acct, src))
            if already is not None:
                dst_rel = already
                remap[(acct, r["p"])] = dst_rel
            else:
                folder = await _company_folder_by_vehicle(conn, acct, r["vehicle_id"])
                # keep the ORIGINAL legacy filename — it's unique
                # (id-prefixed) and must not clobber the current
                # per-vehicle key the live writer uses.
                dst_rel = f"data/userdata/account-{acct}/{folder}/{new_seg}/{os.path.basename(src)}"
                log.append(f"  [{new_seg}] {table}#{r['id']} ({folder}): {r['p']}")
                if not _move(src, dst_rel, apply, log):
                    continue
                moved += 1
                remap[(acct, r["p"])] = dst_rel
                remap[(acct, src)] = dst_rel
        if apply:
            await conn.execute(
                f"UPDATE {table} SET {col}=$1 WHERE id=$2 AND account_id=$3",
                dst_rel, r["id"], acct,
            )
        rows_updated += 1
    log.append(f"  [{new_seg}] {moved} file(s), {rows_updated} row(s) repointed")
    return moved


# ── Orphan report (never moved) ─────────────────────────────────────

def report_orphans(log: list[str]) -> int:
    """Files still sitting in the legacy root buckets after the DB-driven
    passes: nothing references them, so we report instead of guessing."""
    count = 0
    if not os.path.isdir(USERDATA):
        return 0
    for acct_dir in sorted(os.listdir(USERDATA)):
        base = os.path.join(USERDATA, acct_dir)
        for legacy in ("camera_images", "parking_maps", "company-logos", "company-banners"):
            d = os.path.join(base, legacy)
            if not os.path.isdir(d):
                continue
            files = [p for p in (os.path.join(dp, f) for dp, _, fs in os.walk(d) for f in fs)
                     if p not in _MOVED_SRCS]
            if files:
                count += len(files)
                log.append(f"  [orphans] {acct_dir}/{legacy}: {len(files)} unreferenced file(s) left in place")
    return count


async def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--apply", action="store_true",
                        help="actually move files and update DB rows (default: dry-run report)")
    args = parser.parse_args()

    db_url = os.environ.get("DATABASE_URL", "").strip()
    if not db_url:
        sys.stderr.write("ERROR: DATABASE_URL is required.\n")
        return 2

    conn = await asyncpg.connect(db_url)
    log: list[str] = []
    try:
        a = await relocate_branding(conn, args.apply, log)
        b = await relocate_applications(conn, args.apply, log)
        c = await relocate_vehicle_media(conn, args.apply, log,
                                         table="camera_checks", col="image_path",
                                         legacy_seg="camera_images", new_seg="camera-images")
        d = await relocate_vehicle_media(conn, args.apply, log,
                                         table="parking_events", col="map_image_path",
                                         legacy_seg="parking_maps", new_seg="parking-maps")
    finally:
        await conn.close()
    o = report_orphans(log)

    print("\n".join(log) if log else "(nothing to relocate)")
    mode = "APPLIED" if args.apply else "DRY-RUN (nothing changed; re-run with --apply)"
    print(f"\n{mode}: branding={a} applications={b} camera={c} parking={d} moved; orphans left in place={o}")
    if not args.apply and (a or b or c or d):
        print("Note: previously Drive-synced copies of moved files stay at their old "
              "Drive locations — new uploads sync to the corrected folders.")
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
