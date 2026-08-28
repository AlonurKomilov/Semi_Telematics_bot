"""Front-door split — the global login bot must not serve tenant menus.

The login bot ran the single-bot era's full start flow: any registered
user got their complete account menu (and a system owner got an
/admin hint) in the login bot's chat.  After the 2026-08 bot rotation
the owner spotted the mix immediately: three surfaces (login, tenant,
system) in one window, and the platform-wide login token carrying a
control surface for every account.  The gate sends users whose
account has its OWN bot to that bot; accounts without one keep the
login bot as their full fallback.
"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

import interfaces.bot.registration as reg


def _ctx(account_id=None):
    return SimpleNamespace(bot_data={} if account_id is None
                           else {"account_id": account_id})


def _user(account_id=42):
    return SimpleNamespace(account_id=account_id)


def _platform(bot_username=""):
    async def get_account(_account_id):
        return SimpleNamespace(
            id=42, name="ACME TRUCKING", bot_username=bot_username,
        )
    return SimpleNamespace(get_account=get_account)


@pytest.mark.asyncio
async def test_no_redirect_on_per_account_bot(monkeypatch):
    shown = AsyncMock()
    monkeypatch.setattr(reg, "_show", shown)
    redirected = await reg._front_door_redirect(
        object(), _ctx(account_id=42), _user(),
    )
    assert redirected is False
    shown.assert_not_called()


@pytest.mark.asyncio
async def test_no_redirect_when_account_has_no_bot(monkeypatch):
    """Fallback contract: an account that never connected a bot keeps
    the login bot as its full surface."""
    shown = AsyncMock()
    monkeypatch.setattr(reg, "_show", shown)
    monkeypatch.setattr(reg, "get_platform_db", lambda: _platform(""))
    redirected = await reg._front_door_redirect(object(), _ctx(), _user())
    assert redirected is False
    shown.assert_not_called()


@pytest.mark.asyncio
async def test_redirects_to_account_bot_on_global_bot(monkeypatch):
    shown = AsyncMock()
    monkeypatch.setattr(reg, "_show", shown)
    monkeypatch.setattr(
        reg, "get_platform_db", lambda: _platform("ptg_4truck_bot"),
    )
    redirected = await reg._front_door_redirect(object(), _ctx(), _user())
    assert redirected is True
    shown.assert_called_once()
    args, kwargs = shown.call_args
    texts = args[2]
    assert any("ptg_4truck_bot" in t for t in texts)
    kb = kwargs["keyboard"]
    button = kb.inline_keyboard[0][0]
    assert button.url == "https://t.me/ptg_4truck_bot"


@pytest.mark.asyncio
async def test_storage_error_keeps_legacy_behavior(monkeypatch):
    """A platform-DB hiccup must not lock users out of the login bot —
    the gate declines and the caller renders as before."""
    shown = AsyncMock()
    monkeypatch.setattr(reg, "_show", shown)

    def _boom():
        raise RuntimeError("platform db down")

    monkeypatch.setattr(reg, "get_platform_db", _boom)
    redirected = await reg._front_door_redirect(object(), _ctx(), _user())
    assert redirected is False
    shown.assert_not_called()


def test_sysadmin_hint_no_longer_rendered():
    """The '/admin' hint advertised a command only the SYSTEM bot
    handles — cmd_start must not append it to tenant menus anymore."""
    import inspect
    src = inspect.getsource(reg.cmd_start)
    assert "sysadmin_hint" not in src
