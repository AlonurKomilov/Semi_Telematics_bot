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

    Under the TRUCK it was done on::

        ACME TRUCKING INC/vehicles/221/work-orders/WO-00128_2026-04-12_BobsDieselShop

    so one folder holds everything about unit 221 — its papers and its
    repair history — which is how a fleet actually asks the question
    ("what has this truck cost me?").  The old shape filed by calendar
    month across the whole account, which answers "what did we spend in
    April" and answers it just as well from the cost report, on data
    the folder tree cannot beat.  The truck token drops out of the
    folder NAME: it is the parent now, and repeating it read as a
    typo.

    A work order with NO vehicle — shop supplies, a bulk parts invoice
    — keeps the dated tree::

        ACME TRUCKING INC/work-orders/2026/04-april/WO-00131_2026-04-12_NAPA

    deliberately not the ``_generic`` pen: that one means "no company
    could be established", and here the company is known perfectly
    well.  Only the truck is missing, and inventing one would be worse
    than filing it by date.

    ``company_folder`` is the already-sanitised company name produced
    by :func:`resolve_company_folder`.  ``service_date`` is the
    canonical "when did the work happen" — when absent (a draft created
    from a bot photo before the manager filled in details) we fall back
    to today rather than an "unsorted" pile.
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

    vendor = _slugify(vendor_name, 30) if vendor_name else "no-vendor"
    # Guard on the RAW name: slugify() answers "unknown" for an empty
    # string, which would file a shop-supplies invoice under a truck
    # called unknown — a folder that looks like a fleet asset and is
    # not one.
    unit = _slugify(vehicle_name, 20) if (vehicle_name or "").strip() else ""
    if unit:
        # Under the truck; its name is the parent, so it leaves the leaf.
        return (f"{company_folder}/vehicles/{unit}/work-orders/"
                f"WO-{work_order_id:05d}_{date_str}_{vendor}")
    # No truck to file it under — the dated tree, which is what that
    # tree is now FOR.
    return (f"{company_folder}/work-orders/{year}/{month}/"
            f"WO-{work_order_id:05d}_{date_str}_{vendor}")


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
