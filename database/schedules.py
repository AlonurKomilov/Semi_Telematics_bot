"""Work schedules and shift handoff CRUD mixin."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Optional


class SchedulesMixin:

    async def create_work_schedule(
        self, account_id: int, label: str,
        start_hour: int, end_hour: int, created_by: int,
        target_role: str = "all",
    ) -> dict:
        """Create a working-hour preset for an account."""
        now = self._now()
        cur = await self._db.execute(
            """INSERT INTO work_schedules
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

    async def get_work_schedules(self, account_id: int) -> list[dict]:
        """List all work schedules for an account."""
        cur = await self._db.execute(
            "SELECT * FROM work_schedules WHERE account_id = ? ORDER BY label",
            (account_id,),
        )
        rows = await cur.fetchall()
        return [dict(r) for r in rows]

    async def get_work_schedule(self, schedule_id: int) -> Optional[dict]:
        """Get a single work schedule by ID."""
        cur = await self._db.execute(
            "SELECT * FROM work_schedules WHERE id = ?", (schedule_id,),
        )
        row = await cur.fetchone()
        return dict(row) if row else None

    async def update_work_schedule(self, schedule_id: int, **kwargs) -> bool:
        """Update work schedule fields."""
        allowed = {"label", "start_hour", "end_hour", "target_role"}
        updates = {k: v for k, v in kwargs.items() if k in allowed}
        if not updates:
            return False
        set_clause = ", ".join(f"{k} = ?" for k in updates)
        values = list(updates.values()) + [schedule_id]
        await self._db.execute(
            f"UPDATE work_schedules SET {set_clause} WHERE id = ?", values,
        )
        await self._db.commit()
        return True

    async def delete_work_schedule(self, schedule_id: int) -> None:
        """Delete a work schedule."""
        await self._db.execute(
            "DELETE FROM work_schedules WHERE id = ?", (schedule_id,),
        )
        await self._db.commit()

    async def get_work_schedules_for_role(
        self, account_id: int, role: str,
    ) -> list[dict]:
        """Get schedules matching a role (includes 'all' target_role)."""
        cur = await self._db.execute(
            "SELECT * FROM work_schedules WHERE account_id = ? "
            "AND (target_role = 'all' OR target_role = ?) ORDER BY label",
            (account_id, role),
        )
        rows = await cur.fetchall()
        return [dict(r) for r in rows]

    async def get_shift_handoff_data(
        self, account_id: int, telegram_id: int,
    ) -> dict:
        """Build shift handoff summary: pending alerts, resolved alerts, pending maintenance."""
        # Pending (unacked) alerts for user
        cur = await self._db.execute(
            "SELECT * FROM alert_acknowledgments "
            "WHERE account_id = ? AND sent_to = ? AND acknowledged_at IS NULL "
            "AND status = 'active' ORDER BY created_at DESC",
            (account_id, telegram_id),
        )
        pending_alerts = [dict(r) for r in await cur.fetchall()]

        # Recently resolved alerts (last 24h)
        cutoff = (datetime.now(timezone.utc) - timedelta(hours=24)).isoformat()
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

        return {
            "pending_alerts": pending_alerts,
            "resolved_alerts": resolved_alerts,
            "pending_maintenance": pending_maintenance,
        }
