"""Alert acknowledgments, DND queue, and alert history CRUD mixin."""

from __future__ import annotations

from typing import Optional


class AlertsMixin:

    # ── Alert Acknowledgments ─────────────────────────────────────

    async def create_alert_ack(
        self, account_id: int, alert_type: str,
        vehicle_id: str, vehicle_name: str, alert_key: str,
        message_id: int, chat_id: int, sent_to: int,
    ) -> int:
        """Record a sent CRITICAL/WARNING alert that needs acknowledgment."""
        now = self._now()
        cur = await self._db.execute(
            """INSERT INTO alert_acknowledgments
               (account_id, alert_type, vehicle_id, vehicle_name, alert_key,
                message_id, chat_id, sent_to, created_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (account_id, alert_type, vehicle_id, vehicle_name, alert_key,
             message_id, chat_id, sent_to, now),
        )
        await self._db.commit()
        return cur.lastrowid

    async def create_info_alert_ack(
        self, account_id: int, alert_type: str,
        vehicle_id: str, vehicle_name: str, alert_key: str,
        message_id: int, chat_id: int, sent_to: int,
    ) -> int:
        """Record a sent INFO alert for per-subscriber message tracking.

        Status is 'info' — not shown as pending, not acknowledged.
        Supersedes older 'info' rows for this subscriber+vehicle+type so only
        the latest message_id is tracked per subscriber.
        """
        now = self._now()
        # Supersede old info rows for this subscriber+vehicle+type
        await self._db.execute(
            "UPDATE alert_acknowledgments SET status = 'superseded' "
            "WHERE account_id = ? AND vehicle_id = ? AND alert_type = ? "
            "AND sent_to = ? AND status = 'info'",
            (account_id, vehicle_id, alert_type, sent_to),
        )
        cur = await self._db.execute(
            """INSERT INTO alert_acknowledgments
               (account_id, alert_type, vehicle_id, vehicle_name, alert_key,
                message_id, chat_id, sent_to, created_at, status)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 'info')""",
            (account_id, alert_type, vehicle_id, vehicle_name, alert_key,
             message_id, chat_id, sent_to, now),
        )
        await self._db.commit()
        return cur.lastrowid

    async def get_info_alert_ack(
        self, account_id: int, vehicle_id: str, alert_type: str, sent_to: int,
    ) -> Optional[dict]:
        """Get the latest INFO delivery record for a subscriber+vehicle+type.

        Used to retrieve the previous message_id for deletion before sending
        a new INFO alert to the same subscriber.
        """
        cur = await self._db.execute(
            "SELECT * FROM alert_acknowledgments "
            "WHERE account_id = ? AND vehicle_id = ? AND alert_type = ? "
            "AND sent_to = ? AND status = 'info' "
            "ORDER BY created_at DESC LIMIT 1",
            (account_id, vehicle_id, alert_type, sent_to),
        )
        row = await cur.fetchone()
        return dict(row) if row else None

    async def get_active_vehicle_acks(
        self, account_id: int, vehicle_id: str, sent_to: int,
    ) -> list[dict]:
        """Get active (unacked) alert acks for a vehicle/subscriber pair."""
        cur = await self._db.execute(
            "SELECT * FROM alert_acknowledgments "
            "WHERE account_id = ? AND vehicle_id = ? AND sent_to = ? "
            "AND acknowledged_at IS NULL AND status = 'active'",
            (account_id, vehicle_id, sent_to),
        )
        rows = await cur.fetchall()
        return [dict(r) for r in rows]

    async def supersede_alert_ack(self, ack_id: int, account_id: int = 0):
        """Mark an alert ack as superseded (replaced by a newer alert).

        If account_id is provided, the row must belong to that account.
        """
        if account_id:
            await self._db.execute(
                "UPDATE alert_acknowledgments SET status = 'superseded' "
                "WHERE id = ? AND account_id = ?",
                (ack_id, account_id),
            )
        else:
            await self._db.execute(
                "UPDATE alert_acknowledgments SET status = 'superseded' "
                "WHERE id = ?",
                (ack_id,),
            )
        await self._db.commit()

    async def acknowledge_alert(self, ack_id: int, user_id: int, account_id: int = 0) -> bool:
        """Mark an alert as acknowledged — also acks all rows with the same alert_key."""
        now = self._now()
        # Get the alert_key for this alert
        if account_id:
            cur = await self._db.execute(
                "SELECT alert_key, account_id FROM alert_acknowledgments WHERE id = ? AND account_id = ?",
                (ack_id, account_id),
            )
        else:
            cur = await self._db.execute(
                "SELECT alert_key, account_id FROM alert_acknowledgments WHERE id = ?",
                (ack_id,),
            )
        row = await cur.fetchone()
        if not row:
            return False
        alert_key = row["alert_key"]
        row_account_id = row["account_id"]
        # Ack all rows with the same alert_key (shared acknowledgment)
        await self._db.execute(
            "UPDATE alert_acknowledgments SET acknowledged_by = ?, acknowledged_at = ?, "
            "status = 'acknowledged' "
            "WHERE alert_key = ? AND account_id = ? AND acknowledged_at IS NULL",
            (user_id, now, alert_key, row_account_id),
        )
        await self._db.commit()
        return True

    async def get_alert_history(
        self, account_id: int, limit: int = 50,
    ) -> list[dict]:
        """Get alert acknowledgment history for an account (all statuses)."""
        cur = await self._db.execute(
            "SELECT * FROM alert_acknowledgments WHERE account_id = ? "
            "ORDER BY created_at DESC LIMIT ?",
            (account_id, limit),
        )
        rows = await cur.fetchall()
        return [dict(r) for r in rows]

    async def get_pending_alerts(self, account_id: int) -> list[dict]:
        """Get all active (unacknowledged) alerts for an account.

        Uses a LEFT JOIN with alert_history to exclude ack rows whose shared
        history record has already been cleared — guarding against orphaned
        'active' acks left behind by any incomplete auto-resolve path.
        Acks that have no history row at all are included (conservative: the
        ack is genuinely active even if history wasn't written yet).
        """
        cur = await self._db.execute(
            """SELECT a.*
               FROM alert_acknowledgments a
               LEFT JOIN alert_history h
                    ON  h.account_id = a.account_id
                    AND h.alert_type = a.alert_type
                    AND h.vehicle_id = a.vehicle_id
               WHERE a.account_id = ?
                 AND a.status = 'active'
                 AND (h.id IS NULL OR h.status = 'active')
               ORDER BY a.created_at DESC""",
            (account_id,),
        )
        rows = await cur.fetchall()
        return [dict(r) for r in rows]

    async def get_alert_ack_by_id(self, ack_id: int, account_id: int = 0) -> dict | None:
        """Fetch an alert acknowledgment row by its ID.

        If account_id is provided, the row must belong to that account.
        """
        if account_id:
            cur = await self._db.execute(
                "SELECT * FROM alert_acknowledgments WHERE id = ? AND account_id = ?",
                (ack_id, account_id),
            )
        else:
            cur = await self._db.execute(
                "SELECT * FROM alert_acknowledgments WHERE id = ?", (ack_id,),
            )
        row = await cur.fetchone()
        if not row:
            return None
        return dict(row)

    # ── DND Alert Queue ───────────────────────────────────────────

    async def queue_dnd_alert(
        self, account_id: int, telegram_id: int,
        alert_type: str, vehicle_name: str, alert_text: str,
    ) -> int:
        """Queue a non-critical alert suppressed by DND for later delivery."""
        now = self._now()
        cur = await self._db.execute(
            "INSERT INTO dnd_alert_queue "
            "(account_id, telegram_id, alert_type, vehicle_name, alert_text, created_at) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            (account_id, telegram_id, alert_type, vehicle_name, alert_text, now),
        )
        await self._db.commit()
        return cur.lastrowid

    async def get_pending_dnd_alerts(self, telegram_id: int) -> list[dict]:
        """Get all undelivered DND-queued alerts for a user."""
        cur = await self._db.execute(
            "SELECT * FROM dnd_alert_queue "
            "WHERE telegram_id = ? AND delivered = 0 "
            "ORDER BY created_at",
            (telegram_id,),
        )
        rows = await cur.fetchall()
        return [dict(r) for r in rows]

    async def mark_dnd_alerts_delivered(self, telegram_id: int) -> int:
        """Mark all pending DND alerts as delivered for a user. Returns count."""
        cur = await self._db.execute(
            "UPDATE dnd_alert_queue SET delivered = 1 "
            "WHERE telegram_id = ? AND delivered = 0",
            (telegram_id,),
        )
        await self._db.commit()
        return cur.rowcount

    # ── Alert History (consolidation) ────────────────────────────

    async def get_active_alert_history(
        self, account_id: int, alert_type: str, vehicle_id: str,
    ) -> Optional[dict]:
        """Get the single shared alert history record for a vehicle+type.

        One row per (account_id, alert_type, vehicle_id) — not per subscriber.
        Per-subscriber Telegram message tracking is in alert_acknowledgments.
        """
        cur = await self._db.execute(
            "SELECT * FROM alert_history "
            "WHERE account_id = ? AND alert_type = ? "
            "AND vehicle_id = ? AND status = 'active'",
            (account_id, alert_type, vehicle_id),
        )
        row = await cur.fetchone()
        return dict(row) if row else None

    async def upsert_alert_history(
        self, account_id: int, alert_type: str,
        vehicle_id: str, vehicle_name: str,
        last_detail: str = "",
    ) -> dict:
        """Create or update the single shared alert history record for a vehicle+type.

        Uses INSERT OR IGNORE + UPDATE so only one row ever exists per
        (account_id, alert_type, vehicle_id), regardless of how many
        subscribers received the alert.  Occurrence count is the total
        number of times the alert fired fleet-wide, not per-subscriber.
        Returns the updated record dict.
        """
        now = self._now()
        # Ensure exactly one row exists (UNIQUE constraint on account+type+vehicle)
        await self._db.execute(
            "INSERT OR IGNORE INTO alert_history "
            "(account_id, alert_type, vehicle_id, vehicle_name, "
            "chat_id, message_id, occurrence_count, "
            "first_seen, last_seen, last_detail, status) "
            "VALUES (?, ?, ?, ?, 0, 0, 0, ?, ?, ?, 'active')",
            (account_id, alert_type, vehicle_id, vehicle_name, now, now, last_detail),
        )
        await self._db.execute(
            "UPDATE alert_history SET "
            "occurrence_count = occurrence_count + 1, "
            "vehicle_name = ?, last_seen = ?, last_detail = ? "
            "WHERE account_id = ? AND alert_type = ? AND vehicle_id = ?",
            (vehicle_name, now, last_detail, account_id, alert_type, vehicle_id),
        )
        await self._db.commit()
        row = await self._db.execute(
            "SELECT * FROM alert_history WHERE account_id = ? AND alert_type = ? AND vehicle_id = ?",
            (account_id, alert_type, vehicle_id),
        )
        return dict(await row.fetchone())

    async def auto_resolve_alerts_by_vehicle(
        self, account_id: int, alert_type: str, vehicle_id: str,
    ) -> list[dict]:
        """Auto-resolve all active unacked alerts for a vehicle/type.

        Called from check loops when a vehicle's condition clears.
        Returns resolved rows (with message_id/chat_id for cleanup).
        """
        cur = await self._db.execute(
            "SELECT * FROM alert_acknowledgments "
            "WHERE account_id = ? AND alert_type = ? AND vehicle_id = ? "
            "AND acknowledged_at IS NULL AND status = 'active'",
            (account_id, alert_type, vehicle_id),
        )
        rows = await cur.fetchall()
        resolved = [dict(r) for r in rows]
        if resolved:
            now = self._now()
            ids = [r["id"] for r in resolved]
            placeholders = ",".join("?" for _ in ids)
            await self._db.execute(
                f"UPDATE alert_acknowledgments SET acknowledged_by = 0, "
                f"acknowledged_at = ?, status = 'acknowledged' "
                f"WHERE id IN ({placeholders})",
                [now] + ids,
            )
            await self._db.commit()
        return resolved

    async def clear_alert_history(
        self, account_id: int, alert_type: str,
        vehicle_id: str,
    ) -> list[dict]:
        """Mark active alert history records as cleared for a vehicle.

        Returns the cleared records (with message_id/chat_id for deletion).
        """
        now = self._now()
        cur = await self._db.execute(
            "SELECT * FROM alert_history "
            "WHERE account_id = ? AND alert_type = ? "
            "AND vehicle_id = ? AND status = 'active'",
            (account_id, alert_type, vehicle_id),
        )
        rows = [dict(r) for r in await cur.fetchall()]
        if rows:
            await self._db.execute(
                "UPDATE alert_history SET status = 'cleared' "
                "WHERE account_id = ? AND alert_type = ? "
                "AND vehicle_id = ? AND status = 'active'",
                (account_id, alert_type, vehicle_id),
            )
            await self._db.commit()
        return rows
