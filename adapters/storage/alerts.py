"""Alert acknowledgments, DND queue, and alert history CRUD mixin."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
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


class AlertsMixin(_MixinBase):

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
        row = await self.read_one(
            "SELECT * FROM alert_acknowledgments "
            "WHERE account_id = ? AND vehicle_id = ? AND alert_type = ? "
            "AND sent_to = ? AND status = 'info' "
            "ORDER BY created_at DESC LIMIT 1",
            (account_id, vehicle_id, alert_type, sent_to),
        )
        return dict(row) if row else None

    async def get_active_vehicle_acks(
        self, account_id: int, vehicle_id: str, sent_to: int,
    ) -> list[dict]:
        """Get active (unacked) alert acks for a vehicle/subscriber pair."""
        rows = await self.read_all(
            "SELECT * FROM alert_acknowledgments "
            "WHERE account_id = ? AND vehicle_id = ? AND sent_to = ? "
            "AND acknowledged_at IS NULL AND status = 'active'",
            (account_id, vehicle_id, sent_to),
        )
        return [dict(r) for r in rows]

    async def get_active_vehicle_acks_bulk(
        self, account_id: int, vehicle_id: str, sent_tos: list[int],
    ) -> dict[int, list[dict]]:
        """Bulk variant: ``{telegram_id: [active_acks]}`` for every
        subscriber in ``sent_tos``. Replaces the per-subscriber query in
        the alerting fan-out — one chunked SELECT instead of S queries."""
        out: dict[int, list[dict]] = {tid: [] for tid in sent_tos}
        if not sent_tos:
            return out
        for i in range(0, len(sent_tos), 500):
            chunk = sent_tos[i:i + 500]
            placeholders = ",".join("?" * len(chunk))
            rows = await self.read_all(
                f"SELECT * FROM alert_acknowledgments "
                f"WHERE account_id = ? AND vehicle_id = ? "
                f"  AND sent_to IN ({placeholders}) "
                f"  AND acknowledged_at IS NULL AND status = 'active'",
                (account_id, vehicle_id, *chunk),
            )
            for r in rows:
                d = dict(r)
                tid = d.get("sent_to")
                if tid is not None:
                    out.setdefault(int(tid), []).append(d)
        return out

    async def get_info_alert_acks_bulk(
        self, account_id: int, vehicle_id: str, alert_type: str,
        sent_tos: list[int],
    ) -> dict[int, dict]:
        """Bulk variant of ``get_info_alert_ack`` — return the latest INFO
        delivery record per subscriber. ``{telegram_id: ack_dict}``;
        subscribers with no prior INFO ack are absent from the result."""
        out: dict[int, dict] = {}
        if not sent_tos:
            return out
        for i in range(0, len(sent_tos), 500):
            chunk = sent_tos[i:i + 500]
            placeholders = ",".join("?" * len(chunk))
            # MAX(id) wins per (sent_to) — id is monotonic so it tracks
            # created_at without a join back to the row.
            rows = await self.read_all(
                f"SELECT a.* FROM alert_acknowledgments a "
                f"INNER JOIN ( "
                f"  SELECT sent_to, MAX(id) AS max_id "
                f"  FROM alert_acknowledgments "
                f"  WHERE account_id = ? AND vehicle_id = ? "
                f"    AND alert_type = ? AND status = 'info' "
                f"    AND sent_to IN ({placeholders}) "
                f"  GROUP BY sent_to "
                f") latest ON a.id = latest.max_id",
                (account_id, vehicle_id, alert_type, *chunk),
            )
            for r in rows:
                d = dict(r)
                tid = d.get("sent_to")
                if tid is not None:
                    out[int(tid)] = d
        return out

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

    async def supersede_alert_acks_bulk(
        self, ack_ids: list[int], account_id: int = 0,
    ) -> int:
        """Bulk supersede — replaces N × per-row UPDATE-then-commit
        loops in the alert pipeline's edit-in-place / send-fresh
        fallbacks. Same-type ack lists are usually 0–3 entries but
        spike during escalation milestones.
        """
        if not ack_ids:
            return 0
        touched = 0
        for i in range(0, len(ack_ids), 500):
            chunk = ack_ids[i:i + 500]
            placeholders = ",".join("?" * len(chunk))
            if account_id:
                cur = await self._db.execute(
                    f"UPDATE alert_acknowledgments SET status = 'superseded' "
                    f"WHERE account_id = ? AND id IN ({placeholders})",
                    (account_id, *chunk),
                )
            else:
                cur = await self._db.execute(
                    f"UPDATE alert_acknowledgments SET status = 'superseded' "
                    f"WHERE id IN ({placeholders})",
                    tuple(chunk),
                )
            touched += cur.rowcount or 0
        await self._db.commit()
        return touched

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

    async def acknowledge_alert_history(
        self, history_id: int, user_id: int, account_id: int,
    ) -> Optional[dict]:
        """Clear an `alert_history` row (the canonical logical alert) and
        cascade-ack every related `alert_acknowledgments` delivery row.

        This is the "ack the whole logical alert" operation that fits the
        new dashboard model where one row = one alert (not one row per
        delivery).  Returns the cleared history record (with vehicle_id
        + alert_type) so callers can edit Telegram messages in chat to
        show the ack receipt; returns None when the row doesn't exist or
        is already cleared.
        """
        now = self._now()
        cur = await self._db.execute(
            "SELECT * FROM alert_history "
            "WHERE id = ? AND account_id = ? AND status = 'active'",
            (history_id, account_id),
        )
        row = await cur.fetchone()
        if not row:
            return None
        history = dict(row)
        # 1. Clear the history record (this is what the dashboard reads)
        await self._db.execute(
            "UPDATE alert_history SET status = 'cleared', last_seen = ? "
            "WHERE id = ? AND account_id = ?",
            (now, history_id, account_id),
        )
        # 2. Cascade-ack every active delivery for this (account, type, vehicle)
        await self._db.execute(
            "UPDATE alert_acknowledgments "
            "SET acknowledged_by = ?, acknowledged_at = ?, status = 'acknowledged' "
            "WHERE account_id = ? AND alert_type = ? AND vehicle_id = ? "
            "AND acknowledged_at IS NULL",
            (user_id, now, account_id, history["alert_type"], history["vehicle_id"]),
        )
        await self._db.commit()
        return history

    async def get_alert_history(
        self, account_id: int, limit: int = 50,
        *,
        alert_type: str | None = None,
        vehicle_substring: str | None = None,
        status: str | None = None,
    ) -> list[dict]:
        """Get alert acknowledgment history for an account.

        Optional filters are pushed into the WHERE clause so the
        dashboard's /alerts/history endpoint doesn't have to load the
        full window into memory before narrowing.
        """
        where = ["account_id = ?"]
        args: list[Any] = [account_id]
        if alert_type:
            where.append("alert_type = ?")
            args.append(alert_type)
        if vehicle_substring:
            where.append("LOWER(vehicle_name) LIKE ?")
            args.append(f"%{vehicle_substring.lower()}%")
        if status:
            where.append("status = ?")
            args.append(status)
        rows = await self.read_all(
            f"SELECT * FROM alert_acknowledgments "
            f"WHERE {' AND '.join(where)} "
            f"ORDER BY created_at DESC LIMIT ?",
            (*args, limit),
        )
        return [dict(r) for r in rows]

    async def get_pending_alerts(self, account_id: int) -> list[dict]:
        """Get all active (unacknowledged) alerts for an account.

        Uses a LEFT JOIN with alert_history to exclude ack rows whose shared
        history record has already been cleared — guarding against orphaned
        'active' acks left behind by any incomplete auto-resolve path.
        Acks that have no history row at all are included (conservative: the
        ack is genuinely active even if history wasn't written yet).
        """
        rows = await self.read_all(
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
        return [dict(r) for r in rows]

    async def get_alert_ack_by_id(self, ack_id: int, account_id: int = 0) -> dict | None:
        """Fetch an alert acknowledgment row by its ID.

        If account_id is provided, the row must belong to that account.
        """
        if account_id:
            row = await self.read_one(
                "SELECT * FROM alert_acknowledgments WHERE id = ? AND account_id = ?",
                (ack_id, account_id),
            )
        else:
            row = await self.read_one(
                "SELECT * FROM alert_acknowledgments WHERE id = ?", (ack_id,),
            )
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
        rows = await self.read_all(
            "SELECT * FROM dnd_alert_queue "
            "WHERE telegram_id = ? AND delivered = 0 "
            "ORDER BY created_at",
            (telegram_id,),
        )
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
        alert_subkey: str = "",
    ) -> Optional[dict]:
        """Get the single shared alert history record for a vehicle+type+subkey.

        One row per (account_id, alert_type, vehicle_id, alert_subkey).
        For events, *alert_subkey* is the event_type (rollingStop, braking,
        …) so each behavior gets its own occurrence count.  For fault /
        fuel / health the subkey is '' (default) and dedup is unchanged.
        """
        row = await self.read_one(
            "SELECT * FROM alert_history "
            "WHERE account_id = ? AND alert_type = ? "
            "AND vehicle_id = ? AND alert_subkey = ? AND status = 'active'",
            (account_id, alert_type, vehicle_id, alert_subkey),
        )
        return dict(row) if row else None

    async def upsert_alert_history(
        self, account_id: int, alert_type: str,
        vehicle_id: str, vehicle_name: str,
        last_detail: str = "",
        severity: str = "warning",
        location: str = "",
        alert_subkey: str = "",
    ) -> dict:
        """Create or update the shared alert history record for a vehicle+type+subkey.

        Uses INSERT OR IGNORE + UPDATE so only one row ever exists per
        (account_id, alert_type, vehicle_id, alert_subkey), regardless
        of how many subscribers received the alert.  *alert_subkey*
        differentiates subtypes within a class — e.g. event_type for
        the events alert family — so per-subtype occurrence counts
        are accurate.  Empty subkey ('') keeps the legacy single-bucket
        behavior for fault / fuel / health.

        ``severity`` is the cross-surface SSOT: bot, dashboard, and
        mini-app all read this value instead of re-deriving from
        alert_type.  Severity is "sticky upward" — a fault that starts
        as WARNING and escalates to CRITICAL on a later cycle gets
        promoted, but never demoted.  An once-critical alert stays
        critical until explicitly cleared.

        ``location`` is a human-readable snapshot ("Mojave Freeway, CA")
        captured at first fire and refreshed whenever a non-empty
        location is supplied on a re-fire (so a moving truck's
        latest known location is always shown).

        Returns the updated record dict.
        """
        now = self._now()
        sev = (severity or "warning").lower()
        if sev not in ("critical", "warning", "info"):
            sev = "warning"
        # Ensure exactly one row exists (UNIQUE constraint on
        # account+type+vehicle+subkey).
        await self._db.execute(
            "INSERT OR IGNORE INTO alert_history "
            "(account_id, alert_type, vehicle_id, vehicle_name, "
            "chat_id, message_id, occurrence_count, "
            "first_seen, last_seen, last_detail, status, severity, location, alert_subkey) "
            "VALUES (?, ?, ?, ?, 0, 0, 0, ?, ?, ?, 'active', ?, ?, ?)",
            (account_id, alert_type, vehicle_id, vehicle_name,
             now, now, last_detail, sev, location, alert_subkey),
        )
        # Sticky-upward severity:
        #   critical wins over everything; warning beats info; info is
        #   the floor.  CASE keeps the write atomic with the rest of
        #   the upsert.
        await self._db.execute(
            """
            UPDATE alert_history SET
              occurrence_count = occurrence_count + 1,
              vehicle_name = ?,
              last_seen    = ?,
              last_detail  = ?,
              location     = CASE WHEN ? != '' THEN ? ELSE location END,
              severity     = CASE
                  WHEN ? = 'critical' THEN 'critical'
                  WHEN severity = 'critical' THEN 'critical'
                  WHEN ? = 'warning' AND severity != 'critical' THEN 'warning'
                  ELSE severity
              END
            WHERE account_id = ? AND alert_type = ? AND vehicle_id = ?
              AND alert_subkey = ?
            """,
            (vehicle_name, now, last_detail,
             location, location,
             sev, sev,
             account_id, alert_type, vehicle_id, alert_subkey),
        )
        await self._db.commit()
        row = await self._db.execute(
            "SELECT * FROM alert_history WHERE account_id = ? AND alert_type = ? "
            "AND vehicle_id = ? AND alert_subkey = ?",
            (account_id, alert_type, vehicle_id, alert_subkey),
        )
        return dict(await row.fetchone())

    async def get_earliest_human_ack(
        self, account_id: int, alert_type: str, vehicle_id: str,
    ) -> dict | None:
        """Return the earliest *human* acknowledgment for an active alert.

        Used by the auto-resolve path to surface "Acked by <name>"
        context in the resolve receipt — so when one team member
        handled the alert before it cleared, every other recipient
        and the group topic see who closed the loop.

        ``acknowledged_by = 0`` is the system sentinel set by
        ``auto_resolve_alerts_by_vehicle`` for un-acked rows it
        sweeps; filtering it out keeps the chip true to its meaning
        ("a person handled this", not "the system auto-cleared it").

        Must be called BEFORE ``auto_resolve_alerts_by_vehicle`` —
        once that runs it overwrites ``acknowledged_at`` on all
        previously-un-acked rows, hiding the real human-ack time.
        """
        cur = await self._db.execute(
            "SELECT acknowledged_by, acknowledged_at FROM alert_acknowledgments "
            "WHERE account_id = ? AND alert_type = ? AND vehicle_id = ? "
            "AND acknowledged_by != 0 AND acknowledged_at IS NOT NULL "
            "ORDER BY acknowledged_at ASC LIMIT 1",
            (account_id, alert_type, vehicle_id),
        )
        row = await cur.fetchone()
        return dict(row) if row else None

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

    async def get_active_fault_history_for_account(self, account_id: int) -> list[dict]:
        """Return all active fault alert_history rows for an account.

        Used by the fault check loop to find vehicles whose faults cleared
        while alerts were tracked in Redis (where the in-memory _known_faults
        dict is never populated and cannot be iterated for stale-key detection).
        """
        cur = await self._db.execute(
            "SELECT * FROM alert_history "
            "WHERE account_id = ? AND alert_type = 'fault' AND status = 'active'",
            (account_id,),
        )
        rows = await cur.fetchall()
        return [dict(r) for r in rows]

    async def count_active_alert_history_for_account(self, account_id: int) -> int:
        """Lightweight COUNT(*) for the badge-poll endpoint.

        Hit every few seconds by the dashboard / mini-app to refresh the
        red dot on the Alerts tab.  The full row fetch in
        ``get_active_alert_history_for_account`` was wasteful for a
        caller that only needs an integer — this method skips the
        SELECT *, the CASE-rank ORDER BY, and the network transfer.
        """
        cur = await self._db.execute(
            "SELECT COUNT(*) AS c FROM alert_history "
            "WHERE account_id = ? AND status = 'active'",
            (account_id,),
        )
        row = await cur.fetchone()
        if row is None:
            return 0
        try:
            return int(row["c"])
        except (KeyError, TypeError):
            # Tuple-row fallback (some adapter paths)
            return int(row[0])

    async def get_active_alert_history_for_account_paged(
        self, account_id: int,
        alert_type: str | None = None,
        vehicle_substring: str | None = None,
        limit: int | None = None,
        offset: int = 0,
    ) -> list[dict]:
        """Paginated variant of ``get_active_alert_history_for_account``.

        Pushes optional alert_type / vehicle filters and LIMIT/OFFSET into
        SQL so the API layer no longer has to fetch the entire account's
        active set just to slice 100 rows.  After the per-subtype event
        dedup, fleets routinely accumulate 1000+ active rows; the prior
        full-fetch path was the dominant cost on the dashboard load.

        ORDER BY uses an inline CASE on severity (the existing index
        only knows the text value) — with LIMIT applied, the engine can
        do a top-K sort instead of a full sort, so this is a real win.
        """
        sql = (
            "SELECT *, "
            "  CASE severity "
            "    WHEN 'critical' THEN 0 "
            "    WHEN 'warning'  THEN 1 "
            "    WHEN 'info'     THEN 2 "
            "    ELSE 3 "
            "  END AS _sev_rank "
            "FROM alert_history "
            "WHERE account_id = ? AND status = 'active' "
        )
        params: list = [account_id]
        if alert_type:
            sql += "AND alert_type = ? "
            params.append(alert_type)
        if vehicle_substring:
            sql += "AND LOWER(vehicle_name) LIKE ? "
            params.append(f"%{vehicle_substring.lower()}%")
        sql += "ORDER BY _sev_rank ASC, last_seen DESC "
        if limit is not None:
            sql += "LIMIT ? OFFSET ? "
            params.extend([int(limit), int(offset)])
        cur = await self._db.execute(sql, tuple(params))
        rows = await cur.fetchall()
        return [dict(r) for r in rows]

    async def count_active_alert_history_for_account_filtered(
        self, account_id: int,
        alert_type: str | None = None,
        vehicle_substring: str | None = None,
    ) -> int:
        """Filtered COUNT(*) matching the same WHERE as the paged query.

        Used by the API to compute the total page count without a
        second full fetch.
        """
        sql = (
            "SELECT COUNT(*) AS c FROM alert_history "
            "WHERE account_id = ? AND status = 'active' "
        )
        params: list = [account_id]
        if alert_type:
            sql += "AND alert_type = ? "
            params.append(alert_type)
        if vehicle_substring:
            sql += "AND LOWER(vehicle_name) LIKE ? "
            params.append(f"%{vehicle_substring.lower()}%")
        cur = await self._db.execute(sql, tuple(params))
        row = await cur.fetchone()
        if row is None:
            return 0
        try:
            return int(row["c"])
        except (KeyError, TypeError):
            return int(row[0])

    async def get_active_alert_history_for_vehicle(
        self, account_id: int, vehicle_id: str,
    ) -> list[dict]:
        """Active alerts for one (account, vehicle) — pushes the
        vehicle_id filter into SQL instead of loading the full account
        and filtering in Python.  Backed by
        ``idx_alert_history_active`` (account_id, alert_type,
        vehicle_id, status).
        """
        cur = await self._db.execute(
            "SELECT * FROM alert_history "
            "WHERE account_id = ? AND vehicle_id = ? AND status = 'active' "
            "ORDER BY last_seen DESC",
            (account_id, vehicle_id),
        )
        rows = await cur.fetchall()
        return [dict(r) for r in rows]

    async def get_active_alert_history_for_account(self, account_id: int) -> list[dict]:
        """Return one row per *logical* active alert for the account.

        Sorted by **severity then last_seen** so a fresh CRITICAL bubbles
        above older WARNINGs, and within each severity tier the most
        recently re-fired alert comes first.  Backed by the
        ``idx_alert_history_active_sort`` index.

        Each row carries:
            - id (the AlertID surfaced in UI)
            - alert_type, vehicle_id, vehicle_name
            - severity ('critical' | 'warning' | 'info')
            - location (snapshot string)
            - occurrence_count (× N times)
            - first_seen, last_seen
            - last_detail (latest description)
            - status (always 'active' here; cleared rows are filtered out)

        Crucially, this does NOT fan out by recipient — `alert_history`
        has one row per (account, alert_type, vehicle), so 7 subscribers
        receiving the same alert produce ONE row, not seven.
        """
        cur = await self._db.execute(
            """
            SELECT *,
                   CASE severity
                       WHEN 'critical' THEN 0
                       WHEN 'warning'  THEN 1
                       WHEN 'info'     THEN 2
                       ELSE 3
                   END AS _sev_rank
              FROM alert_history
             WHERE account_id = ? AND status = 'active'
             ORDER BY _sev_rank ASC, last_seen DESC
            """,
            (account_id,),
        )
        rows = await cur.fetchall()
        return [dict(r) for r in rows]

    async def get_stale_unacked_alerts(
        self,
        account_id: int,
        older_than_minutes: int,
        severity: str = "critical",  # noqa: ARG002 — reserved for future filter
    ) -> list[dict]:
        """Return active acks created more than ``older_than_minutes`` ago.

        Used by the re-escalation job to find CRITICAL/WARNING alerts that
        nobody has acknowledged yet.  Severity is not currently stored on
        the row (it lives on the originating check); the caller decides
        which alert types qualify for re-escalation.
        """
        cutoff = (
            datetime.now(timezone.utc) - timedelta(minutes=older_than_minutes)
        ).isoformat()
        cur = await self._db.execute(
            "SELECT * FROM alert_acknowledgments "
            "WHERE account_id = ? "
            "AND status = 'active' "
            "AND acknowledged_at IS NULL "
            "AND created_at < ? "
            "ORDER BY created_at ASC",
            (account_id, cutoff),
        )
        rows = await cur.fetchall()
        return [dict(r) for r in rows]

    # ── Per-alert mute (D2) ────────────────────────────────────────
    # Sister table to alert_history. Operators can pause Telegram delivery
    # for a specific logical alert without acking it, e.g. "I know SPN
    # 524133 is broken on Truck 200, mute it for 7 days while we order
    # the part".  The dashboard still shows the alert so it isn't lost.

    async def mute_alert_history(
        self,
        history_id: int,
        account_id: int,
        muted_by: int,
        hours: int = 24 * 7,
        reason: str = "",
    ) -> Optional[dict]:
        """Mute alert_history row for ``hours`` hours.  Returns the new
        mute record dict, or None if the history row doesn't belong to
        the account (defensive — prevents cross-tenant mute writes)."""
        # Verify the alert belongs to this account
        cur = await self._db.execute(
            "SELECT id FROM alert_history WHERE id = ? AND account_id = ?",
            (history_id, account_id),
        )
        if not await cur.fetchone():
            return None
        now_dt = datetime.now(timezone.utc)
        until = (now_dt + timedelta(hours=hours)).isoformat()
        now = self._now()
        await self._db.execute(
            "INSERT INTO alert_mutes "
            "(account_id, alert_history_id, muted_by, scope, "
            " reason, muted_until, created_at) "
            "VALUES (?, ?, ?, 'all_recipients', ?, ?, ?)",
            (account_id, history_id, muted_by, reason, until, now),
        )
        await self._db.commit()
        return {
            "alert_history_id": history_id,
            "muted_by": muted_by,
            "muted_until": until,
            "hours": hours,
        }

    async def is_alert_history_muted(
        self, history_id: int, account_id: int,
    ) -> bool:
        """Cheap check: any unexpired mute on this alert_history row?

        Pipeline calls this before every send/edit.  Indexed query —
        single-row hit, microseconds even on large mute tables.
        """
        now = self._now()
        cur = await self._db.execute(
            "SELECT 1 FROM alert_mutes "
            "WHERE account_id = ? AND alert_history_id = ? "
            "AND muted_until > ? "
            "LIMIT 1",
            (account_id, history_id, now),
        )
        return (await cur.fetchone()) is not None

    async def get_active_mutes_for_account(
        self, account_id: int, *, limit: int = 500,
    ) -> list[dict]:
        """Return active (unexpired) mutes — used by dashboard to show
        a "muted" badge on alerts so operators know what's silenced.

        ``limit`` caps the result so a runaway account with thousands
        of stale mutes can't blow up the response payload.
        """
        now = self._now()
        rows = await self.read_all(
            "SELECT * FROM alert_mutes "
            "WHERE account_id = ? AND muted_until > ? "
            "ORDER BY muted_until ASC LIMIT ?",
            (account_id, now, int(limit)),
        )
        return [dict(r) for r in rows]

    async def unmute_alert_history(
        self, history_id: int, account_id: int,
    ) -> int:
        """Drop every active mute targeting this alert_history row.
        Returns the number of mute rows removed."""
        cur = await self._db.execute(
            "DELETE FROM alert_mutes "
            "WHERE alert_history_id = ? AND account_id = ?",
            (history_id, account_id),
        )
        await self._db.commit()
        return cur.rowcount or 0

    # ── Re-escalation tracking ─────────────────────────────────────

    async def get_active_unacked_history_for_reescalation(
        self,
        account_id: int,
        cutoff_iso: str,
        max_attempts: int,
    ) -> list[dict]:
        """Logical alerts (alert_history rows) that need re-escalation.

        Reads the canonical history table (one row per vehicle+type)
        rather than alert_acknowledgments, so re-escalation fires once
        per *logical* alert instead of once per subscriber.  Subscriber
        fan-out happens at delivery time inside the scheduler.

        Filters:
          - status = 'active' (auto-resolved alerts excluded)
          - reescalate_count < max_attempts (cap reached → silent)
          - reescalate_last_sent_at IS NULL OR < cutoff
            (cutoff reflects per-attempt exponential backoff)
        """
        if max_attempts <= 0:
            return []
        rows = await self.read_all(
            "SELECT * FROM alert_history "
            "WHERE account_id = ? AND status = 'active' "
            "AND reescalate_count < ? "
            "AND (reescalate_last_sent_at IS NULL OR reescalate_last_sent_at < ?) "
            "ORDER BY first_seen ASC",
            (account_id, max_attempts, cutoff_iso),
        )
        return [dict(r) for r in rows]

    async def bump_reescalate_attempt(
        self, history_id: int, account_id: int,
    ) -> int:
        """Mark a re-escalation as just sent.  Returns the new count."""
        now = self._now()
        await self._db.execute(
            "UPDATE alert_history "
            "SET reescalate_count = reescalate_count + 1, "
            "    reescalate_last_sent_at = ? "
            "WHERE id = ? AND account_id = ?",
            (now, history_id, account_id),
        )
        await self._db.commit()
        cur = await self._db.execute(
            "SELECT reescalate_count FROM alert_history WHERE id = ? AND account_id = ?",
            (history_id, account_id),
        )
        row = await cur.fetchone()
        return int(row["reescalate_count"]) if row else 0
