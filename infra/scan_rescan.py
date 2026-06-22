"""Background batch rescan — re-checks every stored KB attachment
against the current ClamAV signature database.

The per-upload scan (``infra.file_safety.scan_or_reject``) catches
malware at the moment of upload.  But ClamAV's signature DB updates
every few hours via ``freshclam``: a file uploaded yesterday could
be flagged by today's signatures.  This module is the operator's
"re-verify the back catalog" lever, triggered from the System
Dashboard's File Scans page.

Design:
  * One job at a time — enforced at the endpoint via
    ``get_active_rescan_job()`` check before insert.
  * Cancellable via an in-memory token (operator hits Cancel; loop
    checks the flag each iteration and exits cleanly).
  * Each scan goes through the same ``scan_or_reject`` pipeline as
    new uploads (including the rate limit + concurrency cap), so a
    big rescan never starves new uploads of clamd capacity.
  * Infected files are NOT deleted — they're quarantined (marked on
    the KB article).  Operator reviews quarantine list and decides
    restore vs delete.

Job lifecycle:
  start_rescan()
    → status='running', writes scan_log rows as it goes
    → on each file: scan, update counters, check cancel flag
    → on finish:   status='completed'
    → on cancel:   status='cancelled'
    → on error:    status='failed' + last_error captured
"""

from __future__ import annotations

import asyncio
import logging

from infra.platform import get_platform_db
from adapters.storage.object_store import get_object_store_for_account

logger = logging.getLogger(__name__)


# In-memory cancellation state.  Keyed by job_id so a cancel for
# job 42 doesn't affect a future job 43.  Set by ``request_cancel``,
# consumed by the worker loop each iteration.
_cancel_flags: dict[int, asyncio.Event] = {}
# Track the running task so we don't double-spawn even if the DB
# active-job check races.  ``None`` when no job in flight.
_running_task: asyncio.Task | None = None


def is_rescan_in_flight() -> bool:
    """True if a rescan task is currently executing in this process.

    Defence-in-depth alongside the DB ``get_active_rescan_job`` check
    — covers the (unlikely) race where two operators hit Start at the
    same millisecond and both see no active job.
    """
    return _running_task is not None and not _running_task.done()


def request_cancel(job_id: int) -> bool:
    """Signal the rescan loop to stop at its next iteration.

    Returns True if the cancel was accepted (job was running and
    flag was set), False otherwise.  Idempotent — calling twice
    is a no-op.
    """
    ev = _cancel_flags.get(job_id)
    if ev is None or ev.is_set():
        return False
    ev.set()
    return True


async def start_rescan(started_by: int | None) -> int:
    """Create a new rescan job + spawn the background worker task.

    Returns the new job_id.  Caller (the endpoint) should have already
    refused if ``get_active_rescan_job()`` returned a running job and
    ``is_rescan_in_flight()`` returned True.
    """
    global _running_task
    pdb = get_platform_db()
    job_id = await pdb.start_rescan_job(started_by=started_by)
    cancel_ev = asyncio.Event()
    _cancel_flags[job_id] = cancel_ev
    _running_task = asyncio.create_task(_run_job(job_id, cancel_ev))
    return job_id


async def _run_job(job_id: int, cancel_ev: asyncio.Event) -> None:
    """Worker loop — iterate targets, scan each, update progress.

    Wrapped in a broad try/except so any unexpected error transitions
    the job to ``failed`` (with the traceback captured in last_error)
    instead of leaving it stuck in ``running``.  The endpoint's
    "refuse second concurrent run" check depends on that.
    """
    pdb = get_platform_db()
    clean = infected = errors = 0

    try:
        targets = await pdb.list_rescan_targets()
        total = len(targets)
        await pdb.update_rescan_progress(job_id, total_files=total)
        logger.info("Rescan job %d: %d files to scan", job_id, total)

        # Per-account object_store handle cache so we don't reopen on
        # every target.  Most accounts have multiple KB articles, so
        # this is a real win at fleet scale.
        store_cache: dict[int, object] = {}

        for i, t in enumerate(targets, start=1):
            if cancel_ev.is_set():
                logger.info(
                    "Rescan job %d cancelled at %d/%d", job_id, i - 1, total,
                )
                await pdb.complete_rescan_job(job_id, status="cancelled")
                return

            account_id = int(t["account_id"])
            article_id = int(t["id"])
            file_path = (t.get("media_url") or "").strip()

            # Object-store fetch.  Skip silently if the file went missing
            # since upload (rare — the retention orphan scan would have
            # caught it) so a missing file doesn't crash the whole job.
            try:
                store = store_cache.get(account_id)
                if store is None:
                    store = await get_object_store_for_account(account_id, None)
                    store_cache[account_id] = store
                getter = getattr(store, "get_by_id", None)
                data = getter(file_path) if getter else None
            except Exception as e:
                logger.debug(
                    "Rescan job %d: fetch failed for article %d: %s",
                    job_id, article_id, e,
                )
                data = None

            if data is None:
                errors += 1
            else:
                # Run through the same scan_or_reject as live uploads —
                # honours the same rate-limit + concurrency cap so a
                # big rescan doesn't starve new uploads of clamd.
                # Surface='rescan' so the scan_log row is distinguishable
                # from the original upload's scan in audits.
                from infra.file_safety import scan_or_reject as _av_scan
                ok, reason = await _av_scan(
                    data,
                    user_id=None,
                    account_id=account_id,
                    content_type=t.get("media_type"),
                    surface="rescan",
                )
                if ok:
                    clean += 1
                elif reason == "av_scan_unavailable":
                    # Daemon outage mid-rescan — bail rather than mark
                    # everything else as errors.  Operator restarts
                    # when clamd is back.
                    errors += 1
                    logger.error(
                        "Rescan job %d: clamd unavailable at %d/%d — aborting",
                        job_id, i, total,
                    )
                    await pdb.update_rescan_progress(
                        job_id, scanned_files=i,
                        clean_count=clean, infected_count=infected,
                        error_count=errors,
                    )
                    await pdb.complete_rescan_job(
                        job_id, status="failed",
                        last_error="ClamAV unavailable mid-rescan",
                    )
                    return
                else:
                    # Infected — mark the article quarantined.  Reason
                    # is the verdict string from scan_or_reject which
                    # already includes the signature name.
                    infected += 1
                    try:
                        await pdb.mark_article_quarantined(
                            article_id, reason=reason,
                        )
                    except Exception as e:
                        logger.warning(
                            "Rescan job %d: quarantine mark failed for article %d: %s",
                            job_id, article_id, e,
                        )

            # Update progress every 10 scans (or on the last item) so
            # the dashboard's progress bar advances visibly without
            # hammering the DB with one UPDATE per file.
            if i % 10 == 0 or i == total:
                await pdb.update_rescan_progress(
                    job_id,
                    scanned_files=i,
                    clean_count=clean,
                    infected_count=infected,
                    error_count=errors,
                )

        await pdb.complete_rescan_job(job_id, status="completed")
        logger.info(
            "Rescan job %d completed: %d scanned, %d clean, %d infected, %d errors",
            job_id, total, clean, infected, errors,
        )

    except asyncio.CancelledError:
        # Task itself was cancelled (process shutdown / supervisor).
        # Don't swallow — re-raise so the loop runner exits cleanly.
        await pdb.complete_rescan_job(
            job_id, status="cancelled",
            last_error="worker task cancelled",
        )
        raise
    except Exception as e:
        logger.exception("Rescan job %d failed: %s", job_id, e)
        try:
            await pdb.complete_rescan_job(
                job_id, status="failed", last_error=str(e)[:500],
            )
        except Exception:
            pass
    finally:
        # Clean up cancel flag + clear the running-task pointer so a
        # next rescan can start.
        _cancel_flags.pop(job_id, None)
        global _running_task
        _running_task = None
