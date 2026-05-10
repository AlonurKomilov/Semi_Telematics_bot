"""Coaching (Auto Coaching) CRUD mixin.

Tables:
  - coaching_topics           — per-account topic catalogue
  - coaching_rules            — score / incident triggers
  - coaching_assignments      — one row per assigned coaching action
  - coaching_acknowledgments  — driver ack history
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, Optional

if TYPE_CHECKING:
    class _MixinBase:
        """Typing stub — attributes provided by the concrete DB class at runtime."""
        _db: Any

        @staticmethod
        def _now() -> str: ...
else:
    _MixinBase = object


# ── Default topic catalogue (seeded lazily on first access) ──────────
DEFAULT_TOPICS: tuple[tuple[str, str, str], ...] = (
    ("hard_brake",         "Hard Braking",         "Reduce hard braking events by anticipating traffic."),
    ("harsh_accel",        "Harsh Acceleration",   "Use smoother acceleration to save fuel and reduce wear."),
    ("speeding",           "Speeding",             "Stay within posted speed limits at all times."),
    ("distraction",        "Distraction",          "Keep your full attention on the road; avoid phone use."),
    ("following_distance", "Following Distance",   "Maintain a safe gap from the vehicle in front."),
    ("fatigue",            "Fatigue",              "Take regulated breaks and watch for drowsiness signs."),
)


class CoachingMixin(_MixinBase):

    # ── topics ────────────────────────────────────────────────

    async def seed_default_coaching_topics(self, account_id: int) -> int:
        """Insert default topics for ``account_id`` (idempotent — INSERT OR IGNORE)."""
        now = self._now()
        inserted = 0
        for key, label, msg in DEFAULT_TOPICS:
            cur = await self._db.execute(
                "INSERT OR IGNORE INTO coaching_topics "
                "(account_id, key, label, default_message, active, updated_at) "
                "VALUES (?, ?, ?, ?, 1, ?)",
                (account_id, key, label, msg, now),
            )
            if cur.rowcount:
                inserted += 1
        await self._db.commit()
        return inserted

    async def list_coaching_topics(
        self, account_id: int, active_only: bool = False,
    ) -> list[dict]:
        sql = "SELECT * FROM coaching_topics WHERE account_id = ?"
        if active_only:
            sql += " AND active = 1"
        sql += " ORDER BY key ASC"
        cur = await self._db.execute(sql, (account_id,))
        rows = await cur.fetchall()
        return [dict(r) for r in rows]

    async def upsert_coaching_topic(
        self, account_id: int, key: str, *, label: str = "",
        default_message: str = "", active: bool = True,
    ) -> None:
        now = self._now()
        await self._db.execute(
            "INSERT INTO coaching_topics (account_id, key, label, default_message, active, updated_at) "
            "VALUES (?, ?, ?, ?, ?, ?) "
            "ON CONFLICT(account_id, key) DO UPDATE SET "
            "  label = excluded.label, "
            "  default_message = excluded.default_message, "
            "  active = excluded.active, "
            "  updated_at = excluded.updated_at",
            (account_id, key, label, default_message, 1 if active else 0, now),
        )
        await self._db.commit()

    # ── rules ─────────────────────────────────────────────────

    async def list_coaching_rules(
        self, account_id: int, active_only: bool = False,
    ) -> list[dict]:
        sql = "SELECT * FROM coaching_rules WHERE account_id = ?"
        if active_only:
            sql += " AND active = 1"
        sql += " ORDER BY id ASC"
        cur = await self._db.execute(sql, (account_id,))
        rows = await cur.fetchall()
        return [dict(r) for r in rows]

    async def get_coaching_rule(
        self, account_id: int, rule_id: int,
    ) -> Optional[dict]:
        cur = await self._db.execute(
            "SELECT * FROM coaching_rules WHERE account_id = ? AND id = ?",
            (account_id, rule_id),
        )
        row = await cur.fetchone()
        return dict(row) if row else None

    async def create_coaching_rule(
        self, account_id: int, *, name: str, kind: str,
        topic_key: str, period_days: int = 7,
        score_max: Optional[float] = None,
        event_type: Optional[str] = None,
        min_count: Optional[int] = None,
        severity: str = "medium",
        message: str = "",
        active: bool = True,
    ) -> int:
        now = self._now()
        cur = await self._db.execute(
            "INSERT INTO coaching_rules "
            "(account_id, name, kind, score_max, event_type, min_count, "
            " period_days, topic_key, severity, message, active, created_at, updated_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (account_id, name, kind, score_max, event_type, min_count,
             period_days, topic_key, severity, message,
             1 if active else 0, now, now),
        )
        await self._db.commit()
        return cur.lastrowid or 0

    async def update_coaching_rule(
        self, account_id: int, rule_id: int, **fields: Any,
    ) -> bool:
        if not fields:
            return False
        allowed = {
            "name", "kind", "score_max", "event_type", "min_count",
            "period_days", "topic_key", "severity", "message", "active",
        }
        sets: list[str] = []
        vals: list[Any] = []
        for k, v in fields.items():
            if k not in allowed:
                continue
            sets.append(f"{k} = ?")
            vals.append(int(v) if k == "active" else v)
        if not sets:
            return False
        sets.append("updated_at = ?")
        vals.extend([self._now(), account_id, rule_id])
        await self._db.execute(
            f"UPDATE coaching_rules SET {', '.join(sets)} "
            "WHERE account_id = ? AND id = ?",
            tuple(vals),
        )
        await self._db.commit()
        return True

    async def delete_coaching_rule(
        self, account_id: int, rule_id: int,
    ) -> bool:
        await self._db.execute(
            "DELETE FROM coaching_rules WHERE account_id = ? AND id = ?",
            (account_id, rule_id),
        )
        await self._db.commit()
        return True

    # ── assignments ───────────────────────────────────────────

    async def create_coaching_assignment(
        self, account_id: int, *, driver_id: str, topic_key: str,
        rule_id: Optional[int] = None,
        severity: str = "medium",
        reason: str = "",
        assigned_by: int = 0,
        due_at: Optional[str] = None,
    ) -> int:
        now = self._now()
        cur = await self._db.execute(
            "INSERT INTO coaching_assignments "
            "(account_id, driver_id, rule_id, topic_key, severity, reason, "
            " status, assigned_by, assigned_at, due_at) "
            "VALUES (?, ?, ?, ?, ?, ?, 'pending', ?, ?, ?)",
            (account_id, driver_id, rule_id, topic_key, severity, reason,
             assigned_by, now, due_at),
        )
        await self._db.commit()
        return cur.lastrowid or 0

    async def create_coaching_assignments_bulk(
        self, account_id: int, items: list[dict],
        *, assigned_by: int = 0,
    ) -> list[int]:
        """Bulk-create coaching assignments and return inserted IDs.

        ``items`` is a list of dicts with keys: ``driver_id``,
        ``topic_key``, ``rule_id``, ``severity``, ``reason``. Replaces
        the P × per-proposal INSERT-then-commit loop in
        coaching/service.py with one ``executemany`` and one commit.

        Uses ``RETURNING id`` (SQLite 3.35+ / Postgres) so callers still
        get the new ids back without a second round-trip. The ordering
        of returned IDs matches the input order.
        """
        if not items:
            return []
        now = self._now()
        params = [
            (
                account_id,
                str(it.get("driver_id") or ""),
                it.get("rule_id"),
                str(it.get("topic_key") or ""),
                str(it.get("severity") or "medium"),
                str(it.get("reason") or ""),
                assigned_by,
                now,
                it.get("due_at"),
            )
            for it in items
        ]
        # executemany + RETURNING is supported by aiosqlite ≥ 0.18 and
        # asyncpg; both drivers expose .fetchall() on the cursor.
        cur = await self._db.executemany(
            "INSERT INTO coaching_assignments "
            "(account_id, driver_id, rule_id, topic_key, severity, reason, "
            " status, assigned_by, assigned_at, due_at) "
            "VALUES (?, ?, ?, ?, ?, ?, 'pending', ?, ?, ?) "
            "RETURNING id",
            params,
        )
        ids: list[int] = []
        try:
            rows = await cur.fetchall()
            ids = [int(r[0]) for r in rows if r and r[0] is not None]
        except Exception:
            # Driver doesn't surface RETURNING from executemany — fall
            # back to a SELECT keyed on the timestamp we just stamped.
            ids = []
        await self._db.commit()
        if not ids:
            sel = await self._db.execute(
                "SELECT id FROM coaching_assignments "
                "WHERE account_id = ? AND assigned_at = ? AND assigned_by = ? "
                "ORDER BY id",
                (account_id, now, assigned_by),
            )
            ids = [int(r[0]) for r in await sel.fetchall()]
        return ids

    async def has_pending_coaching_assignment(
        self, account_id: int, driver_id: str, topic_key: str,
        within_days: int,
    ) -> bool:
        """Return True if a pending assignment exists for (driver, topic)
        whose ``assigned_at`` falls within the last ``within_days``."""
        cur = await self._db.execute(
            "SELECT 1 FROM coaching_assignments "
            "WHERE account_id = ? AND driver_id = ? AND topic_key = ? "
            "  AND status = 'pending' "
            "  AND datetime(assigned_at) >= datetime('now', ?) "
            "LIMIT 1",
            (account_id, driver_id, topic_key, f"-{int(within_days)} days"),
        )
        row = await cur.fetchone()
        return row is not None

    async def get_pending_assignments_recent(
        self, account_id: int, within_days: int,
    ) -> dict[tuple[str, str], str]:
        """Return ``{(driver_id, topic_key): most_recent_assigned_at}`` for
        every pending assignment within the trailing ``within_days``.

        Bulk replacement for ``has_pending_coaching_assignment`` when the
        coaching engine evaluates a whole fleet — one query instead of
        D × R round-trips. Caller passes the *largest* ``period_days``
        across all rules and then re-filters per-rule in Python so each
        rule's idempotency window stays correct.
        """
        cur = await self._db.execute(
            "SELECT driver_id, topic_key, MAX(assigned_at) "
            "FROM coaching_assignments "
            "WHERE account_id = ? AND status = 'pending' "
            "  AND datetime(assigned_at) >= datetime('now', ?) "
            "GROUP BY driver_id, topic_key",
            (account_id, f"-{int(within_days)} days"),
        )
        out: dict[tuple[str, str], str] = {}
        for row in await cur.fetchall():
            did = str(row[0] or "")
            tk = str(row[1] or "")
            out[(did, tk)] = str(row[2] or "")
        return out

    async def list_coaching_assignments(
        self, account_id: int, *,
        driver_id: Optional[str] = None,
        status: Optional[str] = None,
        limit: int = 200,
    ) -> list[dict]:
        sql = "SELECT * FROM coaching_assignments WHERE account_id = ?"
        args: list[Any] = [account_id]
        if driver_id:
            sql += " AND driver_id = ?"
            args.append(driver_id)
        if status:
            sql += " AND status = ?"
            args.append(status)
        sql += " ORDER BY assigned_at DESC LIMIT ?"
        args.append(int(limit))
        cur = await self._db.execute(sql, tuple(args))
        rows = await cur.fetchall()
        return [dict(r) for r in rows]

    async def get_coaching_assignment(
        self, account_id: int, assignment_id: int,
    ) -> Optional[dict]:
        cur = await self._db.execute(
            "SELECT * FROM coaching_assignments "
            "WHERE account_id = ? AND id = ?",
            (account_id, assignment_id),
        )
        row = await cur.fetchone()
        return dict(row) if row else None

    async def set_coaching_assignment_status(
        self, account_id: int, assignment_id: int, status: str,
    ) -> bool:
        if status == "acknowledged":
            await self._db.execute(
                "UPDATE coaching_assignments "
                "SET status = ?, acknowledged_at = ? "
                "WHERE account_id = ? AND id = ?",
                (status, self._now(), account_id, assignment_id),
            )
        else:
            await self._db.execute(
                "UPDATE coaching_assignments SET status = ? "
                "WHERE account_id = ? AND id = ?",
                (status, account_id, assignment_id),
            )
        await self._db.commit()
        return True

    # ── acknowledgments ───────────────────────────────────────

    async def add_coaching_acknowledgment(
        self, *, assignment_id: int, driver_id: str, note: str = "",
    ) -> int:
        cur = await self._db.execute(
            "INSERT INTO coaching_acknowledgments "
            "(assignment_id, driver_id, acked_at, note) "
            "VALUES (?, ?, ?, ?)",
            (assignment_id, driver_id, self._now(), note),
        )
        await self._db.commit()
        return cur.lastrowid or 0

    async def get_coaching_acknowledgments(
        self, assignment_id: int,
    ) -> list[dict]:
        cur = await self._db.execute(
            "SELECT * FROM coaching_acknowledgments "
            "WHERE assignment_id = ? ORDER BY acked_at ASC",
            (assignment_id,),
        )
        rows = await cur.fetchall()
        return [dict(r) for r in rows]
