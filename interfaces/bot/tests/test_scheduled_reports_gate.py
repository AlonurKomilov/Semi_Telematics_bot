"""The bot offers, accepts and delivers only the report types the role
holds — the same two-grant rule the API applies."""

from __future__ import annotations

import os

os.environ.setdefault("ENCRYPTION_KEY", "")

import pytest

from capabilities.permissions import roles
from capabilities.permissions.roles import FeatureSet


def _can_only(*flags):
    def can(role, feature):
        return feature in flags
    return can


def test_the_type_picker_offers_only_the_types_the_role_holds(monkeypatch):
    from interfaces.bot.keyboards import scheduled_reports_type_kb
    monkeypatch.setattr(roles, "can", _can_only("can_view_faults", "can_view_fuel"))
    kb = scheduled_reports_type_kb("dispatcher")
    data = [b.callback_data for row in kb.inline_keyboard for b in row]
    assert data == ["ar_type_faults", "ar_type_fuel", "cmd_auto_reports"]

    monkeypatch.setattr(roles, "can", _can_only())
    kb = scheduled_reports_type_kb("recruiter")
    assert [b.callback_data for row in kb.inline_keyboard for b in row] == ["cmd_auto_reports"]


def test_a_chosen_type_needs_the_service_and_its_own_verb(monkeypatch):
    from interfaces.bot import scheduled_reports as mod
    monkeypatch.setattr(mod, "can", _can_only("can_view_reports", "can_view_faults"))
    assert mod._role_holds_type("dispatcher", "faults")
    assert not mod._role_holds_type("dispatcher", "camera")
    monkeypatch.setattr(mod, "can", _can_only("can_view_faults"))
    assert not mod._role_holds_type("dispatcher", "faults")      # Reports itself withheld
    assert not mod._role_holds_type("dispatcher", "nonsense")


@pytest.mark.asyncio
async def test_delivery_asks_the_account_with_the_members_tier(monkeypatch):
    from interfaces.bot import scheduled_reports as mod
    asked = []

    async def fake(role, account_id, is_manager=False, is_primary_owner=False, company_id=None):
        asked.append((role, account_id, is_manager, is_primary_owner))
        return FeatureSet(can_view_reports=True, can_view_faults=True, can_view_cameras=False)

    monkeypatch.setattr(roles, "get_user_permissions", fake)
    sub = {"user_id": 3, "account_id": 9, "role": "safety", "is_manager": 1, "is_primary_owner": 0}
    assert await mod._still_allowed(sub, "faults")
    assert not await mod._still_allowed(sub, "camera")
    assert asked[0] == ("safety", 9, True, False)
