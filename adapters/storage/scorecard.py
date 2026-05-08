"""Scorecard storage mixin — CRUD for score_rules, score_events,
daily_scorecard_snapshots."""

from __future__ import annotations

import json
from typing import TYPE_CHECKING, Any, Optional

if TYPE_CHECKING:
    class _MixinBase:
        """Typing stub — attributes provided by the concrete DB class at runtime."""
        _db: Any

        async def read_all(self, sql: str, params: tuple = ()) -> list: ...
        async def read_one(self, sql: str, params: tuple = ()) -> Any: ...
        @staticmethod
        def _now() -> str: ...
else:
    _MixinBase = object


class ScorecardMixin(_MixinBase):

    # ── Rule overrides ────────────────────────────────────────────

    async def upsert_score_rule(
        self, account_id: int, rule_id: str, *,
        label: str = "", category: str = "",
        kind: str = "penalty", points: int = 0,
        cap: Optional[int] = None, enabled: bool = True,
        # ── Audit Option C additions (all optional / nullable) ──
        pillar: str = "",
        curve_x_zero: Optional[float] = None,
        curve_x_max:  Optional[float] = None,
        curve_y_max:  Optional[int]   = None,
    ) -> None:
        await self._db.execute(
            """INSERT INTO score_rules
                   (account_id, rule_id, label, category, kind, points, cap, enabled,
                    pillar, curve_x_zero, curve_x_max, curve_y_max, updated_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
               ON CONFLICT(account_id, rule_id) DO UPDATE SET
                   label        = excluded.label,
                   category     = excluded.category,
                   kind         = excluded.kind,
                   points       = excluded.points,
                   cap          = excluded.cap,
                   enabled      = excluded.enabled,
                   pillar       = excluded.pillar,
                   curve_x_zero = excluded.curve_x_zero,
                   curve_x_max  = excluded.curve_x_max,
                   curve_y_max  = excluded.curve_y_max,
                   updated_at   = excluded.updated_at""",
            (account_id, rule_id, label, category, kind, int(points),
             cap, 1 if enabled else 0,
             pillar, curve_x_zero, curve_x_max,
             int(curve_y_max) if curve_y_max is not None else None,
             self._now()),
        )
        await self._db.commit()

    async def get_score_rule_overrides(self, account_id: int) -> dict[str, dict]:
        """Return ``{rule_id: {points, cap, enabled, curve_*}}`` for account.

        Curve fields are surfaced so ``rules.apply_overrides`` can re-tune
        the per-rule ramp anchors per tenant. NULL values pass through —
        the engine treats them as "use code default".
        """
        rows = await self.read_all(
            "SELECT rule_id, points, cap, enabled, "
            "       pillar, curve_x_zero, curve_x_max, curve_y_max "
            "  FROM score_rules WHERE account_id = ?",
            (account_id,),
        )
        out: dict[str, dict] = {}
        for row in rows:
            keys = row.keys() if hasattr(row, "keys") else []
            out[row["rule_id"]] = {
                "points":  int(row["points"]),
                "cap":     row["cap"],
                "enabled": bool(row["enabled"]),
                "pillar":       row["pillar"]       if "pillar"       in keys else "",
                "curve_x_zero": row["curve_x_zero"] if "curve_x_zero" in keys else None,
                "curve_x_max":  row["curve_x_max"]  if "curve_x_max"  in keys else None,
                "curve_y_max":  row["curve_y_max"]  if "curve_y_max"  in keys else None,
            }
        return out

    async def delete_score_rule(self, account_id: int, rule_id: str) -> bool:
        cur = await self._db.execute(
            "DELETE FROM score_rules WHERE account_id = ? AND rule_id = ?",
            (account_id, rule_id),
        )
        await self._db.commit()
        return cur.rowcount > 0

    # ── Score events (evidence trail) ─────────────────────────────

    async def add_score_event(
        self, account_id: int, *,
        subject_type: str, subject_id: str,
        rule_id: str, points: int,
        occurred_at: Optional[str] = None,
        evidence_type: str = "", evidence_id: str = "",
    ) -> int:
        now = self._now()
        cur = await self._db.execute(
            """INSERT INTO score_events
                   (account_id, subject_type, subject_id, rule_id, points,
                    occurred_at, evidence_type, evidence_id, created_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (account_id, subject_type, subject_id, rule_id, int(points),
             occurred_at or now, evidence_type, evidence_id, now),
        )
        await self._db.commit()
        return cur.lastrowid

    async def batch_add_score_events(
        self, account_id: int, rows: list[dict],
    ) -> int:
        """Insert multiple score_event rows in one transaction.

        Each dict in *rows* must have keys:
          subject_type, subject_id, rule_id, points, occurred_at
        Optional: evidence_type, evidence_id
        Returns the number of rows inserted.
        """
        if not rows:
            return 0
        now = self._now()
        params = [
            (
                account_id,
                r["subject_type"],
                r["subject_id"],
                r["rule_id"],
                int(r["points"]),
                r.get("occurred_at") or now,
                r.get("evidence_type", ""),
                r.get("evidence_id", ""),
                now,
            )
            for r in rows
        ]
        await self._db.executemany(
            """INSERT INTO score_events
                   (account_id, subject_type, subject_id, rule_id, points,
                    occurred_at, evidence_type, evidence_id, created_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            params,
        )
        await self._db.commit()
        return len(params)

    async def prune_score_events(
        self, account_id: int, keep_days: int = 90,
    ) -> int:
        """Delete score_events older than *keep_days* days. Returns deleted count."""
        from datetime import datetime, timedelta, timezone
        cutoff = (datetime.now(timezone.utc) - timedelta(days=keep_days)).strftime("%Y-%m-%d")
        cur = await self._db.execute(
            "DELETE FROM score_events WHERE account_id = ? AND occurred_at < ?",
            (account_id, cutoff),
        )
        await self._db.commit()
        return cur.rowcount

    async def get_score_events(
        self, account_id: int, *,
        subject_type: str, subject_id: str,
        since: Optional[str] = None,
    ) -> list[dict]:
        q = ("SELECT * FROM score_events WHERE account_id = ? "
             "AND subject_type = ? AND subject_id = ?")
        params: list = [account_id, subject_type, subject_id]
        if since:
            q += " AND occurred_at >= ?"
            params.append(since)
        q += " ORDER BY occurred_at DESC"
        rows = await self.read_all(q, tuple(params))
        return [dict(r) for r in rows]

    # ── Daily snapshots (vendor-independence) ─────────────────────

    async def save_scorecard_snapshot(
        self, account_id: int, *,
        snapshot_date: str,
        subject_type: str, subject_id: str, subject_name: str,
        total_score: int, window_days: int,
        breakdown: dict,
        source: str = "live",
    ) -> None:
        await self._db.execute(
            """INSERT INTO daily_scorecard_snapshots
                   (account_id, snapshot_date, subject_type, subject_id,
                    subject_name, total_score, window_days, breakdown_json,
                    source, created_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
               ON CONFLICT(account_id, snapshot_date, subject_type, subject_id)
               DO UPDATE SET
                   subject_name   = excluded.subject_name,
                   total_score    = excluded.total_score,
                   window_days    = excluded.window_days,
                   breakdown_json = excluded.breakdown_json,
                   source         = excluded.source""",
            (account_id, snapshot_date, subject_type, subject_id,
             subject_name, int(total_score), int(window_days),
             json.dumps(breakdown), source, self._now()),
        )
        await self._db.commit()

    async def get_latest_scorecard_snapshot(
        self, account_id: int, subject_type: str, subject_id: str,
    ) -> Optional[dict]:
        row = await self.read_one(
            """SELECT * FROM daily_scorecard_snapshots
               WHERE account_id = ? AND subject_type = ? AND subject_id = ?
               ORDER BY snapshot_date DESC LIMIT 1""",
            (account_id, subject_type, subject_id),
        )
        if not row:
            return None
        d = dict(row)
        try:
            d["breakdown"] = json.loads(d.get("breakdown_json") or "{}")
        except json.JSONDecodeError:
            d["breakdown"] = {}
        return d

    async def get_latest_scorecard_snapshots(
        self, account_id: int, *, subject_type: str = "driver",
    ) -> list[dict]:
        rows = await self.read_all(
            """SELECT * FROM daily_scorecard_snapshots
               WHERE account_id = ? AND subject_type = ?
                 AND snapshot_date = (
                     SELECT MAX(snapshot_date)
                     FROM daily_scorecard_snapshots
                     WHERE account_id = ? AND subject_type = ?
                 )
               ORDER BY total_score DESC, subject_name ASC""",
            (account_id, subject_type, account_id, subject_type),
        )
        out: list[dict] = []
        for row in rows:
            d = dict(row)
            try:
                d["breakdown"] = json.loads(d.get("breakdown_json") or "{}")
            except json.JSONDecodeError:
                d["breakdown"] = {}
            out.append(d)
        return out

    async def get_scorecard_snapshot_history(
        self, account_id: int, *,
        subject_type: str = "driver",
        subject_id: Optional[str] = None,
        days: int = 30,
    ) -> list[dict]:
        q = ("SELECT * FROM daily_scorecard_snapshots "
             "WHERE account_id = ? AND subject_type = ?")
        params: list = [account_id, subject_type]
        if subject_id:
            q += " AND subject_id = ?"
            params.append(subject_id)
        q += " ORDER BY snapshot_date DESC LIMIT ?"
        params.append(int(days))
        rows = await self.read_all(q, tuple(params))
        return [dict(r) for r in rows]

    async def get_scorecard_trends_batch(
        self, account_id: int, *,
        subject_type: str = "driver",
        days: int = 14,
    ) -> dict[str, list[int]]:
        """Return ``{subject_id: [score, score, \u2026]}`` (oldest \u2192 newest)
        for the last ``days`` snapshot dates in one query.

        Powers the per-row sparkline column on the scorecards table
        without firing N parallel history requests.  Subjects with no
        snapshots in the window are simply absent from the map (the
        UI renders an empty cell).
        """
        rows = await self.read_all(
            "SELECT subject_id, snapshot_date, total_score "
            "FROM daily_scorecard_snapshots "
            "WHERE account_id = ? AND subject_type = ? "
            "  AND snapshot_date >= date('now', ? ) "
            "ORDER BY subject_id, snapshot_date ASC",
            (account_id, subject_type, f"-{int(days)} days"),
        )
        out: dict[str, list[int]] = {}
        for r in rows:
            sid = str(r[0])
            out.setdefault(sid, []).append(int(r[2]))
        return out
