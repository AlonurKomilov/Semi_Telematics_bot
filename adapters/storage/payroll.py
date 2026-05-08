"""Payroll (Pay-for-Performance) CRUD mixin.

Tables:
  - bonus_rules         — per-account configurable bonus rules
  - driver_pay_settings — per-driver base pay + opt-in flag
  - payroll_runs        — header row per pay period
  - payroll_run_items   — one row per driver per run
"""

from __future__ import annotations

import json
from typing import TYPE_CHECKING, Any, Optional

if TYPE_CHECKING:
    class _MixinBase:
        """Typing stub — attributes provided by the concrete DB class at runtime."""
        _db: Any

        @staticmethod
        def _now() -> str: ...
else:
    _MixinBase = object


class PayrollMixin(_MixinBase):

    # ── bonus_rules ────────────────────────────────────────────

    async def list_bonus_rules(
        self, account_id: int, active_only: bool = False,
    ) -> list[dict]:
        sql = "SELECT * FROM bonus_rules WHERE account_id = ?"
        args: tuple[Any, ...] = (account_id,)
        if active_only:
            sql += " AND active = 1"
        sql += " ORDER BY id ASC"
        cur = await self._db.execute(sql, args)
        rows = await cur.fetchall()
        return [dict(r) for r in rows]

    async def get_bonus_rule(
        self, account_id: int, rule_id: int,
    ) -> Optional[dict]:
        cur = await self._db.execute(
            "SELECT * FROM bonus_rules WHERE account_id = ? AND id = ?",
            (account_id, rule_id),
        )
        row = await cur.fetchone()
        return dict(row) if row else None

    async def create_bonus_rule(
        self, account_id: int, *, name: str, kind: str,
        amount_cents: int, period_days: int = 30,
        score_min: Optional[float] = None,
        event_type: Optional[str] = None,
        max_count: Optional[int] = None,
        active: bool = True,
    ) -> int:
        now = self._now()
        cur = await self._db.execute(
            "INSERT INTO bonus_rules "
            "(account_id, name, kind, score_min, event_type, max_count, "
            " period_days, amount_cents, active, created_at, updated_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (account_id, name, kind, score_min, event_type, max_count,
             period_days, amount_cents, 1 if active else 0, now, now),
        )
        await self._db.commit()
        return cur.lastrowid or 0

    async def update_bonus_rule(
        self, account_id: int, rule_id: int, **fields: Any,
    ) -> bool:
        if not fields:
            return False
        allowed = {
            "name", "kind", "score_min", "event_type", "max_count",
            "period_days", "amount_cents", "active",
        }
        sets = []
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
            f"UPDATE bonus_rules SET {', '.join(sets)} "
            "WHERE account_id = ? AND id = ?",
            tuple(vals),
        )
        await self._db.commit()
        return True

    async def delete_bonus_rule(self, account_id: int, rule_id: int) -> bool:
        await self._db.execute(
            "DELETE FROM bonus_rules WHERE account_id = ? AND id = ?",
            (account_id, rule_id),
        )
        await self._db.commit()
        return True

    # ── driver_pay_settings ────────────────────────────────────

    async def list_driver_pay_settings(self, account_id: int) -> list[dict]:
        cur = await self._db.execute(
            "SELECT * FROM driver_pay_settings WHERE account_id = ? "
            "ORDER BY driver_id ASC",
            (account_id,),
        )
        rows = await cur.fetchall()
        return [dict(r) for r in rows]

    async def get_driver_pay_settings(
        self, account_id: int, driver_id: str,
    ) -> Optional[dict]:
        cur = await self._db.execute(
            "SELECT * FROM driver_pay_settings "
            "WHERE account_id = ? AND driver_id = ?",
            (account_id, driver_id),
        )
        row = await cur.fetchone()
        return dict(row) if row else None

    async def upsert_driver_pay_settings(
        self, account_id: int, driver_id: str, *,
        base_pay_cents: int = 0, opt_in: bool = True,
    ) -> None:
        now = self._now()
        await self._db.execute(
            "INSERT INTO driver_pay_settings "
            "(account_id, driver_id, base_pay_cents, opt_in, updated_at) "
            "VALUES (?, ?, ?, ?, ?) "
            "ON CONFLICT(account_id, driver_id) DO UPDATE SET "
            "  base_pay_cents = excluded.base_pay_cents, "
            "  opt_in = excluded.opt_in, "
            "  updated_at = excluded.updated_at",
            (account_id, driver_id, base_pay_cents, 1 if opt_in else 0, now),
        )
        await self._db.commit()

    # ── payroll_runs ───────────────────────────────────────────

    async def create_payroll_run(
        self, account_id: int, *, period_start: str, period_end: str,
        created_by: int = 0,
    ) -> int:
        now = self._now()
        cur = await self._db.execute(
            "INSERT INTO payroll_runs "
            "(account_id, period_start, period_end, status, created_by, created_at) "
            "VALUES (?, ?, ?, 'draft', ?, ?)",
            (account_id, period_start, period_end, created_by, now),
        )
        await self._db.commit()
        return cur.lastrowid or 0

    async def list_payroll_runs(
        self, account_id: int, limit: int = 50,
    ) -> list[dict]:
        cur = await self._db.execute(
            "SELECT * FROM payroll_runs WHERE account_id = ? "
            "ORDER BY period_start DESC, id DESC LIMIT ?",
            (account_id, limit),
        )
        rows = await cur.fetchall()
        return [dict(r) for r in rows]

    async def get_payroll_run(
        self, account_id: int, run_id: int,
    ) -> Optional[dict]:
        cur = await self._db.execute(
            "SELECT * FROM payroll_runs WHERE account_id = ? AND id = ?",
            (account_id, run_id),
        )
        row = await cur.fetchone()
        return dict(row) if row else None

    async def set_payroll_run_status(
        self, account_id: int, run_id: int, status: str,
    ) -> None:
        now = self._now()
        if status == "finalized":
            await self._db.execute(
                "UPDATE payroll_runs SET status = ?, finalized_at = ? "
                "WHERE account_id = ? AND id = ?",
                (status, now, account_id, run_id),
            )
        else:
            await self._db.execute(
                "UPDATE payroll_runs SET status = ? "
                "WHERE account_id = ? AND id = ?",
                (status, account_id, run_id),
            )
        await self._db.commit()

    async def set_payroll_run_total(
        self, account_id: int, run_id: int, total_cents: int,
    ) -> None:
        await self._db.execute(
            "UPDATE payroll_runs SET total_cents = ? "
            "WHERE account_id = ? AND id = ?",
            (total_cents, account_id, run_id),
        )
        await self._db.commit()

    # ── payroll_run_items ──────────────────────────────────────

    async def add_payroll_run_item(
        self, *, run_id: int, driver_id: str, driver_name: str,
        base_pay_cents: int, bonus_total_cents: int,
        total_cents: int, breakdown: list[dict],
    ) -> int:
        now = self._now()
        cur = await self._db.execute(
            "INSERT INTO payroll_run_items "
            "(run_id, driver_id, driver_name, base_pay_cents, "
            " bonus_total_cents, total_cents, breakdown_json, created_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (run_id, driver_id, driver_name, base_pay_cents,
             bonus_total_cents, total_cents, json.dumps(breakdown), now),
        )
        await self._db.commit()
        return cur.lastrowid or 0

    async def get_payroll_run_items(self, run_id: int) -> list[dict]:
        cur = await self._db.execute(
            "SELECT * FROM payroll_run_items WHERE run_id = ? "
            "ORDER BY driver_name ASC, driver_id ASC",
            (run_id,),
        )
        rows = await cur.fetchall()
        out = []
        for r in rows:
            d = dict(r)
            try:
                d["breakdown"] = json.loads(d.get("breakdown_json") or "[]")
            except Exception:
                d["breakdown"] = []
            out.append(d)
        return out

    async def get_paystub_history_for_driver(
        self, account_id: int, driver_id: str, limit: int = 24,
    ) -> list[dict]:
        """Return paystub items for one driver across finalized runs."""
        cur = await self._db.execute(
            "SELECT i.*, r.period_start, r.period_end, r.status, r.finalized_at "
            "FROM payroll_run_items i "
            "JOIN payroll_runs r ON r.id = i.run_id "
            "WHERE r.account_id = ? AND i.driver_id = ? "
            "  AND r.status IN ('finalized', 'draft') "
            "ORDER BY r.period_start DESC, i.id DESC LIMIT ?",
            (account_id, driver_id, limit),
        )
        rows = await cur.fetchall()
        out = []
        for r in rows:
            d = dict(r)
            try:
                d["breakdown"] = json.loads(d.get("breakdown_json") or "[]")
            except Exception:
                d["breakdown"] = []
            out.append(d)
        return out
