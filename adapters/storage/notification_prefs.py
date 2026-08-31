"""Notification preference matrix — the multi-channel prefs store.

ONE row per rule (capabilities/notifications/docs/ARCHITECTURE.md §6): "recipient
wants CATEGORY on channel (+ cadence)".  Replaces the per-channel column
explosion the legacy ``users.alert_*`` booleans would have become once
Email/Push arrived.  The column shipped as ``alert_type`` and was renamed
to ``category`` by ``migrate_notification_category_rename``; a key is
``<source>.<key>`` and six source namespaces register today — ``alert``,
``system``, ``team``, ``applications``, ``ai``, ``kpi``.

This mixin is LIVE: the alert seam reads it for real delivery.  What is
still owed is the flip of the remaining senders — events, geofences and
scorecards continue to read the legacy ``users.alert_*`` columns through
``get_typed_alert_subscribers`` until ``NOTIFICATIONS_MATRIX_READER`` is
turned on by default.

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
        channel: str, category: str, *, enabled: bool,
        cadence: str = "immediate",
    ) -> None:
        """Upsert one rule (recipient × channel × category)."""
        if cadence not in _VALID_CADENCES:
            # Fail loudly at the write boundary: an unrecognised cadence
            # has no flush job, so it would silently swallow every
            # notification it matched.
            raise ValueError(f"unknown cadence {cadence!r}")
        now = self._now()
        await self._db.execute(
            """INSERT INTO notification_pref
                 (account_id, recipient_type, recipient_id, channel,
                  category, enabled, cadence, updated_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?)
               ON CONFLICT (account_id, recipient_type, recipient_id, channel, category)
               DO UPDATE SET enabled = ?, cadence = ?, updated_at = ?""",
            (account_id, recipient_type, str(recipient_id), channel, category,
             1 if enabled else 0, cadence, now,
             1 if enabled else 0, cadence, now),
        )
        await self._db.commit()

    async def set_channel_cadence(
        self, account_id: int, recipient_type: str, recipient_id: str,
        channel: str, cadence: str,
    ) -> int:
        """Set the delivery cadence for EVERY per-type rule on a channel at
        once — the UI models cadence as a channel-level choice (one 'send
        email as a daily digest' control), while the matrix stores it
        per-row.  Returns rows updated."""
        if cadence not in _VALID_CADENCES:
            raise ValueError(f"unknown cadence {cadence!r}")
        cur = await self._db.execute(
            """UPDATE notification_pref SET cadence = ?, updated_at = ?
                WHERE account_id = ? AND recipient_type = ?
                  AND recipient_id = ? AND channel = ?""",
            (cadence, self._now(), account_id, recipient_type,
             str(recipient_id), channel),
        )
        await self._db.commit()
        return cur.rowcount or 0

    async def list_recipient_notification_prefs(
        self, account_id: int, recipient_type: str, recipient_id: str,
    ) -> list[dict]:
        """Every rule for one recipient (all channels × categories)."""
        cur = await self._db.execute(
            """SELECT channel, category, enabled, cadence
                 FROM notification_pref
                WHERE account_id = ? AND recipient_type = ? AND recipient_id = ?
                ORDER BY channel, category""",
            (account_id, recipient_type, str(recipient_id)),
        )
        return [
            {"channel": r[0], "category": r[1],
             "enabled": bool(r[2]), "cadence": r[3]}
            for r in await cur.fetchall()
        ]

    async def get_notification_subscribers(
        self, account_id: int, category: str, channel: str,
    ) -> list[dict]:
        """Recipients who want ``category`` on ``channel`` — enabled at
        BOTH the per-category rule AND the channel master switch, and with
        a verified address.  Returns ``{recipient_type, recipient_id,
        address, cadence}``.  A ``'*'`` pref row (all categories) also
        matches.

        This returns the RAW matrix rows — it no longer applies any
        role/audience filter.  Category-relevance is a notification-domain
        concern (each source declares its category's audience), so the
        filter lives in ``dispatch()``, keeping this storage query free of
        any ``capabilities.alerting`` dependency.
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
                  AND p.category IN (?, '*')
                  AND p.enabled = 1
                  AND c.enabled_master = 1
                  AND c.verified_at <> ''
                  AND c.address <> ''""",
            (account_id, channel, category),
        )
        return [
            {"recipient_type": r[0], "recipient_id": r[1],
             "address": r[2], "cadence": r[3]}
            for r in await cur.fetchall()
        ]

    async def get_roles_for_users(self, user_ids: list[int]) -> dict[int, str]:
        """``{user_id: role}`` for the given ids — the audience filter in
        ``dispatch()`` uses it to drop broadcast recipients whose current
        role isn't eligible for a category."""
        ids = [int(i) for i in user_ids if str(i).lstrip("-").isdigit()]
        if not ids:
            return {}
        ph = ", ".join("?" for _ in ids)
        cur = await self._db.execute(
            f"SELECT id, role FROM users WHERE id IN ({ph})", tuple(ids))
        return {row[0]: row[1] for row in await cur.fetchall()}

    async def get_pref_categories(
        self, account_id: int, recipient_type: str, recipient_id: str,
        channel: str,
    ) -> dict[str, bool]:
        """``{category: enabled}`` for one recipient+channel — lets the
        targeted (opt-out) path see an explicit mute without loading the
        whole matrix."""
        cur = await self._db.execute(
            """SELECT category, enabled FROM notification_pref
                WHERE account_id = ? AND recipient_type = ?
                  AND recipient_id = ? AND channel = ?""",
            (account_id, recipient_type, str(recipient_id), channel),
        )
        return {row[0]: bool(row[1]) for row in await cur.fetchall()}

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

    async def verify_notification_channel(
        self, account_id: int, recipient_type: str, recipient_id: str,
        channel: str, address: str,
    ) -> bool:
        """Mark a channel connection verified — ONLY if the stored address
        still matches the one the verification link was issued for.  A user
        who changed their address after requesting verification must
        re-verify the new one (the stale link can't silently verify it)."""
        now = self._now()
        cur = await self._db.execute(
            """UPDATE notification_channel
                  SET verified_at = ?, updated_at = ?
                WHERE account_id = ? AND recipient_type = ?
                  AND recipient_id = ? AND channel = ? AND address = ?""",
            (now, now, account_id, recipient_type, str(recipient_id),
             channel, address),
        )
        await self._db.commit()
        return (cur.rowcount or 0) > 0

    async def disable_notification_channel(
        self, account_id: int, recipient_type: str, recipient_id: str,
        channel: str,
    ) -> bool:
        """Flip the channel master switch off (the one-click unsubscribe
        target).  Address + per-type prefs are kept, so re-enabling later
        doesn't lose the user's choices."""
        cur = await self._db.execute(
            """UPDATE notification_channel
                  SET enabled_master = 0, updated_at = ?
                WHERE account_id = ? AND recipient_type = ?
                  AND recipient_id = ? AND channel = ?""",
            (self._now(), account_id, recipient_type, str(recipient_id), channel),
        )
        await self._db.commit()
        return (cur.rowcount or 0) > 0

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
        channel: str, cadence: str, category: str,
        summary: str, address: str = "", *, severity: str = "info",
    ) -> None:
        """Buffer one notification for a batched cadence.  Stores RAW
        semantic fields (``summary`` is unescaped one-line text, plus
        ``severity``) so the flush renders per-channel exactly once —
        never a pre-rendered string."""
        await self._db.execute(
            """INSERT INTO notification_digest_queue
                 (account_id, recipient_type, recipient_id, channel,
                  cadence, category, summary, severity, address, created_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (account_id, recipient_type, str(recipient_id), channel,
             cadence, category, summary[:500], severity, address, self._now()),
        )
        await self._db.commit()

    async def fetch_due_digest_items(
        self, cadence: str, limit: int = 2000,
    ) -> list[dict]:
        """Queued items for a cadence, ordered so a consumer can group by
        (account, recipient, channel) in one pass."""
        cur = await self._db.execute(
            """SELECT id, account_id, recipient_type, recipient_id, channel,
                      category, summary, address, severity
                 FROM notification_digest_queue
                WHERE cadence = ?
                ORDER BY account_id, recipient_type, recipient_id, channel, id
                LIMIT ?""",
            (cadence, limit),
        )
        return [
            {"id": r[0], "account_id": r[1], "recipient_type": r[2],
             "recipient_id": r[3], "channel": r[4], "category": r[5],
             "summary": r[6], "address": r[7], "severity": r[8]}
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
