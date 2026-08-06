"""Platform run ledgers — scheduler_jobs + retention_runs snapshots.

These span EVERY feature (retention prunes scorecards and email too;
the scheduler snapshot covers all jobs), so they live at the storage
root rather than inside the warehouse family they were split from.
"""

from __future__ import annotations

import json
import logging
import statistics
from datetime import date, datetime, timedelta, timezone
from typing import TYPE_CHECKING, Any, Iterable, Optional

from adapters.storage.warehouse._util import (
    VELOCITY_DRIVE_DAY_MIN_MILES,
    VELOCITY_MIN_COVERAGE_DAYS,
    VELOCITY_MIN_DRIVE_DAYS,
    _MixinBase,
    _dtc_id,
    _now_iso,
    _opt_float,
)

logger = logging.getLogger(__name__)


class OpsRunsMixin(_MixinBase):

    # ── Retention-run telemetry ──────────────────────────────────
    # One summary row per target per nightly retention run, so the
    # operator console can show "last run + rows deleted".  Global
    # (no account_id) — the run is aggregated across all accounts.
    # The table self-creates on first write so this stays a
    # behaviour-preserving, self-contained addition.

    async def _ensure_retention_runs_table(self) -> None:
        await self._db.execute(
            """
            CREATE TABLE IF NOT EXISTS retention_runs (
                id            INTEGER PRIMARY KEY AUTOINCREMENT,
                target_key    TEXT    NOT NULL,
                scope         TEXT    NOT NULL DEFAULT '',
                keep_days     INTEGER NOT NULL DEFAULT 0,
                rows_deleted  INTEGER NOT NULL DEFAULT 0,
                accounts      INTEGER NOT NULL DEFAULT 0,
                ran_at        TEXT    NOT NULL
            )
            """
        )
        await self._db.execute(
            "CREATE INDEX IF NOT EXISTS idx_retention_runs_target_ran "
            "ON retention_runs(target_key, ran_at DESC)"
        )


    async def record_retention_runs(self, ran_at: str, rows) -> int:
        """Persist one summary row per target for a completed run.

        ``rows`` is an iterable of dicts with ``target_key`` plus optional
        ``scope`` / ``keep_days`` / ``rows_deleted`` / ``accounts``.
        """
        await self._ensure_retention_runs_table()
        values = [
            (
                str(r.get("target_key") or ""),
                str(r.get("scope") or ""),
                int(r.get("keep_days") or 0),
                int(r.get("rows_deleted") or 0),
                int(r.get("accounts") or 0),
                ran_at,
            )
            for r in rows
            if r.get("target_key")
        ]
        if not values:
            return 0
        await self._db.executemany(
            "INSERT INTO retention_runs "
            "(target_key, scope, keep_days, rows_deleted, accounts, ran_at) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            values,
        )
        await self._db.commit()
        return len(values)


    async def get_latest_retention_runs(self) -> list[dict[str, Any]]:
        """The most recent run row per target (newest ``ran_at`` wins).

        Returns ``[]`` when no run has been recorded yet (table absent)."""
        try:
            cur = await self._db.execute(
                """
                SELECT DISTINCT ON (target_key)
                       target_key, scope, keep_days, rows_deleted, accounts, ran_at
                  FROM retention_runs
                 ORDER BY target_key, ran_at DESC
                """
            )
            return [dict(r) for r in await cur.fetchall()]
        except Exception:
            return []


    # ── Scheduler-job snapshot ───────────────────────────────────
    # The bot process snapshots its registered APScheduler jobs here so
    # the (separate) API process can show them on the operator console —
    # it can't read the bot's in-memory scheduler directly.  Self-creating
    # table, full-replace on each snapshot so removed jobs drop out.

    async def _ensure_scheduler_jobs_table(self) -> None:
        await self._db.execute(
            """
            CREATE TABLE IF NOT EXISTS scheduler_jobs (
                job_id       TEXT    PRIMARY KEY,
                trigger      TEXT    NOT NULL DEFAULT '',
                next_run_at  TEXT,
                category     TEXT    NOT NULL DEFAULT '',
                description  TEXT    NOT NULL DEFAULT '',
                updated_at   TEXT    NOT NULL
            )
            """
        )
        # Upgrade path for a table created before ``category`` existed.
        await self._db.execute(
            "ALTER TABLE scheduler_jobs ADD COLUMN IF NOT EXISTS "
            "category TEXT NOT NULL DEFAULT ''"
        )


    async def record_scheduler_jobs(self, rows) -> int:
        """Replace the snapshot of currently-registered scheduler jobs.

        ``rows`` is an iterable of dicts with ``job_id`` plus optional
        ``trigger`` / ``next_run_at`` / ``category`` / ``description``."""
        await self._ensure_scheduler_jobs_table()
        now = self._now()
        values = [
            (
                str(r.get("job_id") or ""),
                str(r.get("trigger") or ""),
                r.get("next_run_at"),
                str(r.get("category") or ""),
                str(r.get("description") or ""),
                now,
            )
            for r in rows
            if r.get("job_id")
        ]
        await self._db.execute("DELETE FROM scheduler_jobs")
        if values:
            await self._db.executemany(
                "INSERT INTO scheduler_jobs "
                "(job_id, trigger, next_run_at, category, description, updated_at) "
                "VALUES (?, ?, ?, ?, ?, ?)",
                values,
            )
        await self._db.commit()
        return len(values)


    async def get_scheduler_jobs(self) -> list[dict[str, Any]]:
        """The last snapshot of registered scheduler jobs, soonest-next
        first.  Returns ``[]`` when no snapshot exists yet."""
        try:
            cur = await self._db.execute(
                "SELECT job_id, trigger, next_run_at, category, description, updated_at "
                "FROM scheduler_jobs ORDER BY next_run_at ASC NULLS LAST, job_id"
            )
            return [dict(r) for r in await cur.fetchall()]
        except Exception:
            return []
