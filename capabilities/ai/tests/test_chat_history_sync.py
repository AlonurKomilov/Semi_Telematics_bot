"""``sync_history_from_db`` makes the DB the source of truth for the model's
conversational context.

Pins the multi-worker bug: ``_chat_histories`` is per-process, so a follow-up
question landing on a different gunicorn worker than the previous turn found
an empty cache and the model "forgot" the conversation.  The sync (called in
the chat request path) replaces the local slice from the DB every turn — and
because it REPLACES rather than seeds-on-miss, a DELETE /ai/history served by
one worker empties every other worker's stale memory on their next turn too.
"""

from __future__ import annotations

import os

os.environ.setdefault("ENCRYPTION_KEY", "")

import pytest

import capabilities.ai.chat as chat
from capabilities.ai.chat import sync_history_from_db


class _FakePlatformDB:
    def __init__(self, rows):
        self._rows = rows

    async def get_chat_history(self, account_id, user_id, limit=100,
                               conversation_id=None):
        if self._rows is None:
            raise RuntimeError("db down")
        return list(self._rows[-limit:])


@pytest.fixture(autouse=True)
def _clean_cache(monkeypatch):
    monkeypatch.setattr(chat, "_chat_histories", {})


@pytest.mark.asyncio
async def test_cold_worker_gets_context_from_db():
    """Fresh worker, prior turns in the DB → cache filled, prompt roles mapped."""
    db = _FakePlatformDB([
        {"role": "user", "text": "any overdue maintenance?", "created_at": "t1"},
        {"role": "model", "text": "Yes, truck 228 is overdue.", "created_at": "t1"},
    ])
    await sync_history_from_db(db, account_id=1, user_id=7)

    hist = chat._chat_histories[(7, 1)]
    assert hist == [
        {"role": "User", "text": "any overdue maintenance?"},
        {"role": "Assistant", "text": "Yes, truck 228 is overdue."},
    ]


@pytest.mark.asyncio
async def test_stale_local_memory_is_replaced_not_merged():
    """This worker's cache holds turns the DB no longer has (or never had in
    this order) → the DB slice wins wholesale.
    """
    chat._chat_histories[(7, 1)] = [{"role": "User", "text": "old local-only turn"}]
    db = _FakePlatformDB([
        {"role": "user", "text": "fresh question", "created_at": "t2"},
        {"role": "model", "text": "fresh answer", "created_at": "t2"},
    ])
    await sync_history_from_db(db, account_id=1, user_id=7)

    assert [e["text"] for e in chat._chat_histories[(7, 1)]] == [
        "fresh question", "fresh answer",
    ]


@pytest.mark.asyncio
async def test_cleared_db_empties_this_workers_memory():
    """User hit Clear (served by another worker, DB emptied) → this worker
    must drop its stale memory instead of resurrecting the conversation.
    """
    chat._chat_histories[(7, 1)] = [{"role": "User", "text": "deleted convo"}]
    await sync_history_from_db(_FakePlatformDB([]), account_id=1, user_id=7)

    assert (7, 1) not in chat._chat_histories


@pytest.mark.asyncio
async def test_db_error_keeps_local_cache_as_fallback():
    existing = [{"role": "User", "text": "kept"}]
    chat._chat_histories[(7, 1)] = existing
    await sync_history_from_db(_FakePlatformDB(None), account_id=1, user_id=7)

    assert chat._chat_histories[(7, 1)] is existing
