"""Storage-sync mixin — the durable outbox for the hybrid storage tier.

Phase 1 foundation for the disk-primary / cloud-async model.  This
module owns just the data layer:

  * The state machine on ``pti_inspection_media`` rows (advancing
    ``local → syncing → remote`` with ``stuck`` as a side state).
  * The polymorphic ``storage_sync_queue`` outbox — durable across
    process restarts, claimed by workers with ``SELECT … FOR UPDATE
    SKIP LOCKED`` so adding worker #2 (Phase 3) needs no code change.
  * Per-account quota arithmetic — the byte total that currently sits
    on the server's local disk waiting to sync.  Pre-upload gating
    (Phase 4) reads this; the storage dashboard (Phase 6) renders it.

The actual disk write, the Drive upload, and the worker scheduling
all live in later phases; the mixin only knows about the DB.
"""

from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone
from typing import Optional

logger = logging.getLogger(__name__)

# ── Settings keys ───────────────────────────────────────────────────

# Per-account override (bytes).  Read by ``get_account_disk_quota``;
# falls back to ``DEFAULT_DISK_QUOTA_BYTES`` when unset.
DISK_QUOTA_KEY = "storage.disk_quota_bytes"

# 5 GB — generous enough for normal use, small enough that a stuck
# account hits the alarm before swamping the server.
DEFAULT_DISK_QUOTA_BYTES = 5 * 1024 * 1024 * 1024


# ── State + queue constants ─────────────────────────────────────────

# Forward-only state machine on ``pti_inspection_media``.  Legacy rows
# default to ``'remote'`` so they're inert from the worker's POV.
STATE_LOCAL   = "local"
STATE_SYNCING = "syncing"
STATE_REMOTE  = "remote"
STATE_STUCK   = "stuck"

# Backoff schedule (seconds).  Chosen so a multi-hour outage recovers
# automatically + a permanently dead account bails out within a day.
# Phase 3's sync worker references this; Phase 1 lays the math.
_BACKOFF_SECONDS: list[int] = [
    30,           # attempt 1 → 30 s
    120,          # attempt 2 → 2 min
    600,          # attempt 3 → 10 min
    3600,         # attempt 4 → 1 h
    6 * 3600,     # attempt 5 → 6 h
    24 * 3600,    # attempt 6 → 24 h
]
# After this many failed attempts the row is promoted to ``stuck``.
MAX_SYNC_ATTEMPTS = len(_BACKOFF_SECONDS)

# Error codes the queue stores so the dashboard + circuit breaker can
# act on machine-readable causes (Phase 3 + 6 consume these).
ERR_TOKEN_EXPIRED  = "token_expired"
ERR_RATE_LIMITED   = "rate_limited"
ERR_QUOTA_EXCEEDED = "quota_exceeded"
ERR_FORBIDDEN      = "forbidden"
ERR_TRANSIENT      = "transient"
ERR_UNKNOWN        = "unknown"

# Errors that should NOT consume retries — they won't get better on
# their own.  The worker short-circuits to ``stuck`` immediately.
_PERMANENT_ERROR_CODES = frozenset({
    ERR_TOKEN_EXPIRED, ERR_QUOTA_EXCEEDED, ERR_FORBIDDEN,
})


def _iso_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def compute_next_attempt_at(attempts: int) -> str:
    """ISO timestamp for the next retry given the current attempt count.

    ``attempts`` is the count *after* the current failure (so the first
    failure passes ``1``).  Capped at the longest backoff; the caller
    is responsible for promoting to ``stuck`` once
    ``attempts >= MAX_SYNC_ATTEMPTS``.
    """
    idx = max(0, min(attempts - 1, len(_BACKOFF_SECONDS) - 1))
    delay = _BACKOFF_SECONDS[idx]
    return (
        datetime.now(timezone.utc) + timedelta(seconds=delay)
    ).isoformat(timespec="seconds")


def is_permanent_error(code: Optional[str]) -> bool:
    """True for failures that won't recover by retrying."""
    return (code or "") in _PERMANENT_ERROR_CODES


class StorageSyncMixin:
    """Database operations for the hybrid storage outbox + media state.

    Phase 1 surface only — no disk writes, no Drive calls.  Those plug
    in at the ObjectStore + worker layers in Phase 2/3.
    """

    # ── Queue: enqueue ──────────────────────────────────────────────

    async def enqueue_storage_sync(
        self,
        *,
        account_id: int,
        entity_type: str,
        entity_id: int,
        bucket: str,
        filename: str,
        local_path: str,
        file_size: int = 0,
    ) -> int:
        """Schedule a file for upload to the account's cloud backend.

        Polymorphic by ``entity_type`` (``'pti_media'`` today; later
        ``'work_order'``, ``'driver_doc'``, etc.) so one queue serves
        every module.  Idempotent on ``(entity_type, entity_id)``:
        re-enqueueing the same file resets the schedule rather than
        creating a duplicate row.

        Schedules the first attempt for ~now so the kick (Phase 3)
        picks it up immediately.
        """
        now = _iso_now()
        cur = await self._db.execute(
            """INSERT INTO storage_sync_queue
                 (account_id, entity_type, entity_id, bucket, filename,
                  local_path, file_size, attempts, next_attempt_at,
                  enqueued_at, updated_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, 0, ?, ?, ?)
               ON CONFLICT (entity_type, entity_id) DO UPDATE SET
                  bucket = excluded.bucket,
                  filename = excluded.filename,
                  local_path = excluded.local_path,
                  file_size = excluded.file_size,
                  attempts = 0,
                  next_attempt_at = excluded.next_attempt_at,
                  last_error = NULL,
                  error_code = NULL,
                  updated_at = excluded.updated_at
               RETURNING id""",
            (account_id, entity_type, entity_id, bucket, filename,
             local_path, int(file_size), now, now, now),
        )
        row = await cur.fetchone()
        await self._db.commit()
        return int(row["id"]) if row else 0

    # ── Queue: claim ────────────────────────────────────────────────

    async def claim_pending_sync(
        self,
        *,
        limit: int = 4,
        account_id: Optional[int] = None,
        lease_seconds: int = 300,
    ) -> list[dict]:
        """Atomically lease up to ``limit`` due queue rows for a worker.

        Two layers of crash safety:

        1. ``SELECT … FOR UPDATE SKIP LOCKED`` — multiple workers can
           run side-by-side; none picks a row another is reading.
        2. **Lease push**: ``next_attempt_at`` is bumped to
           ``now + lease_seconds`` inside the same transaction.  After
           commit the row-level lock releases, but the row is now
           "due" at the future lease expiry — so a worker that picks
           it up only sees it again if the leasing worker crashed
           past ``lease_seconds``.  The lease IS the recovery clock.

        ``attempts`` is incremented so the next outcome call
        (``mark_sync_failed``) computes backoff from the right base
        and a permanent-stuck check ``attempts >= MAX_SYNC_ATTEMPTS``
        works correctly even if the worker crashes.

        ``account_id`` (optional) scopes the claim to one tenant —
        used by the fair-share scheduler in the worker layer.

        ``lease_seconds`` defaults to 5 min — comfortably longer than
        any single Drive upload but short enough that crash recovery
        is fast.
        """
        now = _iso_now()
        lease_until = (
            datetime.now(timezone.utc) + timedelta(seconds=int(lease_seconds))
        ).isoformat(timespec="seconds")
        where = "next_attempt_at <= ?"
        params: list = [now]
        if account_id is not None:
            where += " AND account_id = ?"
            params.append(account_id)
        params.append(int(limit))
        cur = await self._db.execute(
            f"""SELECT id, account_id, entity_type, entity_id,
                       bucket, filename, local_path, file_size,
                       attempts, last_error, error_code,
                       enqueued_at, updated_at
                FROM storage_sync_queue
                WHERE {where}
                ORDER BY next_attempt_at ASC
                LIMIT ?
                FOR UPDATE SKIP LOCKED""",
            tuple(params),
        )
        rows = [dict(r) for r in await cur.fetchall()]
        if rows:
            # Bump attempts AND push next_attempt_at by the lease so
            # other workers' WHERE-clauses skip this row until either
            # we report an outcome or our lease expires.
            await self._db.executemany(
                "UPDATE storage_sync_queue "
                "SET attempts = attempts + 1, "
                "    next_attempt_at = ?, updated_at = ? "
                "WHERE id = ?",
                [(lease_until, now, r["id"]) for r in rows],
            )
        await self._db.commit()
        return rows

    # ── Queue: outcomes ─────────────────────────────────────────────

    async def mark_sync_succeeded(self, queue_id: int) -> bool:
        """Remove the queue row after the file landed in cloud storage.
        The companion ``transition_media_to_remote`` call handles the
        media-row state advance + local-file deletion."""
        cur = await self._db.execute(
            "DELETE FROM storage_sync_queue WHERE id = ?",
            (queue_id,),
        )
        await self._db.commit()
        return (cur.rowcount or 0) > 0

    async def mark_sync_failed(
        self,
        queue_id: int,
        *,
        error: str,
        error_code: str = ERR_TRANSIENT,
    ) -> dict:
        """Record a transient failure and schedule the next retry.

        Returns the row's current state ``{attempts, error_code,
        next_attempt_at}`` so the worker can decide whether to escalate
        to ``stuck`` (caller checks ``attempts >= MAX_SYNC_ATTEMPTS``
        OR ``is_permanent_error(error_code)``).
        """
        # The row already had its ``attempts`` incremented at claim
        # time, so we just write the error + recompute the schedule.
        cur = await self._db.execute(
            "SELECT attempts FROM storage_sync_queue WHERE id = ?",
            (queue_id,),
        )
        row = await cur.fetchone()
        if not row:
            return {}
        attempts = int(row["attempts"])
        next_at = compute_next_attempt_at(attempts)
        now = _iso_now()
        await self._db.execute(
            "UPDATE storage_sync_queue "
            "SET last_error = ?, error_code = ?, "
            "    next_attempt_at = ?, updated_at = ? "
            "WHERE id = ?",
            (error[:500], error_code, next_at, now, queue_id),
        )
        await self._db.commit()
        return {
            "attempts": attempts,
            "error_code": error_code,
            "next_attempt_at": next_at,
        }

    async def mark_sync_stuck(
        self,
        queue_id: int,
        *,
        error: str,
        error_code: str,
    ) -> bool:
        """Park a queue row indefinitely after a permanent failure.

        We push ``next_attempt_at`` 30 days out — the worker's polling
        ``WHERE next_attempt_at <= now()`` skips it, but the row stays
        visible for the dashboard's "stuck files" table + the manual
        "Retry now" action.  When the operator reconnects Drive and
        clicks Retry, the API resets ``next_attempt_at = now()`` and
        the row re-enters the worker loop.
        """
        now = _iso_now()
        parked = (
            datetime.now(timezone.utc) + timedelta(days=30)
        ).isoformat(timespec="seconds")
        cur = await self._db.execute(
            "UPDATE storage_sync_queue "
            "SET last_error = ?, error_code = ?, "
            "    next_attempt_at = ?, updated_at = ? "
            "WHERE id = ?",
            (error[:500], error_code, parked, now, queue_id),
        )
        await self._db.commit()
        return (cur.rowcount or 0) > 0

    async def retry_stuck_sync(self, queue_id: int) -> bool:
        """Operator action: re-schedule a stuck row for an immediate
        retry.  Wired from the storage dashboard's "Retry now" button."""
        now = _iso_now()
        cur = await self._db.execute(
            "UPDATE storage_sync_queue "
            "SET next_attempt_at = ?, last_error = NULL, "
            "    error_code = NULL, updated_at = ? "
            "WHERE id = ?",
            (now, now, queue_id),
        )
        await self._db.commit()
        return (cur.rowcount or 0) > 0

    # ── Queue: reads (for dashboard + worker fairness) ──────────────

    async def list_pending_sync(
        self, account_id: int, limit: int = 100,
    ) -> list[dict]:
        """Pending + stuck rows for one account, newest first.  Powers
        the storage dashboard table."""
        cur = await self._db.execute(
            """SELECT id, entity_type, entity_id, bucket, filename,
                      file_size, attempts, next_attempt_at,
                      last_error, error_code,
                      enqueued_at, updated_at
               FROM storage_sync_queue
               WHERE account_id = ?
               ORDER BY enqueued_at DESC
               LIMIT ?""",
            (account_id, limit),
        )
        return [dict(r) for r in await cur.fetchall()]

    async def list_storage_queue(
        self,
        account_id: int,
        *,
        only: str = "all",
        limit: int = 100,
    ) -> list[dict]:
        """Storage-dashboard listing — queue rows enriched with the
        inspection/media context the operator needs to recognise the
        file.

        ``only`` filter:
          * ``'all'``     — pending + stuck
          * ``'pending'`` — only rows still in the retry ladder
          * ``'stuck'``   — only rows parked on a permanent error

        Permanent errors short-circuit the backoff schedule (Phase 3),
        so the distinction maps to ``error_code`` — see
        ``is_permanent_error``.

        LEFT JOINs keep the query robust against polymorphic
        ``entity_type`` (today PTI media; later work orders, driver
        docs).  Rows whose entity type doesn't join through media
        still come back — the dashboard renders them with a generic
        "other" source label.
        """
        # Build a single SQL with WHERE filters applied conditionally.
        clauses = ["q.account_id = ?"]
        params: list = [account_id]
        if only == "stuck":
            # Listed permanent error codes match
            # ``_PERMANENT_ERROR_CODES`` in this module — kept inline
            # to avoid an IN-list parameter binding dance.
            clauses.append(
                "q.error_code IN ('token_expired','quota_exceeded','forbidden')"
            )
        elif only == "pending":
            clauses.append(
                "(q.error_code IS NULL OR q.error_code NOT IN "
                "('token_expired','quota_exceeded','forbidden'))"
            )
        where = " AND ".join(clauses)
        params.append(int(limit))

        cur = await self._db.execute(
            f"""SELECT
                  q.id, q.entity_type, q.entity_id,
                  q.bucket, q.filename, q.file_size,
                  q.attempts, q.next_attempt_at,
                  q.last_error, q.error_code,
                  q.enqueued_at, q.updated_at,
                  m.media_type, m.content_type,
                  m.inspection_id,
                  di.vehicle_name
                FROM storage_sync_queue q
                LEFT JOIN pti_inspection_media m
                       ON q.entity_type = 'pti_media' AND q.entity_id = m.id
                LEFT JOIN driver_inspections di
                       ON di.id = m.inspection_id
                WHERE {where}
                ORDER BY q.enqueued_at DESC
                LIMIT ?""",
            tuple(params),
        )
        return [dict(r) for r in await cur.fetchall()]

    async def retry_all_stuck_sync(self, account_id: int) -> int:
        """Re-schedule every stuck row in the account for an immediate
        retry.  Wired from the storage dashboard's "Retry all stuck"
        bulk action — the natural follow-up to reconnecting Drive.
        """
        now = _iso_now()
        cur = await self._db.execute(
            "UPDATE storage_sync_queue "
            "SET next_attempt_at = ?, last_error = NULL, "
            "    error_code = NULL, updated_at = ? "
            "WHERE account_id = ? "
            "  AND error_code IN ('token_expired','quota_exceeded','forbidden')",
            (now, now, account_id),
        )
        await self._db.commit()
        return int(cur.rowcount or 0)

    async def count_pending_sync(self, account_id: int) -> tuple[int, int]:
        """Returns ``(pending, stuck)`` for the account.  ``stuck`` is
        the subset whose ``error_code`` is permanent."""
        cur = await self._db.execute(
            "SELECT error_code FROM storage_sync_queue WHERE account_id = ?",
            (account_id,),
        )
        rows = await cur.fetchall()
        total = len(rows)
        stuck = sum(1 for r in rows if is_permanent_error(r["error_code"]))
        return total, stuck

    # ── Media state machine ─────────────────────────────────────────

    async def set_media_storage_state(
        self,
        media_id: int,
        *,
        state: str,
        local_path: Optional[str] = None,
        remote_path: Optional[str] = None,
        sha256: Optional[str] = None,
        file_path: Optional[str] = None,
    ) -> bool:
        """Advance a media row's storage state + path columns.

        Only updates columns the caller passes — a transition that
        doesn't yet know the remote path leaves ``remote_path``
        unchanged.  ``file_path`` is the legacy column some callers
        still read; we update it in lockstep so backward-compat code
        sees the canonical current location.
        """
        if state not in (STATE_LOCAL, STATE_SYNCING, STATE_REMOTE, STATE_STUCK):
            raise ValueError(f"unknown storage state: {state!r}")

        sets = ["storage_state = ?"]
        params: list = [state]
        if local_path is not None:
            sets.append("local_path = ?")
            params.append(local_path)
        if remote_path is not None:
            sets.append("remote_path = ?")
            params.append(remote_path)
        if sha256 is not None:
            sets.append("sha256 = ?")
            params.append(sha256)
        if file_path is not None:
            sets.append("file_path = ?")
            params.append(file_path)
        params.append(media_id)

        cur = await self._db.execute(
            f"UPDATE pti_inspection_media SET {', '.join(sets)} WHERE id = ?",
            tuple(params),
        )
        await self._db.commit()
        return (cur.rowcount or 0) > 0

    # ── Media-state aggregates (for /api/storage/health) ───────────

    async def count_media_by_state(self, account_id: int) -> dict[str, int]:
        """Count ``pti_inspection_media`` rows per ``storage_state``
        for the account.  Drives the storage-health widget.

        Joins through ``driver_inspections`` to scope by account —
        media rows don't carry an ``account_id`` of their own.  Returns
        a complete dict (all four states present, zero-defaulted) so
        the dashboard can render without null-coalescing each key.
        """
        cur = await self._db.execute(
            """SELECT m.storage_state, COUNT(*) AS cnt
                 FROM pti_inspection_media m
                 JOIN driver_inspections di ON di.id = m.inspection_id
                WHERE di.account_id = ?
                GROUP BY m.storage_state""",
            (account_id,),
        )
        out = {STATE_LOCAL: 0, STATE_SYNCING: 0, STATE_REMOTE: 0, STATE_STUCK: 0}
        for r in await cur.fetchall():
            state = (r["storage_state"] or STATE_REMOTE).lower()
            if state in out:
                out[state] = int(r["cnt"])
        return out

    # ── Per-account disk quota ──────────────────────────────────────

    async def get_account_disk_quota(self, account_id: int) -> int:
        """Bytes the account is permitted to hold on the local cache.

        Reads ``account_settings.storage.disk_quota_bytes``; falls
        back to ``DEFAULT_DISK_QUOTA_BYTES``.  Stored as a stringy
        integer in account_settings.
        """
        raw = await self.get_account_setting(  # type: ignore[attr-defined]
            account_id, DISK_QUOTA_KEY, str(DEFAULT_DISK_QUOTA_BYTES),
        )
        try:
            return max(0, int(raw))
        except (TypeError, ValueError):
            return DEFAULT_DISK_QUOTA_BYTES

    async def set_account_disk_quota(self, account_id: int, bytes_: int) -> None:
        await self.set_account_setting(  # type: ignore[attr-defined]
            account_id, DISK_QUOTA_KEY, str(max(0, int(bytes_))),
        )

    async def get_account_local_bytes(self, account_id: int) -> int:
        """Total bytes currently sitting on local disk for this account.

        Sums ``file_size`` across queue rows — they're the source of
        truth for "what's pending sync."  Cheap (index on account_id).
        """
        cur = await self._db.execute(
            "SELECT COALESCE(SUM(file_size), 0) AS s "
            "FROM storage_sync_queue WHERE account_id = ?",
            (account_id,),
        )
        row = await cur.fetchone()
        if row is None:
            return 0
        return int(row["s"] or 0)

    async def account_has_quota_for(
        self, account_id: int, additional_bytes: int,
    ) -> tuple[bool, int, int]:
        """Quota check used by the pre-upload gate (Phase 4).

        Returns ``(allowed, current_local_bytes, quota_bytes)`` so the
        caller can render an actionable 413 (e.g. "1.2 GB used of 5 GB,
        12 files pending sync — reconnect Drive to free space").
        """
        used = await self.get_account_local_bytes(account_id)
        quota = await self.get_account_disk_quota(account_id)
        return (used + max(0, int(additional_bytes)) <= quota, used, quota)

    # ── Account storage backend setter ──────────────────────────────

    async def set_account_storage_backend(
        self, account_id: int, backend: str,
    ) -> None:
        """Flip an account between ``'disk'``, ``'gdrive'``, ``'hybrid'``.

        Convenience for both the immediate Phase-1 unblock (ACME
        → disk) and the Phase 6 storage dashboard's "Switch backend"
        action.  Doesn't migrate existing files — that's a separate
        operational decision (existing GDrive files stay reachable as
        long as the OAuth token is alive).
        """
        if backend not in ("disk", "gdrive", "hybrid"):
            raise ValueError(f"unknown storage backend: {backend!r}")
        from adapters.storage.object_store import (
            STORAGE_BACKEND_KEY,
            invalidate_object_store_for_account,
        )
        await self.set_account_setting(  # type: ignore[attr-defined]
            account_id, STORAGE_BACKEND_KEY, backend,
        )
        # Flush the cached store so the next request rebuilds from the
        # new setting.
        invalidate_object_store_for_account(account_id)
