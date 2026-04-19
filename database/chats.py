"""Authorized chats CRUD mixin."""

from __future__ import annotations

from typing import Optional

from .models import AuthorizedChat


class ChatsMixin:

    async def add_authorized_chat(
        self, account_id: int, chat_id: int, chat_title: str, added_by: int,
    ) -> AuthorizedChat:
        """Authorize a group/channel for this account."""
        now = self._now()
        cur = await self._db.execute(
            """INSERT INTO authorized_chats
               (account_id, chat_id, chat_title, added_by, created_at)
               VALUES (?, ?, ?, ?, ?)
               ON CONFLICT(account_id, chat_id) DO UPDATE
               SET is_active = 1, chat_title = excluded.chat_title""",
            (account_id, chat_id, chat_title, added_by, now),
        )
        await self._db.commit()
        return AuthorizedChat(
            id=cur.lastrowid, account_id=account_id, chat_id=chat_id,
            chat_title=chat_title, added_by=added_by,
            is_active=True, created_at=now,
        )

    async def remove_authorized_chat(self, account_id: int, chat_id: int) -> bool:
        """Deauthorize a group/channel."""
        await self._db.execute(
            "UPDATE authorized_chats SET is_active = 0 WHERE account_id = ? AND chat_id = ?",
            (account_id, chat_id),
        )
        await self._db.commit()
        return True

    async def get_authorized_chats(self, account_id: int) -> list[AuthorizedChat]:
        """List all authorized chats for an account."""
        cur = await self._db.execute(
            "SELECT * FROM authorized_chats WHERE account_id = ? AND is_active = 1 ORDER BY chat_title",
            (account_id,),
        )
        rows = await cur.fetchall()
        return [self._row_to_authorized_chat(r) for r in rows]

    async def is_chat_authorized(self, chat_id: int, account_id: int = 0) -> bool:
        """Check if a group/channel is authorized.

        If account_id is provided, checks authorization for that specific
        account (multi-tenant scoping).  Otherwise checks any active account.
        """
        if account_id:
            cur = await self._db.execute(
                """SELECT 1 FROM authorized_chats ac
                   JOIN accounts a ON a.id = ac.account_id
                   WHERE ac.chat_id = ? AND ac.account_id = ?
                     AND ac.is_active = 1 AND a.is_active = 1
                   LIMIT 1""",
                (chat_id, account_id),
            )
        else:
            cur = await self._db.execute(
                """SELECT 1 FROM authorized_chats ac
                   JOIN accounts a ON a.id = ac.account_id
                   WHERE ac.chat_id = ? AND ac.is_active = 1 AND a.is_active = 1
                   LIMIT 1""",
                (chat_id,),
            )
        row = await cur.fetchone()
        return row is not None

    async def get_chat_account_id(self, chat_id: int) -> Optional[int]:
        """Get the account_id that owns an authorized chat."""
        cur = await self._db.execute(
            """SELECT ac.account_id FROM authorized_chats ac
               JOIN accounts a ON a.id = ac.account_id
               WHERE ac.chat_id = ? AND ac.is_active = 1 AND a.is_active = 1
               LIMIT 1""",
            (chat_id,),
        )
        row = await cur.fetchone()
        return row[0] if row else None
