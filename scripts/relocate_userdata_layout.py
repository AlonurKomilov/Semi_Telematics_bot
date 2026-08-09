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
5. media stranded in the PLACEHOLDER company folder (``unnamed-company/``,
   ``_generic/``) → ``{COMPANY}/camera-images|parking-maps/``
   (company resolved via the vehicle REGISTRY, the SSOT for unit → company;
   a unit owned by two companies, or by none, is left in place and named
   in the report rather than guessed at)

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
Reads DATABASE_URL from .env — no environment prefix needed::

    python3 -m scripts.relocate_userdata_layout            # dry-run (default)
    python3 -m scripts.relocate_userdata_layout --apply    # move + update rows
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import shutil
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from features.work_orders.storage import (  # noqa: E402
    GENERIC_COMPANY_FOLDER, sanitize_company_folder,
)

from dotenv import load_dotenv  # noqa: E402

# DATABASE_URL lives in .env; load it the way the other scripts here do
# rather than demanding an environment prefix on the command line.
load_dotenv()

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
        "SELECT company_code FROM warehouse.vehicle_state_live WHERE account_id=$1 AND vehicle_id=$2",
        account_id, str(vehicle_id),
    )
    if not code:
        return GENERIC_COMPANY_FOLDER
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


# ── Phase E: files stranded in the placeholder company folder ───────

async def _company_folder_for_vehicle_key(
    conn, account_id: int, key: str,
) -> str | None:
    """vehicle key → its company's folder, or None when we must not guess.

    ``key`` is whatever identifier the row carries — a telematics id
    ("281474996861095") or a unit number ("250").

    ORDER MATTERS, and it is why this is not one OR'd query.  A unit
    number is a LABEL, not an identity: this account really does run two
    different trucks both called "103", one in G1 and one in OSY, with
    different VINs.  Matched by name they are indistinguishable and
    every one of their photos would have to be abandoned as ambiguous.
    Matched by telematics id first, they separate cleanly.  Fall back to
    the name only when the id finds nothing.

    The registry is the SSOT for which company owns a vehicle, so it —
    not the path the file currently sits at — decides where the file
    belongs.  None when the key resolves to two companies or to none: a
    wrong company reads as correct, and would be far harder to notice
    later than a file left where it is.
    """
    k = (key or "").strip()
    if not k:
        return None
    # ACTIVE rows only, and that resolves MORE than including retired
    # ones, which is the opposite of what you would expect from a
    # historical backfill.  A unit number gets reused: truck 6729 exists
    # under two companies once retired rows are counted, so widening the
    # search turns 208 confidently-placed files into an ambiguous pile
    # and rescues only 37.  The active row is the current truth about
    # who owns that number.
    code = None
    for predicate in ("telematics_ref=$2", "lower(unit_number)=lower($2)"):
        codes = await conn.fetch(
            "SELECT DISTINCT company_code FROM vehicles "
            "WHERE account_id=$1 AND is_active=1 AND company_code<>'' "
            f"AND {predicate}",
            account_id, k,
        )
        if len(codes) == 1:
            code = codes[0]["company_code"]
            break
        if len(codes) > 1:
            return None          # genuinely ambiguous — do not guess
    if code is None:
        # Not in the registry under either identifier — the live
        # warehouse row may still know the org (an un-backfilled
        # vehicle, or one retired before the registry existed).
        code = await conn.fetchval(
            "SELECT company_code FROM warehouse.vehicle_state_live "
            "WHERE account_id=$1 AND vehicle_id=$2", account_id, k,
        )
    if not code:
        return None
    name = await conn.fetchval(
        "SELECT display_name FROM companies WHERE account_id=$1 AND code=$2",
        account_id, str(code).strip().upper(),
    )
    return sanitize_company_folder(name or code)


async def relocate_placeholder_media(
    conn, apply: bool, log: list[str],
    *, table: str, col: str, key_cols: tuple[str, ...], seg: str,
) -> tuple[int, int]:
    """Move media out of the placeholder folder into its real company.

    The writers used to lose the company between analysis and storage,
    so correctly-shaped paths accumulated under a folder that merely
    named the absence: ``{placeholder}/camera-images/…``.  Phases C/D
    do not see these — those hunt the pre-layout underscore buckets, and
    these paths are already in the modern shape, just under the wrong
    top segment.

    Far more ROWS than files: retention deletes the photo and keeps the
    history row, so most rows here point at something already gone.  A
    missing file is normal, not an error — the row is left untouched so
    it keeps describing where the photo used to be.

    Returns ``(files_moved, rows_repointed)``.
    """
    placeholders = ("unnamed-company", GENERIC_COMPANY_FOLDER)
    # Identifiers in priority order — the telematics id before the unit
    # name, because two trucks can share a name but not an id.
    keys_sql = ", ".join(f"{c} AS key{i}" for i, c in enumerate(key_cols))
    rows = []
    for ph in placeholders:
        rows += await conn.fetch(
            f"SELECT id, account_id, {keys_sql}, {col} AS p "
            f"FROM {table} WHERE strpos({col}, $1) > 0",
            f"/{ph}/{seg}/",
        )
    moved = rows_updated = missing = 0
    unresolved: dict[str, int] = {}
    remap: dict[tuple[int, str], str] = {}
    folders: dict[tuple[int, str], str | None] = {}

    for r in rows:
        acct, stored = r["account_id"], r["p"] or ""
        dst_rel = remap.get((acct, stored))
        if dst_rel is None:
            src = _find_file(stored, acct)
            if not src:
                missing += 1
                continue
            ids = [str(r[f"key{i}"] or "").strip()
                   for i in range(len(key_cols))]
            folder = None
            for ident in ids:
                if not ident:
                    continue
                ck = (acct, ident.lower())
                if ck not in folders:
                    folders[ck] = await _company_folder_for_vehicle_key(
                        conn, acct, ident,
                    )
                if folders[ck]:
                    folder = folders[ck]
                    break
            if not folder:
                label = " / ".join(i for i in ids if i) or "(no identifier)"
                unresolved[label] = unresolved.get(label, 0) + 1
                continue
            # The filename repeats the company deliberately, so a file
            # mailed out of Drive still says whose truck it was.  Moving
            # it without rewriting that prefix would leave the lie
            # behind in the one place that travels.
            fname = os.path.basename(src)
            for ph in placeholders:
                if fname.startswith(f"{ph}_"):
                    fname = f"{folder}_{fname[len(ph) + 1:]}"
                    break
            dst_rel = (f"data/userdata/account-{acct}/{folder}/{seg}/{fname}")
            log.append(f"  [{seg}] {table}#{r['id']} ({folder}): {stored}")
            if not _move(src, dst_rel, apply, log):
                continue
            moved += 1
            remap[(acct, stored)] = dst_rel
        if apply:
            await conn.execute(
                f"UPDATE {table} SET {col}=$1 WHERE id=$2 AND account_id=$3",
                dst_rel, r["id"], acct,
            )
        rows_updated += 1

    log.append(f"  [{seg}] {moved} file(s) rehomed, {rows_updated} row(s) "
               f"repointed, {missing} row(s) whose file is already pruned")
    if unresolved:
        log.append(f"  [{seg}] LEFT IN PLACE — no single active vehicle "
                   f"in the registry owns these keys:")
        for unit, n in sorted(unresolved.items(), key=lambda kv: -kv[1]):
            log.append(f"      {unit!r}: {n} row(s)")
    return moved, rows_updated


# ── Phase F: branding whose DB pointer was lost ─────────────────────

# Leading bytes of the image types the brand uploaders accept.  Used to
# tell a real upload from a test stub without trusting the extension.
_IMAGE_MAGIC = (b"\xff\xd8\xff", b"\x89PNG\r\n\x1a\n", b"RIFF", b"GIF8")


def _looks_like_an_image(path: str) -> bool:
    try:
        with open(path, "rb") as fh:
            head = fh.read(12)
    except OSError:
        return False
    return any(head.startswith(m) for m in _IMAGE_MAGIC)


async def relocate_orphaned_branding(conn, apply: bool, log: list[str]) -> int:
    """Rescue a logo/banner sitting in the pre-layout root with NO DB row.

    Phase A cannot see these.  It walks FROM the database — companies
    whose ``logo_object_id`` still points at the old root — and a file
    whose pointer was lost or cleared is invisible from that direction.
    One real 1.8 MB recruiter banner was sitting unreachable this way:
    the file was on disk, the column was empty, and the public apply page
    had been rendering without it.

    So this walks from the DISK instead, and the folder name carries the
    company id (``company-banners/{id}/``) which is what makes the rescue
    safe rather than a guess.

    Two rules keep it conservative: a column that already points
    somewhere is never overwritten (a live pointer wins over a file we
    found lying around), and a file that is not really an image is left
    for the purge — a 44-byte "banner.jpg" is a test stub, and restoring
    it would put a broken image on a customer's public page.
    """
    moved = 0
    if not os.path.isdir(USERDATA):
        return 0
    for acct_dir in sorted(os.listdir(USERDATA)):
        if not acct_dir.startswith("account-"):
            continue
        try:
            acct = int(acct_dir.split("-", 1)[1])
        except ValueError:
            continue
        for kind in ("logo", "banner"):
            root = os.path.join(USERDATA, acct_dir, f"company-{kind}s")
            if not os.path.isdir(root):
                continue
            for id_dir in sorted(os.listdir(root)):
                src_dir = os.path.join(root, id_dir)
                if not os.path.isdir(src_dir):
                    continue
                try:
                    company_id = int(id_dir)
                except ValueError:
                    continue
                row = await conn.fetchrow(
                    "SELECT display_name, code, logo_object_id, banner_object_id "
                    "FROM companies WHERE id=$1 AND account_id=$2",
                    company_id, acct,
                )
                if not row:
                    log.append(f"  [branding-rescue] company {company_id} is not in "
                               f"account {acct} — {src_dir} left for review")
                    continue
                if (row[f"{kind}_object_id"] or "").strip():
                    continue          # a live pointer already wins
                # Several files can sit here across re-uploads; take the
                # largest REAL image — the stubs are tiny by construction.
                cands = [os.path.join(src_dir, f) for f in os.listdir(src_dir)]
                cands = [f for f in cands
                         if os.path.isfile(f) and _looks_like_an_image(f)]
                if not cands:
                    continue
                src = max(cands, key=os.path.getsize)
                folder = sanitize_company_folder(
                    row["display_name"] or row["code"] or "")
                ext = os.path.splitext(src)[1].lstrip(".").lower() or "png"
                dst_rel = (f"data/userdata/account-{acct}/{folder}/branding/"
                           f"{kind}-{company_id}.{ext}")
                log.append(f"  [branding-rescue] company {company_id} ({folder}) "
                           f"{kind}: {os.path.relpath(src, PROJECT_ROOT)} "
                           f"— {os.path.getsize(src):,} bytes, DB pointer was empty")
                if not _move(src, dst_rel, apply, log):
                    continue
                moved += 1
                if apply:
                    await conn.execute(
                        f"UPDATE companies SET {kind}_object_id=$1 "
                        "WHERE id=$2 AND account_id=$3",
                        dst_rel, company_id, acct,
                    )
                    log.append(f"    {kind}_object_id set — the page can reach it again")
    return moved


# ── Phase G: applications off the account root ──────────────────────

async def relocate_rootless_applications(conn, apply: bool, log: list[str]) -> int:
    """Move an application out of the bare account-root ``applications/``.

    Phase B places company-branded applications; this handles the ones
    whose link named no company, which Phase B skips by design.  They
    used to stay at the account root, where they read like a sixth
    business next to the five real ones.  They go to the holding pen
    instead — still findable, no longer masquerading as a company.
    """
    rows = await conn.fetch(
        "SELECT id, account_id, reference, docs_json, sig_object_id "
        "FROM driver_applications WHERE company_id IS NULL "
        "AND (docs_json LIKE $1 OR sig_object_id LIKE $1)",
        "%/account-%/applications/%",
    )
    moved = 0
    for r in rows:
        acct, ref = r["account_id"], r["reference"]
        src_dir = os.path.join(USERDATA, f"account-{acct}", "applications", ref)
        if not os.path.isdir(src_dir):
            continue
        docs = r["docs_json"] or ""
        sig = r["sig_object_id"] or ""
        old_seg = f"account-{acct}/applications/{ref}/"
        new_seg = f"account-{acct}/{GENERIC_COMPANY_FOLDER}/applications/{ref}/"
        log.append(f"  [app-root] application #{r['id']} ({ref}): "
                   f"{old_seg} → {new_seg}")
        ok = True
        for fname in sorted(os.listdir(src_dir)):
            src = os.path.join(src_dir, fname)
            if not os.path.isfile(src):
                continue
            dst_rel = (f"data/userdata/account-{acct}/{GENERIC_COMPANY_FOLDER}"
                       f"/applications/{ref}/{fname}")
            if not _move(src, dst_rel, apply, log):
                ok = False
        if not ok:
            log.append("    partial — DB left pointing at the old paths")
            continue
        moved += 1
        if apply:
            await conn.execute(
                "UPDATE driver_applications SET docs_json=$1, sig_object_id=$2 "
                "WHERE id=$3 AND account_id=$4",
                docs.replace(old_seg, new_seg), sig.replace(old_seg, new_seg),
                r["id"], acct,
            )
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
    if not db_url.startswith(("postgresql://", "postgres://")):
        sys.stderr.write(
            f"ERROR: DATABASE_URL is not a Postgres DSN (got {db_url[:40]!r}).\n"
            "Set it in .env, or export it before running.\n")
        return 2

    conn = await asyncpg.connect(db_url)
    # Raw connection bypasses the app pool's server_settings — set the
    # same search_path so vehicle_state resolves on either side of the
    # warehouse schema move.
    await conn.execute("SET search_path TO public, warehouse")
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
        e1, _ = await relocate_placeholder_media(
            conn, args.apply, log, table="camera_checks",
            col="image_path", key_cols=("vehicle_id", "vehicle_name"),
            seg="camera-images")
        e2, _ = await relocate_placeholder_media(
            conn, args.apply, log, table="parking_events",
            col="map_image_path", key_cols=("vehicle_id",),
            seg="parking-maps")
        e = e1 + e2
        f = await relocate_orphaned_branding(conn, args.apply, log)
        g = await relocate_rootless_applications(conn, args.apply, log)
    finally:
        await conn.close()
    o = report_orphans(log)

    print("\n".join(log) if log else "(nothing to relocate)")
    mode = "APPLIED" if args.apply else "DRY-RUN (nothing changed; re-run with --apply)"
    print(f"\n{mode}: branding={a} applications={b} camera={c} parking={d} "
      f"placeholder={e} branding-rescue={f} app-root={g} moved; "
      f"orphans left in place={o}")
    if not args.apply and (a or b or c or d or e or f or g):
        print("Note: previously Drive-synced copies of moved files stay at their old "
              "Drive locations — new uploads sync to the corrected folders.")
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
