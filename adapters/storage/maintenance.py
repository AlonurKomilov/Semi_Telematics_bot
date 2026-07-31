"""Maintenance tasks CRUD mixin."""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import TYPE_CHECKING, Any, Optional

from capabilities.activity_trail import delete_changes, diff_rows

logger = logging.getLogger("bot.storage")


if TYPE_CHECKING:
    # Typing stub — actual ``_db`` is provided by the ``_DatabaseCore``
    # class that gets composed alongside this mixin on ``Database``.
    class _MixinBase:
        _db: Any

        def _now(self) -> str: ...
        def transaction(self) -> Any: ...
        async def append_activity_events(
            self, account_id: int, events: list[dict],
        ) -> None: ...
else:
    _MixinBase = object


class MaintenanceMixin(_MixinBase):

    # ── The accountability trail (capabilities/activity_trail) ────
    # Every HUMAN mutation below appends an activity_events row inside
    # the same transaction as the write — unskippable by construction
    # (the loads/inventory pattern, unified).  Machine callers (the 6-h
    # schedulers, telemetry backfill) simply don't pass an actor and
    # stay un-evented: the trail records PEOPLE.  Deletes carry every
    # field as {from, to:null} — the trail IS the recovery record
    # (lesson of the 2026-07-30 bulk delete).  Bulk ops write one event
    # per task sharing a group_id, never a truncatable id list.

    async def _trail_task(
        self, account_id: int, task_id: int | str, action: str, *,
        actor_user_id: Optional[int] = None,
        changes: Optional[dict] = None,
        group_id: Optional[str] = None,
        context: Optional[dict] = None,
        entity_type: str = "maintenance_task",
    ) -> None:
        """Append one trail event — silently skipped for machine writes
        (no actor, no declared system context)."""
        if actor_user_id is None and not (context or {}).get("system"):
            return
        await self.append_activity_events(account_id, [{
            "entity_type": entity_type, "entity_id": task_id,
            "action": action, "changes": changes or {},
            "actor_user_id": actor_user_id, "group_id": group_id,
            "context": context or {},
        }])

    # The custom-task-types mixin (list/create/delete over
    # ``maintenance_custom_task_types``) lived here until the
    # service-tasks cutover.  Migration 162 folded the table's rows
    # into ``service_tasks``; the shim endpoints that fronted it are
    # gone, and the table itself is dropped alongside the legacy
    # string columns (the migration-170 batch).

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
        # Backfill values for the progress-bar columns.  Historically these
        # were NULL at creation time and only filled by the 6-h scheduler
        # (mark_overdue_tasks_by_mileage), which meant a brand-new task
        # showed "no telemetry" in the dashboard until the next tick.
        # Callers that have current telemetry on hand (dashboard create,
        # bot wizard, fault auto-create) pass it here so the progress bar
        # appears immediately after creation.
        last_odometer: Optional[float] = None,
        last_engine_hours: Optional[float] = None,
        # Trail: who did this (None = machine caller, not evented) and
        # how to label it ('create' normally, 'spawn' for recurrence).
        actor_user_id: Optional[int] = None,
        trail_action: str = "create",
        trail_group_id: Optional[str] = None,
        trail_context: Optional[dict] = None,
    ) -> int:
        now = self._now()
        # The service_tasks reference is the record.  Resolving HERE
        # (the one choke point every writer — dashboard, bot, AI tool,
        # fault auto-create — passes through) means no caller can
        # forget.  The resolver is fail-open: an unrecognised slug
        # becomes an archived custom task rather than rejecting the
        # write.  The legacy ``task_type`` column is stored only when
        # resolution FAILED — the tag must never be lost, and the
        # backfill sweep repairs such rows — otherwise it gets ''.
        service_task_id = None
        if task_type:
            try:
                service_task_id = await self.resolve_service_task_id(
                    account_id, task_type, created_by=created_by,
                )
            except Exception:
                logger.warning(
                    "service_task resolve failed for %r (account %s)",
                    task_type, account_id, exc_info=True,
                )
        legacy_task_type = "" if service_task_id else task_type

        async with self.transaction():
            cur = await self._db.execute(
                """INSERT INTO maintenance_tasks
                   (account_id, company_code, vehicle_id, vehicle_name,
                    task_type, service_task_id, description,
                    due_date, due_miles, due_engine_hours,
                    priority, created_by, created_at, updated_at,
                    recur_interval_days, recur_interval_miles, recur_interval_engine_hours,
                    work_order_id, spawned_from_id,
                    last_odometer, last_engine_hours)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (account_id, company_code, vehicle_id, vehicle_name,
                 legacy_task_type, service_task_id, description,
                 due_date, due_miles, due_engine_hours,
                 priority, created_by, now, now,
                 recur_interval_days, recur_interval_miles, recur_interval_engine_hours,
                 work_order_id, spawned_from_id,
                 last_odometer, last_engine_hours),
            )
            task_id = cur.lastrowid
            await self._trail_task(
                account_id, task_id, trail_action,
                actor_user_id=actor_user_id, group_id=trail_group_id,
                context=trail_context,
                # The birth certificate: defining fields as None → value.
                changes=diff_rows({}, {
                    "vehicle_name": vehicle_name, "task_type": task_type,
                    "description": description, "due_date": due_date,
                    "due_miles": due_miles,
                    "due_engine_hours": due_engine_hours,
                    "priority": priority,
                    "recur_interval_days": recur_interval_days,
                    "recur_interval_miles": recur_interval_miles,
                    "recur_interval_engine_hours": recur_interval_engine_hours,
                    "spawned_from_id": spawned_from_id,
                }),
            )
            return task_id

    @staticmethod
    def _apply_resolved_task_type(row: dict) -> dict:
        """Let the service_tasks REFERENCE win over the legacy string.

        Every consumer of ``task_type`` — the DOT binder's
        ``== 'dot_inspection'`` compliance filter, the CSV export, both
        report PDFs, the AI tool, the dashboard grid — now reads a
        value derived from ``service_task_id`` without any of them
        changing, because the resolution happens here at the one read
        choke point.  That's what makes the reference authoritative
        instead of merely present.

        A row written before the reference existed (or one whose task
        was deleted) keeps its stored string, so nothing loses its
        label.
        """
        resolved = row.pop("_resolved_task_type", None)
        if resolved:
            row["task_type"] = resolved
        return row

    # Resolution shared by both maintenance reads: a STANDARD task
    # resolves to its canonical key (what 'dot_inspection' compares
    # against), a custom one to its name.
    _TASK_TYPE_JOIN = (
        " LEFT JOIN service_tasks st ON st.id = m.service_task_id "
    )
    _TASK_TYPE_SELECT = (
        ", COALESCE(NULLIF(st.canonical_key, ''), st.name) "
        "  AS _resolved_task_type "
    )

    async def get_maintenance_tasks(
        self, account_id: int, status: Optional[str] = None,
        vehicle_name: Optional[str] = None,
    ) -> list[dict]:
        q = ("SELECT m.*" + self._TASK_TYPE_SELECT
             + "FROM maintenance_tasks m" + self._TASK_TYPE_JOIN
             + "WHERE m.account_id = ?")
        params: list = [account_id]
        if status:
            q += " AND m.status = ?"
            params.append(status)
        if vehicle_name:
            q += " AND m.vehicle_name = ?"
            params.append(vehicle_name)
        # Sort in three keys, all server-side:
        #   1. Status bucket  — overdue → in_progress → pending → others
        #   2. Priority       — critical → high → medium → low (within bucket)
        #   3. created_at DESC — stable tiebreak; newer surfaces first
        # This lets the dashboard show critical-overdue at the very top
        # and low-priority pending at the bottom, without re-sorting on
        # the client.  COALESCE + LOWER guard against NULL / case drift
        # on legacy rows that pre-date the priority column.
        q += """
            ORDER BY
              CASE m.status
                WHEN 'overdue'     THEN 0
                WHEN 'in_progress' THEN 1
                WHEN 'pending'     THEN 2
                WHEN 'cancelled'   THEN 3
                WHEN 'completed'   THEN 4
                ELSE 5
              END,
              CASE LOWER(COALESCE(m.priority, 'medium'))
                WHEN 'critical' THEN 0
                WHEN 'high'     THEN 1
                WHEN 'medium'   THEN 2
                WHEN 'low'      THEN 3
                ELSE 4
              END,
              m.created_at DESC
        """
        cur = await self._db.execute(q, params)
        rows = await cur.fetchall()
        return [self._apply_resolved_task_type(dict(r)) for r in rows]

    async def update_maintenance_status(
        self, task_id: int, status: str, account_id: int = 0,
        actor_user_id: Optional[int] = None,
        trail_group_id: Optional[str] = None,
    ) -> bool:
        # Historical schism: the bot uses ``"done"``, the API + dashboard
        # use ``"completed"``.  Both mean the same thing — completion-time
        # is stamped for either.  We don't migrate one to the other here
        # (would invalidate existing audit logs / external integrations);
        # we just make both surfaces work.
        #
        # Mirrors the bulk helper's overdue → critical promotion so a
        # single-task path can't escape the rule.  The bulk path is the
        # current hot path (scheduler), but keeping the helpers in
        # lock-step removes one footgun for future callers.
        now = self._now()
        completed_at = now if status in ("done", "completed") else None
        promote_to_critical = status == "overdue"
        priority_clause = ", priority = 'critical'" if promote_to_critical else ""
        async with self.transaction():
            old_status: Optional[str] = None
            if actor_user_id is not None:
                cur = await self._db.execute(
                    "SELECT status FROM maintenance_tasks WHERE id = ?"
                    + (" AND account_id = ?" if account_id else ""),
                    (task_id, account_id) if account_id else (task_id,),
                )
                row = await cur.fetchone()
                old_status = row[0] if row else None
            if account_id:
                cur = await self._db.execute(
                    f"UPDATE maintenance_tasks SET status = ?, completed_at = ?, "
                    f"updated_at = ?{priority_clause} "
                    f"WHERE id = ? AND account_id = ?",
                    (status, completed_at, now, task_id, account_id),
                )
            else:
                cur = await self._db.execute(
                    f"UPDATE maintenance_tasks SET status = ?, completed_at = ?, "
                    f"updated_at = ?{priority_clause} "
                    f"WHERE id = ?",
                    (status, completed_at, now, task_id),
                )
            touched = cur.rowcount > 0
            if touched and account_id and old_status != status:
                await self._trail_task(
                    account_id, task_id,
                    "complete" if status in ("done", "completed") else "status_change",
                    actor_user_id=actor_user_id, group_id=trail_group_id,
                    changes={"status": {"from": old_status, "to": status}},
                )
            return touched

    async def update_maintenance_status_bulk(
        self, account_id: int, task_ids: list[int], status: str,
        actor_user_id: Optional[int] = None,
        trail_group_id: Optional[str] = None,
    ) -> int:
        """Bulk-update task status — replaces N × per-task UPDATEs in
        the scheduled overdue-marker jobs. One IN-clause UPDATE per
        chunk of 500 ids, single commit.

        Mirrors the single-task ``update_maintenance_status`` behavior:
        stamps ``completed_at`` for either ``"done"`` (bot) or
        ``"completed"`` (API/dashboard), and always refreshes
        ``updated_at`` so the dashboard's "Updated" column doesn't go
        stale after a bulk operation.

        Overdue auto-promotes priority to ``critical``
        --------------------------------------------
        When a task crosses into ``overdue`` (by date / mileage / engine
        hours), its priority is bumped to ``critical`` in the same
        UPDATE.  Operators sort the list by priority and expect "this
        truck is past due — fix it first" to bubble straight to the
        top; without the bump, a Medium-overdue task ranks below a
        Critical-pending task even though the overdue one is the more
        urgent ticket in reality.  The bump is persistent — once an
        operator closes the task the priority stays Critical in the
        history view, which is the operator-correct read of "this
        ticket WAS critical when we closed it" rather than rewriting
        the past.
        """
        if not task_ids:
            return 0
        now = self._now()
        completed_at = now if status in ("done", "completed") else None
        promote_to_critical = status == "overdue"
        touched = 0
        async with self.transaction():
            for i in range(0, len(task_ids), 500):
                chunk = task_ids[i:i + 500]
                placeholders = ",".join("?" * len(chunk))
                # Trail (human callers only): old statuses BEFORE the
                # UPDATE — one event per genuinely-transitioning task,
                # all sharing the caller's group.
                old_statuses: list[tuple[int, str]] = []
                if actor_user_id is not None:
                    cur = await self._db.execute(
                        f"SELECT id, status FROM maintenance_tasks "
                        f"WHERE account_id = ? AND id IN ({placeholders})",
                        (account_id, *chunk),
                    )
                    old_statuses = [
                        (r[0], r[1]) for r in await cur.fetchall()
                        if r[1] != status
                    ]
                if promote_to_critical:
                    cur = await self._db.execute(
                        f"UPDATE maintenance_tasks "
                        f"SET status = ?, completed_at = ?, updated_at = ?, "
                        f"    priority = 'critical' "
                        f"WHERE account_id = ? AND id IN ({placeholders})",
                        (status, completed_at, now, account_id, *chunk),
                    )
                else:
                    cur = await self._db.execute(
                        f"UPDATE maintenance_tasks "
                        f"SET status = ?, completed_at = ?, updated_at = ? "
                        f"WHERE account_id = ? AND id IN ({placeholders})",
                        (status, completed_at, now, account_id, *chunk),
                    )
                touched += cur.rowcount or 0
                if actor_user_id is not None and old_statuses:
                    await self.append_activity_events(account_id, [{
                        "entity_type": "maintenance_task",
                        "entity_id": tid,
                        "action": ("complete" if status in ("done", "completed")
                                   else "status_change"),
                        "changes": {"status": {"from": old, "to": status}},
                        "actor_user_id": actor_user_id,
                        "group_id": trail_group_id,
                    } for tid, old in old_statuses])
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

        Snooze: rows with ``snoozed_until > now`` are also excluded; the
        comparison is lexicographic on ISO 8601 strings which is
        equivalent to chronological for the formats we write.
        """
        now = self._now()
        cur = await self._db.execute(
            "SELECT * FROM maintenance_tasks"
            " WHERE account_id = ? AND status = 'pending'"
            " AND due_date IS NOT NULL AND due_date < ?"
            " AND alerted_at IS NULL"
            " AND (snoozed_until IS NULL OR snoozed_until < ?)",
            (account_id, now, now),
        )
        rows = await cur.fetchall()
        return [dict(r) for r in rows]

    async def get_maintenance_task(self, task_id: int, account_id: int = 0) -> Optional[dict]:
        """Get a single maintenance task by ID.

        If account_id is provided, the row must belong to that account.
        """
        sel = ("SELECT m.*" + self._TASK_TYPE_SELECT
               + "FROM maintenance_tasks m" + self._TASK_TYPE_JOIN)
        if account_id:
            cur = await self._db.execute(
                sel + "WHERE m.id = ? AND m.account_id = ?",
                (task_id, account_id),
            )
        else:
            cur = await self._db.execute(
                sel + "WHERE m.id = ?", (task_id,),
            )
        row = await cur.fetchone()
        return self._apply_resolved_task_type(dict(row)) if row else None

    async def update_maintenance_task(
        self, task_id: int, account_id: int = 0,
        actor_user_id: Optional[int] = None, **kwargs,
    ) -> bool:
        """Update maintenance task fields.

        If account_id is provided, the row must belong to that account.
        """
        allowed = {"task_type", "description", "due_date", "due_miles",
                   "recur_interval_days", "recur_interval_miles", "last_odometer",
                   # Only listed fields can be updated by callers, so
                   # accidentally PUT'ing a status or account_id field
                   # still gets dropped silently.
                   "priority", "due_engine_hours", "last_engine_hours",
                   "recur_interval_engine_hours", "work_order_id",
                   "cost_cents", "vendor_name"}
        updates = {k: v for k, v in kwargs.items() if k in allowed}
        if not updates:
            return False
        async with self.transaction():
            # Trail: the pre-edit values — the audit log used to record
            # only field NAMES here, which is what made the 2026-07-30
            # deletions unrecoverable.  Values, always.
            old: dict = {}
            if actor_user_id is not None:
                cur = await self._db.execute(
                    "SELECT * FROM maintenance_tasks WHERE id = ?"
                    + (" AND account_id = ?" if account_id else ""),
                    (task_id, account_id) if account_id else (task_id,),
                )
                row = await cur.fetchone()
                old = dict(row) if row else {}
            # Always stamp updated_at on a successful write so the dashboard's
            # "Updated" column reflects the last edit (migration 058 added
            # the column; before that, the field was always NULL).
            updates["updated_at"] = self._now()
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
            touched = cur.rowcount > 0
            if touched and account_id and old:
                changes = diff_rows(old, updates, fields=set(updates) - {"updated_at"})
                if changes:
                    await self._trail_task(
                        account_id, task_id, "update",
                        actor_user_id=actor_user_id, changes=changes,
                    )
            return touched

    async def delete_maintenance_tasks_bulk(
        self, account_id: int, task_ids: list[int],
        actor_user_id: Optional[int] = None,
        trail_group_id: Optional[str] = None,
    ) -> int:
        """Bulk-delete maintenance tasks scoped to one account.

        Returns the number of rows actually deleted (i.e. tasks that
        existed AND belonged to ``account_id``).  Chunks at 500 ids to
        stay under SQLite's parameter limit on large selections.

        The trail records EVERY deleted row's full body ({from, to:null}
        per field) as one event per task sharing ``trail_group_id`` —
        the 2026-07-30 incident's fix: no truncated id list, no lost
        values, restorable from the trail alone.
        """
        if not task_ids:
            return 0
        touched = 0
        async with self.transaction():
            for i in range(0, len(task_ids), 500):
                chunk = task_ids[i:i + 500]
                placeholders = ",".join("?" * len(chunk))
                doomed: list[dict] = []
                if actor_user_id is not None:
                    cur = await self._db.execute(
                        f"SELECT * FROM maintenance_tasks "
                        f"WHERE account_id = ? AND id IN ({placeholders})",
                        (account_id, *chunk),
                    )
                    doomed = [dict(r) for r in await cur.fetchall()]
                cur = await self._db.execute(
                    f"DELETE FROM maintenance_tasks "
                    f"WHERE account_id = ? AND id IN ({placeholders})",
                    (account_id, *chunk),
                )
                touched += cur.rowcount or 0
                if doomed:
                    await self.append_activity_events(account_id, [{
                        "entity_type": "maintenance_task",
                        "entity_id": row["id"],
                        "action": "delete",
                        "changes": delete_changes(row),
                        "actor_user_id": actor_user_id,
                        "group_id": trail_group_id,
                    } for row in doomed])
        return touched

    async def delete_maintenance_task(
        self, task_id: int, account_id: int = 0,
        actor_user_id: Optional[int] = None,
    ) -> None:
        """Delete a maintenance task.

        If account_id is provided, the row must belong to that account.
        The trail keeps the full row body — deletion is recoverable.
        """
        async with self.transaction():
            row = None
            if actor_user_id is not None and account_id:
                cur = await self._db.execute(
                    "SELECT * FROM maintenance_tasks WHERE id = ? AND account_id = ?",
                    (task_id, account_id),
                )
                r = await cur.fetchone()
                row = dict(r) if r else None
            if account_id:
                await self._db.execute(
                    "DELETE FROM maintenance_tasks WHERE id = ? AND account_id = ?",
                    (task_id, account_id),
                )
            else:
                await self._db.execute(
                    "DELETE FROM maintenance_tasks WHERE id = ?", (task_id,),
                )
            if row:
                await self._trail_task(
                    account_id, task_id, "delete",
                    actor_user_id=actor_user_id,
                    changes=delete_changes(row),
                )

    async def get_pending_tasks_by_miles(self, account_id: int) -> list[dict]:
        """Get pending tasks with due_miles set for one account.

        Skips tasks with ``alerted_at IS NOT NULL`` — already-notified tasks
        stay in the result set for ``last_odometer`` progress updates, but
        the service layer filters those out before triggering notifications.
        Actually simpler: filter here so neither the progress UPDATE nor the
        notification fires twice for the same crossing.

        ``account_id`` is required — without it the cron hot-path full-scans
        every account's tasks every 6h.  The existing
        ``(account_id, status, priority)`` index covers this query.
        """
        now = self._now()
        cur = await self._db.execute(
            "SELECT * FROM maintenance_tasks"
            " WHERE account_id = ? AND status = 'pending'"
            " AND due_miles IS NOT NULL AND alerted_at IS NULL"
            " AND (snoozed_until IS NULL OR snoozed_until < ?)",
            (account_id, now),
        )
        rows = await cur.fetchall()
        return [dict(r) for r in rows]

    async def get_pending_tasks_by_engine_hours(self, account_id: int) -> list[dict]:
        """Engine-hours twin of ``get_pending_tasks_by_miles``.

        Mileage misses wear on trucks that run heavy idle (PTO, reefer,
        generator).  This method's results feed ``mark_overdue_tasks_by_engine_hours``,
        which compares each task's ``due_engine_hours`` against the
        warehouse ``vehicle_state.engine_hours`` reading.  Alerted-throttle
        filter applied here for the same reason as the mileage path.
        """
        now = self._now()
        cur = await self._db.execute(
            "SELECT * FROM maintenance_tasks"
            " WHERE account_id = ? AND status = 'pending'"
            " AND due_engine_hours IS NOT NULL AND alerted_at IS NULL"
            " AND (snoozed_until IS NULL OR snoozed_until < ?)",
            (account_id, now),
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
        now = self._now()
        cur = await self._db.execute(
            "SELECT * FROM maintenance_tasks"
            " WHERE account_id = ? AND status = 'pending'"
            "   AND alerted_at IS NULL"
            "   AND warning_sent_at IS NULL"
            "   AND (snoozed_until IS NULL OR snoozed_until < ?)"
            "   AND ("
            "       (due_date IS NOT NULL AND due_date <= ?)"
            "    OR (due_miles IS NOT NULL AND last_odometer IS NOT NULL"
            "        AND last_odometer + ? >= due_miles)"
            "    OR (due_engine_hours IS NOT NULL AND last_engine_hours IS NOT NULL"
            "        AND last_engine_hours + ? >= due_engine_hours)"
            "   )",
            (account_id, now, cutoff_date, float(miles_ahead), float(engine_hours_ahead)),
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

    async def set_task_attachment(
        self, task_id: int, account_id: int,
        attachment_path: Optional[str],
        attachment_name: Optional[str],
        attachment_content_type: Optional[str],
        actor_user_id: Optional[int] = None,
    ) -> bool:
        """Set the attachment fields on a task, or clear them all.

        Pass all three values to attach, or all three as ``None`` to
        clear.  Account-scoped: never touches another tenant's row.
        Caller is responsible for actually deleting the bytes from the
        object store on clear; this method only updates the metadata
        row so the UI stops linking to a missing file.
        """
        async with self.transaction():
            old_name = None
            if actor_user_id is not None:
                cur = await self._db.execute(
                    "SELECT attachment_name FROM maintenance_tasks "
                    "WHERE id = ? AND account_id = ?",
                    (task_id, account_id),
                )
                r = await cur.fetchone()
                old_name = r[0] if r else None
            cur = await self._db.execute(
                "UPDATE maintenance_tasks "
                "SET attachment_path = ?, attachment_name = ?, "
                "    attachment_content_type = ?, updated_at = ? "
                "WHERE id = ? AND account_id = ?",
                (
                    attachment_path, attachment_name, attachment_content_type,
                    self._now(), task_id, account_id,
                ),
            )
            touched = cur.rowcount > 0
            if touched and actor_user_id is not None:
                await self._trail_task(
                    account_id, task_id,
                    "attachment_add" if attachment_name else "attachment_remove",
                    actor_user_id=actor_user_id,
                    changes={"attachment_name": {"from": old_name, "to": attachment_name}},
                )
            return touched

    async def snooze_task(
        self, task_id: int, account_id: int, until_iso: Optional[str],
        actor_user_id: Optional[int] = None,
    ) -> bool:
        """Set ``snoozed_until`` on a task (or clear it when ``until_iso`` is None).

        The schedulers consult this column and skip the row while it
        points to the future; once the timestamp falls into the past the
        task re-enters the normal overdue path.  Also clears
        ``alerted_at`` so the next alert fires fresh once the snooze
        expires — otherwise the throttle would suppress the post-snooze
        notification too.

        Account-scoped: never touches another tenant's row.
        """
        async with self.transaction():
            old_until = None
            if actor_user_id is not None:
                cur = await self._db.execute(
                    "SELECT snoozed_until FROM maintenance_tasks "
                    "WHERE id = ? AND account_id = ?",
                    (task_id, account_id),
                )
                r = await cur.fetchone()
                old_until = r[0] if r else None
            cur = await self._db.execute(
                "UPDATE maintenance_tasks "
                "SET snoozed_until = ?, alerted_at = NULL, updated_at = ? "
                "WHERE id = ? AND account_id = ?",
                (until_iso, self._now(), task_id, account_id),
            )
            touched = cur.rowcount > 0
            if touched and actor_user_id is not None and old_until != until_iso:
                await self._trail_task(
                    account_id, task_id, "snooze",
                    actor_user_id=actor_user_id,
                    changes={"snoozed_until": {"from": old_until, "to": until_iso}},
                )
            return touched

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
        actor_user_id: Optional[int] = None,
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
            # Trail: the spawn is attributed to the human whose
            # completion triggered it, labeled 'spawn' not 'create'.
            actor_user_id=actor_user_id,
            trail_action="spawn",
            trail_context={"from_parent": int(parent_task_id)},
        )

    # ── Maintenance templates ──────────────────────────────

    async def list_maintenance_templates(self, account_id: int) -> list[dict]:
        """All templates for one account, alphabetically by name."""
        cur = await self._db.execute(
            "SELECT * FROM maintenance_templates "
            "WHERE account_id = ? ORDER BY name ASC",
            (account_id,),
        )
        return [dict(r) for r in await cur.fetchall()]

    async def get_maintenance_template(
        self, template_id: int, account_id: int,
    ) -> Optional[dict]:
        cur = await self._db.execute(
            "SELECT * FROM maintenance_templates "
            "WHERE id = ? AND account_id = ?",
            (template_id, account_id),
        )
        row = await cur.fetchone()
        return dict(row) if row else None

    async def add_maintenance_template(
        self, *, account_id: int, name: str,
        task_type: str = "custom",
        description: str = "",
        priority: str = "medium",
        due_in_days: Optional[int] = None,
        due_in_miles: Optional[float] = None,
        due_in_hours: Optional[float] = None,
        recur_interval_days: Optional[int] = None,
        recur_interval_miles: Optional[float] = None,
        recur_interval_engine_hours: Optional[float] = None,
        created_by: int = 0,
        actor_user_id: Optional[int] = None,
    ) -> int:
        now = self._now()
        async with self.transaction():
            cur = await self._db.execute(
                """INSERT INTO maintenance_templates
                   (account_id, name, task_type, description, priority,
                    due_in_days, due_in_miles, due_in_hours,
                    recur_interval_days, recur_interval_miles, recur_interval_engine_hours,
                    created_by, created_at, updated_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (account_id, name, task_type, description, priority,
                 due_in_days, due_in_miles, due_in_hours,
                 recur_interval_days, recur_interval_miles, recur_interval_engine_hours,
                 created_by, now, now),
            )
            template_id = cur.lastrowid
            await self._trail_task(
                account_id, template_id, "create",
                entity_type="maintenance_template",
                actor_user_id=actor_user_id,
                changes=diff_rows({}, {
                    "name": name, "task_type": task_type,
                    "priority": priority, "due_in_days": due_in_days,
                    "due_in_miles": due_in_miles, "due_in_hours": due_in_hours,
                }),
            )
            return template_id

    async def update_maintenance_template(
        self, template_id: int, account_id: int,
        actor_user_id: Optional[int] = None, **kwargs,
    ) -> bool:
        """Update fields on a template.  Allowed columns mirror the
        ``add`` signature; anything else is silently dropped so a
        malformed PUT can't blank the ``account_id`` column."""
        allowed = {
            "name", "task_type", "description", "priority",
            "due_in_days", "due_in_miles", "due_in_hours",
            "recur_interval_days", "recur_interval_miles",
            "recur_interval_engine_hours",
        }
        updates = {k: v for k, v in kwargs.items() if k in allowed}
        if not updates:
            return False
        async with self.transaction():
            old: dict = {}
            if actor_user_id is not None:
                cur = await self._db.execute(
                    "SELECT * FROM maintenance_templates "
                    "WHERE id = ? AND account_id = ?",
                    (template_id, account_id),
                )
                r = await cur.fetchone()
                old = dict(r) if r else {}
            updates["updated_at"] = self._now()
            set_clause = ", ".join(f"{k} = ?" for k in updates)
            values = list(updates.values()) + [template_id, account_id]
            cur = await self._db.execute(
                f"UPDATE maintenance_templates SET {set_clause} "
                f"WHERE id = ? AND account_id = ?",
                values,
            )
            touched = cur.rowcount > 0
            if touched and old:
                changes = diff_rows(old, updates, fields=set(updates) - {"updated_at"})
                if changes:
                    await self._trail_task(
                        account_id, template_id, "update",
                        entity_type="maintenance_template",
                        actor_user_id=actor_user_id, changes=changes,
                    )
            return touched

    async def delete_maintenance_template(
        self, template_id: int, account_id: int,
        actor_user_id: Optional[int] = None,
    ) -> bool:
        async with self.transaction():
            row = None
            if actor_user_id is not None:
                cur = await self._db.execute(
                    "SELECT * FROM maintenance_templates "
                    "WHERE id = ? AND account_id = ?",
                    (template_id, account_id),
                )
                r = await cur.fetchone()
                row = dict(r) if r else None
            cur = await self._db.execute(
                "DELETE FROM maintenance_templates "
                "WHERE id = ? AND account_id = ?",
                (template_id, account_id),
            )
            touched = cur.rowcount > 0
            if touched and row:
                await self._trail_task(
                    account_id, template_id, "delete",
                    entity_type="maintenance_template",
                    actor_user_id=actor_user_id,
                    changes=delete_changes(row),
                )
            return touched

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
