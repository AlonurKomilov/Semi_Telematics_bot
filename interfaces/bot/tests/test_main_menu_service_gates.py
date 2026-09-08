"""The bot's main menu offers a service only to a role that holds it, and
reads the ACCOUNT's permission set — not the role's seed.

After the services became per-role grants (2026-09-06) the handlers
refused without the verb, but the menu still drew the buttons from
older conditions: Alerts on vehicles, AI on "an API key exists", Reports
on what it would list.  And every keyboard read ``get_permissions`` — the
seed — so an owner's matrix change never reached the menu at all.
"""

from __future__ import annotations

import os
import time

os.environ.setdefault("ENCRYPTION_KEY", "")

import pytest

from adapters.storage import Role
from capabilities.permissions import roles
from capabilities.permissions.roles import FeatureSet
from interfaces.bot import keyboards


def _buttons(kb) -> set[str]:
    return {b.callback_data for row in kb.inline_keyboard for b in row if b.callback_data}


@pytest.fixture
def ai_on(monkeypatch):
    import capabilities.ai as ai
    monkeypatch.setattr(ai, "is_configured", lambda: True)


def _menu(monkeypatch, **flags):
    fs = FeatureSet(can_view_vehicles=True, can_view_events=True, **flags)
    monkeypatch.setattr(keyboards, "current_permissions", lambda role: fs)
    return _buttons(keyboards.main_menu_kb(Role.DISPATCHER, ["CO1"], wide=True))


def test_each_service_button_needs_its_own_verb(monkeypatch, ai_on):
    none = _menu(monkeypatch, can_view_alerts=False, can_view_ai_assistant=False, can_view_reports=False)
    assert not {"cmd_alerts", "cmd_ai", "submenu_reports"} & none

    all3 = _menu(monkeypatch, can_view_alerts=True, can_view_ai_assistant=True, can_view_reports=True)
    assert {"cmd_alerts", "cmd_ai", "submenu_reports"} <= all3


def test_vehicles_alone_no_longer_opens_the_alerts_button(monkeypatch, ai_on):
    assert "cmd_alerts" not in _menu(monkeypatch, can_view_alerts=False, can_view_ai_assistant=True, can_view_reports=True)


def test_reports_still_needs_something_to_list(monkeypatch, ai_on):
    fs = FeatureSet(can_view_reports=True, can_view_vehicles=False, can_view_events=False)
    monkeypatch.setattr(keyboards, "current_permissions", lambda role: fs)
    assert "submenu_reports" not in _buttons(keyboards.main_menu_kb(Role.DISPATCHER, ["CO1"], wide=True))


def test_current_permissions_reads_the_primed_account_set_then_the_seed(monkeypatch):
    role = Role.DISPATCHER
    seed = roles.get_permissions(role)
    account_set = FeatureSet(can_view_alerts=not seed.can_view_alerts)
    token = roles._active_account_id.set(4242)
    try:
        monkeypatch.setitem(roles._permissions_cache, (4242, role.value, None),
                            (time.monotonic() + 60, account_set))
        assert roles.current_permissions(role) is account_set
        assert roles.can(role, "can_view_alerts") == account_set.can_view_alerts
        # stale → the seed, exactly as can() always did
        monkeypatch.setitem(roles._permissions_cache, (4242, role.value, None),
                            (time.monotonic() - 1, account_set))
        assert roles.current_permissions(role) is seed
    finally:
        roles._active_account_id.reset(token)
    assert roles.current_permissions(role) is seed      # no context → the seed
