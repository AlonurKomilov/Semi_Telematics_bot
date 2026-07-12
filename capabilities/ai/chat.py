"""Conversation history per user (bounded ring buffer)."""

from __future__ import annotations

_chat_histories: dict[tuple[int, int], list[dict]] = {}
_MAX_HISTORY = 10    # keep last 10 exchanges
_MAX_CHAT_USERS = 1000


def _store_history(user_id: int, question: str, answer: str, account_id: int = 0):
    """Store the last N exchanges for conversation context."""
    key = (user_id, account_id)
    if key not in _chat_histories:
        if len(_chat_histories) >= _MAX_CHAT_USERS:
            oldest = next(iter(_chat_histories))
            del _chat_histories[oldest]
        _chat_histories[key] = []
    history = _chat_histories[key]
    history.append({"role": "User", "text": question[:1000]})
    history.append({"role": "Assistant", "text": answer[:2000]})
    if len(history) > _MAX_HISTORY * 2:
        _chat_histories[key] = history[-_MAX_HISTORY * 2:]


def clear_history(user_id: int, account_id: int = 0):
    """Clear conversation history for a user."""
    _chat_histories.pop((user_id, account_id), None)


async def sync_history_from_db(
    platform_db, account_id: int, user_id: int,
    conversation_id: int | None = None,
) -> None:
    """Replace this user's in-memory context slice with the DB's recent rows.

    Called in the chat REQUEST path (both /ai/chat and /ai/chat/stream)
    so the DB — not per-process memory — is the source of truth for the
    model's conversational context.  Under multi-worker gunicorn each
    worker has its own ``_chat_histories``; without this sync a
    follow-up question usually lands on a worker that never saw the
    previous turn and the model "forgets" the conversation.  Always
    replacing (not seed-on-miss) also propagates DELETE /ai/history:
    a clear served by one worker empties the DB, and every other
    worker's stale memory gets dropped on its next sync instead of
    resurrecting a conversation the user deleted.

    One indexed ~20-row SELECT per chat turn — noise next to the
    multi-second LLM call it precedes.  Any DB error keeps the local
    cache as a degraded-but-working fallback.

    ``conversation_id`` scopes the context to ONE thread — the model
    must not remember a different conversation than the one on screen.
    """
    key = (user_id, account_id)
    try:
        rows = await platform_db.get_chat_history(
            account_id, user_id, limit=_MAX_HISTORY * 2,
            conversation_id=conversation_id,
        )
    except Exception:
        return
    if not rows:
        _chat_histories.pop(key, None)
        return
    _chat_histories[key] = [
        {"role": ("User" if r["role"] == "user" else "Assistant"),
         "text": r["text"]}
        for r in rows
    ]
