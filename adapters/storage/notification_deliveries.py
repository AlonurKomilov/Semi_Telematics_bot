"""Notification delivery ledger — "what did the spine send, where".

One row per successful delivery that a source may want to UPDATE later
(``update_delivery()``: "reminder 2/4", "✅ acked", "🟢 resolved").  The
``handle`` column is the channel's opaque edit-address (Telegram:
chat_id + message_id + kind), stored as JSON text; ``correlation_key``
is the source's stable name for the logical event across all its
deliveries (e.g. ``alert:{history_id}``), so one update fans out to
every recorded message.

Rows are only written when the caller passes a ``correlation_key`` —
fire-and-forget notices (invites, billing) keep the ledger empty.
Pruned by the retention hub (``notifications.deliveries``): the edit
window for reminders/acks is measured in days, not months.

Design note: this ledger is DELIVERY memory, not domain state — the
alerting side keeps its ack state machine; this table only remembers
message addresses (docs/architecture/alert-dm-migration.md).
"""

from __future__ import annotations

import json
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    class _MixinBase:
        _db: Any
        @staticmethod
        def _now() -> str: ...
else:
    _MixinBase = object


class NotificationDeliveriesMixin(_MixinBase):

    async def record_notification_delivery(
        self, account_id: int, *, channel: str, recipient_type: str,
        recipient_id: str, category: str, correlation_key: str,
        handle: dict,
    ) -> None:
        """Remember one delivered message's edit-address."""
        await self._db.execute(
            """INSERT INTO notification_deliveries
                   (account_id, channel, recipient_type, recipient_id,
                    category, correlation_key, handle, created_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
            (account_id, channel, recipient_type, str(recipient_id),
             category, correlation_key, json.dumps(handle or {}),
             self._now()),
        )
        await self._db.commit()

    async def get_notification_deliveries(
        self, account_id: int, correlation_key: str, *, channel: str = "",
    ) -> list[dict]:
        """Every recorded delivery for one logical event (oldest first —
        edits replay in send order).  ``handle`` comes back as a dict."""
        q = ("SELECT * FROM notification_deliveries "
             "WHERE account_id = ? AND correlation_key = ?")
        params: list = [account_id, correlation_key]
        if channel:
            q += " AND channel = ?"
            params.append(channel)
        q += " ORDER BY id ASC"
        cur = await self._db.execute(q, params)
        rows = [dict(r) for r in await cur.fetchall()]
        for r in rows:
            try:
                r["handle"] = json.loads(r.get("handle") or "{}")
            except (TypeError, ValueError):
                r["handle"] = {}
        return rows

    async def clear_notification_deliveries(
        self, account_id: int, correlation_key: str,
    ) -> int:
        """Drop a logical event's rows once the source is done editing it
        (e.g. the alert resolved and got its final edit)."""
        cur = await self._db.execute(
            "DELETE FROM notification_deliveries "
            "WHERE account_id = ? AND correlation_key = ?",
            (account_id, correlation_key),
        )
        await self._db.commit()
        return getattr(cur, "rowcount", 0) or 0

    async def prune_notification_deliveries(self, *, days: int) -> int:
        """Retention sweep — an edit-window measured in days.  ISO-text
        cutoff compares lexically, same as the inbox/digest sweeps."""
        from datetime import datetime, timedelta, timezone
        cutoff = (datetime.now(timezone.utc) - timedelta(days=days)).isoformat()
        cur = await self._db.execute(
            "DELETE FROM notification_deliveries WHERE created_at < ?",
            (cutoff,),
        )
        await self._db.commit()
        return cur.rowcount or 0
