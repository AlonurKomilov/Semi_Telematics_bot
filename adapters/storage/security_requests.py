"""Security request ledger — persistence for the security console.

Writes come from ``capabilities.security.recorder`` (called by the API's
metering middleware, best-effort); reads feed the operator console's
Security page: an account's timeline, and the map of which endpoints
held and which broke.  A mixin so the single ``Database`` exposes it —
register on ``Database`` only (see the dual-class history note).

What is stored is deliberately narrow: method, path, a truncated query
(never on /api/auth/*), status, timing, the true client IP, a truncated
user agent, and who was authenticated.  There is no column for a body
and no method takes one.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone


class SecurityRequestsMixin:

    async def record_security_request(
        self,
        *,
        method: str,
        path: str,
        status: int,
        account_id: int | None = None,
        user_id: int | None = None,
        role: str | None = None,
        kind: str | None = None,
        query: str | None = None,
        duration_ms: int | None = None,
        ip: str | None = None,
        ua: str | None = None,
        request_id: str | None = None,
    ) -> None:
        """Persist one request.  The recorder decides WHETHER; this only writes."""
        now = self._now()
        await self._db.execute(
            """INSERT INTO security_requests
               (created_at, account_id, user_id, role, kind, method, path, query,
                status, duration_ms, ip, ua, request_id)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (now, account_id, user_id, role, kind, method, path, query,
             int(status), duration_ms, ip, ua, request_id),
        )
        await self._db.commit()

    async def list_security_requests(
        self,
        *,
        account_id: int | None = None,
        statuses: tuple[int, ...] | None = None,
        since_hours: int | None = None,
        limit: int = 200,
    ) -> list[dict]:
        """Newest first.  ``account_id`` gives one account's timeline;
        ``statuses`` narrows (e.g. the denials); ``since_hours`` bounds."""
        where: list[str] = []
        params: list = []
        if account_id is not None:
            where.append("account_id = ?")
            params.append(account_id)
        if statuses:
            where.append("status IN (" + ",".join("?" * len(statuses)) + ")")
            params.extend(int(s) for s in statuses)
        if since_hours:
            cutoff = (datetime.now(timezone.utc) - timedelta(hours=int(since_hours))).isoformat()
            where.append("created_at >= ?")
            params.append(cutoff)
        params.append(int(limit))
        sql = ("SELECT id, created_at, account_id, user_id, role, kind, method, path, query, "
               "status, duration_ms, ip, ua, request_id FROM security_requests "
               + ("WHERE " + " AND ".join(where) if where else "")
               + " ORDER BY created_at DESC, id DESC LIMIT ?")
        cur = await self._db.execute(sql, params)
        return [dict(r) for r in await cur.fetchall()]

    async def security_endpoint_map(
        self, *, account_id: int | None = None, since_hours: int | None = None,
    ) -> list[dict]:
        """Per endpoint, how many of each status class — the good/bad map.

        401/403 = the wall held.  500 = a bug they found.  A 2xx on a
        path that should have refused is the row to look at.  Grouped by
        method + path; the console renders the counts side by side.
        """
        where: list[str] = []
        params: list = []
        if account_id is not None:
            where.append("account_id = ?")
            params.append(account_id)
        if since_hours:
            cutoff = (datetime.now(timezone.utc) - timedelta(hours=int(since_hours))).isoformat()
            where.append("created_at >= ?")
            params.append(cutoff)
        sql = ("SELECT method, path, "
               "SUM(CASE WHEN status BETWEEN 200 AND 299 THEN 1 ELSE 0 END) AS ok, "
               "SUM(CASE WHEN status IN (401, 403) THEN 1 ELSE 0 END) AS refused, "
               "SUM(CASE WHEN status = 429 THEN 1 ELSE 0 END) AS throttled, "
               "SUM(CASE WHEN status BETWEEN 400 AND 499 AND status NOT IN (401, 403, 429) THEN 1 ELSE 0 END) AS rejected, "
               "SUM(CASE WHEN status >= 500 THEN 1 ELSE 0 END) AS broke, "
               "COUNT(*) AS total "
               "FROM security_requests "
               + ("WHERE " + " AND ".join(where) if where else "")
               + " GROUP BY method, path ORDER BY broke DESC, refused DESC, total DESC")
        cur = await self._db.execute(sql, params)
        return [dict(r) for r in await cur.fetchall()]

    async def count_security_requests(
        self, *, since_hours: int = 24, statuses: tuple[int, ...] | None = None,
    ) -> int:
        cutoff = (datetime.now(timezone.utc) - timedelta(hours=int(since_hours))).isoformat()
        sql = "SELECT COUNT(*) FROM security_requests WHERE created_at >= ?"
        params: list = [cutoff]
        if statuses:
            sql += " AND status IN (" + ",".join("?" * len(statuses)) + ")"
            params.extend(int(s) for s in statuses)
        cur = await self._db.execute(sql, params)
        row = await cur.fetchone()
        return int(row[0]) if row else 0

    async def prune_security_requests(self, keep_days: int) -> int:
        """Delete rows older than ``keep_days``.  Retention target executor.

        The cutoff is computed here as the same ISO-8601 text the rows
        were written with, so the comparison is dialect-free.
        """
        cutoff = (datetime.now(timezone.utc) - timedelta(days=int(keep_days))).isoformat()
        cur = await self._db.execute(
            "DELETE FROM security_requests WHERE created_at < ?", (cutoff,))
        await self._db.commit()
        return int(getattr(cur, "rowcount", 0) or 0)
