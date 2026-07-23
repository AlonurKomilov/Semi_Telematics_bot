"""In-app notification inbox — the persisted record behind the bell.

One row per (user × notice).  Rows are written by the intrinsic
``in_app`` channel's ``send()`` (capabilities/notifications/inapp.py), so
every row has already passed the same audience / scoping / mute gates as a
real transport delivery.  Alerts are NOT stored here — the bell reads
those from ``alert_history`` (dual-source by design; an inbox row can't
honestly mirror ack/occurrence semantics).

Read model: ``read_at`` is per row ('' = unread).  The feed reads newest-
first via ``idx_notification_inbox_feed``; the unread badge counts via the
partial ``idx_notification_inbox_unread``.
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


class NotificationInboxMixin(_MixinBase):

    async def add_inbox_notice(
        self, account_id: int, user_id: int, *,
        category: str, title: str, body: str = "", severity: str = "info",
        url: str = "", meta: str = "",
    ) -> None:
        """Insert one inbox row.  ``source`` is derived from the category
        namespace so the feed can group/filter without re-parsing."""
        source = category.split(".", 1)[0] if "." in category else ""
        await self._db.execute(
            """INSERT INTO notification_inbox
                   (account_id, user_id, category, source, severity,
                    title, body, url, meta, created_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (account_id, user_id, category, source, severity,
             title, body, url, meta, self._now()),
        )
        await self._db.commit()

    async def list_inbox_notices(
        self, account_id: int, user_id: int, *,
        source: str = "", limit: int = 30, before_id: int | None = None,
    ) -> list[dict]:
        """Newest-first page of a user's notices.  ``source`` filters to one
        namespace tab; ``before_id`` is keyset pagination (ids are
        monotonic, so this stays index-backed at any depth)."""
        q = ("SELECT * FROM notification_inbox "
             "WHERE account_id = ? AND user_id = ?")
        params: list = [account_id, user_id]
        if source:
            q += " AND source = ?"
            params.append(source)
        if before_id is not None:
            q += " AND id < ?"
            params.append(before_id)
        q += " ORDER BY id DESC LIMIT ?"
        params.append(max(1, min(int(limit), 100)))
        cur = await self._db.execute(q, params)
        return [dict(r) for r in await cur.fetchall()]

    async def count_inbox_unread(self, account_id: int, user_id: int) -> int:
        cur = await self._db.execute(
            "SELECT COUNT(*) FROM notification_inbox "
            "WHERE account_id = ? AND user_id = ? AND read_at = ''",
            (account_id, user_id),
        )
        row = await cur.fetchone()
        return int(row[0]) if row else 0

    async def mark_inbox_read(
        self, account_id: int, user_id: int, notice_ids: list[int],
    ) -> int:
        """Mark specific notices read.  Scoped to the caller's own rows —
        a foreign id silently matches nothing."""
        ids = [int(i) for i in notice_ids][:200]
        if not ids:
            return 0
        ph = ", ".join("?" for _ in ids)
        cur = await self._db.execute(
            f"UPDATE notification_inbox SET read_at = ? "
            f"WHERE account_id = ? AND user_id = ? AND read_at = '' "
            f"AND id IN ({ph})",
            (self._now(), account_id, user_id, *ids),
        )
        await self._db.commit()
        return cur.rowcount or 0

    async def mark_inbox_all_read(self, account_id: int, user_id: int) -> int:
        cur = await self._db.execute(
            "UPDATE notification_inbox SET read_at = ? "
            "WHERE account_id = ? AND user_id = ? AND read_at = ''",
            (self._now(), account_id, user_id),
        )
        await self._db.commit()
        return cur.rowcount or 0

    async def get_optout_subscribers(
        self, account_id: int, category: str, channel: str,
    ) -> list[dict]:
        """Broadcast recipients for an OPT-OUT channel: every active user of
        the account EXCEPT those whose prefs mute it — with the specific
        row beating the ``'*'`` blanket in both directions (same precedence
        as ``notify_user``).  The intrinsic ``in_app`` channel uses this
        instead of the opt-in matrix query: an inbox is a passive record,
        so requiring opt-in would leave it empty forever.

        Returns the same row shape as ``get_notification_subscribers``
        (recipient_type/recipient_id/address/cadence) so ``dispatch()``'s
        audience + recipient_filter pass applies unchanged."""
        cur = await self._db.execute(
            """SELECT u.id AS user_id,
                      spec.enabled AS spec_enabled,
                      star.enabled AS star_enabled
                 FROM users u
                 LEFT JOIN notification_pref spec
                        ON spec.account_id = u.account_id
                       AND spec.recipient_type = 'user'
                       AND spec.recipient_id = CAST(u.id AS TEXT)
                       AND spec.channel = ?
                       AND spec.category = ?
                 LEFT JOIN notification_pref star
                        ON star.account_id = u.account_id
                       AND star.recipient_type = 'user'
                       AND star.recipient_id = CAST(u.id AS TEXT)
                       AND star.channel = ?
                       AND star.category = '*'
                WHERE u.account_id = ? AND u.is_active = 1""",
            (channel, category, channel, account_id),
        )
        out = []
        for r in await cur.fetchall():
            row = dict(r)
            spec = row["spec_enabled"]          # None | 0/1
            star = row["star_enabled"]
            if spec is not None:
                if not spec:
                    continue                    # explicit mute of this category
            elif star is not None and not star:
                continue                        # blanket mute, no override
            out.append({
                "recipient_type": "user",
                "recipient_id": str(row["user_id"]),
                "address": "",
                "cadence": "immediate",
            })
        return out

    async def prune_notification_inbox(self, *, days: int) -> int:
        """Retention sweep: drop notices older than ``days`` regardless of
        read state (the inbox is a recent-activity glance, not an
        archive).  ISO-text cutoff compares lexically, same as the digest
        sweep."""
        from datetime import datetime, timedelta, timezone
        cutoff = (datetime.now(timezone.utc) - timedelta(days=days)).isoformat()
        cur = await self._db.execute(
            "DELETE FROM notification_inbox WHERE created_at < ?",
            (cutoff,),
        )
        await self._db.commit()
        return cur.rowcount or 0
