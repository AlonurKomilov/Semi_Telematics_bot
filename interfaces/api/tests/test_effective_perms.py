"""``effective_perms`` / ``holds`` — the gate's answer, for readers.

A gated route already carries the resolved FeatureSet on
``user["_perms"]``; an ungated route resolves it once, the way the
gates do, and stashes it the same way.  Neither ever asks the role's
built-in default.
"""

from __future__ import annotations

import os

os.environ.setdefault("ENCRYPTION_KEY", "")

import pytest

from capabilities.permissions.roles import FeatureSet
from interfaces.api import deps


@pytest.fixture
def account_answer(monkeypatch):
    """The account resolved Manage=False for maintenance — the seed for
    fleet says True, so any read of the built-in default shows."""
    calls = []

    async def fake_get_user_permissions(role, account_id, is_manager=False,
                                        is_primary_owner=False, company_id=None):
        calls.append((role, account_id, is_manager, is_primary_owner))
        return FeatureSet(can_view_maintenance=True, can_manage_maintenance=False)

    monkeypatch.setattr(deps, "get_user_permissions", fake_get_user_permissions)
    return calls


@pytest.mark.asyncio
async def test_a_gated_route_reuses_the_stash(account_answer):
    user = {"role": "fleet", "account_id": 1,
            "_perms": FeatureSet(can_manage_maintenance=True)}
    assert await deps.holds(user, "can_manage_maintenance") is True
    assert account_answer == []          # no second resolve


@pytest.mark.asyncio
async def test_an_ungated_route_resolves_the_account_and_stashes(account_answer):
    user = {"role": "fleet", "account_id": 7, "is_manager": True}
    assert await deps.holds(user, "can_manage_maintenance") is False   # the account's answer, not fleet's seed
    assert await deps.holds(user, "can_view_maintenance") is True
    assert account_answer == [("fleet", 7, True, False)]               # resolved once
    assert user["_perms"].can_manage_maintenance is False              # stashed like a gate


@pytest.mark.asyncio
async def test_an_unknown_flag_is_false(account_answer):
    user = {"role": "fleet", "account_id": 7}
    assert await deps.holds(user, "can_do_nonsense") is False


@pytest.mark.asyncio
async def test_the_token_scope_narrows_the_resolved_set(account_answer):
    # An extension token carries a scope; what it cannot see is False
    # even when the account grants it.
    user = {"role": "fleet", "account_id": 7, "aud": "extension",
            "scope": ["can_view_location"]}
    perms = await deps.effective_perms(user)
    assert perms.can_view_maintenance is False
