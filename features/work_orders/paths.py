"""Where a work order's attachments live in the tenant tree.

Only work orders.  This module is what remains of a file that had
grown to place driver documents, vehicle documents and the shared
company-folder resolver as well — each of those now sits with the
feature that owns it, or in the object-storage capability.
"""

from __future__ import annotations

import re
from datetime import datetime, timezone
from typing import Optional

from capabilities.object_storage.paths import slugify as _slugify

_MONTHS = ("01-january", "02-february", "03-march", "04-april",
           "05-may", "06-june", "07-july", "08-august",
           "09-september", "10-october", "11-november", "12-december")


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

        ACME TRUCKING INC/work-orders/2026/04-april/WO-00128_truck221_2026-04-12_BobsDieselShop

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
