"""Conversation history per user (bounded ring buffer)."""

from __future__ import annotations

_chat_histories: dict[int, list[dict]] = {}
_MAX_HISTORY = 10    # keep last 10 exchanges
_MAX_CHAT_USERS = 1000


def _store_history(user_id: int, question: str, answer: str):
    """Store the last N exchanges for conversation context."""
    if user_id not in _chat_histories:
        if len(_chat_histories) >= _MAX_CHAT_USERS:
            oldest = next(iter(_chat_histories))
            del _chat_histories[oldest]
        _chat_histories[user_id] = []
    history = _chat_histories[user_id]
    history.append({"role": "User", "text": question[:300]})
    history.append({"role": "Assistant", "text": answer[:500]})
    if len(history) > _MAX_HISTORY * 2:
        _chat_histories[user_id] = history[-_MAX_HISTORY * 2:]


def clear_history(user_id: int):
    """Clear conversation history for a user."""
    _chat_histories.pop(user_id, None)
