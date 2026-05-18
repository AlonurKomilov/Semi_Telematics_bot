"""Driver-invite text-handler tests.

Verifies the bot enforces a vehicle/truck number when an admin invites
a driver — ``/skip`` and blank entries are rejected with a re-prompt
instead of creating a vehicle-less invite (which would orphan the
driver from the alert pipeline).
"""

from __future__ import annotations

import os
import sys
from unittest.mock import AsyncMock, MagicMock, patch

os.environ.setdefault("ENCRYPTION_KEY", "")

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))


def _fake_user(account_id: int = 1, user_id: int = 7):
    """Minimal user stand-in matching the attributes the handler reads."""
    u = MagicMock()
    u.account_id = account_id
    u.id = user_id
    u.label = "Test Admin"
    u.role = "owner"
    return u


def _make_update_and_context(text: str, *, pending: str = "invite_driver"):
    """Build minimal Telegram ``update`` / ``context`` mocks driving
    ``handle_text``.  ``user_data`` is a real dict so the handler's
    ``pop("_pending")`` mutates it the same way it would in prod.

    Sets ``effective_chat.type = "private"`` explicitly because the
    handler now hard-bails on non-private chats as defense-in-depth.
    """
    update = MagicMock()
    update.message = MagicMock()
    update.message.text = text
    update.effective_chat = MagicMock()
    update.effective_chat.type = "private"
    context = MagicMock()
    # user_data must be a real dict — the handler does pop/get/set on it.
    context.user_data = {
        "_pending": pending,
        "_db_user": _fake_user(),
    }
    return update, context


@pytest.fixture
def mocks():
    """Patch the handler's external dependencies so the test only
    exercises the branch logic, not Telegram / DB / i18n."""
    with patch("interfaces.bot.callbacks.text_handlers._show", new=AsyncMock()) as mock_show, \
         patch("interfaces.bot.callbacks.text_handlers._group_chat_guard",
               new=AsyncMock(return_value=True)) as mock_guard, \
         patch("interfaces.bot.callbacks.text_handlers.get_platform_db") as mock_pdb, \
         patch("interfaces.bot.callbacks.text_handlers.make_invite_link",
               return_value="https://t.me/bot?start=ABCD-1234") as _link, \
         patch("interfaces.bot.callbacks.text_handlers.format_invite_created",
               return_value="Invite created.") as _fmt, \
         patch("interfaces.bot.callbacks.text_handlers.invite_kb",
               return_value=MagicMock()) as _kb, \
         patch("interfaces.bot.callbacks.text_handlers.role_display",
               return_value="Driver") as _rd, \
         patch("interfaces.bot.callbacks.text_handlers.t",
               side_effect=lambda key, **kw: key):
        fake_invite = MagicMock()
        fake_invite.code = "ABCD-1234"
        mock_pdb.return_value.create_invite = AsyncMock(return_value=fake_invite)
        yield {
            "show": mock_show,
            "guard": mock_guard,
            "create_invite": mock_pdb.return_value.create_invite,
        }


def _shown_texts(mock_show) -> list[str]:
    """Flatten every text passed to ``_show`` across all calls."""
    out: list[str] = []
    for call in mock_show.call_args_list:
        # _show(update, context, texts, keyboard=...) — texts is args[2].
        texts = call.args[2] if len(call.args) >= 3 else call.kwargs.get("texts", [])
        for t in texts:
            out.append(str(t))
    return out


class TestDriverInviteRequiresVehicle:
    async def test_skip_is_rejected_with_reprompt(self, mocks):
        from interfaces.bot.callbacks.text_handlers import handle_text
        update, context = _make_update_and_context("skip")
        await handle_text(update, context)
        shown = _shown_texts(mocks["show"])
        assert any("driver_vehicle_required" in s for s in shown), \
            f"Expected re-prompt key in shown texts: {shown}"
        mocks["create_invite"].assert_not_awaited()

    async def test_slash_skip_is_rejected(self, mocks):
        from interfaces.bot.callbacks.text_handlers import handle_text
        update, context = _make_update_and_context("/skip")
        await handle_text(update, context)
        shown = _shown_texts(mocks["show"])
        assert any("driver_vehicle_required" in s for s in shown), \
            f"Expected re-prompt key in shown texts: {shown}"
        mocks["create_invite"].assert_not_awaited()

    async def test_uppercase_skip_is_rejected(self, mocks):
        """Case-insensitive — admin typing ``SKIP`` shouldn't slip past."""
        from interfaces.bot.callbacks.text_handlers import handle_text
        update, context = _make_update_and_context("SKIP")
        await handle_text(update, context)
        mocks["create_invite"].assert_not_awaited()

    async def test_valid_vehicle_creates_invite(self, mocks):
        from interfaces.bot.callbacks.text_handlers import handle_text
        update, context = _make_update_and_context("107")
        await handle_text(update, context)
        mocks["create_invite"].assert_awaited_once()
        call = mocks["create_invite"].await_args
        assert call.kwargs["truck_num"] == "107"
        # Pending state is cleared so a subsequent text doesn't loop back
        # into the invite flow.
        assert "_pending" not in context.user_data
