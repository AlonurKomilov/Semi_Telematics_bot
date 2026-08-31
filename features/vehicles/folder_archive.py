"""When a truck leaves the fleet, its whole folder goes with it.

    {COMPANY}/vehicles/110/            →  {COMPANY}/vehicles/_archive/{date}/110/
      documents/                            documents/
      work-orders/WO-…/                     work-orders/WO-…/

ARCHIVING A VEHICLE, not archiving documents.  The first version of
this moved only the truck's ``documents/`` subfolder into the truck
archive — it archived a vehicle by touching its paperwork, left its
work orders behind under a live truck that no longer existed, and named
the result as though documents were the thing being retired.  Archiving
one DOCUMENT is a different act entirely and lives in the documents
sub-feature: that one moves a superseded paper into
``…/vehicles/110/documents/_archive/`` and leaves the truck alone.

A folder move is only half the job.  Both row sets that address files
under the truck must be repointed, or their downloads 404 forever with
nothing in the product to explain it:

  * ``vehicle_documents.bucket``          — exact, one bucket per truck
  * ``work_order_attachments.file_path``  — prefix, one folder per WO

Best-effort by contract: the archive itself has already happened by the
time this runs, and a folder that failed to move is a misfiling rather
than a loss — the rows still point where the files actually are, so
every download keeps working either way.  What must never happen is a
move that succeeds while the rows are left behind, so the rewrite
follows the move immediately and the move is skipped when there is
nothing to move.
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone

logger = logging.getLogger(__name__)


async def archive_vehicle_folder(
    tenant, account_id: int, vehicle_id: int,
) -> str | None:
    """Move the truck's folder into the dated archive tree.  Returns
    the new folder, or None when there was nothing to move."""
    return await _move(tenant, account_id, vehicle_id, to_archive=True)


async def restore_vehicle_folder(
    tenant, account_id: int, vehicle_id: int,
) -> str | None:
    """The reverse — a restored truck's folder comes home."""
    return await _move(tenant, account_id, vehicle_id, to_archive=False)


async def _move(
    tenant, account_id: int, vehicle_id: int, *, to_archive: bool,
) -> str | None:
    from adapters.storage.object_storage import get_object_storage_for_account
    from capabilities.object_storage.paths import (
        resolve_company_folder, vehicle_archive_folder, vehicle_folder,
    )

    v = await tenant.get_vehicle(account_id, vehicle_id)
    if v is None or not v.unit_number:
        return None
    company_folder = await resolve_company_folder(
        tenant, account_id, v.company_code)
    live = vehicle_folder(company_folder, v.unit_number)

    if to_archive:
        date = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        src = live
        dst = vehicle_archive_folder(company_folder, v.unit_number, date)
    else:
        # Coming home: the CURRENT location is whatever archive date the
        # rows carry, which only the rows can tell us — the truck does
        # not remember when it left.
        docs = await tenant.list_vehicle_documents(account_id, vehicle_id)
        current = docs[0].bucket if docs else ""
        if not current or "/_archive/" not in current:
            return None
        # bucket is "<truck folder>/documents"; the truck folder is its parent.
        src = current.rsplit("/documents", 1)[0]
        dst = live
        if src == dst:
            return None

    store = await get_object_storage_for_account(account_id, tenant)
    if not store.move_folder(src, dst):
        return None

    # The move happened — repoint everything that addressed the old tree.
    try:
        await tenant.move_vehicle_documents_bucket(
            account_id, vehicle_id, f"{src}/documents", f"{dst}/documents")
    except Exception:
        logger.warning("archive: document rows not repointed v=%d acct=%d",
                       vehicle_id, account_id, exc_info=True)
    try:
        moved = await tenant.repoint_attachment_paths(account_id, src, dst)
        if moved:
            logger.info("archive: repointed %d work-order attachment(s) "
                        "for v=%d", moved, vehicle_id)
    except Exception:
        logger.warning("archive: attachment paths not repointed v=%d acct=%d",
                       vehicle_id, account_id, exc_info=True)
    return dst
