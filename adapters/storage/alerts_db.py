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
        """Record a sent alert that needs acknowledgment."""
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
        """Get all active (unacknowledged, not expired) alerts for an account."""
        cur = await self._db.execute(
            "SELECT * FROM alert_acknowledgments "
            "WHERE account_id = ? AND status = 'active' "
            "ORDER BY created_at DESC",
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
        self, account_id: int, alert_type: str,
        vehicle_id: str, chat_id: int,
    ) -> Optional[dict]:
        """Get the active alert history record for a vehicle+type+chat."""
        cur = await self._db.execute(
            "SELECT * FROM alert_history "
            "WHERE account_id = ? AND alert_type = ? "
            "AND vehicle_id = ? AND chat_id = ? AND status = 'active'",
            (account_id, alert_type, vehicle_id, chat_id),
        )
        row = await cur.fetchone()
        return dict(row) if row else None

    async def upsert_alert_history(
        self, account_id: int, alert_type: str,
        vehicle_id: str, vehicle_name: str,
        chat_id: int, message_id: int,
        last_detail: str = "",
    ) -> dict:
        """Create or update an alert history record.

        If an active record exists: increment count, update last_seen
        and message_id. Otherwise create a new record.
        Returns the record dict.
        """
        now = self._now()
        existing = await self.get_active_alert_history(
            account_id, alert_type, vehicle_id, chat_id,
        )
        if existing:
            await self._db.execute(
                "UPDATE alert_history SET "
                "occurrence_count = occurrence_count + 1, "
                "last_seen = ?, message_id = ?, last_detail = ? "
                "WHERE id = ?",
                (now, message_id, last_detail, existing["id"]),
            )
            await self._db.commit()
            existing["occurrence_count"] += 1
            existing["last_seen"] = now
            existing["message_id"] = message_id
            existing["last_detail"] = last_detail
            return existing
        else:
            cur = await self._db.execute(
                "INSERT INTO alert_history "
                "(account_id, alert_type, vehicle_id, vehicle_name, "
                "chat_id, message_id, occurrence_count, "
                "first_seen, last_seen, last_detail, status) "
                "VALUES (?, ?, ?, ?, ?, ?, 1, ?, ?, ?, 'active')",
                (account_id, alert_type, vehicle_id, vehicle_name,
                 chat_id, message_id, now, now, last_detail),
            )
            await self._db.commit()
            return {
                "id": cur.lastrowid,
                "account_id": account_id,
                "alert_type": alert_type,
                "vehicle_id": vehicle_id,
                "vehicle_name": vehicle_name,
                "chat_id": chat_id,
                "message_id": message_id,
                "occurrence_count": 1,
                "first_seen": now,
                "last_seen": now,
                "last_detail": last_detail,
                "status": "active",
            }

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
