"""Loads write-scope: a base dispatcher manages only their OWN loads;
managing another dispatcher's (or an unassigned) load needs the
delegated can_loads_manage_all grant.  Enforced in features/loads/router.
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from capabilities.permissions.roles import FeatureSet
import features.loads.router as r


def _load(dispatcher_user_id):
    return SimpleNamespace(dispatcher_user_id=dispatcher_user_id, company_code="")


@pytest.mark.asyncio
async def test_manage_all_writes_any_load(monkeypatch):
    async def _perms(_u):
        return FeatureSet(can_manage_loads=True, can_loads_manage_all=True)
    monkeypatch.setattr(r, "_user_perms", _perms)
    user = {"id": 5, "account_id": 1, "role": "admin"}
    assert await r._can_write_load(user, _load(9)) is True     # someone else's
    assert await r._can_write_load(user, _load(None)) is True  # unassigned
    assert await r._is_manage_all(user) is True


@pytest.mark.asyncio
async def test_own_scope_dispatcher_only_writes_own(monkeypatch):
    async def _perms(_u):
        return FeatureSet(can_manage_loads=True, can_loads_manage_all=False)
    monkeypatch.setattr(r, "_user_perms", _perms)
    user = {"id": 5, "account_id": 1, "role": "dispatcher"}
    assert await r._can_write_load(user, _load(5)) is True     # own
    assert await r._can_write_load(user, _load(9)) is False    # another's
    assert await r._can_write_load(user, _load(None)) is False # unassigned = manager-only
    assert await r._is_manage_all(user) is False
