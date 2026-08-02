"""``track_for_sync`` — the one call that makes a written file reach cloud.

``HybridObjectStore.put`` deliberately writes to disk ONLY; enqueueing
the Drive upload is a separate step so a slow, fragile Drive call never
blocks a user-facing upload.  The consequence is that a feature which
writes a file and forgets this call gets silent half-behaviour: the
account is configured for cloud sync, the file is on our disk, and it
never leaves.

That is exactly what happened.  For a long time only
``features/inspections`` made the call — its own private helper — so on
a ``hybrid`` account PTI media reached Drive and the other eight file
types (parking maps, dashcam stills, work-order attachments, FMCSA
application documents, knowledge files, maintenance attachments,
avatars, driver documents) never did.  Nothing errored; the promise was
just quietly smaller than the setting implied.

The helper now lives here, once, so wiring a writer is a single call and
the "did anyone remember?" question has one place to look.

TWO THINGS A CALLER MUST GET RIGHT
----------------------------------
``entity_type``  must exist in ``repointers.ENTITY_REFERENCE``.  The
                 worker deletes the local copy after upload and refuses
                 to do so for a type it cannot repoint, so an unknown
                 value parks the row instead of breaking reads.
``entity_id``    the PK of the row that HOLDS the reference — not a
                 parent.  For an application document that is the
                 application id (its docs_json carries every slot); for
                 a work-order attachment it is the attachment row, not
                 the work order.
"""

from __future__ import annotations

import logging

logger = logging.getLogger(__name__)


async def track_for_sync_if_hybrid(
    store,
    bucket: str,
    key: str,
    file_path: str,
    *,
    entity_type: str,
    entity_id: int,
    file_size: int = 0,
) -> None:
    """Enqueue a freshly-written file for cloud upload, if the account
    is on a backend that has a queue.

    Duck-typed: Disk and GDrive stores have no ``track_for_sync`` and are
    silent no-ops — disk keeps the file locally forever (correct), and
    gdrive already wrote straight to the cloud (also correct).

    NEVER raises.  The bytes are already safely on disk and the owning
    row already references them, so a failure here costs a delayed
    upload, not data.  Escalating it would turn a background-sync hiccup
    into a failed user upload.
    """
    method = getattr(store, "track_for_sync", None)
    if method is None:
        return
    try:
        await method(
            bucket, key, file_path,
            entity_type=entity_type, entity_id=entity_id,
            file_size=file_size,
        )
    except Exception:
        logger.exception(
            "track_for_sync failed for %s/%s entity=%s:%s "
            "(file is on disk and referenced; sync will be retried)",
            bucket, key, entity_type, entity_id,
        )
