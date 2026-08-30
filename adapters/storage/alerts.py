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
        severity: str = "warning",
    ) -> int:
        """Record a sent CRITICAL/WARNING alert that needs acknowledgment.

        Stamps ``expires_at`` per the severity TTL ladder so the row
        ages out naturally if nobody acks and the system never
        auto-resolves (chronic fault, dead driver, etc.):

            critical  →  NULL    (never expires)
            warning   →  +14 days
            info      →  +7 days

        The nightly stale-close job and the ``/alerts/pending``
        dashboard query both honour ``expires_at`` so an aged-out
        row stops appearing without manual sweep logic.
        """
        now = self._now()
        expires_at = self._compute_expires_at(severity, now)
        cur = await self._db.execute(
            """INSERT INTO alert_acknowledgments
               (account_id, alert_type, vehicle_id, vehicle_name, alert_key,
                message_id, chat_id, sent_to, created_at, expires_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (account_id, alert_type, vehicle_id, vehicle_name, alert_key,
             message_id, chat_id, sent_to, now, expires_at),
        )
        await self._db.commit()
        return cur.lastrowid

    @staticmethod
    def _compute_expires_at(severity: str, created_at_iso: str) -> str | None:
        """Compute ``expires_at`` for an ack row from severity + created_at.

        Critical alerts return ``None`` — they must be human-acked or
        system-resolved; never aged out by TTL.  Warning/info get a
        rolling expiry based on the severity ladder.
        """
        from datetime import datetime, timedelta, timezone
        sev = (severity or "warning").lower()
        if sev == "critical":
            return None
        days = 7 if sev == "info" else 14  # warning is the default
        try:
            dt = datetime.fromisoformat(created_at_iso.replace("Z", "+00:00"))
        except (ValueError, TypeError):
            dt = datetime.now(timezone.utc)
        return (dt + timedelta(days=days)).isoformat()

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
        """Mark an alert as acknowledged.

        Previously this cascaded only rows sharing the same
        ``alert_key``.  Problem: per-recipient DM acks have
        ``alert_key = "{co}:{vid}:{detail}"`` while the group-post
        sentinel has ``alert_key = "{co}:{vid}:group"`` — different
        strings, so the bot-side ack closed only one side and left
        the other piling up in the table forever (visible on the
        dashboard as ever-growing "pending" counts even after the
        operator acked from Telegram).

        New behaviour mirrors ``acknowledge_alert_history`` — cascade
        across every delivery row for the (account, alert_type,
        vehicle_id) tuple, regardless of which surface (group vs DM)
        it lives on, AND clear the matching ``alert_history`` rows
        so the canonical "is this in flight?" gate also closes.
        Single ack from anywhere now closes everything related.

        Native Postgres SQL with ``$N`` placeholders — passes through
        the adapter's translator unchanged (the translator only
        rewrites SQLite-specific patterns).
        """
        now = self._now()
        # Get the ack row's tenant context.
        if account_id:
            cur = await self._db.execute(
                "SELECT alert_type, vehicle_id, account_id "
                "FROM alert_acknowledgments "
                "WHERE id = $1 AND account_id = $2",
                (ack_id, account_id),
            )
        else:
            cur = await self._db.execute(
                "SELECT alert_type, vehicle_id, account_id "
                "FROM alert_acknowledgments WHERE id = $1",
                (ack_id,),
            )
        row = await cur.fetchone()
        if not row:
            return False
        alert_type = row["alert_type"]
        vehicle_id = row["vehicle_id"]
        row_account_id = row["account_id"]

        # 1. Cascade-ack every active delivery row for this logical alert —
        #    closes both the per-recipient DM rows AND the group-post
        #    sentinel (sent_to=0) in one shot.
        await self._db.execute(
            "UPDATE alert_acknowledgments "
            "SET acknowledged_by = $1, acknowledged_at = $2, "
            "    status = 'acknowledged' "
            "WHERE account_id = $3 AND alert_type = $4 AND vehicle_id = $5 "
            "  AND acknowledged_at IS NULL",
            (user_id, now, row_account_id, alert_type, vehicle_id),
        )

        # 2. Clear the matching alert_history rows so the resolve flow's
        #    "is this in flight?" gate also reflects the ack.  Without
        #    this, the next health/fault check would re-fire receipts.
        #    Stamp the actor so the dashboard can attribute the ack
        #    (vs. a NULL acknowledged_by, which reads as "Auto-resolved").
        await self._db.execute(
            "UPDATE alert_history "
            "SET status = 'cleared', last_seen = $1, "
            "    acknowledged_by = $2, acknowledged_at = $3 "
            "WHERE account_id = $4 AND alert_type = $5 AND vehicle_id = $6 "
            "  AND status = 'active'",
            (now, user_id, now, row_account_id, alert_type, vehicle_id),
        )
        await self._db.commit()
        return True

    async def acknowledge_alert_history(
        self, history_id: int, user_id: int, account_id: int,
        *, allowed_vehicle_names: list[str] | None = None,
    ) -> Optional[dict]:
        """Clear an `alert_history` row (the canonical logical alert) and
        cascade-ack every related `alert_acknowledgments` delivery row.

        This is the "ack the whole logical alert" operation that fits the
        new dashboard model where one row = one alert (not one row per
        delivery).  Returns the cleared history record (with vehicle_id
        + alert_type) so callers can edit Telegram messages in chat to
        show the ack receipt; returns None when the row doesn't exist or
        is already cleared.

        ``allowed_vehicle_names`` enforces Vehicle-Access scope IN the same
        statements that read and clear the row (TOCTOU-free — a stale AI
        proposal can never clear a vehicle the approver can't access):
        ``None`` = unrestricted; a list = only alerts whose ``vehicle_name``
        matches (case-insensitive); ``[]`` = nothing.  An out-of-scope id
        returns None — the same idempotent-skip path as an unknown id.
        """
        now = self._now()
        # Scope predicate, appended to BOTH the guard SELECT and the UPDATE so
        # the clear can't race a scope change between read and write.
        scope_sql = ""
        scope_args: tuple = ()
        if allowed_vehicle_names is not None:
            names = [n.strip().lower() for n in allowed_vehicle_names if n and n.strip()]
            if not names:
                return None   # scoped to nothing — fail closed
            placeholders = ",".join("?" for _ in names)
            scope_sql = f" AND LOWER(vehicle_name) IN ({placeholders})"
            scope_args = tuple(names)
        cur = await self._db.execute(
            "SELECT * FROM alert_history "
            "WHERE id = ? AND account_id = ? AND status = 'active'" + scope_sql,
            (history_id, account_id, *scope_args),
        )
        row = await cur.fetchone()
        if not row:
            return None
        history = dict(row)
        # 1. Clear the history record (this is what the dashboard reads).
        #    Stamp the actor + time so the windowed dashboard view can
        #    render "Acknowledged by {name}" — a NULL acknowledged_by
        #    later means the row was auto-resolved by a check loop
        #    (clear_alert_history), which the UI labels "Auto-resolved".
        await self._db.execute(
            "UPDATE alert_history "
            "SET status = 'cleared', last_seen = ?, "
            "    acknowledged_by = ?, acknowledged_at = ? "
            "WHERE id = ? AND account_id = ?" + scope_sql,
            (now, user_id, now, history_id, account_id, *scope_args),
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

    async def get_alert_history_vehicles(
        self, account_id: int, history_ids: list[int],
    ) -> dict[int, str]:
        """Map each `alert_history` id (within this account) to its
        `vehicle_name`.  Used to scope-check an acknowledge request BEFORE
        proposing it — a scoped caller may only ack their own vehicles'
        alerts.  Unknown / foreign ids are simply absent from the result
        (account_id already filters cross-tenant ids out)."""
        ids = [int(i) for i in history_ids][:50]
        if not ids:
            return {}
        placeholders = ",".join("?" for _ in ids)
        rows = await self.read_all(
            f"SELECT id, vehicle_name FROM alert_history "
            f"WHERE account_id = ? AND id IN ({placeholders})",
            (account_id, *ids),
        )
        out: dict[int, str] = {}
        for r in rows:
            d = dict(r)
            out[int(d["id"])] = d.get("vehicle_name") or ""
        return out

    async def get_alert_history(
        self, account_id: int, limit: int = 50,
        *,
        alert_type: str | None = None,
        vehicle_substring: str | None = None,
        status: str | None = None,
        severity: str | None = None,
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
        if severity:
            where.append("severity = ?")
            args.append(severity)
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

    async def get_alert_history_by_id(
        self, account_id: int, alert_id: int,
    ) -> Optional[dict]:
        """One alert_history row by id, scoped to the account.

        NOT filtered by status or date: this backs the deep link that opens
        a specific alert, which must work for a chronic alert older than any
        board window and for one a colleague already acknowledged.
        """
        row = await self.read_one(
            "SELECT * FROM alert_history WHERE id = ? AND account_id = ?",
            (int(alert_id), account_id),
        )
        return dict(row) if row else None

    async def get_active_alert_history_by_ids(
        self, account_id: int, ids: list[int],
    ) -> list[dict]:
        """Of the given alert_history ids, the ones still ``status='active'``
        (this account only).  The AUTHORITATIVE "is this specific alert still
        open?" check the live-banner watcher needs — unlike the recent-alerts
        feed, it is not capped by page size or a first_seen window, so a
        long-running critical never falsely reads as resolved.  Ids that are
        cleared, resolved, or pruned simply don't come back."""
        if not ids:
            return []
        ph = ", ".join("?" for _ in ids)
        cur = await self._db.execute(
            f"SELECT * FROM alert_history "
            f"WHERE account_id = ? AND status = 'active' AND id IN ({ph})",
            (account_id, *[int(i) for i in ids]),
        )
        return [dict(r) for r in await cur.fetchall()]

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

        ``acknowledged_by > 0`` filters out the two system sentinels:

          * ``= 0`` — set by ``auto_resolve_alerts_by_vehicle`` when
            it sweeps an un-acked row at clear time.
          * ``= -1`` (``SYSTEM_USER_ID``) — set by AI auto-action
            flows (AI maintenance auto-create, parking AI vision)
            when the system itself takes an action.

        Real Telegram user IDs are always strictly positive, so the
        ``> 0`` predicate cleanly admits humans only.  Without it,
        the receipt would render "Acked by user -1" when AI handled
        the alert before it cleared.

        Must be called BEFORE ``auto_resolve_alerts_by_vehicle`` —
        once that runs it overwrites ``acknowledged_at`` on all
        previously-un-acked rows, hiding the real human-ack time.
        """
        cur = await self._db.execute(
            "SELECT acknowledged_by, acknowledged_at FROM alert_acknowledgments "
            "WHERE account_id = ? AND alert_type = ? AND vehicle_id = ? "
            "AND acknowledged_by > 0 AND acknowledged_at IS NOT NULL "
            "ORDER BY acknowledged_at ASC LIMIT 1",
            (account_id, alert_type, vehicle_id),
        )
        row = await cur.fetchone()
        return dict(row) if row else None

    async def mark_alerts_seen(
        self, account_id: int, user_id: int, alert_ids: list[int],
    ) -> int:
        """Record that this person's screen actually showed these alerts.

        The INSERT..SELECT is the tenancy wall: ids are joined against
        this account's own ``alert_history`` rows, so a foreign or
        invented id inserts nothing rather than poisoning the ledger —
        the caller never gets to assert "alert 5 exists", only to view
        what its account owns.  ON CONFLICT DO NOTHING because first-seen
        wins: "this person has seen it" cannot become truer, and a busy
        board would otherwise rewrite the same fact on every scroll.
        """
        ids = [int(i) for i in alert_ids][:200]
        if not ids:
            return 0
        ph = ", ".join("?" for _ in ids)
        cur = await self._db.execute(
            f"INSERT INTO alert_seen (account_id, alert_history_id, user_id, seen_at) "
            f"SELECT account_id, id, ?, ? FROM alert_history "
            f" WHERE account_id = ? AND id IN ({ph}) "
            f"ON CONFLICT (account_id, alert_history_id, user_id) DO NOTHING",
            (int(user_id), self._now(), account_id, *ids),
        )
        await self._db.commit()
        return cur.rowcount or 0

    async def get_seen_for_alerts(
        self, account_id: int, alert_ids: list[int],
    ) -> dict[int, list[dict]]:
        """``alert_id -> [{user_id, name, seen_at}...]`` for one page.

        Names resolve through ``users`` at read time, the same way
        ``acknowledged_by_name`` does — a rename flows through without a
        backfill.  Ordered by seen_at so "Seen by AK, JD" reads in the
        order people actually saw it.
        """
        ids = [int(i) for i in alert_ids]
        if not ids:
            return {}
        ph = ", ".join("?" for _ in ids)
        cur = await self._db.execute(
            f"SELECT s.alert_history_id, s.user_id, s.seen_at, "
            f"       COALESCE(u.display_name, '') AS name "
            f"  FROM alert_seen s "
            f"  LEFT JOIN users u ON u.id = s.user_id "
            f" WHERE s.account_id = ? AND s.alert_history_id IN ({ph}) "
            f" ORDER BY s.seen_at",
            (account_id, *ids),
        )
        out: dict[int, list[dict]] = {}
        for r in await cur.fetchall():
            row = dict(r)
            out.setdefault(int(row["alert_history_id"]), []).append({
                "user_id": int(row["user_id"]),
                "name": row["name"] or "",
                "seen_at": row["seen_at"],
            })
        return out

    async def claim_alert(
        self, account_id: int, user_id: int, alert_id: int,
    ) -> bool:
        """One person claims one alert: "I'm working on this."

        Voluntary — nothing asks for it — and additive: a second claim
        by a second person joins the first rather than replacing it,
        because a big task takes several hands.  Same tenancy wall as
        the seen ledger: the id joins against this account's own
        alert_history, so a foreign id claims nothing.  Idempotent for
        the same person (first claim wins).
        """
        cur = await self._db.execute(
            "INSERT INTO alert_workers (account_id, alert_history_id, user_id, claimed_at) "
            "SELECT account_id, id, ?, ? FROM alert_history "
            " WHERE account_id = ? AND id = ? "
            "ON CONFLICT (account_id, alert_history_id, user_id) DO NOTHING",
            (int(user_id), self._now(), account_id, int(alert_id)),
        )
        await self._db.commit()
        return (cur.rowcount or 0) > 0

    async def get_workers_for_alerts(
        self, account_id: int, alert_ids: list[int],
    ) -> dict[int, list[dict]]:
        """``alert_id -> [{user_id, name, claimed_at}...]`` — who has
        hands on each alert, in the order they claimed it.  Same
        name-at-read-time join as the seen ledger and acknowledged_by:
        a rename flows through without a backfill."""
        ids = [int(i) for i in alert_ids]
        if not ids:
            return {}
        ph = ", ".join("?" for _ in ids)
        cur = await self._db.execute(
            f"SELECT w.alert_history_id, w.user_id, w.claimed_at, "
            f"       COALESCE(u.display_name, '') AS name "
            f"  FROM alert_workers w "
            f"  LEFT JOIN users u ON u.id = w.user_id "
            f" WHERE w.account_id = ? AND w.alert_history_id IN ({ph}) "
            f" ORDER BY w.claimed_at",
            (account_id, *ids),
        )
        out: dict[int, list[dict]] = {}
        for r in await cur.fetchall():
            row = dict(r)
            out.setdefault(int(row["alert_history_id"]), []).append({
                "user_id": int(row["user_id"]),
                "name": row["name"] or "",
                "claimed_at": row["claimed_at"],
            })
        return out

    async def list_grouped_alerts(
        self, account_id: int, *, days: int = 7,
        allowed_vehicle_names: list[str] | None = None,
        limit: int = 500,
    ) -> list[dict]:
        """The board as SITUATIONS rather than deliveries.

        Every fire writes its own ``alert_history`` row on purpose: the
        subkey carries a timestamp so each delivered message keeps a
        unique id, and a dispatcher quoting "Alert #13066" finds exactly
        that message. Nothing here changes that — the rows are untouched
        and every id stays valid. This only READS them differently.

        The cost of per-delivery rows is what an operator sees: 12,970
        rows for 1,015 real situations on the live account, one truck
        contributing 354 for a single kind of event. A queue nobody can
        finish stops being a queue, which is why 85% were never
        acknowledged — not carelessness, an unusable pile.

        THE GROUPING KEY is (alert_type, vehicle, first segment of
        last_detail). ``last_detail`` is already the subkey minus the
        timestamp, so no parsing is needed, and its first segment is the
        right grain — verified against real rows:

          fault    SPN520640:Manufacturer...  -> SPN520640, so two DTCs
                                                 on one truck stay apart
          events   followingDistance:2814     -> followingDistance, kept
                                                 apart from rollingStop
          fuel     fuel:17 / fuel:19          -> fuel, so one truck
                                                 running low is ONE
                                                 situation, not one per
                                                 reading
          health   coolant_dtc                -> coolant_dtc

        ``days`` bounds the COUNT, not the history. "×32 in 7d" is a
        number that means something operationally; "×912 ever" is a
        number people stop reading.
        """
        scope_sql, scope_args = "", ()
        if allowed_vehicle_names is not None:
            names = [n.strip().lower() for n in allowed_vehicle_names if n and n.strip()]
            if not names:
                return []                      # scoped to nothing — fail closed
            ph = ",".join("?" for _ in names)
            scope_sql = f" AND LOWER(vehicle_name) IN ({ph})"
            scope_args = tuple(names)
        cutoff = (datetime.now(timezone.utc) - timedelta(days=max(1, int(days)))).isoformat()
        cur = await self._db.execute(
            "SELECT alert_type, vehicle_id, "
            "       MAX(vehicle_name) AS vehicle_name, "
            "       split_part(last_detail, ':', 1) AS subtype, "
            "       COUNT(*) AS deliveries, "
            "       SUM(occurrence_count) AS occurrences, "
            "       MIN(first_seen) AS first_seen, "
            "       MAX(last_seen) AS last_seen, "
            "       MAX(id) AS newest_id, "
            "       COUNT(*) FILTER (WHERE acknowledged_at IS NULL "
            "                           OR acknowledged_at = '') AS unacked, "
            "       COUNT(*) FILTER (WHERE severity = 'critical') AS critical, "
            "       COUNT(*) FILTER (WHERE severity = 'warning') AS warning "
            "  FROM alert_history "
            " WHERE account_id = ? AND last_seen >= ?" + scope_sql +
            " GROUP BY alert_type, vehicle_id, split_part(last_detail, ':', 1) "
            " ORDER BY MAX(last_seen) DESC "
            " LIMIT ?",
            (account_id, cutoff, *scope_args, max(1, min(int(limit), 1000))),
        )
        out = []
        for r in await cur.fetchall():
            g = dict(r)
            # Severity is the WORST in the group, not the newest: a group
            # holding one critical is a critical row, however many
            # warnings arrived after it.
            g["severity"] = ("critical" if g.pop("critical", 0)
                             else "warning" if g.pop("warning", 0) else "info")
            g["group_key"] = (f"{g['alert_type']}|{g['vehicle_id']}|"
                              f"{g.get('subtype') or ''}")
            out.append(g)
        return out

    async def grouped_alert_member_ids(
        self, account_id: int, alert_type: str, vehicle_id: str,
        subtype: str, *, days: int = 7, only_unacked: bool = True,
    ) -> list[int]:
        """The alert ids behind one grouped row.

        Resolved SERVER-side from the group's identity rather than taken
        from the client, so acknowledging a group cannot be turned into
        "acknowledge these arbitrary ids" by editing a request. Bounded
        by the same window the count used, so a person acknowledges what
        the number in front of them actually said.
        """
        cutoff = (datetime.now(timezone.utc) - timedelta(days=max(1, int(days)))).isoformat()
        ack_sql = (" AND (acknowledged_at IS NULL OR acknowledged_at = '')"
                   if only_unacked else "")
        cur = await self._db.execute(
            "SELECT id FROM alert_history "
            " WHERE account_id = ? AND alert_type = ? AND vehicle_id = ? "
            "   AND split_part(last_detail, ':', 1) = ? AND last_seen >= ?"
            + ack_sql,
            (account_id, alert_type, vehicle_id, subtype, cutoff),
        )
        return [int(r[0]) for r in await cur.fetchall()]

    async def get_recent_alerts_for_resolution(
        self, account_id: int, alert_type: str, vehicle_id: str,
    ) -> list[dict]:
        """Return the latest in-flight delivery row per surface for a
        vehicle — used by the resolve path to thread the receipt as a
        reply to the ORIGINAL alert.

        "In-flight" means the alert hasn't been system-resolved yet:

          * ``status = 'active'`` — un-acked, still waiting
          * ``status = 'acknowledged' AND acknowledged_by != 0`` —
            a real user (or AI sentinel = -1) acked it, but the
            system hasn't auto-resolved (condition still present)

        Excluded:

          * ``status = 'superseded'`` — old, replaced by a newer alert
          * ``status = 'acknowledged' AND acknowledged_by = 0`` —
            the system already auto-resolved this delivery; including
            it would make a fresh resolve cycle re-fire a "RESOLVED"
            message for an alert that was cleared long ago.  This
            scoping caught the regression where one account got 1000+
            spurious "Health Cleared" messages because every healthy
            vehicle that had ANY past resolved health alert re-fired
            a receipt on each health-check tick.

        Per-``sent_to`` dedup picks the most recent row so the group
        post (sent_to=0) and each recipient DM get exactly one entry.
        """
        rows = await self.read_all(
            "SELECT * FROM alert_acknowledgments "
            "WHERE account_id = ? AND alert_type = ? AND vehicle_id = ? "
            "AND ("
            "    status = 'active' "
            "    OR (status = 'acknowledged' AND acknowledged_by != 0)"
            ") "
            "ORDER BY created_at DESC",
            (account_id, alert_type, vehicle_id),
        )
        seen: set = set()
        latest: list[dict] = []
        for r in rows:
            recipient = r["sent_to"]
            if recipient in seen:
                continue
            seen.add(recipient)
            latest.append(dict(r))
        return latest

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

    async def close_alerts_for_retired_vehicle(
        self, account_id: int, vehicle_id: str,
    ) -> int:
        """Close every open alert for a truck that has left the fleet.

        Archiving stops NEW alerts, but says nothing about the ones
        already on the board — and `critical_reescalate` re-notifies
        unacknowledged rows straight out of ``alert_history``, which
        never consults the registry.  So a fault raised last week went
        on paging people hourly about a truck that was archived
        yesterday, up to its retry cap.

        Every alert TYPE, unlike ``clear_alert_history``: the reason
        these close is the vehicle, not the condition.  ``'cleared'``
        is the same status a condition-cleared alert gets, so nothing
        downstream needs to learn a new word; WHY they closed is in the
        activity trail entry the archive writes.

        History is untouched — the rows stay, they stop being active.
        """
        if not vehicle_id:
            return 0
        cur = await self._db.execute(
            "UPDATE alert_history SET status = 'cleared' "
            "WHERE account_id = ? AND vehicle_id = ? AND status = 'active'",
            (account_id, vehicle_id),
        )
        closed = getattr(cur, "rowcount", 0) or 0
        # The acknowledgment side too, or the bell keeps a pending row
        # nobody can act on: its vehicle is gone from every list.
        await self._db.execute(
            "UPDATE alert_acknowledgments SET acknowledged_by = 0, "
            "acknowledged_at = ?, status = 'acknowledged' "
            "WHERE account_id = ? AND vehicle_id = ? "
            "AND acknowledged_at IS NULL AND status = 'active'",
            (self._now(), account_id, vehicle_id),
        )
        await self._db.commit()
        return closed

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

    @staticmethod
    def _order_by(sort_by: str | None, sort_dir: str) -> str:
        """ORDER BY for the board, chosen from an ALLOW-LIST.

        The column name arrives from a query string, so it is never
        interpolated — it is a KEY into the map below, and anything
        unrecognised falls back to the default ordering rather than
        reaching SQL.  ``sort_dir`` is likewise reduced to one of two
        literals.

        The default is severity-then-recency, which is triage order: a
        fresh critical outranks an old warning.  It stays the default
        precisely because it is the order an operator wants before they
        have expressed any preference.
        """
        columns = {
            "id": "h.id",
            # Rank, not the string — alphabetically 'critical' sorts
            # between 'info' and 'warning', which would bury the rows
            # that matter in the middle.
            "severity": "_sev_rank",
            "vehicle_name": "LOWER(h.vehicle_name)",
            "alert_type": "h.alert_type",
            "last_seen": "h.last_seen",
            "first_seen": "h.first_seen",
            "acknowledged_at": "h.acknowledged_at",
            "occurrence_count": "h.occurrence_count",
        }
        target = columns.get(sort_by or "")
        if target is None:
            # The id tie-break matters MOST here: this is the default view,
            # and a batch job that stamps last_seen for many rows in one
            # transaction leaves large groups tied on (severity, last_seen).
            # Without it those rows reshuffle between queries and paging
            # shows some twice and others never.
            return "ORDER BY _sev_rank ASC, h.last_seen DESC, h.id DESC "
        direction = "ASC" if str(sort_dir).lower() == "asc" else "DESC"
        # Tie-break on id so paging is STABLE: without it, rows sharing a
        # sort value can shuffle between pages and an operator paging
        # through sees one row twice and another never.
        return f"ORDER BY {target} {direction}, h.id DESC "

    @staticmethod
    def _alert_filter_clause(
        *,
        alert_type: str | None = None,
        severity: str | None = None,
        vehicle_substring: str | None = None,
        text_search: str | None = None,
        ack_state: str = "active",
        days: int | None = None,
        alias: str = "",
    ) -> tuple[str, list]:
        """Build the shared WHERE fragment used by the dashboard's
        paged / count / per-vehicle queries so they stay in lock-step.

        ``ack_state`` maps to the alert_history status:
            "active"        → status = 'active'   (not acknowledged)
            "acknowledged"  → status <> 'active'  (human ack or auto-resolve)
            "all"           → no status filter

        ``days`` windows on ``first_seen`` (when the alert *first*
        fired) so a 7d view shows alerts that started in the last 7
        days — a chronic alert that began 60 days ago doesn't leak
        into the recent window even if it keeps re-firing.

        ``alert_type`` and ``severity`` accept a COMMA-SEPARATED list and
        become an ``IN`` — the dashboard's column filters are multi-select,
        so "Fault or Health" has to be one query rather than two.  A single
        value behaves exactly as before.

        ``text_search`` matches vehicle name OR location.  One box covering
        both is what lets the board drop its separate vehicle-search
        control: location used to be searchable only within the rows already
        loaded, which quietly meant "some of your alerts".

        ``alias`` prefixes columns (e.g. "h") for joined queries; pass
        "" for single-table queries.  Returns ``(" AND ...", params)``
        with leading-AND so callers append it after their own first
        predicate; empty string when nothing applies.
        """
        p = f"{alias}." if alias else ""
        clauses: list[str] = []
        params: list = []
        if ack_state == "active":
            clauses.append(f"{p}status = 'active'")
        elif ack_state == "acknowledged":
            clauses.append(f"{p}status <> 'active'")
        # "all" → no status predicate
        # The date window bounds RESOLVED HISTORY, never open work.
        #
        # ``first_seen`` is stamped once and never bumped on a re-fire, so a
        # window applied to active rows hides the longest-running unresolved
        # problems — a fault still firing today drops off a 30-day board
        # simply because it started 40 days ago.  For a safety queue that is
        # backwards: an unacknowledged alert is open regardless of age.
        #
        # The rule is therefore per-ROW (by status), not per-QUERY (by
        # ack_state).  Applying it per-query made 'all' drop old open rows
        # that 'active' kept, so the board's tabs could read
        # "Not acknowledged 3,984 · All 3,295" — a total smaller than one
        # of its own parts, which is nonsense however true each number is
        # in isolation.  Now 'all' is exactly the union of the other two.
        # (Owner decision; the UI disables the date control while viewing
        # the open queue so it can't look like it applies there.)
        if days:
            from datetime import datetime, timedelta, timezone
            cutoff = (
                datetime.now(timezone.utc) - timedelta(days=int(days))
            ).isoformat()
            if ack_state == "acknowledged":
                clauses.append(f"{p}first_seen >= ?")
                params.append(cutoff)
            elif ack_state != "active":
                # 'all': window the resolved rows, keep every open one.
                clauses.append(f"({p}status = 'active' OR {p}first_seen >= ?)")
                params.append(cutoff)
        def _in_clause(column: str, raw: str) -> None:
            values = [v.strip() for v in raw.split(",") if v.strip()]
            if not values:
                # A value that was PROVIDED but reduces to nothing (",,,")
                # must narrow to empty, never fall through to no predicate:
                # dropping the clause here would widen the result to every
                # type, which is the opposite of what the caller asked for.
                clauses.append("1 = 0")
                return
            if len(values) == 1:
                clauses.append(f"{p}{column} = ?")
                params.append(values[0])
            else:
                placeholders = ", ".join("?" for _ in values)
                clauses.append(f"{p}{column} IN ({placeholders})")
                params.extend(values)

        if alert_type:
            _in_clause("alert_type", alert_type)
        if severity:
            _in_clause("severity", severity)
        if vehicle_substring:
            clauses.append(f"LOWER({p}vehicle_name) LIKE ?")
            params.append(f"%{vehicle_substring.lower()}%")
        if text_search:
            # Escape the LIKE metacharacters so a literal '%' or '_' in the
            # search box matches itself instead of "anything" — an operator
            # typing a percentage should not silently select the whole
            # queue.  Backslash is the escape, declared via ESCAPE.
            escaped = (
                text_search.lower()
                .replace("\\", "\\\\")
                .replace("%", "\\%")
                .replace("_", "\\_")
            )
            needle = f"%{escaped}%"
            clauses.append(
                f"(LOWER({p}vehicle_name) LIKE ? ESCAPE '\\' "
                f"OR LOWER(COALESCE({p}location, '')) LIKE ? ESCAPE '\\')"
            )
            params.extend([needle, needle])
        sql = (" AND " + " AND ".join(clauses)) if clauses else ""
        return sql, params

    async def get_active_alert_history_for_account_paged(
        self, account_id: int,
        alert_type: str | None = None,
        vehicle_substring: str | None = None,
        severity: str | None = None,
        text_search: str | None = None,
        ack_state: str = "active",
        days: int | None = None,
        sort_by: str | None = None,
        sort_dir: str = "desc",
        limit: int | None = None,
        offset: int = 0,
    ) -> list[dict]:
        """Paginated logical-alert list for the dashboard.

        Pushes optional alert_type / severity / vehicle / ack-state /
        date-window filters and LIMIT/OFFSET into SQL so the API layer
        never fetches the whole account just to slice one page.  After
        the per-subtype event dedup, fleets routinely accumulate 1000+
        rows, so server-side pagination is the difference between a
        snappy and a multi-second dashboard load.

        LEFT JOINs ``users`` to resolve ``acknowledged_by`` (a
        telegram_id) into ``acknowledged_by_name`` at read time — a
        rename flows through without a backfill.  A NULL name on a
        cleared row means it was auto-resolved by a check loop, which
        the UI labels "Auto-resolved" rather than attributing a human.

        ORDER BY uses an inline CASE on severity — with LIMIT applied,
        the engine can do a top-K sort instead of a full sort.
        """
        sql = (
            "SELECT h.*, "
            "  u.display_name AS acknowledged_by_name, "
            "  CASE h.severity "
            "    WHEN 'critical' THEN 0 "
            "    WHEN 'warning'  THEN 1 "
            "    WHEN 'info'     THEN 2 "
            "    ELSE 3 "
            "  END AS _sev_rank "
            "FROM alert_history h "
            "LEFT JOIN users u "
            "       ON u.telegram_id = h.acknowledged_by "
            "      AND u.account_id = h.account_id "
            "WHERE h.account_id = ? "
        )
        params: list = [account_id]
        frag, fp = self._alert_filter_clause(
            alert_type=alert_type, severity=severity,
            vehicle_substring=vehicle_substring, text_search=text_search,
            ack_state=ack_state, days=days, alias="h",
        )
        sql += frag + " "
        params += fp
        sql += self._order_by(sort_by, sort_dir)
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
        severity: str | None = None,
        text_search: str | None = None,
        ack_state: str = "active",
        days: int | None = None,
    ) -> int:
        """Filtered COUNT(*) matching the same WHERE as the paged query.

        Used by the API to compute the total page count without a
        second full fetch.  Mirrors every filter the paged query
        applies so the totals stay consistent with what's displayed.
        """
        sql = (
            "SELECT COUNT(*) AS c FROM alert_history "
            "WHERE account_id = ? "
        )
        params: list = [account_id]
        frag, fp = self._alert_filter_clause(
            alert_type=alert_type, severity=severity,
            vehicle_substring=vehicle_substring, text_search=text_search,
            ack_state=ack_state, days=days,
        )
        sql += frag
        params += fp
        cur = await self._db.execute(sql, tuple(params))
        row = await cur.fetchone()
        if row is None:
            return 0
        try:
            return int(row["c"])
        except (KeyError, TypeError):
            return int(row[0])

    async def get_active_vehicles_with_alerts_paged(
        self, account_id: int,
        *,
        alert_type: str | None = None,
        vehicle_substring: str | None = None,
        severity: str | None = None,
        ack_state: str = "active",
        days: int | None = None,
        limit: int = 50,
        offset: int = 0,
    ) -> tuple[list[dict], int]:
        """Return a page of *vehicles* (not alerts), each with its
        alerts embedded — backs the dashboard's per-vehicle view.

        Pagination counts what the user actually sees (vehicle cards)
        instead of underlying alert rows: ``Page 1 of 22`` for 2164
        alerts becomes ``Page 1 of 2`` for 80 vehicles.  All filters
        (type / severity / vehicle / ack-state / date-window) mirror
        the per-alert query via the shared ``_alert_filter_clause`` so
        the two views agree on what's in scope.

        Returns ``(vehicles, total_vehicle_count)``.  Each vehicle
        dict carries vehicle_id, vehicle_name, an embedded ``alerts``
        list (with ``acknowledged_by_name`` resolved), per-severity
        counts, and latest_seen.
        """
        # ── 1. Filter fragment shared by all three queries ──────────
        frag, fargs = self._alert_filter_clause(
            alert_type=alert_type, severity=severity,
            vehicle_substring=vehicle_substring,
            ack_state=ack_state, days=days,
        )
        where_sql = "account_id = ?" + frag
        args: list[Any] = [account_id, *fargs]

        # ── 2. Page of vehicles with their aggregates ───────────────
        sql_page = (
            f"SELECT vehicle_id, "
            f"       MAX(vehicle_name) AS vehicle_name, "
            f"       COUNT(*) AS alert_count, "
            f"       SUM(CASE WHEN severity = 'critical' THEN 1 ELSE 0 END) AS critical_count, "
            f"       SUM(CASE WHEN severity = 'warning'  THEN 1 ELSE 0 END) AS warning_count, "
            f"       SUM(CASE WHEN severity = 'info'     THEN 1 ELSE 0 END) AS info_count, "
            f"       MIN(CASE severity "
            f"               WHEN 'critical' THEN 0 "
            f"               WHEN 'warning'  THEN 1 "
            f"               WHEN 'info'     THEN 2 "
            f"               ELSE 3 END) AS sev_rank, "
            f"       MAX(last_seen) AS latest_seen "
            f"FROM alert_history "
            f"WHERE {where_sql} "
            f"GROUP BY vehicle_id "
            f"ORDER BY sev_rank ASC, latest_seen DESC "
            f"LIMIT ? OFFSET ?"
        )
        cur = await self._db.execute(sql_page, (*args, int(limit), int(offset)))
        vehicle_rows = [dict(r) for r in await cur.fetchall()]

        # ── 3. Count distinct vehicles for pagination total ─────────
        sql_count = (
            f"SELECT COUNT(*) AS c FROM ("
            f"  SELECT 1 FROM alert_history WHERE {where_sql} GROUP BY vehicle_id"
            f") AS sub"
        )
        cur = await self._db.execute(sql_count, tuple(args))
        row = await cur.fetchone()
        total = 0
        if row is not None:
            try:
                total = int(row["c"])
            except (KeyError, TypeError):
                total = int(row[0])

        # ── 4. Fetch alerts for the visible vehicles only ───────────
        if not vehicle_rows:
            return [], total
        vehicle_ids = [v["vehicle_id"] for v in vehicle_rows]
        placeholders = ",".join(["?"] * len(vehicle_ids))
        h_frag, h_fargs = self._alert_filter_clause(
            alert_type=alert_type, severity=severity,
            vehicle_substring=vehicle_substring,
            ack_state=ack_state, days=days, alias="h",
        )
        sql_alerts = (
            f"SELECT h.*, "
            f"  u.display_name AS acknowledged_by_name, "
            f"  CASE h.severity "
            f"    WHEN 'critical' THEN 0 "
            f"    WHEN 'warning'  THEN 1 "
            f"    WHEN 'info'     THEN 2 "
            f"    ELSE 3 "
            f"  END AS _sev_rank "
            f"FROM alert_history h "
            f"LEFT JOIN users u "
            f"       ON u.telegram_id = h.acknowledged_by "
            f"      AND u.account_id = h.account_id "
            f"WHERE h.account_id = ?{h_frag} "
            f"  AND h.vehicle_id IN ({placeholders}) "
            f"ORDER BY _sev_rank ASC, h.last_seen DESC"
        )
        cur = await self._db.execute(
            sql_alerts, (account_id, *h_fargs, *vehicle_ids),
        )
        alerts_rows = [dict(r) for r in await cur.fetchall()]

        by_vid: dict[str, list[dict]] = {}
        for a in alerts_rows:
            by_vid.setdefault(a["vehicle_id"], []).append(a)
        for v in vehicle_rows:
            v["alerts"] = by_vid.get(v["vehicle_id"], [])

        return vehicle_rows, total

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

    # ── Chronic-pattern suppression (Fix C) ────────────────────────

    async def mute_chronic_pattern(
        self,
        account_id: int,
        alert_type: str,
        vehicle_id: str,
        *,
        hours: int = 24 * 7,
        muted_by: int | None = None,
        reason: str = "",
    ) -> dict:
        """Mute every future fire of ``(alert_type, vehicle_id)`` for
        ``hours`` hours.  Used for chronic-known issues — operator
        already knows about the broken sensor, doesn't want repeats
        flooding the chat.

        UPSERT shape: re-muting the same pattern UPDATEs the existing
        row's ``muted_until`` rather than failing on the unique
        constraint.  Returns the resulting row.

        Native Postgres SQL with ``$N`` placeholders.
        """
        from datetime import datetime, timedelta, timezone
        now_iso = self._now()
        until_iso = (
            datetime.now(timezone.utc) + timedelta(hours=hours)
        ).isoformat()
        await self._db.execute(
            "INSERT INTO chronic_alert_suppressions "
            "(account_id, alert_type, vehicle_id, muted_by, muted_until, "
            " reason, created_at) "
            "VALUES ($1, $2, $3, $4, $5, $6, $7) "
            "ON CONFLICT (account_id, alert_type, vehicle_id) "
            "DO UPDATE SET muted_until = $5, "
            "              muted_by    = $4, "
            "              reason      = $6",
            (account_id, alert_type, vehicle_id, muted_by, until_iso,
             reason, now_iso),
        )
        await self._db.commit()
        return {
            "account_id": account_id,
            "alert_type":  alert_type,
            "vehicle_id":  vehicle_id,
            "muted_until": until_iso,
            "hours":       hours,
        }

    async def is_chronic_pattern_muted(
        self,
        account_id: int,
        alert_type: str,
        vehicle_id: str,
    ) -> bool:
        """Hot-path check called by ``send_alert`` before every fire.

        Returns True when a non-expired mute row exists for the
        ``(account, alert_type, vehicle_id)`` triple.  Backed by the
        partial index from migration 064 so the lookup is microseconds.
        """
        cur = await self._db.execute(
            "SELECT 1 FROM chronic_alert_suppressions "
            "WHERE account_id = $1 AND alert_type = $2 AND vehicle_id = $3 "
            "  AND muted_until > $4::text "
            "LIMIT 1",
            (account_id, alert_type, vehicle_id, self._now()),
        )
        return (await cur.fetchone()) is not None

    async def unmute_chronic_pattern(
        self,
        account_id: int,
        alert_type: str,
        vehicle_id: str,
    ) -> int:
        """Lift a chronic-pattern mute early.  Returns rows deleted (0/1)."""
        cur = await self._db.execute(
            "DELETE FROM chronic_alert_suppressions "
            "WHERE account_id = $1 AND alert_type = $2 AND vehicle_id = $3",
            (account_id, alert_type, vehicle_id),
        )
        await self._db.commit()
        return cur.rowcount or 0

    async def list_active_chronic_mutes(
        self, account_id: int, *, limit: int = 500,
    ) -> list[dict]:
        """Return active chronic-pattern mutes for an account — used by
        the dashboard to show a "muted patterns" badge so operators can
        see what's silenced and lift mutes if needed."""
        rows = await self.read_all(
            "SELECT * FROM chronic_alert_suppressions "
            "WHERE account_id = $1 AND muted_until > $2::text "
            "ORDER BY muted_until ASC LIMIT $3",
            (account_id, self._now(), int(limit)),
        )
        return [dict(r) for r in rows]

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
            # Two conditions beyond the originals, both owner decisions
            # (2026-08-30, from live data):
            #
            # CLAIMED alerts stop paging.  The pager's true job was
            # always finding an owner, and a claim IS an owner — the
            # "Working on" ledger replaced Acknowledge as the user verb,
            # so an alert someone has hands on needs no more reminders.
            # Legacy acks still stop it too, via acknowledged-status.
            #
            # CRITICAL only.  420 reminders went out for warnings and
            # not one was ever answered — warning-level paging was pure
            # Telegram noise.  The 30-day window held zero criticals in
            # the escalating types, so this net is dormant until the
            # night it matters, at zero cost meanwhile.
            "SELECT * FROM alert_history h "
            "WHERE h.account_id = ? AND h.status = 'active' "
            "AND h.severity = 'critical' "
            "AND h.reescalate_count < ? "
            "AND (h.reescalate_last_sent_at IS NULL OR h.reescalate_last_sent_at < ?) "
            "AND NOT EXISTS (SELECT 1 FROM alert_workers w "
            "                 WHERE w.account_id = h.account_id "
            "                   AND w.alert_history_id = h.id) "
            "ORDER BY h.first_seen ASC",
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
