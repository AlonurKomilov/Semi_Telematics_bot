"""Scan-log mixin — persistence for per-upload virus scan events.

Writes come from ``infra.file_safety.scan_or_reject`` (one row per
upload scan attempt — clean, infected, OR daemon-unavailable).
Reads feed the operator console's File Scans page:

  * ``record_scan_event``   — single-row insert, called from the hot path
  * ``get_recent_scans``    — tail-read for the recent-activity table
  * ``get_scan_stats``      — aggregates for the 7-day summary card

Lives as a mixin so the single ``Database`` class exposes it the same
way as ai_usage / error_log.  Append-only; pruning is handled by the
nightly retention engine (``platform.scan_log`` target, 90-day window).
"""

from __future__ import annotations


class ScanLogMixin:

    async def record_scan_event(
        self,
        *,
        account_id: int | None,
        user_id: int | None,
        surface: str,
        file_size: int | None,
        content_type: str | None,
        verdict: str,
        signature: str | None = None,
        latency_ms: int | None = None,
    ) -> None:
        """Persist one scan_log row.  Best-effort: any exception is
        swallowed so a logging hiccup never blocks the actual upload
        rejection that prompted the record."""
        try:
            await self._db.execute(
                """INSERT INTO scan_log
                   (account_id, user_id, surface, file_size, content_type,
                    verdict, signature, latency_ms)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
                (account_id, user_id, surface, file_size, content_type,
                 verdict, signature, latency_ms),
            )
        except Exception:
            # Logger import deferred — same pattern as error_log to
            # avoid a circular import at module load.
            import logging
            logging.getLogger(__name__).debug(
                "record_scan_event insert failed (verdict=%s) — swallowed",
                verdict,
            )

    async def get_recent_scans(self, *, limit: int = 50) -> list[dict]:
        """Last ``limit`` scan events, newest first.  Used by the
        operator console's recent-activity table."""
        cur = await self._db.execute(
            """SELECT id, account_id, user_id, surface, file_size,
                      content_type, verdict, signature, latency_ms, created_at
               FROM scan_log
               ORDER BY created_at DESC
               LIMIT ?""",
            (int(limit),),
        )
        rows = await cur.fetchall()
        return [dict(r) for r in rows]

    async def get_scan_stats(self, *, days: int = 7) -> dict:
        """Aggregate the last ``days`` days of scan activity into a
        single dict matching the operator console's stats card:

            {
              "total":        <count>,
              "clean":        <count>,
              "infected":     <count>,
              "unavailable":  <count>,
              "latency_p50":  <ms>,
              "latency_p95":  <ms>,
              "latency_max":  <ms>,
              "top_signatures": [{"signature": "...", "count": N}, ...],
              "window_days":  <days>,
            }

        Empty result (no scans in window) returns sensible defaults
        (zeros / empty list) so the dashboard renders cleanly without
        special-casing the cold-start path.
        """
        since = (
            "NOW() - INTERVAL '%d days'" % int(days)
        )
        # One aggregate query for counts; one for the percentile rollup.
        # Postgres ``percentile_disc`` handles NULL latency rows by
        # ignoring them (intended — unavailable rows have no latency).
        cur = await self._db.execute(
            f"""SELECT
                   COUNT(*)                                AS total,
                   COUNT(*) FILTER (WHERE verdict='clean')       AS clean,
                   COUNT(*) FILTER (WHERE verdict='infected')    AS infected,
                   COUNT(*) FILTER (WHERE verdict='unavailable') AS unavailable,
                   percentile_disc(0.50) WITHIN GROUP (
                       ORDER BY latency_ms
                   )                                       AS p50,
                   percentile_disc(0.95) WITHIN GROUP (
                       ORDER BY latency_ms
                   )                                       AS p95,
                   MAX(latency_ms)                         AS p_max
               FROM scan_log
               WHERE created_at >= {since}""",
            (),
        )
        row = await cur.fetchone()
        if row is None:
            row_d: dict = {}
        else:
            row_d = dict(row)

        # Top-5 most-frequently-blocked signatures in the window.
        # Filtered to non-null + verdict=infected so a clean run never
        # surfaces a phantom "top signature".
        sig_cur = await self._db.execute(
            f"""SELECT signature, COUNT(*) AS count
                FROM scan_log
                WHERE created_at >= {since}
                  AND verdict='infected'
                  AND signature IS NOT NULL
                GROUP BY signature
                ORDER BY count DESC
                LIMIT 5""",
            (),
        )
        sig_rows = await sig_cur.fetchall()

        return {
            "total":        int(row_d.get("total") or 0),
            "clean":        int(row_d.get("clean") or 0),
            "infected":     int(row_d.get("infected") or 0),
            "unavailable":  int(row_d.get("unavailable") or 0),
            "latency_p50":  int(row_d.get("p50") or 0),
            "latency_p95":  int(row_d.get("p95") or 0),
            "latency_max":  int(row_d.get("p_max") or 0),
            "top_signatures": [
                {"signature": r["signature"], "count": int(r["count"])}
                for r in sig_rows
            ],
            "window_days":  int(days),
        }

    # ── Tier 3: ad-hoc rescan job + quarantine ────────────────────

    async def start_rescan_job(self, *, started_by: int | None) -> int:
        """Create a new ``running`` rescan job row, return its id.

        Caller is expected to have checked ``get_active_rescan_job``
        first and confirmed nothing is running — this method does NOT
        re-check (race-free single-job-at-a-time is enforced at the
        endpoint via an advisory lock pattern would be nicer; for now
        relying on the operator console's UI not exposing a second
        Start button while one is running).
        """
        cur = await self._db.execute(
            "INSERT INTO scan_rescan_jobs (started_by, status) "
            "VALUES (?, 'running') RETURNING id",
            (started_by,),
        )
        row = await cur.fetchone()
        return int(row["id"]) if row else 0

    async def update_rescan_progress(
        self,
        job_id: int,
        *,
        total_files: int | None = None,
        scanned_files: int | None = None,
        clean_count: int | None = None,
        infected_count: int | None = None,
        error_count: int | None = None,
    ) -> None:
        """Patch one or more progress counters on a running job.

        Called periodically by the background worker so the dashboard's
        live progress card reflects ongoing work without each scan
        triggering its own DB roundtrip.  Any unset arg is left alone.
        """
        sets = []
        vals = []
        if total_files is not None:
            sets.append("total_files = ?")
            vals.append(int(total_files))
        if scanned_files is not None:
            sets.append("scanned_files = ?")
            vals.append(int(scanned_files))
        if clean_count is not None:
            sets.append("clean_count = ?")
            vals.append(int(clean_count))
        if infected_count is not None:
            sets.append("infected_count = ?")
            vals.append(int(infected_count))
        if error_count is not None:
            sets.append("error_count = ?")
            vals.append(int(error_count))
        if not sets:
            return
        vals.append(int(job_id))
        await self._db.execute(
            f"UPDATE scan_rescan_jobs SET {', '.join(sets)} WHERE id = ?",
            tuple(vals),
        )

    async def complete_rescan_job(
        self,
        job_id: int,
        *,
        status: str = "completed",
        last_error: str | None = None,
    ) -> None:
        """Mark a job as terminal (completed / cancelled / failed)."""
        await self._db.execute(
            "UPDATE scan_rescan_jobs SET status = ?, completed_at = NOW(), "
            "last_error = ? WHERE id = ?",
            (status, last_error, int(job_id)),
        )

    async def cancel_rescan_job(self, job_id: int, *, cancelled_by: int | None) -> None:
        """Mark a job as ``cancelled``.  The actual loop interruption
        happens in-process via a cancellation token; this just records
        WHO cancelled + transitions the DB status."""
        await self._db.execute(
            "UPDATE scan_rescan_jobs SET status = 'cancelled', "
            "completed_at = NOW(), cancelled_by = ? WHERE id = ?",
            (cancelled_by, int(job_id)),
        )

    async def get_active_rescan_job(self) -> dict | None:
        """Return the currently-running job row, or None.  Used by the
        endpoint to refuse a second concurrent rescan."""
        cur = await self._db.execute(
            "SELECT * FROM scan_rescan_jobs WHERE status = 'running' "
            "ORDER BY started_at DESC LIMIT 1",
            (),
        )
        row = await cur.fetchone()
        return dict(row) if row else None

    async def get_rescan_job(self, job_id: int) -> dict | None:
        cur = await self._db.execute(
            "SELECT * FROM scan_rescan_jobs WHERE id = ?", (int(job_id),),
        )
        row = await cur.fetchone()
        return dict(row) if row else None

    async def list_recent_rescan_jobs(self, *, limit: int = 10) -> list[dict]:
        """Recent jobs for the dashboard's history table (excludes the
        currently-running one — that's surfaced separately)."""
        cur = await self._db.execute(
            "SELECT * FROM scan_rescan_jobs WHERE status != 'running' "
            "ORDER BY started_at DESC LIMIT ?",
            (int(limit),),
        )
        rows = await cur.fetchall()
        return [dict(r) for r in rows]

    async def list_quarantined_articles(self) -> list[dict]:
        """KB articles currently flagged as infected by a rescan.

        Returns a flat list across all accounts (system-operator view).
        Per-article details: account, title, who created, when
        quarantined, the signature name from the rescan.
        """
        cur = await self._db.execute(
            "SELECT id, account_id, title, category, visibility, "
            "       quarantined_at, quarantined_reason, created_by, "
            "       creator_name "
            "FROM knowledge_base "
            "WHERE quarantined_at IS NOT NULL "
            "ORDER BY quarantined_at DESC",
            (),
        )
        rows = await cur.fetchall()
        return [dict(r) for r in rows]

    async def mark_article_quarantined(self, article_id: int, *, reason: str) -> None:
        """Set quarantine columns on a KB article.  Called by the
        rescan worker when ClamAV flags an existing file.  Idempotent
        — re-marking just overwrites the reason + bumps the timestamp."""
        await self._db.execute(
            "UPDATE knowledge_base SET quarantined_at = NOW(), "
            "quarantined_reason = ? WHERE id = ?",
            (reason, int(article_id)),
        )

    async def restore_quarantined_article(self, article_id: int) -> None:
        """Clear the quarantine flag — operator confirms the file is
        a false positive or has been cleaned externally."""
        await self._db.execute(
            "UPDATE knowledge_base SET quarantined_at = NULL, "
            "quarantined_reason = NULL WHERE id = ?",
            (int(article_id),),
        )

    async def list_rescan_targets(self) -> list[dict]:
        """Every KB article whose stored file is uploaded-to-our-store
        (not an external URL).  This is the iteration target for the
        rescan worker — external URLs aren't ours to scan, and rows
        with no media_url have nothing to scan.

        Returns a flat list across all accounts.  Ordering by id
        (stable) so a cancelled+resumed run could pick up where it
        left off (resume isn't implemented yet but ordering is
        future-proofed).
        """
        cur = await self._db.execute(
            "SELECT id, account_id, media_url, media_type "
            "FROM knowledge_base "
            "WHERE media_url IS NOT NULL "
            "  AND media_url <> '' "
            "  AND media_url NOT LIKE 'http://%' "
            "  AND media_url NOT LIKE 'https://%' "
            "ORDER BY id",
            (),
        )
        rows = await cur.fetchall()
        return [dict(r) for r in rows]
