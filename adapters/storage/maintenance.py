"""Maintenance tasks CRUD mixin."""

from __future__ import annotations

from typing import Optional


class MaintenanceMixin:

    async def add_maintenance_task(
        self, account_id: int, company_code: str,
        vehicle_name: str, task_type: str, description: str,
        due_date: Optional[str] = None, due_miles: Optional[float] = None,
        created_by: int = 0, vehicle_id: str = "",
        recur_interval_days: Optional[int] = None,
        recur_interval_miles: Optional[float] = None,
    ) -> int:
        now = self._now()
        cur = await self._db.execute(
            """INSERT INTO maintenance_tasks
               (account_id, company_code, vehicle_id, vehicle_name,
                task_type, description, due_date, due_miles, created_by, created_at,
                recur_interval_days, recur_interval_miles)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (account_id, company_code, vehicle_id, vehicle_name,
             task_type, description, due_date, due_miles, created_by, now,
             recur_interval_days, recur_interval_miles),
        )
        await self._db.commit()
        return cur.lastrowid

    async def get_maintenance_tasks(
        self, account_id: int, status: Optional[str] = None,
        vehicle_name: Optional[str] = None,
    ) -> list[dict]:
        q = "SELECT * FROM maintenance_tasks WHERE account_id = ?"
        params: list = [account_id]
        if status:
            q += " AND status = ?"
            params.append(status)
        if vehicle_name:
            q += " AND vehicle_name = ?"
            params.append(vehicle_name)
        q += " ORDER BY CASE status WHEN 'overdue' THEN 0 WHEN 'pending' THEN 1 ELSE 2 END, created_at DESC"
        cur = await self._db.execute(q, params)
        rows = await cur.fetchall()
        return [dict(r) for r in rows]

    async def update_maintenance_status(self, task_id: int, status: str, account_id: int = 0) -> bool:
        completed_at = self._now() if status == "done" else None
        if account_id:
            cur = await self._db.execute(
                "UPDATE maintenance_tasks SET status = ?, completed_at = ? WHERE id = ? AND account_id = ?",
                (status, completed_at, task_id, account_id),
            )
        else:
            cur = await self._db.execute(
                "UPDATE maintenance_tasks SET status = ?, completed_at = ? WHERE id = ?",
                (status, completed_at, task_id),
            )
        await self._db.commit()
        return cur.rowcount > 0

    async def get_overdue_tasks(self, account_id: int) -> list[dict]:
        cur = await self._db.execute(
            "SELECT * FROM maintenance_tasks WHERE account_id = ? AND status = 'overdue'",
            (account_id,),
        )
        rows = await cur.fetchall()
        return [dict(r) for r in rows]

    async def get_pending_tasks_by_date(self, account_id: int) -> list[dict]:
        """Get pending tasks with a due_date in the past for a specific account."""
        now = self._now()
        cur = await self._db.execute(
            "SELECT * FROM maintenance_tasks"
            " WHERE account_id = ? AND status = 'pending' AND due_date IS NOT NULL AND due_date < ?",
            (account_id, now),
        )
        rows = await cur.fetchall()
        return [dict(r) for r in rows]

    async def get_maintenance_task(self, task_id: int, account_id: int = 0) -> Optional[dict]:
        """Get a single maintenance task by ID.

        If account_id is provided, the row must belong to that account.
        """
        if account_id:
            cur = await self._db.execute(
                "SELECT * FROM maintenance_tasks WHERE id = ? AND account_id = ?",
                (task_id, account_id),
            )
        else:
            cur = await self._db.execute(
                "SELECT * FROM maintenance_tasks WHERE id = ?", (task_id,),
            )
        row = await cur.fetchone()
        return dict(row) if row else None

    async def update_maintenance_task(self, task_id: int, account_id: int = 0, **kwargs) -> bool:
        """Update maintenance task fields.

        If account_id is provided, the row must belong to that account.
        """
        allowed = {"task_type", "description", "due_date", "due_miles",
                   "recur_interval_days", "recur_interval_miles", "last_odometer"}
        updates = {k: v for k, v in kwargs.items() if k in allowed}
        if not updates:
            return False
        set_clause = ", ".join(f"{k} = ?" for k in updates)
        if account_id:
            values = list(updates.values()) + [task_id, account_id]
            cur = await self._db.execute(
                f"UPDATE maintenance_tasks SET {set_clause} WHERE id = ? AND account_id = ?",
                values,
            )
        else:
            values = list(updates.values()) + [task_id]
            cur = await self._db.execute(
                f"UPDATE maintenance_tasks SET {set_clause} WHERE id = ?", values,
            )
        await self._db.commit()
        return cur.rowcount > 0

    async def delete_maintenance_task(self, task_id: int, account_id: int = 0) -> None:
        """Delete a maintenance task.

        If account_id is provided, the row must belong to that account.
        """
        if account_id:
            await self._db.execute(
                "DELETE FROM maintenance_tasks WHERE id = ? AND account_id = ?",
                (task_id, account_id),
            )
        else:
            await self._db.execute(
                "DELETE FROM maintenance_tasks WHERE id = ?", (task_id,),
            )
        await self._db.commit()

    async def get_pending_tasks_by_miles(self) -> list[dict]:
        """Get all pending tasks with due_miles set (across all accounts)."""
        cur = await self._db.execute(
            "SELECT * FROM maintenance_tasks WHERE status = 'pending' AND due_miles IS NOT NULL",
        )
        rows = await cur.fetchall()
        return [dict(r) for r in rows]

    async def is_vehicle_in_maintenance(
        self, account_id: int, vehicle_name: str,
    ) -> bool:
        """Check if a vehicle has any pending/overdue maintenance tasks."""
        cur = await self._db.execute(
            "SELECT COUNT(*) FROM maintenance_tasks "
            "WHERE account_id = ? AND vehicle_name = ? "
            "AND status IN ('pending', 'overdue')",
            (account_id, vehicle_name),
        )
        row = await cur.fetchone()
        return row[0] > 0
