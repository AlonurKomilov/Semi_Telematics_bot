"""Detector findings and the signals they are computed from.

Two halves, one mixin:

- ``security_signal_*`` — the SQL the rules read.  Grouped counts over
  a time window from the tables the 2026-09-08 probe left its traces
  in: platform_audit_log (signups, with the IP in ``details``),
  login_attempts, users, password_reset_tokens, error_log and the
  security_requests ledger.  Thresholds live in the rules, not here.
- ``security_events`` — what the rules concluded.  One OPEN row per
  (kind, subject); a sweep that sees the same thing again updates that
  row (count = the latest window, last_seen moves) rather than opening
  a duplicate.  An acked or dismissed event is the operator's word; a
  later recurrence opens a NEW row so the decision is not silently
  reopened.

Timestamps are compared as the ISO-8601 text the rows were written
with, so the cutoff maths is dialect-free.
"""
from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone


def _cutoff(hours: int) -> str:
    return (datetime.now(timezone.utc) - timedelta(hours=int(hours))).isoformat()


class SecurityEventsMixin:

    # ── signals ───────────────────────────────────────────────────

    async def security_signal_signups(self, *, since_hours: int) -> list[dict]:
        """Self-serve signups in the window: account_id, name, ip, created_at."""
        cur = await self._db.execute(
            "SELECT account_id, details, created_at FROM platform_audit_log "
            "WHERE event = 'account_created' AND created_at >= ? ORDER BY created_at",
            (_cutoff(since_hours),),
        )
        out = []
        for r in await cur.fetchall():
            d = dict(r)
            details = d.get("details") or ""
            ip = None
            for tok in details.split():
                if tok.startswith("ip="):
                    ip = tok[3:]
            name = None
            if "name='" in details:
                name = details.split("name='", 1)[1].split("'", 1)[0]
            out.append({"account_id": d["account_id"], "name": name, "ip": ip, "created_at": d["created_at"]})
        return out

    async def security_signal_new_users(self, *, since_hours: int) -> list[dict]:
        cur = await self._db.execute(
            "SELECT id, account_id, email, created_at FROM users "
            "WHERE created_at >= ? AND email IS NOT NULL AND email != ''",
            (_cutoff(since_hours),),
        )
        return [dict(r) for r in await cur.fetchall()]

    async def security_signal_login_failures(self, *, since_hours: int) -> list[dict]:
        """Per (ip, failure_reason): how many, plus a sample user agent."""
        cur = await self._db.execute(
            "SELECT ip_address AS ip, failure_reason, COUNT(*) AS n, MAX(user_agent) AS ua, "
            "MAX(attempted_at) AS last_at "
            "FROM login_attempts WHERE attempted_at >= ? AND success = 0 "
            "GROUP BY ip_address, failure_reason",
            (_cutoff(since_hours),),
        )
        return [dict(r) for r in await cur.fetchall()]

    async def security_signal_auth_user_agents(self, *, since_hours: int) -> list[dict]:
        """Per (ip, user_agent) over ALL attempts — the tool, not the outcome."""
        cur = await self._db.execute(
            "SELECT ip_address AS ip, user_agent AS ua, COUNT(*) AS n, MAX(attempted_at) AS last_at "
            "FROM login_attempts WHERE attempted_at >= ? AND user_agent IS NOT NULL "
            "GROUP BY ip_address, user_agent",
            (_cutoff(since_hours),),
        )
        return [dict(r) for r in await cur.fetchall()]

    async def security_signal_reset_tokens(self, *, since_hours: int) -> list[dict]:
        cur = await self._db.execute(
            "SELECT t.user_id, u.email, u.account_id, COUNT(*) AS n, MAX(t.created_at) AS last_at "
            "FROM password_reset_tokens t LEFT JOIN users u ON u.id = t.user_id "
            "WHERE t.created_at >= ? GROUP BY t.user_id, u.email, u.account_id",
            (_cutoff(since_hours),),
        )
        return [dict(r) for r in await cur.fetchall()]

    async def security_signal_error_messages(self, *, since_hours: int) -> list[dict]:
        cur = await self._db.execute(
            "SELECT id, job_name, account_id, error_type, error_msg, created_at FROM error_log "
            "WHERE created_at >= ? AND source = 'api'",
            (_cutoff(since_hours),),
        )
        return [dict(r) for r in await cur.fetchall()]

    async def security_signal_refusals(self, *, since_hours: int) -> list[dict]:
        """Per (ip, account_id, path prefix class): refusals from the ledger."""
        cur = await self._db.execute(
            "SELECT ip, account_id, "
            "SUM(CASE WHEN path LIKE '/api/system/%' THEN 1 ELSE 0 END) AS system_refusals, "
            "SUM(CASE WHEN path LIKE '/api/admin/%' THEN 1 ELSE 0 END) AS admin_refusals, "
            "COUNT(*) AS refusals, MAX(created_at) AS last_at "
            "FROM security_requests WHERE created_at >= ? AND status IN (401, 403) "
            "GROUP BY ip, account_id",
            (_cutoff(since_hours),),
        )
        return [dict(r) for r in await cur.fetchall()]

    async def security_signal_request_queries(self, *, since_hours: int) -> list[dict]:
        cur = await self._db.execute(
            "SELECT id, ip, account_id, method, path, query, created_at FROM security_requests "
            "WHERE created_at >= ? AND query IS NOT NULL AND query != ''",
            (_cutoff(since_hours),),
        )
        return [dict(r) for r in await cur.fetchall()]

    # ── events ────────────────────────────────────────────────────

    async def upsert_security_event(
        self, *, kind: str, severity: str, subject_type: str, subject: str,
        summary: str, evidence: dict | None, count: int, seen_at: str,
    ) -> tuple[int, bool]:
        """Merge into the OPEN row for (kind, subject) or open a new one.

        Returns ``(event_id, opened)`` — ``opened`` is what the sweep
        alerts on: a recurrence of something the operator already saw
        is a row update, not a second ping.
        """
        now = self._now()
        cur = await self._db.execute(
            "SELECT id FROM security_events WHERE kind = ? AND subject = ? AND status = 'open' "
            "ORDER BY id DESC LIMIT 1",
            (kind, subject),
        )
        row = await cur.fetchone()
        ev = json.dumps(evidence or {}, default=str)
        if row:
            await self._db.execute(
                "UPDATE security_events SET severity = ?, summary = ?, evidence = ?, count = ?, "
                "last_seen = CASE WHEN ? > last_seen THEN ? ELSE last_seen END, updated_at = ? WHERE id = ?",
                (severity, summary, ev, int(count), seen_at, seen_at, now, row[0]),
            )
            await self._db.commit()
            return int(row[0]), False
        cur = await self._db.execute(
            "INSERT INTO security_events (kind, severity, subject_type, subject, summary, evidence, "
            "count, first_seen, last_seen, status, created_at, updated_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 'open', ?, ?) RETURNING id",
            (kind, severity, subject_type, subject, summary, ev, int(count), seen_at, seen_at, now, now),
        )
        new = await cur.fetchone()
        await self._db.commit()
        return int(new[0]), True

    async def list_security_events(self, *, status: str | None = "open", limit: int = 200) -> list[dict]:
        params: list = []
        where = ""
        if status and status != "all":
            where = "WHERE status = ?"
            params.append(status)
        params.append(int(limit))
        cur = await self._db.execute(
            f"SELECT id, kind, severity, subject_type, subject, summary, evidence, count, "
            f"first_seen, last_seen, status, created_at, updated_at FROM security_events {where} "
            "ORDER BY CASE severity WHEN 'high' THEN 0 WHEN 'medium' THEN 1 ELSE 2 END, last_seen DESC, id DESC LIMIT ?",
            params,
        )
        out = []
        for r in await cur.fetchall():
            d = dict(r)
            try:
                d["evidence"] = json.loads(d["evidence"]) if d.get("evidence") else {}
            except ValueError:
                d["evidence"] = {"raw": d.get("evidence")}
            out.append(d)
        return out

    async def get_security_event(self, event_id: int) -> dict | None:
        rows = await self.list_security_events(status="all", limit=100000)
        for r in rows:
            if r["id"] == int(event_id):
                return r
        return None

    async def set_security_event_status(self, event_id: int, status: str) -> bool:
        if status not in ("open", "acked", "monitored", "dismissed"):
            raise ValueError(f"unknown security event status: {status!r}")
        cur = await self._db.execute(
            "UPDATE security_events SET status = ?, updated_at = ? WHERE id = ?",
            (status, self._now(), int(event_id)),
        )
        await self._db.commit()
        return bool(getattr(cur, "rowcount", 0))

    async def count_security_events(self, *, status: str = "open") -> int:
        cur = await self._db.execute("SELECT COUNT(*) FROM security_events WHERE status = ?", (status,))
        row = await cur.fetchone()
        return int(row[0]) if row else 0

    async def prune_security_events(self, keep_days: int) -> int:
        """Only settled events age out; an open finding is never pruned."""
        cutoff = (datetime.now(timezone.utc) - timedelta(days=int(keep_days))).isoformat()
        cur = await self._db.execute(
            "DELETE FROM security_events WHERE status != 'open' AND updated_at < ?", (cutoff,))
        await self._db.commit()
        return int(getattr(cur, "rowcount", 0) or 0)
