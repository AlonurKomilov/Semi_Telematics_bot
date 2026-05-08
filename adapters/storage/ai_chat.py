"""AI chat history persistence mixin."""

from __future__ import annotations

_MAX_ROWS_PER_USER = 20  # keep last 20 messages (10 exchanges)


class AIChatHistoryMixin:

    async def save_chat_messages(
        self,
        account_id: int,
        user_id: int,
        question: str,
        answer: str,
    ) -> None:
        """Persist a user/model exchange and prune old rows beyond the cap."""
        now = self._now()
        await self._db.execute(
            "INSERT INTO ai_chat_history (account_id, user_id, role, text, created_at)"
            " VALUES (?, ?, 'user', ?, ?)",
            (account_id, user_id, question[:1000], now),
        )
        await self._db.execute(
            "INSERT INTO ai_chat_history (account_id, user_id, role, text, created_at)"
            " VALUES (?, ?, 'model', ?, ?)",
            (account_id, user_id, answer[:2000], now),
        )
        # Prune: keep only the newest _MAX_ROWS_PER_USER rows
        await self._db.execute(
            """DELETE FROM ai_chat_history
               WHERE account_id = ? AND user_id = ?
                 AND id NOT IN (
                     SELECT id FROM ai_chat_history
                     WHERE account_id = ? AND user_id = ?
                     ORDER BY id DESC LIMIT ?
                 )""",
            (account_id, user_id, account_id, user_id, _MAX_ROWS_PER_USER),
        )
        await self._db.commit()

    async def get_chat_history(
        self,
        account_id: int,
        user_id: int,
        limit: int = _MAX_ROWS_PER_USER,
    ) -> list[dict]:
        """Return chat history ordered oldest-first."""
        cur = await self._db.execute(
            """SELECT role, text FROM ai_chat_history
               WHERE account_id = ? AND user_id = ?
               ORDER BY id DESC LIMIT ?""",
            (account_id, user_id, limit),
        )
        rows = await cur.fetchall()
        # Rows are newest-first; reverse for chronological order
        return [{"role": r[0], "text": r[1]} for r in reversed(rows)]

    async def clear_chat_history(self, account_id: int, user_id: int) -> None:
        """Delete all chat history for a user."""
        await self._db.execute(
            "DELETE FROM ai_chat_history WHERE account_id = ? AND user_id = ?",
            (account_id, user_id),
        )
        await self._db.commit()
