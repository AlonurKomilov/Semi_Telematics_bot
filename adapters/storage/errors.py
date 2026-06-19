"""Error-log mixin — the built-in error reporter's persistence layer.

Writes (``log_error``) come from ``infra.error_reporter``; reads
(``list_recent_errors`` / ``count_recent_errors``) feed the operator
console's Errors page and the Health card.  Lives as a mixin so the
single ``Database`` class (and the legacy ``PlatformDB``) both expose
it — register on ``Database`` only for anything new (see the
dual-class history note).
"""
from __future__ import annotations


class ErrorLogMixin:

    async def log_error(
        self,
        source: str,
        error_type: str,
        error_msg: str,
        traceback_text: str = "",
        *,
        job_name: str | None = None,
        account_id: int | None = None,
    ) -> None:
        """Persist one row to error_log.  Called from infra.error_reporter."""
        now = self._now()
        await self._db.execute(
            """INSERT INTO error_log
               (source, job_name, account_id, error_type, error_msg, traceback, created_at)
               VALUES (?, ?, ?, ?, ?, ?, ?)""",
            (source, job_name, account_id, error_type, error_msg, traceback_text or None, now),
        )
        await self._db.commit()

    async def list_recent_errors(
        self,
        source: str = "",
        limit: int = 100,
    ) -> list[dict]:
        """Recent ``error_log`` rows for the operator console, newest first.

        ``source`` filters to one of {api, bot, scheduler, system_bot,
        task, startup, …} when set.  The traceback can be large, so
        callers that only want a list should render it collapsed.
        """
        where = ""
        params: list = []
        if source:
            where = "WHERE source = ?"
            params.append(source)
        params.append(limit)
        cur = await self._db.execute(
            f"""
            SELECT id, source, job_name, account_id, error_type,
                   error_msg, traceback, created_at
            FROM error_log
            {where}
            ORDER BY created_at DESC, id DESC
            LIMIT ?
            """,
            params,
        )
        return [dict(r) for r in await cur.fetchall()]

    async def count_recent_errors(self, hours: int = 24) -> int:
        """Count error_log rows in the last ``hours`` — health-page signal.

        ``created_at`` is TEXT (ISO-8601); cast to timestamptz so the
        comparison against ``NOW() - INTERVAL`` is legal in Postgres.
        """
        cur = await self._db.execute(
            "SELECT COUNT(*) FROM error_log "
            f"WHERE created_at::timestamptz > datetime('now', '-{int(hours)} hours')",
        )
        row = await cur.fetchone()
        return int(row[0]) if row else 0
