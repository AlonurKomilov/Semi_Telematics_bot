"""Background sync worker — drains ``storage_sync_queue`` to cloud.

Runs as an APScheduler interval job (see ``interfaces/bot/scheduler``),
ticking every ~60 s.  One pass:

  1. Claim up to ``SYNC_WORKER_BATCH_SIZE`` due rows using
     ``StorageSyncMixin.claim_pending_sync`` — that helper handles the
     ``FOR UPDATE SKIP LOCKED`` + lease push so multiple workers can
     run side-by-side at scale without ever processing the same row.
  2. Group claimed rows by account.  Build the per-account Drive
     client ONCE per pass — if that build fails (no Drive yet, or
     expired refresh token), mark every row in that account's batch
     as ``stuck`` immediately.  No retry storm — one ``invalid_grant``
     is enough signal that retrying the rest is wasted Drive API
     spend.
  3. For each account, upload each row's bytes from disk to Drive in
     parallel under ``SYNC_WORKER_ACCOUNT_CONCURRENCY`` (default 4).
     On success: flip the media row's ``storage_state`` to
     ``remote``, set ``remote_path``, delete the local file, remove
     the queue row.  On transient failure: ``mark_sync_failed``
     (backoff schedule in Phase 1).  On permanent failure
     (``invalid_grant`` / ``quota`` / ``forbidden``): ``mark_sync_stuck``.

Disk and Drive paths stay in lockstep — the worker uploads each row
under exactly the bucket the caller originally wrote to on disk.  That
keeps ``HybridObjectStore.get`` correct on the read-after-sync path
(disk miss → fall through to Drive at the SAME folder).

Pluggable ``drive_builder`` callable so tests inject a stub —
unit tests never touch the real Google API.
"""

from __future__ import annotations

import asyncio
import logging
import os
from typing import Awaitable, Callable, Optional

from adapters.storage.object_store import DiskObjectStore
from capabilities.object_store.repointers import build_all, other_rows_reference
from adapters.storage.storage_sync import (
    ERR_FORBIDDEN, ERR_QUOTA_EXCEEDED, ERR_RATE_LIMITED, ERR_TOKEN_EXPIRED,
    ERR_TRANSIENT, ERR_UNKNOWN,
    STATE_REMOTE,
)

logger = logging.getLogger(__name__)


# Outcomes the per-row pipeline returns — used for the aggregate
# summary the cron job logs each pass.
OUTCOME_SUCCEEDED = "succeeded"
OUTCOME_FAILED    = "failed"
OUTCOME_STUCK     = "stuck"


# Type alias for the Drive-client builder.  Production uses
# ``GDriveObjectStore.connect``; tests inject a stub that fabricates
# put-results from a dict.  The builder is async because Drive
# requires a DB read (encrypted refresh token).
DriveBuilder = Callable[[int, object], Awaitable[object]]


# ── Drive error classification ──────────────────────────────────────


def classify_drive_error(message: str) -> str:
    """Turn a free-text Drive failure into a stable error code.

    Permanent codes short-circuit the backoff ladder — we mark the row
    ``stuck`` immediately rather than retrying a state Google won't
    fix on its own (token expired, customer Drive quota full, folder
    permission revoked).  Transient codes go through the normal
    backoff so a momentary 5xx or rate-limit auto-recovers.
    """
    if not message:
        return ERR_UNKNOWN
    m = message.lower()
    # Permanent / actionable
    if "invalid_grant" in m or "expired or revoked" in m or "token has been expired" in m:
        return ERR_TOKEN_EXPIRED
    if "quota" in m and "exceeded" in m:
        return ERR_QUOTA_EXCEEDED
    if "storagequotaexceeded" in m:
        return ERR_QUOTA_EXCEEDED
    if "403" in m and ("forbidden" in m or "permission" in m or "insufficient" in m):
        return ERR_FORBIDDEN
    # Transient / retry-friendly
    if "429" in m or "rate" in m and "limit" in m:
        return ERR_RATE_LIMITED
    if "503" in m or "500" in m or "504" in m or "timeout" in m or "temporarily" in m:
        return ERR_TRANSIENT
    return ERR_UNKNOWN


def _is_permanent_outcome(code: str) -> bool:
    return code in (ERR_TOKEN_EXPIRED, ERR_QUOTA_EXCEEDED, ERR_FORBIDDEN)


# ── Default Drive builder ───────────────────────────────────────────


async def _default_drive_builder(account_id: int, tenant_db):
    from adapters.storage.object_store_gdrive import GDriveObjectStore
    return await GDriveObjectStore.connect(account_id, tenant_db)


# ── Worker entry point ──────────────────────────────────────────────


async def sync_pending_storage(
    _app=None,
    *,
    db=None,
    drive_builder: Optional[DriveBuilder] = None,
    disk: Optional[DiskObjectStore] = None,
) -> dict:
    """One pass of the storage-sync worker.

    Called by APScheduler every minute.  Tests call it directly with
    ``db=`` and ``drive_builder=`` set to a stub.

    Returns a small summary dict (``{succeeded, failed, stuck,
    accounts}``) — the scheduler logs it; later phases (Phase 5
    health endpoint) can persist it for metrics.
    """
    if db is None:
        from infra.platform import get_db
        db = get_db()
    # ``disk`` parameter is preserved for backwards compatibility
    # (some tests inject a bare DiskObjectStore stub).  When supplied,
    # we use it directly; when None, each account gets its own
    # account-aware DiskObjectStore so the per-tenant prefix
    # ``data/userdata/account-{id}/`` resolves correctly.
    explicit_disk = disk
    build_drive: DriveBuilder = drive_builder or _default_drive_builder

    batch_size = int(os.getenv("SYNC_WORKER_BATCH_SIZE", "8"))
    account_concurrency = int(os.getenv("SYNC_WORKER_ACCOUNT_CONCURRENCY", "4"))

    rows = await db.claim_pending_sync(limit=batch_size)
    summary = {"succeeded": 0, "failed": 0, "stuck": 0, "accounts": 0}
    if not rows:
        return summary

    by_account: dict[int, list] = {}
    for r in rows:
        by_account.setdefault(int(r["account_id"]), []).append(r)
    summary["accounts"] = len(by_account)

    for account_id, account_rows in by_account.items():
        # Build the account-aware disk store fresh for this batch so
        # ``disk.get(bucket, filename)`` walks under
        # ``data/userdata/account-{id}/...`` — matching where
        # ``HybridObjectStore`` wrote the bytes.  Without this, the
        # worker would read the un-prefixed path and miss every
        # newly-synced file.
        per_account_disk = explicit_disk if explicit_disk is not None else (
            DiskObjectStore(account_id=account_id)
        )
        try:
            account_summary = await _process_account_batch(
                db, per_account_disk, build_drive,
                account_id=account_id, rows=account_rows,
                concurrency=account_concurrency,
            )
        except Exception:
            logger.exception(
                "storage sync: account %d batch crashed; rows will be "
                "picked up after lease expiry", account_id,
            )
            continue
        for k in ("succeeded", "failed", "stuck"):
            summary[k] += account_summary.get(k, 0)

    if summary["succeeded"] or summary["failed"] or summary["stuck"]:
        logger.info(
            "storage sync pass: %d accounts, +%d synced, %d failed, %d stuck",
            summary["accounts"], summary["succeeded"],
            summary["failed"], summary["stuck"],
        )
    return summary


# ── Per-account batch ──────────────────────────────────────────────


async def _process_account_batch(
    db, disk, build_drive: DriveBuilder, *,
    account_id: int, rows: list, concurrency: int,
) -> dict:
    """Process one account's claimed rows.

    Builds Drive once.  If the build fails (token dead or no Drive
    yet), marks EVERY row in the batch as stuck with the appropriate
    code — no per-row retry of the same dead account.  This is the
    circuit-breaker baked into the design.
    """
    summary = {"succeeded": 0, "failed": 0, "stuck": 0}
    try:
        drive = await build_drive(account_id, db)
    except Exception as e:
        # No Drive connection / expired token.  Park the whole batch.
        code = classify_drive_error(str(e))
        if code == ERR_UNKNOWN:
            # Treat a "drive not configured" build error as token-
            # expired for UX — the dashboard message is "reconnect
            # Google Drive" either way, and we don't want to thrash
            # the queue with transient retries on a misconfiguration.
            code = ERR_TOKEN_EXPIRED
        logger.warning(
            "storage sync: drive unavailable for account %d (%s) — "
            "parking %d row(s) as stuck", account_id, e, len(rows),
        )
        for r in rows:
            await db.mark_sync_stuck(
                int(r["id"]), error=str(e)[:500], error_code=code,
            )
            summary["stuck"] += 1
        return summary

    sem = asyncio.Semaphore(max(1, int(concurrency)))

    async def _one(row):
        async with sem:
            try:
                return await _sync_one_row(db, disk, drive, row)
            except Exception as e:
                logger.exception(
                    "storage sync: row %s crashed", row.get("id"),
                )
                # Defensive: treat unexpected exceptions as transient
                # so the row drains via the lease + backoff instead of
                # being lost.
                await db.mark_sync_failed(
                    int(row["id"]), error=str(e)[:500],
                    error_code=ERR_TRANSIENT,
                )
                return OUTCOME_FAILED

    outcomes = await asyncio.gather(*(_one(r) for r in rows))
    for o in outcomes:
        if o == OUTCOME_SUCCEEDED:
            summary["succeeded"] += 1
        elif o == OUTCOME_STUCK:
            summary["stuck"] += 1
        else:
            summary["failed"] += 1
    return summary


# ── Repointers: entity_type → "the row now lives in Drive" ─────────
#
# The worker deletes the local file after a successful upload, so every
# synced entity type MUST have a way to update its owning row to the
# Drive id first.  Registering here is the ONLY thing that makes an
# entity type eligible for sync; an unregistered type is parked rather
# than silently broken (see _sync_one_row step 3).
#
# Signature: async (db, entity_id, drive_id) -> None.  Raise to abort the
# delete — the caller keeps the local copy and parks the row.


async def _repoint_pti_media(db, *, entity_id: int, drive_id: str, **_ctx) -> None:
    """PTI inspection media — the original (and long the only) case.

    Hand-written rather than registry-derived because it advances a
    STATE MACHINE as well as a path: ``storage_state`` drives which read
    path HybridObjectStore takes, so a generic single-column UPDATE
    would repoint the file and leave the row claiming it is still local.
    """
    await db.set_media_storage_state(
        entity_id,
        state=STATE_REMOTE,
        remote_path=drive_id,
        file_path=drive_id,
    )


# Registry-derived for every other type — see repointers.py for why
# these are built from the same declaration the orphan scan reads.
_REPOINTERS: dict[str, "Callable[..., Awaitable[None]]"] = build_all()
_REPOINTERS["pti_media"] = _repoint_pti_media


def register_repointer(entity_type: str, fn) -> None:
    """Declare how one entity type's row is updated after upload.

    Exposed so a feature can register its own without this module
    importing every feature — the same inversion the retention hub uses.
    """
    _REPOINTERS[entity_type] = fn


# ── Per-row pipeline ───────────────────────────────────────────────


async def _sync_one_row(db, disk, drive, row: dict) -> str:
    """Read from disk, push to Drive, transition state, clean up.

    Returns the outcome label so the batch aggregator can tally.
    Never raises — outcomes are reported via the mark_sync_* helpers
    so the row gracefully ends up in the right state regardless of
    what went wrong.
    """
    queue_id = int(row["id"])
    bucket = row["bucket"]
    filename = row["filename"]
    entity_type = row.get("entity_type") or ""
    entity_id = int(row.get("entity_id") or 0)

    # 1) Read bytes from disk.
    data = disk.get(bucket, filename)
    if data is None:
        # Orphaned queue row — the file vanished before we could sync.
        # Don't retry forever; park it so the dashboard shows the
        # operator and the queue stops trying.
        await db.mark_sync_stuck(
            queue_id,
            error="local file missing — orphan queue row",
            error_code="missing",
        )
        return OUTCOME_STUCK

    # 2) Push to Drive at the SAME bucket the file landed on disk —
    # no path rewriting.  Keeping disk and Drive in lockstep means
    # ``HybridObjectStore.get`` can fall through from a disk miss to
    # Drive's ``get(bucket, filename)`` and land on the right file.
    last_error_before = getattr(drive, "last_error", None)
    drive_id = await asyncio.to_thread(drive.put, bucket, filename, data)

    if not drive_id:
        # Drive's put swallows the exception + sets last_error to the
        # string form — surface that to classify the failure.
        reason = (getattr(drive, "last_error", None) or last_error_before
                  or "drive put returned empty")
        code = classify_drive_error(reason)
        if _is_permanent_outcome(code):
            await db.mark_sync_stuck(
                queue_id, error=str(reason)[:500], error_code=code,
            )
            return OUTCOME_STUCK
        await db.mark_sync_failed(
            queue_id, error=str(reason)[:500], error_code=code,
        )
        return OUTCOME_FAILED

    # 3) Success — REPOINT the owning row, then delete local, then
    #    remove the queue row.  Order matters and so does the guard.
    #
    #    Deleting the local file is only safe once the DB row points at
    #    Drive instead.  Until this guard existed the worker repointed
    #    ``pti_media`` and nothing else, while deleting the local copy
    #    unconditionally — so any other entity type would have had its
    #    row left pointing at a path that no longer exists.  Every image
    #    for that feature 404s, with no error anywhere: the upload
    #    succeeded, the queue row cleared, the file is safely in Drive,
    #    and the app simply can't find it.
    #
    #    Fail CLOSED for an unregistered entity type: keep the local
    #    file, park the row, and say why.  A feature that gets wired
    #    into sync without a repointer is a bug to surface, not a
    #    silent breakage to discover from user reports.
    repoint = _REPOINTERS.get(entity_type)
    if repoint is None:
        logger.error(
            "storage sync: no repointer registered for entity_type=%r "
            "(queue_id=%d) — file is in Drive but the local copy is kept "
            "and the row is NOT repointed, because deleting it would "
            "break every read for this feature",
            entity_type, queue_id,
        )
        await db.mark_sync_stuck(
            queue_id,
            error=(
                f"no repointer for entity_type={entity_type!r} — register "
                f"one in capabilities/object_store/sync_worker._REPOINTERS"
            ),
            error_code="no_repointer",
        )
        return OUTCOME_STUCK

    if entity_id:
        try:
            # Full queue-row context: a multi-file entity (an application
            # holding cdlFront/cdlBack/medical/signature) cannot tell
            # WHICH file just synced from entity_id alone — it matches on
            # local_path.
            await repoint(
                db,
                entity_id=entity_id,
                drive_id=str(drive_id),
                bucket=bucket,
                filename=filename,
                local_path=row.get("local_path") or "",
            )
        except Exception:
            # The bytes are in Drive but the row still points at disk.
            # Keep the local file and park the row: retrying the repoint
            # later is recoverable, deleting the only readable copy is
            # not.
            logger.exception(
                "storage sync: repoint failed for %s id=%d — keeping the "
                "local copy so reads keep working",
                entity_type, entity_id,
            )
            await db.mark_sync_stuck(
                queue_id,
                error="repoint failed after successful upload",
                error_code="repoint_failed",
            )
            return OUTCOME_STUCK

    # NEVER delete a local file another row still points at.  Some
    # writers keyed their object-store entry by vehicle rather than by
    # event, so one file backs many rows (cameras 52x, legacy parking
    # 12x).  Repointing one row and deleting the file would 404 every
    # other row silently.  Keeping the file costs disk; deleting it
    # costs images nobody can get back.
    shared_with = 0
    try:
        shared_with = await other_rows_reference(
            db, entity_type,
            entity_id=entity_id,
            local_path=row.get("local_path") or "",
        )
    except Exception:
        # Can't prove exclusivity -> assume shared. Fail safe.
        logger.exception(
            "storage sync: could not check shared references for %s id=%d "
            "— keeping the local copy", entity_type, entity_id,
        )
        shared_with = 1

    if shared_with:
        logger.info(
            "storage sync: keeping local %s/%s — %d other row(s) still "
            "reference it (shared object-store key)",
            bucket, filename, shared_with,
        )
    else:
        # The row now points at Drive, so a failed delete only costs
        # disk, never correctness.
        try:
            disk.delete(bucket, filename)
        except Exception:
            logger.warning(
                "storage sync: local delete failed for %s/%s (file is in "
                "Drive, ignoring)", bucket, filename,
            )

    await db.mark_sync_succeeded(queue_id)
    return OUTCOME_SUCCEEDED
