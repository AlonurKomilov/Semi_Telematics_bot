"""DEPRECATED re-exports — the path helpers moved to their owners.

This module was "path / folder layout helpers for Work Order
attachments" and had quietly become the address for four unrelated
concerns: the shared company-folder resolver (14 of its 17 importers
wanted only that), driver document paths, vehicle document paths, and
work-order paths.  Two different record types sharing one file is how
they come to share one shape.

Where they went:

    GENERIC_COMPANY_FOLDER     capabilities/object_storage/paths
    ACCOUNT_LEVEL_FOLDER       capabilities/object_storage/paths
    resolve_company_folder     capabilities/object_storage/paths
    sanitize_company_folder    capabilities/object_storage/paths
    slugify (was _slugify)     capabilities/object_storage/paths
    driver_docs_bucket         features/drivers/documents/paths
    driver_docs_archive_bucket features/drivers/documents/paths
    vehicle_docs_bucket        features/vehicles/documents/paths
    vehicle_docs_archive_bucket features/vehicles/documents/paths
    work_order_folder          features/work_orders/paths

Same objects, not copies — the settings_registry / capabilities.source
shim pattern — so every existing importer keeps working while call
sites move at their own pace.  Delete when none remain.

``safe_attachment_name`` genuinely belongs to work-order attachments
and stays defined here.
"""

from __future__ import annotations

import re

# Deprecated aliases — same objects, so ``old is new`` holds and no
# second implementation can drift into existence.
from capabilities.object_storage.paths import (  # noqa: F401
    ACCOUNT_LEVEL_FOLDER,
    GENERIC_COMPANY_FOLDER,
    resolve_company_folder,
    sanitize_company_folder,
    slugify as _slugify,
)
from features.drivers.documents.paths import (  # noqa: F401
    driver_docs_archive_bucket,
    driver_docs_bucket,
)
from features.vehicles.documents.paths import (  # noqa: F401
    vehicle_docs_archive_bucket,
    vehicle_docs_bucket,
)
from features.work_orders.paths import work_order_folder  # noqa: F401


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
