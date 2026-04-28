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
