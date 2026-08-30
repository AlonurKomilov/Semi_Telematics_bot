"""Where a truck's paperwork goes when the truck leaves, and comes back.

The driver company-change recipe, applied to vehicles: move the
physical folder, then rewrite the rows' ``bucket`` so reads keep
resolving.  Called by the vehicles router's archive and restore
routes.

Best-effort by contract: the lifecycle act itself already happened, and
a folder that failed to move is a misfiling rather than a loss — the
rows still point where the files are, so downloads work either way.
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone

logger = logging.getLogger(__name__)


async def move_documents_on_archive(
    tenant, account_id: int, vehicle_id: int,
) -> str | None:
    """Move a retiring truck's folder to the archive tree.

    Best-effort by contract: the archive itself already happened, and a
    folder that failed to move is a cosmetic misfiling, not a data
    loss — the rows still point at the old bucket, so downloads keep
    working either way.  Returns the new bucket, or None."""
    return await _move_documents(tenant, account_id, vehicle_id,
                                 to_archive=True)


async def move_documents_on_restore(
    tenant, account_id: int, vehicle_id: int,
) -> str | None:
    """The reverse — a restored truck's paperwork comes home."""
    return await _move_documents(tenant, account_id, vehicle_id,
                                 to_archive=False)


async def _move_documents(
    tenant, account_id: int, vehicle_id: int, *, to_archive: bool,
) -> str | None:
    from adapters.storage.object_storage import get_object_storage_for_account
    from features.work_orders.storage import (
        resolve_company_folder, vehicle_docs_archive_bucket,
        vehicle_docs_bucket,
    )

    docs = await tenant.list_vehicle_documents(account_id, vehicle_id)
    if not docs:
        return None
    v = await tenant.get_vehicle(account_id, vehicle_id)
    if v is None:
        return None
    company_folder = await resolve_company_folder(
        tenant, account_id, v.company_code,
    )
    live = vehicle_docs_bucket(company_folder, v.unit_number)
    if to_archive:
        date = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        src, dst = live, vehicle_docs_archive_bucket(
            company_folder, v.unit_number, date)
    else:
        # Restore: the CURRENT bucket on the rows is the archive
        # location (whatever date it carries); home is the live path.
        src, dst = docs[0].bucket, live
        if src == dst:
            return None
    store = await get_object_storage_for_account(account_id, tenant)
    moved = store.move_folder(src, dst)
    if moved:
        await tenant.move_vehicle_documents_bucket(
            account_id, vehicle_id, src, dst)
        return dst
    return None
