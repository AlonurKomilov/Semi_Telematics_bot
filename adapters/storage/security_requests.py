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
        status_class: str | None = None,
        since_hours: int | None = None,
        limit: int = 200,
    ) -> list[dict]:
        """Newest first.  ``account_id`` gives one account's timeline;
        ``statuses`` narrows to a list, ``status_class`` to a class —
        ``denied`` (401/403/429: the wall), ``broke`` (5xx: something
        they reached), ``ok`` (2xx) — and ``since_hours`` bounds.

        Rows carry the acting user's display name so the console can
        say WHO, not just which id: the operator is reading a person's
        timeline, and a person has a name.
        """
        where: list[str] = []
        params: list = []
        if account_id is not None:
            where.append("r.account_id = ?")
            params.append(account_id)
        if statuses:
            where.append("r.status IN (" + ",".join("?" * len(statuses)) + ")")
            params.extend(int(s) for s in statuses)
        if status_class == "denied":
            where.append("r.status IN (401, 403, 429)")
        elif status_class == "broke":
            where.append("r.status >= 500")
        elif status_class == "ok":
            where.append("r.status BETWEEN 200 AND 299")
        elif status_class not in (None, "", "all"):
            raise ValueError(f"unknown status_class: {status_class!r}")
        if since_hours:
            cutoff = (datetime.now(timezone.utc) - timedelta(hours=int(since_hours))).isoformat()
            where.append("r.created_at >= ?")
            params.append(cutoff)
        params.append(int(limit))
        sql = ("SELECT r.id, r.created_at, r.account_id, r.user_id, u.display_name AS user_name, "
               "r.role, r.kind, r.method, r.path, r.query, r.status, r.duration_ms, r.ip, r.ua, "
               "r.request_id FROM security_requests r LEFT JOIN users u ON u.id = r.user_id "
               + ("WHERE " + " AND ".join(where) if where else "")
               + " ORDER BY r.created_at DESC, r.id DESC LIMIT ?")
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

    async def security_monitored_summary(self, *, since_hours: int = 24) -> list[dict]:
        """Every monitored account with its ledger counts in the window.

        One query, accounts-first: an account marked monitored an hour
        ago with nothing recorded yet still appears, at zero — the
        operator must see that watching started, not infer it from
        absence.  Ordered so the noisiest sits on top.
        """
        cutoff = (datetime.now(timezone.utc) - timedelta(hours=int(since_hours))).isoformat()
        cur = await self._db.execute(
            """
            SELECT a.id AS account_id, a.name, a.kind, a.created_at,
                   COUNT(r.id) AS requests,
                   COALESCE(SUM(CASE WHEN r.status IN (401, 403) THEN 1 ELSE 0 END), 0) AS refused,
                   COALESCE(SUM(CASE WHEN r.status >= 500 THEN 1 ELSE 0 END), 0) AS broke,
                   MAX(r.created_at) AS last_seen
              FROM accounts a
              LEFT JOIN security_requests r
                     ON r.account_id = a.id AND r.created_at >= ?
             WHERE a.kind = 'monitored'
             GROUP BY a.id, a.name, a.kind, a.created_at
             ORDER BY requests DESC, a.name
            """,
            (cutoff,),
        )
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
