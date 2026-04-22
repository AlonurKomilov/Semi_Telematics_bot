"""Work schedules and shift handoff CRUD mixin."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Optional


class SchedulesMixin:

    async def create_work_hour(
        self, account_id: int, label: str,
        start_hour: int, end_hour: int, created_by: int,
        target_role: str = "all",
    ) -> dict:
        """Create a working-hour preset for an account."""
        now = self._now()
        cur = await self._db.execute(
            """INSERT INTO work_hours
               (account_id, label, start_hour, end_hour, target_role, created_by, created_at)
               VALUES (?, ?, ?, ?, ?, ?, ?)""",
            (account_id, label, start_hour, end_hour, target_role, created_by, now),
        )
        await self._db.commit()
        return {
            "id": cur.lastrowid, "account_id": account_id,
            "label": label, "start_hour": start_hour, "end_hour": end_hour,
            "target_role": target_role, "created_by": created_by, "created_at": now,
        }

    async def get_work_hours(self, account_id: int) -> list[dict]:
        """List all work schedules for an account."""
        cur = await self._db.execute(
            "SELECT * FROM work_hours WHERE account_id = ? ORDER BY label",
            (account_id,),
        )
        rows = await cur.fetchall()
        return [dict(r) for r in rows]

    async def get_work_hour(self, schedule_id: int, account_id: int = 0) -> Optional[dict]:
        """Get a single work schedule by ID.

        If account_id is provided, the row must belong to that account.
        """
        if account_id:
            cur = await self._db.execute(
                "SELECT * FROM work_hours WHERE id = ? AND account_id = ?",
                (schedule_id, account_id),
            )
        else:
            cur = await self._db.execute(
                "SELECT * FROM work_hours WHERE id = ?", (schedule_id,),
            )
        row = await cur.fetchone()
        return dict(row) if row else None

    async def update_work_hour(self, schedule_id: int, account_id: int = 0, **kwargs) -> bool:
        """Update work schedule fields.

        If account_id is provided, the row must belong to that account.
        """
        allowed = {"label", "start_hour", "end_hour", "target_role"}
        updates = {k: v for k, v in kwargs.items() if k in allowed}
        if not updates:
            return False
        set_clause = ", ".join(f"{k} = ?" for k in updates)
        if account_id:
            values = list(updates.values()) + [schedule_id, account_id]
            cur = await self._db.execute(
                f"UPDATE work_hours SET {set_clause} WHERE id = ? AND account_id = ?",
                values,
            )
        else:
            values = list(updates.values()) + [schedule_id]
            cur = await self._db.execute(
                f"UPDATE work_hours SET {set_clause} WHERE id = ?", values,
            )
        await self._db.commit()
        return cur.rowcount > 0

    async def delete_work_hour(self, schedule_id: int, account_id: int = 0) -> None:
        """Delete a work schedule.

        If account_id is provided, the row must belong to that account.
        """
        if account_id:
            await self._db.execute(
                "DELETE FROM work_hours WHERE id = ? AND account_id = ?",
                (schedule_id, account_id),
            )
        else:
            await self._db.execute(
                "DELETE FROM work_hours WHERE id = ?", (schedule_id,),
            )
        await self._db.commit()

    async def get_work_hours_for_role(
        self, account_id: int, role: str,
    ) -> list[dict]:
        """Get schedules matching a role (includes 'all' target_role)."""
        cur = await self._db.execute(
            "SELECT * FROM work_hours WHERE account_id = ? "
            "AND (target_role = 'all' OR target_role = ?) ORDER BY label",
            (account_id, role),
        )
        rows = await cur.fetchall()
        return [dict(r) for r in rows]

    async def get_shift_handoff_data(
        self, account_id: int, telegram_id: int, *, days: int = 1,
    ) -> dict:
        """Build shift handoff summary: pending alerts, resolved alerts, pending maintenance, recent alert history."""
        # Pending (unacked) alerts for user
        cur = await self._db.execute(
            "SELECT * FROM alert_acknowledgments "
            "WHERE account_id = ? AND sent_to = ? AND acknowledged_at IS NULL "
            "AND status = 'active' ORDER BY created_at DESC",
            (account_id, telegram_id),
        )
        pending_alerts = [dict(r) for r in await cur.fetchall()]

        # Recently resolved alerts
        cutoff = (datetime.now(timezone.utc) - timedelta(days=days)).isoformat()
        cur = await self._db.execute(
            "SELECT * FROM alert_acknowledgments "
            "WHERE account_id = ? AND sent_to = ? AND acknowledged_at IS NOT NULL "
            "AND acknowledged_at >= ? ORDER BY acknowledged_at DESC",
            (account_id, telegram_id, cutoff),
        )
        resolved_alerts = [dict(r) for r in await cur.fetchall()]

        # Pending maintenance tasks
        cur = await self._db.execute(
            "SELECT * FROM maintenance_tasks "
            "WHERE account_id = ? AND status IN ('pending', 'overdue') "
            "ORDER BY CASE status WHEN 'overdue' THEN 0 ELSE 1 END, created_at DESC",
            (account_id,),
        )
        pending_maintenance = [dict(r) for r in await cur.fetchall()]

        # Recent alert history — broader view of all alerts for the account
        cur = await self._db.execute(
            "SELECT * FROM alert_history "
            "WHERE account_id = ? AND last_seen >= ? "
            "ORDER BY last_seen DESC LIMIT 50",
            (account_id, cutoff),
        )
        recent_history = [dict(r) for r in await cur.fetchall()]

        return {
            "pending_alerts": pending_alerts,
            "resolved_alerts": resolved_alerts,
            "pending_maintenance": pending_maintenance,
            "recent_history": recent_history,
        }
