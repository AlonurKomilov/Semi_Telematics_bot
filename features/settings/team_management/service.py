"""Team Management domain logic — driver-folder archiving on company removal."""
import logging

logger = logging.getLogger(__name__)


async def _archive_driver_folders(
    platform_db, tenant_db, account_id: int,
    user_id: int, removed_company_codes: list[str],
) -> list[str]:
    """Move the driver's docs folder under each removed company to
    that company's ``_archive/{date}/`` subtree.  Updates the bucket
    column on existing driver_documents rows so the download/delete
    routes can still find the files after the move.

    Returns the list of company codes whose folders were actually
    archived (i.e. had a non-empty source folder).  Errors during the
    physical move are logged but don't fail the assignment write —
    the company-change should always succeed at the DB level; storage
    cleanup is best-effort.
    """
    from datetime import datetime, timezone
    from adapters.storage.object_store import get_object_store_for_account
    from features.work_orders.storage import (
        driver_docs_archive_bucket, driver_docs_bucket, resolve_company_folder,
    )

    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    store = await get_object_store_for_account(account_id, tenant_db)
    archived: list[str] = []

    for company_code in removed_company_codes:
        try:
            company_folder = await resolve_company_folder(
                tenant_db, account_id, company_code,
            )
            src = driver_docs_bucket(company_folder, user_id)
            dst = driver_docs_archive_bucket(company_folder, user_id, today)
            moved = store.move_folder(src, dst)
            if moved:
                await platform_db.move_user_documents_bucket(user_id, src, dst)
                archived.append(company_code)
        except Exception as e:
            logger.warning(
                "Archive failed for driver=%d company=%s: %s",
                user_id, company_code, e,
            )
    return archived
