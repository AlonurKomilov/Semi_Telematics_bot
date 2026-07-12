"""AI chat history persistence mixin — threaded conversations.

Message text (and conversation titles, which derive from the first
question) is encrypted at rest (``infra.crypto`` — same Fernet setup
that protects Samsara API tokens): chat content is the most free-form
personal data the platform stores, so a DB dump or misdirected backup
must not expose it.  The ``enc::`` prefix scheme keeps legacy plaintext
rows readable — they decrypt as-is and get re-encrypted naturally as
the per-user row cap prunes them out.

Threading model: every message row carries ``conversation_id``
(``ai_conversations`` row).  The dashboard's History panel lists a
user's conversations with open/export/delete per thread; the miniapp
keeps its single-thread view by always writing into the user's most
recent conversation.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

# NOTE: ``infra.crypto`` is imported lazily inside the functions below —
# ``infra/__init__`` imports ``adapters.storage`` back (platform routing),
# so a module-level import here is a circular-import boot failure.

if TYPE_CHECKING:
    class _MixinBase:
        """Typing stub — attributes provided by the concrete DB class at runtime."""
        _db: Any
        @staticmethod
        def _now() -> str: ...
else:
    _MixinBase = object

# Raised from 20 → 100 (50 exchanges) so the dashboard can show
# meaningful scrollback.  The in-memory ``_MAX_HISTORY`` in
# capabilities/ai/chat.py stays at 10 exchanges — that cap controls
# how much prior conversation gets sent into each prompt (LLM token
# budget) and is independent from the UI scrollback window.
_MAX_ROWS_PER_USER = 100

# History panel cap — a user can't accumulate unbounded thread rows;
# the row cap above empties old threads and the cleanup pass drops them.
_MAX_CONVERSATIONS_LISTED = 50

_TITLE_MAX_CHARS = 60


def decrypt_chat_text(stored: str) -> str:
    """Best-effort decrypt for chat display.

    A row that can't be decrypted (ENCRYPTION_KEY rotated without the
    re-encryption sweep) degrades to a placeholder instead of failing
    the whole history read — losing one old bubble beats a 500 on the
    chat page.  Shared with the operator console's feedback view so
    both readers degrade identically.
    """
    from infra.crypto import decrypt
    try:
        return decrypt(stored or "")
    except ValueError:
        return "[unreadable — encrypted with a previous key]"


def _title_from_question(question: str) -> str:
    """First-question-derived thread title, single-line, bounded."""
    t = " ".join((question or "").split())
    if len(t) > _TITLE_MAX_CHARS:
        t = t[:_TITLE_MAX_CHARS - 1].rstrip() + "…"
    return t or "New chat"


class AIChatHistoryMixin(_MixinBase):

    # ── Conversations (threads) ──────────────────────────────────

    async def create_ai_conversation(
        self, account_id: int, user_id: int, title: str,
    ) -> int:
        """Create a thread; returns its id.  Title encrypted at rest."""
        from infra.crypto import encrypt
        now = self._now()
        cur = await self._db.execute(
            "INSERT INTO ai_conversations"
            " (account_id, user_id, title, created_at, updated_at)"
            " VALUES (?, ?, ?, ?, ?)",
            (account_id, user_id, encrypt(_title_from_question(title)), now, now),
        )
        await self._db.commit()
        return int(cur.lastrowid)

    async def get_latest_ai_conversation_id(
        self, account_id: int, user_id: int,
    ) -> int | None:
        """Most recently active thread id, or None when the user has none."""
        cur = await self._db.execute(
            "SELECT id FROM ai_conversations"
            " WHERE account_id = ? AND user_id = ?"
            " ORDER BY updated_at DESC, id DESC LIMIT 1",
            (account_id, user_id),
        )
        row = await cur.fetchone()
        return int(row[0]) if row else None

    async def list_ai_conversations(
        self, account_id: int, user_id: int,
    ) -> list[dict]:
        """The History panel's rows — newest-activity first.

        ``message_count`` reflects surviving rows (the per-user cap and
        the 90-day age prune both shrink old threads); threads emptied
        by pruning are removed by ``_drop_empty_ai_conversations`` so
        they never show as hollow entries here.
        """
        cur = await self._db.execute(
            """SELECT c.id, c.title, c.created_at, c.updated_at,
                      (SELECT COUNT(*) FROM ai_chat_history h
                        WHERE h.conversation_id = c.id) AS message_count
                 FROM ai_conversations c
                WHERE c.account_id = ? AND c.user_id = ?
                  AND EXISTS (SELECT 1 FROM ai_chat_history h
                               WHERE h.conversation_id = c.id)
                ORDER BY c.updated_at DESC, c.id DESC
                LIMIT ?""",
            (account_id, user_id, _MAX_CONVERSATIONS_LISTED),
        )
        rows = await cur.fetchall()
        return [
            {
                "id": int(r[0]),
                "title": decrypt_chat_text(r[1]),
                "created_at": r[2],
                "updated_at": r[3],
                "message_count": int(r[4] or 0),
            }
            for r in rows
        ]

    async def delete_ai_conversation(
        self, account_id: int, user_id: int, conversation_id: int,
    ) -> bool:
        """Delete one thread + its messages.  Scoped — a user can only
        delete their own; returns False when the id isn't theirs."""
        cur = await self._db.execute(
            "DELETE FROM ai_conversations"
            " WHERE id = ? AND account_id = ? AND user_id = ?",
            (conversation_id, account_id, user_id),
        )
        deleted = bool(cur.rowcount)
        if deleted:
            await self._db.execute(
                "DELETE FROM ai_chat_history"
                " WHERE conversation_id = ? AND account_id = ? AND user_id = ?",
                (conversation_id, account_id, user_id),
            )
        await self._db.commit()
        return deleted

    async def resolve_ai_conversation(
        self, account_id: int, user_id: int,
        conversation_id: int | None, question: str,
    ) -> int:
        """Conversation id a new exchange should land in.

        Explicit id → verified against (account, user); a stale id
        (thread deleted from another tab) falls through to a fresh
        thread rather than resurrecting the deleted one.  No id →
        latest thread, else a new one titled from the question.
        """
        if conversation_id is not None:
            cur = await self._db.execute(
                "SELECT id FROM ai_conversations"
                " WHERE id = ? AND account_id = ? AND user_id = ?",
                (conversation_id, account_id, user_id),
            )
            if await cur.fetchone():
                return conversation_id
            return await self.create_ai_conversation(account_id, user_id, question)
        latest = await self.get_latest_ai_conversation_id(account_id, user_id)
        if latest is not None:
            return latest
        return await self.create_ai_conversation(account_id, user_id, question)

    async def _drop_empty_ai_conversations(
        self, account_id: int | None = None, user_id: int | None = None,
    ) -> None:
        """Remove threads whose messages were all pruned away.

        Scoped to one user when ids are given (the save-path cap
        prune); global for the nightly age prune."""
        if account_id is not None and user_id is not None:
            await self._db.execute(
                """DELETE FROM ai_conversations c
                   WHERE c.account_id = ? AND c.user_id = ?
                     AND NOT EXISTS (SELECT 1 FROM ai_chat_history h
                                      WHERE h.conversation_id = c.id)""",
                (account_id, user_id),
            )
        else:
            await self._db.execute(
                """DELETE FROM ai_conversations c
                   WHERE NOT EXISTS (SELECT 1 FROM ai_chat_history h
                                      WHERE h.conversation_id = c.id)""",
            )

    # ── Messages ─────────────────────────────────────────────────

    async def save_chat_messages(
        self,
        account_id: int,
        user_id: int,
        question: str,
        answer: str,
        conversation_id: int | None = None,
        model_tier: str = "",
    ) -> int:
        """Persist a user/model exchange into a thread; returns the
        thread id (created on the fly for a first message / stale id)
        so callers can hand it back to the client.

        ``model_tier`` (display label, not sensitive) lands on the
        model row so a refresh keeps the per-answer tier attribution.
        Deliberately NOT stored: the chain-of-thought and the process
        timeline — display-only artifacts that live in the user's
        browser (localStorage), never in the DB.
        """
        from infra.crypto import encrypt
        conv_id = await self.resolve_ai_conversation(
            account_id, user_id, conversation_id, question,
        )
        now = self._now()
        for role, text, cap in (("user", question, 1000), ("model", answer, 2000)):
            await self._db.execute(
                "INSERT INTO ai_chat_history"
                " (account_id, user_id, role, text, created_at, conversation_id,"
                "  model_tier)"
                " VALUES (?, ?, ?, ?, ?, ?, ?)",
                (account_id, user_id, role, encrypt(text[:cap]), now, conv_id,
                 model_tier if role == "model" else ""),
            )
        await self._db.execute(
            "UPDATE ai_conversations SET updated_at = ? WHERE id = ?",
            (now, conv_id),
        )
        # Prune: keep only the newest _MAX_ROWS_PER_USER rows (across
        # all threads), then drop any thread that just went empty.
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
        await self._drop_empty_ai_conversations(account_id, user_id)
        await self._db.commit()
        return conv_id

    async def get_chat_history(
        self,
        account_id: int,
        user_id: int,
        limit: int = _MAX_ROWS_PER_USER,
        conversation_id: int | None = None,
    ) -> list[dict]:
        """Return chat history ordered oldest-first, text decrypted.

        ``conversation_id`` scopes to one thread (the dashboard); None
        keeps the legacy all-threads window (the miniapp's flat view).
        Each row carries ``role``, ``text`` and ``created_at`` so the UI
        can render real send-time labels instead of "just now" for every
        loaded message.
        """
        where = "account_id = ? AND user_id = ?"
        params: list = [account_id, user_id]
        if conversation_id is not None:
            where += " AND conversation_id = ?"
            params.append(conversation_id)
        params.append(limit)
        cur = await self._db.execute(
            f"""SELECT role, text, created_at, model_tier
                FROM ai_chat_history
                WHERE {where}
                ORDER BY id DESC LIMIT ?""",
            params,
        )
        rows = await cur.fetchall()
        # Rows are newest-first; reverse for chronological order
        return [
            {
                "role": r[0],
                "text": decrypt_chat_text(r[1]),
                "created_at": r[2],
                "model_tier": r[3] or "",
            }
            for r in reversed(rows)
        ]

    async def clear_chat_history(self, account_id: int, user_id: int) -> None:
        """Delete ALL chat history + threads for a user (miniapp Clear)."""
        await self._db.execute(
            "DELETE FROM ai_chat_history WHERE account_id = ? AND user_id = ?",
            (account_id, user_id),
        )
        await self._db.execute(
            "DELETE FROM ai_conversations WHERE account_id = ? AND user_id = ?",
            (account_id, user_id),
        )
        await self._db.commit()

    async def prune_ai_chat_history(self, days: int = 90) -> int:
        """Age-based privacy cap: delete chat rows older than ``days``.

        The per-user 100-row cap bounds *volume*; this bounds *age* — a
        user who stopped chatting months ago shouldn't have their old
        questions sitting in the DB indefinitely.  Runs from the nightly
        data-retention job (``capabilities/ai/retention.py`` declares
        the window).  Cutoff compares ISO-8601 TEXT lexicographically —
        same format ``_now()`` writes.  Threads emptied by the prune are
        dropped so the History panel never lists hollow entries.
        """
        from datetime import datetime, timedelta, timezone

        cutoff = (
            datetime.now(timezone.utc) - timedelta(days=int(days))
        ).isoformat()
        cur = await self._db.execute(
            "DELETE FROM ai_chat_history WHERE created_at < ?",
            (cutoff,),
        )
        await self._drop_empty_ai_conversations()
        await self._db.commit()
        return cur.rowcount or 0
