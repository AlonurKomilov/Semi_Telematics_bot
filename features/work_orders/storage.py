"""Path / folder layout helpers for Work Order attachments.

Single source of truth for the directory hierarchy used by every storage
backend (``DiskObjectStore`` today, ``GDriveObjectStore`` planned, S3
future).  Keeping the layout in one module means:

* When a user opens their connected Google Drive and browses to a work
  order's folder, the structure looks the same as the local disk
  layout — no surprise.
* When BYO Drive ships, the only thing that changes is which
  ``ObjectStore`` implementation receives the path — the path itself
  is identical.

Layout::

    {COMPANY NAME}/work-orders/{YYYY}/{MM-month}/WO-{id}_truck{name}_{date}_{vendor}/

The top-level segment is the *company display name* (e.g.
``PREMIER TRUCKING GROUP INC``) rather than an opaque ``account-{id}``
prefix.  An account can own multiple companies, and each company's
data lands under its own folder — so a user browsing their Drive sees
their real business names instead of internal IDs.

Inside each work-order folder, attachments are stored under their
original filename (sanitised for filesystem safety).  Drive file IDs
are stored in the DB so a manual rename in Google Drive doesn't break
the link.

The "bucket" in ``ObjectStore.put(bucket, key, data)`` becomes the
full folder path; the "key" is the filename.  DiskObjectStore creates
nested directories from slashes in the bucket name; GDriveObjectStore
resolves the chain via ``_resolve_folder_chain`` (find-or-create).
"""

from __future__ import annotations

import re
from datetime import datetime, timezone
from typing import Optional


_MONTHS = ("01-january", "02-february", "03-march", "04-april",
           "05-may", "06-june", "07-july", "08-august",
           "09-september", "10-october", "11-november", "12-december")


def _slugify(value: str, max_len: int = 40) -> str:
    """Filesystem-safe slug.  Keeps letters/digits/dash/underscore,
    collapses runs of other chars to a single dash, trims length so
    folder names don't blow past the 255-char Drive/POSIX limit.
    """
    s = re.sub(r"[^A-Za-z0-9_-]+", "-", (value or "").strip())
    s = re.sub(r"-{2,}", "-", s).strip("-_")
    return s[:max_len] or "unknown"


def sanitize_company_folder(name: str) -> str:
    """Turn a company display name into a Drive-safe folder name.

    Drive accepts most printable characters in folder names but a few
    (``/`` ``\\`` ``?`` ``<`` ``>`` ``|`` ``*`` NUL) break path
    resolution or are forbidden by the API.  We replace those with an
    underscore, collapse internal whitespace runs to a single space,
    and cap length at 100 characters (Drive's hard limit is 255 but
    keeping it short makes the URLs in browse-Drive screenshots
    readable).

    Empty / whitespace-only input falls back to ``unnamed-company`` so
    a missing display_name never produces a path-less write.
    """
    if not name:
        return "unnamed-company"
    safe = name.strip()
    for ch in "/\\?<>|*\x00":
        safe = safe.replace(ch, "_")
    safe = " ".join(safe.split())
    safe = safe[:100].strip()
    return safe or "unnamed-company"


async def resolve_company_folder(
    tenant_db, account_id: int, company_code: str,
) -> str:
    """Resolve ``company_code`` → Drive-safe folder name for the
    company's display_name.

    Falls back to the sanitised code if the company row is missing or
    has no display_name (legacy / partial data).  Callers should pass
    the empty string for "no specific company" — that yields the
    ``unnamed-company`` bucket.

    Done in the route handler (already async) rather than inside
    ``ObjectStore.put`` so we don't trade a per-byte sync write for an
    async DB round-trip; routes typically resolve once and reuse.
    """
    if not company_code:
        return "unnamed-company"
    try:
        company = await tenant_db.get_company_by_code(account_id, company_code)
    except Exception:
        company = None
    name = (company.display_name if company else "") or company_code
    return sanitize_company_folder(name)


def driver_docs_bucket(company_folder: str, user_id: int) -> str:
    """Compose the bucket path for a driver's personal documents.

    Driver documents (CDL, medical card, DQF entries) live under the
    company that currently employs them, never under a top-level
    ``_drivers`` folder, so each company's compliance officer browsing
    Drive sees a self-contained set of records for their drivers.
    """
    return f"{company_folder}/drivers/user-{user_id}"


def driver_docs_archive_bucket(
    company_folder: str, user_id: int, archive_date: str,
) -> str:
    """Compose the archive bucket path used when a driver leaves a
    company.  The driver's old folder is moved here so the original
    location is free for any future driver who later occupies the same
    seat — and the carrier retains an audit trail dated by the
    transition.

    ``archive_date`` is the YYYY-MM-DD the company-change took effect;
    multiple transitions on the same day collapse into the same date
    folder (rare in practice).
    """
    return f"{company_folder}/drivers/_archive/{archive_date}/user-{user_id}"


def work_order_folder(
    *,
    company_folder: str,
    work_order_id: int,
    vehicle_name: str,
    service_date: Optional[str],
    vendor_name: str,
) -> str:
    """Compose the structured folder path for one work order.

    ``company_folder`` is the already-sanitised company name produced
    by :func:`resolve_company_folder`.  Example output::

        PREMIER TRUCKING GROUP INC/work-orders/2026/04-april/WO-00128_truck221_2026-04-12_BobsDieselShop

    ``service_date`` is the canonical "when did the work happen" — when
    absent (a draft created from a bot photo before the manager filled
    in details) we fall back to today so the file lands in the current
    month bucket instead of an "unsorted" pile.
    """
    if service_date:
        try:
            d = datetime.fromisoformat(service_date.replace("Z", "+00:00"))
        except ValueError:
            d = datetime.now(timezone.utc)
    else:
        d = datetime.now(timezone.utc)
    year = d.strftime("%Y")
    month = _MONTHS[d.month - 1]
    date_str = d.strftime("%Y-%m-%d")

    folder_name = (
        f"WO-{work_order_id:05d}"
        f"_truck{_slugify(vehicle_name, 20)}"
        f"_{date_str}"
        f"_{_slugify(vendor_name, 30) if vendor_name else 'no-vendor'}"
    )
    return f"{company_folder}/work-orders/{year}/{month}/{folder_name}"


def safe_attachment_name(filename: str, *, fallback: str = "file") -> str:
    """Return a filesystem-safe variant of the uploaded filename while
    preserving the original extension (so downstream viewers route by
    type correctly — invoice.pdf stays a PDF).

    Strips path separators that would let a hostile name escape the
    work-order folder, normalises spaces, and caps total length at
    180 chars.
    """
    base = (filename or "").replace("/", "_").replace("\\", "_").strip()
    if not base:
        return fallback
    # Split extension so we can sanitise the stem without losing ``.pdf``.
    if "." in base:
        stem, dot, ext = base.rpartition(".")
        stem = re.sub(r"[^A-Za-z0-9._ -]+", "-", stem).strip(" -") or fallback
        ext = re.sub(r"[^A-Za-z0-9]+", "", ext).lower()[:8] or "bin"
        return f"{stem[:170]}.{ext}" if ext else stem[:180]
    return re.sub(r"[^A-Za-z0-9._ -]+", "-", base).strip(" -")[:180] or fallback
