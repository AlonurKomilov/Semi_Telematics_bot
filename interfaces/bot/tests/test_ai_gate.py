"""The AI assistant is a per-role grant in the bot too.

``can_view_ai_assistant`` became a stored, revocable verb on 2026-09-06.
The dashboard and the API ask it at every door; the bot's AI menu had
no door of its own (the flag was always True before), so a role denied
the assistant could still chat over Telegram — a stale button or a
typed question reaches the handler without passing through the menu.
Two guards: every ``cmd_ai*`` handler carries the gate under the
registration decorator, and the gate refuses without the verb.
"""

from __future__ import annotations

import ast
import os
from unittest.mock import AsyncMock, MagicMock, patch

os.environ.setdefault("ENCRYPTION_KEY", "")

import pytest

from tests._repo import REPO

_AI = os.path.join(REPO, "interfaces/bot/ai.py")


def _handlers():
    tree = ast.parse(open(_AI, encoding="utf-8").read())
    return [n for n in tree.body
            if isinstance(n, ast.AsyncFunctionDef) and n.name.startswith("cmd_ai")]


def test_every_ai_handler_is_gated_under_registration():
    handlers = _handlers()
    assert len(handlers) >= 10, "the AI handlers were not found"
    for fn in handlers:
        names = [d.id for d in fn.decorator_list if isinstance(d, ast.Name)]
        assert "_require_ai_assistant" in names, f"{fn.name} has no AI gate"
        # registration first: the gate reads the user registration stashes
        assert names.index("_require_registered") < names.index("_require_ai_assistant"), fn.name


def _update(callback: bool):
    u = MagicMock()
    u.callback_query = MagicMock() if callback else None
    if callback:
        u.callback_query.answer = AsyncMock()
    return u


def _context(role: str = "dispatcher"):
    c = MagicMock()
    user = MagicMock()
    user.role = role
    c.user_data = {"_db_user": user}
    return c


@pytest.mark.asyncio
async def test_without_the_verb_the_handler_never_runs():
    from interfaces.bot.ai import _require_ai_assistant
    inner = AsyncMock(return_value="ran")
    gated = _require_ai_assistant(inner)
    with patch("interfaces.bot.ai.can", return_value=False), \
         patch("interfaces.bot.ai._show", new=AsyncMock()) as show, \
         patch("interfaces.bot.ai.t", side_effect=lambda k, **kw: k):
        # a button press: answered on the button
        upd = _update(callback=True)
        assert await gated(upd, _context()) is None
        upd.callback_query.answer.assert_awaited_once()
        assert "access.no_access" in upd.callback_query.answer.await_args.args
        # a typed question: answered as a message
        assert await gated(_update(callback=False), _context(), "why is truck 12 idle") is None
        assert show.await_args.args[2] == ["access.no_access"]
    inner.assert_not_awaited()


@pytest.mark.asyncio
async def test_with_the_verb_the_handler_runs_with_its_arguments():
    from interfaces.bot.ai import _require_ai_assistant
    inner = AsyncMock(return_value="ran")
    gated = _require_ai_assistant(inner)
    ctx = _context()
    with patch("interfaces.bot.ai.can", return_value=True) as can:
        assert await gated(_update(callback=False), ctx, "q", index=3) == "ran"
    inner.assert_awaited_once()
    assert inner.await_args.args[2] == "q" and inner.await_args.kwargs == {"index": 3}
    can.assert_called_once_with(ctx.user_data["_db_user"].role, "can_view_ai_assistant")


@pytest.mark.asyncio
async def test_no_stashed_user_is_a_refusal_not_a_crash():
    from interfaces.bot.ai import _require_ai_assistant
    inner = AsyncMock()
    gated = _require_ai_assistant(inner)
    c = MagicMock(); c.user_data = {}
    with patch("interfaces.bot.ai._show", new=AsyncMock()), \
         patch("interfaces.bot.ai.t", side_effect=lambda k, **kw: k):
        assert await gated(_update(callback=False), c) is None
    inner.assert_not_awaited()
