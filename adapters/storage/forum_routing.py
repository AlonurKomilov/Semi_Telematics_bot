"""Forum-group + alert-routing CRUD.

Two-table layer behind the Telegram-group topic routing feature:

  • ``forum_groups``   — one row per account binding it to a Telegram
                         supergroup (with topics enabled).
  • ``alert_routing``  — N rows per account, one per alert type, each
                         pointing at a ``message_thread_id`` inside
                         the bound group.

The pipeline ``send_alert()`` consults ``get_alert_route()`` first;
when a row exists for ``(account_id, alert_type)`` it posts to the
group topic.  When no row exists the legacy per-user DM fanout runs
unchanged — that's the backward-compat guarantee for accounts that
haven't opted into forum routing.
"""

from __future__ import annotations

from typing import Optional

from .models import AlertRoute, ForumGroup


class ForumRoutingMixin:

    # ── forum_groups ─────────────────────────────────────────────

    async def upsert_forum_group(
        self,
        *,
        account_id: int,
        chat_id: int,
        chat_title: str,
        is_forum_enabled: bool,
        created_by_user_id: int,
        setup_status: str = "pending",
    ) -> ForumGroup:
        """Bind a Telegram supergroup to this account, or replace the
        existing binding.  Returns the persisted row.
        """
        now = self._now()
        await self._db.execute(
            """INSERT INTO forum_groups
                 (account_id, chat_id, chat_title, is_forum_enabled,
                  setup_status, created_by_user_id, created_at, updated_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?)
               ON CONFLICT(account_id) DO UPDATE SET
                 chat_id           = excluded.chat_id,
                 chat_title        = excluded.chat_title,
                 is_forum_enabled  = excluded.is_forum_enabled,
                 setup_status      = excluded.setup_status,
                 updated_at        = excluded.updated_at""",
            (account_id, chat_id, chat_title, 1 if is_forum_enabled else 0,
             setup_status, created_by_user_id, now, now),
        )
        await self._db.commit()
        row = await self._fetch_forum_group(account_id)
        assert row is not None  # just upserted
        return row

    async def get_forum_group(self, account_id: int) -> Optional[ForumGroup]:
        return await self._fetch_forum_group(account_id)

    async def get_forum_group_by_chat(self, chat_id: int) -> Optional[ForumGroup]:
        cur = await self._db.execute(
            "SELECT * FROM forum_groups WHERE chat_id = ? LIMIT 1",
            (chat_id,),
        )
        row = await cur.fetchone()
        return self._row_to_forum_group(row) if row else None

    async def mark_forum_group_status(
        self,
        account_id: int,
        status: str,
        *,
        bump_setup: bool = False,
        bump_repair: bool = False,
    ) -> None:
        """Persist the latest setup_status and optionally a setup/repair
        timestamp.  ``bump_setup`` is set by ``/setupforum``;
        ``bump_repair`` by ``/repairforum``.
        """
        now = self._now()
        sets = ["setup_status = ?", "updated_at = ?"]
        args: list = [status, now]
        if bump_setup:
            sets.append("last_setup_at = ?")
            args.append(now)
        if bump_repair:
            sets.append("last_repair_at = ?")
            args.append(now)
        args.append(account_id)
        await self._db.execute(
            f"UPDATE forum_groups SET {', '.join(sets)} WHERE account_id = ?",
            tuple(args),
        )
        await self._db.commit()

    async def delete_forum_group(self, account_id: int) -> None:
        """Remove an account's forum binding AND every alert_routing
        row that pointed at it.  Used by ``/resetforum`` and the
        admin-UI "Disconnect group" action.
        """
        await self._db.execute(
            "DELETE FROM alert_routing WHERE account_id = ?",
            (account_id,),
        )
        await self._db.execute(
            "DELETE FROM forum_groups WHERE account_id = ?",
            (account_id,),
        )
        await self._db.commit()

    async def _fetch_forum_group(self, account_id: int) -> Optional[ForumGroup]:
        cur = await self._db.execute(
            "SELECT * FROM forum_groups WHERE account_id = ?",
            (account_id,),
        )
        row = await cur.fetchone()
        return self._row_to_forum_group(row) if row else None

    def _row_to_forum_group(self, row) -> ForumGroup:
        return ForumGroup(
            account_id=row["account_id"],
            chat_id=row["chat_id"],
            chat_title=row["chat_title"],
            is_forum_enabled=bool(row["is_forum_enabled"]),
            setup_status=row["setup_status"],
            last_setup_at=row["last_setup_at"],
            last_repair_at=row["last_repair_at"],
            created_by_user_id=row["created_by_user_id"],
            created_at=row["created_at"],
            updated_at=row["updated_at"],
        )

    # ── alert_routing ────────────────────────────────────────────

    async def upsert_alert_route(
        self,
        *,
        account_id: int,
        alert_type: str,
        chat_id: int,
        message_thread_id: int,
        topic_name_snapshot: str = "",
        icon_emoji: str = "",
    ) -> AlertRoute:
        """Insert or update the route for a single alert type.

        Re-running with the same ``(account_id, alert_type)`` overwrites
        the message_thread_id — this is what ``/repairforum`` relies on
        when an admin deleted and re-created a topic.
        """
        now = self._now()
        await self._db.execute(
            """INSERT INTO alert_routing
                 (account_id, alert_type, chat_id, message_thread_id,
                  topic_name_snapshot, icon_emoji,
                  is_active, created_at, updated_at)
               VALUES (?, ?, ?, ?, ?, ?, 1, ?, ?)
               ON CONFLICT(account_id, alert_type) DO UPDATE SET
                 chat_id              = excluded.chat_id,
                 message_thread_id    = excluded.message_thread_id,
                 topic_name_snapshot  = excluded.topic_name_snapshot,
                 icon_emoji           = excluded.icon_emoji,
                 is_active            = 1,
                 updated_at           = excluded.updated_at""",
            (account_id, alert_type, chat_id, message_thread_id,
             topic_name_snapshot, icon_emoji, now, now),
        )
        await self._db.commit()
        route = await self.get_alert_route(account_id, alert_type)
        assert route is not None
        return route

    async def get_alert_route(
        self, account_id: int, alert_type: str,
    ) -> Optional[AlertRoute]:
        """Lookup the route for one alert type.

        Returns None if either no row exists OR the row is soft-disabled
        (``is_active=0``) — both states mean "fall back to DM" from the
        pipeline's perspective.
        """
        cur = await self._db.execute(
            """SELECT * FROM alert_routing
               WHERE account_id = ? AND alert_type = ? AND is_active = 1
               LIMIT 1""",
            (account_id, alert_type),
        )
        row = await cur.fetchone()
        return self._row_to_alert_route(row) if row else None

    async def list_alert_routes(self, account_id: int) -> list[AlertRoute]:
        """Return every route (active OR inactive) for an account.

        The admin UI needs the inactive ones so users can re-enable
        without re-running setup.
        """
        cur = await self._db.execute(
            """SELECT * FROM alert_routing
               WHERE account_id = ?
               ORDER BY alert_type""",
            (account_id,),
        )
        rows = await cur.fetchall()
        return [self._row_to_alert_route(r) for r in rows]

    async def set_alert_route_active(
        self, account_id: int, alert_type: str, is_active: bool,
    ) -> None:
        """Soft-toggle a single route.  When inactive the pipeline
        falls back to DM for *just that* alert type — useful when a
        manager wants Maintenance via DM but everything else in the
        group.
        """
        now = self._now()
        await self._db.execute(
            """UPDATE alert_routing
               SET is_active = ?, updated_at = ?
               WHERE account_id = ? AND alert_type = ?""",
            (1 if is_active else 0, now, account_id, alert_type),
        )
        await self._db.commit()

    async def delete_alert_route(self, account_id: int, alert_type: str) -> None:
        await self._db.execute(
            "DELETE FROM alert_routing WHERE account_id = ? AND alert_type = ?",
            (account_id, alert_type),
        )
        await self._db.commit()

    def _row_to_alert_route(self, row) -> AlertRoute:
        # ``send_resolve_receipt`` column was added in migration 079.
        # Use dict-style ``.keys()`` check so this method keeps working
        # against a pre-migration sqlite row (treat missing as True =
        # legacy "always send receipt" behavior).
        keys = row.keys() if hasattr(row, "keys") else []
        send_receipt = bool(row["send_resolve_receipt"]) \
            if "send_resolve_receipt" in keys else True
        return AlertRoute(
            id=row["id"],
            account_id=row["account_id"],
            alert_type=row["alert_type"],
            chat_id=row["chat_id"],
            message_thread_id=row["message_thread_id"],
            topic_name_snapshot=row["topic_name_snapshot"],
            icon_emoji=row["icon_emoji"],
            is_active=bool(row["is_active"]),
            send_resolve_receipt=send_receipt,
            created_at=row["created_at"],
            updated_at=row["updated_at"],
        )

    async def set_alert_route_send_resolve_receipt(
        self, account_id: int, alert_type: str, enabled: bool,
    ) -> bool:
        """Toggle the per-topic resolve-receipt flag.

        The auto-resolve pipeline consults this before posting a
        🟢 RESOLVED receipt to the forum topic — when False, the
        underlying alert_history row still flips to resolved (so the
        dashboard monitoring view stays accurate) but no chat message
        gets posted.
        """
        now = self._now()
        cur = await self._db.execute(
            "UPDATE alert_routing "
            "SET send_resolve_receipt = ?, updated_at = ? "
            "WHERE account_id = ? AND alert_type = ?",
            (1 if enabled else 0, now, account_id, alert_type),
        )
        await self._db.commit()
        return (cur.rowcount or 0) > 0

    # ── forum_topics_seen (duplicate-prevention index) ───────────

    async def record_forum_topic_seen(
        self, chat_id: int, message_thread_id: int, name: str,
    ) -> None:
        """Upsert a (chat_id, thread_id, name) row into the seen-index.

        Called from the service-message handler whenever Telegram
        notifies the bot of a ``forum_topic_created`` /
        ``forum_topic_edited`` event in any forum chat.  Also called
        directly after a successful ``create_topic`` / ``edit_topic``
        in case the bot's own service message is filtered out by
        Telegram for the actor.
        """
        now = self._now()
        await self._db.execute(
            """INSERT INTO forum_topics_seen
                 (chat_id, message_thread_id, name, last_seen_at)
               VALUES (?, ?, ?, ?)
               ON CONFLICT(chat_id, message_thread_id) DO UPDATE SET
                 name         = excluded.name,
                 last_seen_at = excluded.last_seen_at""",
            (chat_id, message_thread_id, name, now),
        )
        await self._db.commit()

    async def find_forum_topic_by_name(
        self, chat_id: int, name: str,
    ) -> Optional[int]:
        """Return the ``message_thread_id`` of a known topic in
        ``chat_id`` whose name matches ``name`` (case-insensitive),
        or None if no match.  Used by ``/setupforum`` and
        ``/repairforum`` to adopt an existing topic instead of
        creating a duplicate.
        """
        cur = await self._db.execute(
            """SELECT message_thread_id FROM forum_topics_seen
               WHERE chat_id = ? AND LOWER(name) = LOWER(?)
               ORDER BY last_seen_at DESC
               LIMIT 1""",
            (chat_id, name),
        )
        row = await cur.fetchone()
        if row is None:
            return None
        # asyncpg-style row supports both index and column access
        return row[0] if not hasattr(row, "keys") else row["message_thread_id"]

    async def forget_forum_topic(
        self, chat_id: int, message_thread_id: int,
    ) -> None:
        """Remove a row from the seen-index — used when the bot
        observes a ``forum_topic_deleted`` event, so the next
        ``/repairforum`` doesn't try to adopt a deleted topic.
        """
        await self._db.execute(
            "DELETE FROM forum_topics_seen WHERE chat_id = ? AND message_thread_id = ?",
            (chat_id, message_thread_id),
        )
        await self._db.commit()
