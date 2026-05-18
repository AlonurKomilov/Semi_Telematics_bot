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
        # All optional with sensible defaults so existing callers
        # (bot wizard, SPN auto-create, AI tool) keep working without
        # code changes; the dashboard form and future Work Orders
        # module pass these explicitly.
        priority: str = "medium",
        due_engine_hours: Optional[float] = None,
        recur_interval_engine_hours: Optional[float] = None,
        work_order_id: Optional[int] = None,
        spawned_from_id: Optional[int] = None,
    ) -> int:
        now = self._now()
        cur = await self._db.execute(
            """INSERT INTO maintenance_tasks
               (account_id, company_code, vehicle_id, vehicle_name,
                task_type, description, due_date, due_miles, due_engine_hours,
                priority, created_by, created_at,
                recur_interval_days, recur_interval_miles, recur_interval_engine_hours,
                work_order_id, spawned_from_id)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (account_id, company_code, vehicle_id, vehicle_name,
             task_type, description, due_date, due_miles, due_engine_hours,
             priority, created_by, now,
             recur_interval_days, recur_interval_miles, recur_interval_engine_hours,
             work_order_id, spawned_from_id),
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
        # Historical schism: the bot uses ``"done"``, the API + dashboard
        # use ``"completed"``.  Both mean the same thing — completion-time
        # is stamped for either.  We don't migrate one to the other here
        # (would invalidate existing audit logs / external integrations);
        # we just make both surfaces work.
        completed_at = self._now() if status in ("done", "completed") else None
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

    async def update_maintenance_status_bulk(
        self, account_id: int, task_ids: list[int], status: str,
    ) -> int:
        """Bulk-update task status — replaces N × per-task UPDATEs in
        the scheduled overdue-marker jobs. One IN-clause UPDATE per
        chunk of 500 ids, single commit.
        """
        if not task_ids:
            return 0
        completed_at = self._now() if status == "done" else None
        touched = 0
        for i in range(0, len(task_ids), 500):
            chunk = task_ids[i:i + 500]
            placeholders = ",".join("?" * len(chunk))
            cur = await self._db.execute(
                f"UPDATE maintenance_tasks "
                f"SET status = ?, completed_at = ? "
                f"WHERE account_id = ? AND id IN ({placeholders})",
                (status, completed_at, account_id, *chunk),
            )
            touched += cur.rowcount or 0
        await self._db.commit()
        return touched

    async def update_maintenance_last_odometer_bulk(
        self, account_id: int, items: list[tuple[int, float]],
    ) -> int:
        """Bulk-update ``last_odometer`` for many tasks via executemany.

        ``items`` is a list of (task_id, odometer_mi). Replaces the
        per-task UPDATE loop in mark_overdue_tasks_by_mileage.
        """
        if not items:
            return 0
        params = [(float(odo), int(tid), account_id) for tid, odo in items]
        await self._db.executemany(
            "UPDATE maintenance_tasks SET last_odometer = ? "
            "WHERE id = ? AND account_id = ?",
            params,
        )
        await self._db.commit()
        return len(params)

    async def get_overdue_tasks(self, account_id: int) -> list[dict]:
        cur = await self._db.execute(
            "SELECT * FROM maintenance_tasks WHERE account_id = ? AND status = 'overdue'",
            (account_id,),
        )
        rows = await cur.fetchall()
        return [dict(r) for r in rows]

    async def get_pending_tasks_by_date(self, account_id: int) -> list[dict]:
        """Get pending tasks with a due_date in the past for a specific account.

        Filters out tasks that have already been alerted (``alerted_at IS NOT
        NULL``) so the daily scheduler doesn't re-notify on every tick.  The
        ``alerted_at`` column is set by ``mark_tasks_alerted_bulk`` after the
        notification fires; auto-spawned recurring follow-up tasks start
        with ``alerted_at = NULL`` so they alert fresh on their own crossing.
        """
        now = self._now()
        cur = await self._db.execute(
            "SELECT * FROM maintenance_tasks"
            " WHERE account_id = ? AND status = 'pending'"
            " AND due_date IS NOT NULL AND due_date < ?"
            " AND alerted_at IS NULL",
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
                   "recur_interval_days", "recur_interval_miles", "last_odometer",
                   # Only listed fields can be updated by callers, so
                   # accidentally PUT'ing a status or account_id field
                   # still gets dropped silently.
                   "priority", "due_engine_hours", "last_engine_hours",
                   "recur_interval_engine_hours", "work_order_id"}
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

    async def delete_maintenance_tasks_bulk(
        self, account_id: int, task_ids: list[int],
    ) -> int:
        """Bulk-delete maintenance tasks scoped to one account.

        Returns the number of rows actually deleted (i.e. tasks that
        existed AND belonged to ``account_id``).  Chunks at 500 ids to
        stay under SQLite's parameter limit on large selections.
        """
        if not task_ids:
            return 0
        touched = 0
        for i in range(0, len(task_ids), 500):
            chunk = task_ids[i:i + 500]
            placeholders = ",".join("?" * len(chunk))
            cur = await self._db.execute(
                f"DELETE FROM maintenance_tasks "
                f"WHERE account_id = ? AND id IN ({placeholders})",
                (account_id, *chunk),
            )
            touched += cur.rowcount or 0
        await self._db.commit()
        return touched

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
        """Get all pending tasks with due_miles set (across all accounts).

        Skips tasks with ``alerted_at IS NOT NULL`` — already-notified tasks
        stay in the result set for ``last_odometer`` progress updates, but
        the service layer filters those out before triggering notifications.
        Actually simpler: filter here so neither the progress UPDATE nor the
        notification fires twice for the same crossing.
        """
        cur = await self._db.execute(
            "SELECT * FROM maintenance_tasks"
            " WHERE status = 'pending' AND due_miles IS NOT NULL"
            " AND alerted_at IS NULL",
        )
        rows = await cur.fetchall()
        return [dict(r) for r in rows]

    async def get_pending_tasks_by_engine_hours(self) -> list[dict]:
        """Engine-hours twin of ``get_pending_tasks_by_miles``.

        Mileage misses wear on trucks that run heavy idle (PTO, reefer,
        generator).  This method's results feed ``mark_overdue_tasks_by_engine_hours``,
        which compares each task's ``due_engine_hours`` against the
        warehouse ``vehicle_state.engine_hours`` reading.  Alerted-throttle
        filter applied here for the same reason as the mileage path.
        """
        cur = await self._db.execute(
            "SELECT * FROM maintenance_tasks"
            " WHERE status = 'pending' AND due_engine_hours IS NOT NULL"
            " AND alerted_at IS NULL",
        )
        rows = await cur.fetchall()
        return [dict(r) for r in rows]

    async def get_pending_tasks_for_warning(
        self, account_id: int,
        days_ahead: int = 7,
        miles_ahead: float = 500.0,
        engine_hours_ahead: float = 50.0,
    ) -> list[dict]:
        """Tasks approaching their threshold but not yet overdue.

        Drives the "due in 7 days / 500 mi / 50 hours" pre-overdue
        notification.  A task qualifies when ANY of:
          * ``due_date <= now + days_ahead``
          * ``last_odometer + miles_ahead >= due_miles``
          * ``last_engine_hours + engine_hours_ahead >= due_engine_hours``

        Excludes tasks where ``warning_sent_at`` is already set — the
        warning fires once per task per cycle (it's cleared on the spawned
        recurring child, which gets its own warning when that child
        approaches).

        Why three thresholds: distance and hours accumulate independently
        of calendar time.  A truck waiting 30 days for a service window
        might cross the mile threshold first; a long-haul truck might
        cross hours first.  We warn on whichever fires.
        """
        from datetime import datetime, timedelta, timezone
        cutoff_date = (
            datetime.now(timezone.utc) + timedelta(days=int(days_ahead))
        ).isoformat()
        cur = await self._db.execute(
            "SELECT * FROM maintenance_tasks"
            " WHERE account_id = ? AND status = 'pending'"
            "   AND alerted_at IS NULL"
            "   AND warning_sent_at IS NULL"
            "   AND ("
            "       (due_date IS NOT NULL AND due_date <= ?)"
            "    OR (due_miles IS NOT NULL AND last_odometer IS NOT NULL"
            "        AND last_odometer + ? >= due_miles)"
            "    OR (due_engine_hours IS NOT NULL AND last_engine_hours IS NOT NULL"
            "        AND last_engine_hours + ? >= due_engine_hours)"
            "   )",
            (account_id, cutoff_date, float(miles_ahead), float(engine_hours_ahead)),
        )
        rows = await cur.fetchall()
        return [dict(r) for r in rows]

    async def mark_tasks_warned_bulk(
        self, account_id: int, task_ids: list[int],
    ) -> int:
        """Stamp ``warning_sent_at = now()`` on each task.

        Mirrors ``mark_tasks_alerted_bulk`` but for the pre-overdue
        notification.  Keeping the two stamps separate (warning vs alert)
        is important: a task can be warned today, sit at "warned"
        status for 3 days, then *cross* the threshold and trigger the
        overdue alert — both events distinct, both auditable.
        """
        if not task_ids:
            return 0
        now = self._now()
        touched = 0
        for i in range(0, len(task_ids), 500):
            chunk = task_ids[i:i + 500]
            placeholders = ",".join("?" * len(chunk))
            cur = await self._db.execute(
                f"UPDATE maintenance_tasks SET warning_sent_at = ? "
                f"WHERE account_id = ? AND id IN ({placeholders})",
                (now, account_id, *chunk),
            )
            touched += cur.rowcount or 0
        await self._db.commit()
        return touched

    async def update_maintenance_last_engine_hours_bulk(
        self, account_id: int, items: list[tuple[int, float]],
    ) -> int:
        """Bulk-update ``last_engine_hours`` for many tasks.

        Engine-hours twin of ``update_maintenance_last_odometer_bulk``.
        Items is a list of (task_id, engine_hours).
        """
        if not items:
            return 0
        params = [(float(hrs), int(tid), account_id) for tid, hrs in items]
        await self._db.executemany(
            "UPDATE maintenance_tasks SET last_engine_hours = ? "
            "WHERE id = ? AND account_id = ?",
            params,
        )
        await self._db.commit()
        return len(params)

    async def record_task_attestation(
        self, task_id: int, account_id: int, attested_by: int,
    ) -> bool:
        """Stamp who confirmed completion of a maintenance task, when.

        Called from the bot's mark-done flow (driver pressed "✅
        Confirm") and from the dashboard's "Update Task" path when a
        completion is recorded by a user with an associated telegram_id.
        Distinct from ``update_maintenance_status`` because attestation
        can be recorded *after* status is already 'completed' (e.g. a
        manager closed the task in the dashboard yesterday; the driver
        confirms today via the bot).

        Returns True if a row was touched.  No-op + False if the task
        doesn't exist or belongs to a different account.
        """
        now = self._now()
        cur = await self._db.execute(
            "UPDATE maintenance_tasks "
            "SET attested_by = ?, attested_at = ? "
            "WHERE id = ? AND account_id = ?",
            (int(attested_by), now, task_id, account_id),
        )
        await self._db.commit()
        return cur.rowcount > 0

    async def mark_tasks_alerted_bulk(
        self, account_id: int, task_ids: list[int],
    ) -> int:
        """Stamp ``alerted_at = now()`` on each task in ``task_ids``.

        Called by the service layer after an overdue notification successfully
        delivers, so subsequent scheduler ticks treat the task as "already
        notified" and skip it.  IDs are chunked at 500 to stay under SQLite's
        parameter-count limit on large fleets.
        """
        if not task_ids:
            return 0
        now = self._now()
        touched = 0
        for i in range(0, len(task_ids), 500):
            chunk = task_ids[i:i + 500]
            placeholders = ",".join("?" * len(chunk))
            cur = await self._db.execute(
                f"UPDATE maintenance_tasks SET alerted_at = ? "
                f"WHERE account_id = ? AND id IN ({placeholders})",
                (now, account_id, *chunk),
            )
            touched += cur.rowcount or 0
        await self._db.commit()
        return touched

    async def spawn_recurring_followup(
        self, parent_task_id: int, account_id: int,
    ) -> Optional[int]:
        """Create the next instance of a recurring task when its parent
        completes.  Returns the new task ID, or ``None`` if the parent
        doesn't have any recurrence fields set.

        How due fields propagate:
          * ``recur_interval_days``         → new ``due_date`` = now + N days
          * ``recur_interval_miles``        → new ``due_miles`` =
            max(parent.due_miles, parent.last_odometer) + N miles
          * ``recur_interval_engine_hours`` → new ``due_engine_hours`` =
            max(parent.due_engine_hours, parent.last_engine_hours) + N hrs

        Using the higher of the configured-due and actually-reached value
        covers the late-completion case (driver crossed the old threshold
        by some amount before getting the service done — next interval
        starts from where we actually are, not where we *should* have been).

        The recurrence fields propagate forward, so completing the child
        spawns a grandchild, and so on indefinitely.  ``alerted_at`` and
        ``warning_sent_at`` are intentionally left NULL on the new row
        so both throttles reset for the next cycle.  ``priority`` is
        propagated (a "critical" oil-change recurs at the same priority).
        ``work_order_id`` is NOT propagated — the child belongs to its
        own future shop visit.
        """
        from datetime import datetime, timedelta, timezone

        parent = await self.get_maintenance_task(parent_task_id, account_id=account_id)
        if not parent:
            return None
        interval_days = parent.get("recur_interval_days")
        interval_miles = parent.get("recur_interval_miles")
        interval_engine_hours = parent.get("recur_interval_engine_hours")
        if not interval_days and not interval_miles and not interval_engine_hours:
            return None  # one-shot task, nothing to spawn

        new_due_date: Optional[str] = None
        if interval_days:
            new_due_date = (
                datetime.now(timezone.utc) + timedelta(days=int(interval_days))
            ).isoformat()

        new_due_miles: Optional[float] = None
        if interval_miles:
            parent_due = float(parent.get("due_miles") or 0)
            parent_odo = float(parent.get("last_odometer") or 0)
            new_due_miles = max(parent_due, parent_odo) + float(interval_miles)

        new_due_engine_hours: Optional[float] = None
        if interval_engine_hours:
            parent_due_hours  = float(parent.get("due_engine_hours") or 0)
            parent_last_hours = float(parent.get("last_engine_hours") or 0)
            new_due_engine_hours = (
                max(parent_due_hours, parent_last_hours) + float(interval_engine_hours)
            )

        return await self.add_maintenance_task(
            account_id=account_id,
            company_code=parent.get("company_code", ""),
            vehicle_name=parent.get("vehicle_name", ""),
            task_type=parent.get("task_type", "custom"),
            description=parent.get("description", ""),
            due_date=new_due_date,
            due_miles=new_due_miles,
            due_engine_hours=new_due_engine_hours,
            priority=parent.get("priority") or "medium",
            created_by=parent.get("created_by", 0),
            vehicle_id=parent.get("vehicle_id", ""),
            recur_interval_days=interval_days,
            recur_interval_miles=interval_miles,
            recur_interval_engine_hours=interval_engine_hours,
            # work_order_id deliberately NOT propagated — the new task
            # is for the *next* shop visit, not the one that closed the
            # parent.
            # spawned_from_id IS propagated so the dashboard can render
            # the "↻ Auto-renewed from #N" breadcrumb on the child.
            spawned_from_id=int(parent_task_id),
        )

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

    async def get_vehicles_in_maintenance(
        self, account_id: int,
    ) -> set[str]:
        """Bulk variant of ``is_vehicle_in_maintenance`` — return the set of
        vehicle_names with pending/overdue maintenance tasks. One query
        replaces V × per-vehicle suppression lookups."""
        cur = await self._db.execute(
            "SELECT DISTINCT vehicle_name FROM maintenance_tasks "
            "WHERE account_id = ? AND status IN ('pending', 'overdue')",
            (account_id,),
        )
        return {str(row[0] or "") for row in await cur.fetchall() if row[0]}
