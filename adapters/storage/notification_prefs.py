"""Notification preference matrix — the multi-channel prefs store.

ONE row per rule (docs/architecture/notifications.md §6): "recipient
wants alert_type on channel (+ cadence)".  Replaces the per-channel
column explosion the legacy ``users.alert_*`` booleans would have become
once Email/SMS/Push arrive.

Phase 2a: this mixin + the tables exist and are backfilled, but NOTHING
reads them for live delivery yet — the reader/writer switch
(``get_typed_alert_subscribers`` + the My-Notifications API) is the
separate, tested 2b step.  So this layer is additive and cannot change
current delivery.

Scope note: personal (``recipient_type='user'``) rows are the phase-2a
focus (the bot→user DM prefs).  The shared side (per-role Telegram
groups) keeps its existing routing (``resolve_alert_targets``) and is
wrapped by the ``telegram_topic`` channel — not migrated here.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    class _MixinBase:
        _db: Any
        @staticmethod
        def _now() -> str: ...
else:
    _MixinBase = object


# Mirror of capabilities.notifications.service.{IMMEDIATE,DIGEST_CADENCES}
# — duplicated on purpose so adapters/ never imports capabilities/.  A
# test pins the two lists together so they can't drift.
_VALID_CADENCES = ("immediate", "hourly", "daily")


class NotificationPrefsMixin(_MixinBase):

    # ── Per-type preferences ─────────────────────────────────────────

    async def set_notification_pref(
        self, account_id: int, recipient_type: str, recipient_id: str,
        channel: str, alert_type: str, *, enabled: bool,
        cadence: str = "immediate",
    ) -> None:
        """Upsert one rule (recipient × channel × alert_type)."""
        if cadence not in _VALID_CADENCES:
            # Fail loudly at the write boundary: an unrecognised cadence
            # has no flush job, so it would silently swallow every
            # notification it matched.
            raise ValueError(f"unknown cadence {cadence!r}")
        now = self._now()
        await self._db.execute(
            """INSERT INTO notification_pref
                 (account_id, recipient_type, recipient_id, channel,
                  alert_type, enabled, cadence, updated_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?)
               ON CONFLICT (account_id, recipient_type, recipient_id, channel, alert_type)
               DO UPDATE SET enabled = ?, cadence = ?, updated_at = ?""",
            (account_id, recipient_type, str(recipient_id), channel, alert_type,
             1 if enabled else 0, cadence, now,
             1 if enabled else 0, cadence, now),
        )
        await self._db.commit()

    async def list_recipient_notification_prefs(
        self, account_id: int, recipient_type: str, recipient_id: str,
    ) -> list[dict]:
        """Every rule for one recipient (all channels × types)."""
        cur = await self._db.execute(
            """SELECT channel, alert_type, enabled, cadence
                 FROM notification_pref
                WHERE account_id = ? AND recipient_type = ? AND recipient_id = ?
                ORDER BY channel, alert_type""",
            (account_id, recipient_type, str(recipient_id)),
        )
        return [
            {"channel": r[0], "alert_type": r[1],
             "enabled": bool(r[2]), "cadence": r[3]}
            for r in await cur.fetchall()
        ]

    async def get_notification_subscribers(
        self, account_id: int, alert_type: str, channel: str,
    ) -> list[dict]:
        """Recipients who want ``alert_type`` on ``channel`` — enabled at
        BOTH the per-type rule AND the channel master switch, and with a
        verified address.  Returns ``{recipient_type, recipient_id,
        address, cadence}``.

        A ``'*'`` pref row (all types) also matches.  This is the query
        the dispatch fan-out will use once 2b wires it in.
        """
        cur = await self._db.execute(
            """SELECT p.recipient_type, p.recipient_id, c.address, p.cadence
                 FROM notification_pref p
                 JOIN notification_channel c
                   ON  c.account_id = p.account_id
                   AND c.recipient_type = p.recipient_type
                   AND c.recipient_id = p.recipient_id
                   AND c.channel = p.channel
                WHERE p.account_id = ? AND p.channel = ?
                  AND p.alert_type IN (?, '*')
                  AND p.enabled = 1
                  AND c.enabled_master = 1
                  AND c.verified_at <> ''
                  AND c.address <> ''""",
            (account_id, channel, alert_type),
        )
        return [
            {"recipient_type": r[0], "recipient_id": r[1],
             "address": r[2], "cadence": r[3]}
            for r in await cur.fetchall()
        ]

    # ── Channel connection ───────────────────────────────────────────

    async def upsert_notification_channel(
        self, account_id: int, recipient_type: str, recipient_id: str,
        channel: str, *, address: str = "", verified: bool = False,
        enabled_master: bool = True,
    ) -> None:
        """Set a recipient's connection for a channel (address + verified
        + master switch)."""
        now = self._now()
        verified_at = now if verified else ""
        await self._db.execute(
            """INSERT INTO notification_channel
                 (account_id, recipient_type, recipient_id, channel,
                  address, verified_at, enabled_master, updated_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?)
               ON CONFLICT (account_id, recipient_type, recipient_id, channel)
               DO UPDATE SET address = ?, verified_at = ?, enabled_master = ?, updated_at = ?""",
            (account_id, recipient_type, str(recipient_id), channel,
             address, verified_at, 1 if enabled_master else 0, now,
             address, verified_at, 1 if enabled_master else 0, now),
        )
        await self._db.commit()

    async def get_notification_channel(
        self, account_id: int, recipient_type: str, recipient_id: str,
        channel: str,
    ) -> dict | None:
        cur = await self._db.execute(
            """SELECT address, verified_at, enabled_master
                 FROM notification_channel
                WHERE account_id = ? AND recipient_type = ?
                  AND recipient_id = ? AND channel = ?""",
            (account_id, recipient_type, str(recipient_id), channel),
        )
        r = await cur.fetchone()
        if r is None:
            return None
        return {"address": r[0], "verified": bool(r[1]),
                "verified_at": r[1], "enabled_master": bool(r[2])}

    # ── Digest queue (batched cadences) ──────────────────────────────

    async def enqueue_digest_item(
        self, account_id: int, recipient_type: str, recipient_id: str,
        channel: str, cadence: str, alert_type: str,
        summary: str, address: str = "",
    ) -> None:
        """Buffer one notification for a batched cadence.  Flushed as part
        of a single summary by :meth:`fetch_due_digest_items`' consumer."""
        await self._db.execute(
            """INSERT INTO notification_digest_queue
                 (account_id, recipient_type, recipient_id, channel,
                  cadence, alert_type, summary, address, created_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (account_id, recipient_type, str(recipient_id), channel,
             cadence, alert_type, summary[:500], address, self._now()),
        )
        await self._db.commit()

    async def fetch_due_digest_items(
        self, cadence: str, limit: int = 2000,
    ) -> list[dict]:
        """Queued items for a cadence, ordered so a consumer can group by
        (account, recipient, channel) in one pass."""
        cur = await self._db.execute(
            """SELECT id, account_id, recipient_type, recipient_id, channel,
                      alert_type, summary, address
                 FROM notification_digest_queue
                WHERE cadence = ?
                ORDER BY account_id, recipient_type, recipient_id, channel, id
                LIMIT ?""",
            (cadence, limit),
        )
        return [
            {"id": r[0], "account_id": r[1], "recipient_type": r[2],
             "recipient_id": r[3], "channel": r[4], "alert_type": r[5],
             "summary": r[6], "address": r[7]}
            for r in await cur.fetchall()
        ]

    async def clear_digest_items(
        self, ids: list[int], *, account_id: int | None = None,
    ) -> int:
        """Delete flushed items.  Only called AFTER a successful send, so a
        failed flush leaves the buffer intact for the next run.

        ``account_id`` scopes the DELETE — belt-and-suspenders so a future
        caller can't delete another tenant's rows by passing mixed ids."""
        ids = [int(i) for i in ids]
        if not ids:
            return 0
        placeholders = ", ".join("?" for _ in ids)
        sql = f"DELETE FROM notification_digest_queue WHERE id IN ({placeholders})"
        params: tuple = tuple(ids)
        if account_id is not None:
            sql += " AND account_id = ?"
            params = (*ids, account_id)
        cur = await self._db.execute(sql, params)
        await self._db.commit()
        return cur.rowcount or 0

    async def prune_notification_digest_queue(self, days: int) -> int:
        """Retention sweep for the digest buffer.  Successful flushes clear
        their own items, so anything older than the window is residue from
        a channel that vanished or a cadence nothing drains — delete it so
        a silent backlog can't grow without bound."""
        from datetime import datetime, timedelta, timezone
        cutoff = (datetime.now(timezone.utc) - timedelta(days=days)).isoformat()
        cur = await self._db.execute(
            "DELETE FROM notification_digest_queue WHERE created_at < ?",
            (cutoff,),
        )
        await self._db.commit()
        return cur.rowcount or 0
