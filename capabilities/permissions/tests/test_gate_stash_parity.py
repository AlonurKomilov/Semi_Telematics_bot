"""Both permission dependencies stash the same side-channels.

require_permission_any recorded ``_perms`` (the effective FeatureSet)
and ``_matched_perm``; the singular require_permission recorded
nothing.  The alerting router's alert-TYPE filter reads ``_perms`` and
fails OPEN when absent — so migrating a pair gate to the singular
factory would have shown every alert type to every caller.  Pinned.
"""

from __future__ import annotations

import os

os.environ.setdefault("ENCRYPTION_KEY", "")

import pytest

from capabilities.permissions.roles import FeatureSet


@pytest.mark.asyncio
async def test_singular_stashes_perms_and_matched(monkeypatch):
    from interfaces.api import deps
    fs = FeatureSet(can_vehicle_all=True)

    async def _fake(role, account_id, **kw):
        return fs
    monkeypatch.setattr(deps, "get_user_permissions", _fake)
    check = deps.require_permission("can_view_vehicles")
    user = {"role": "fleet", "account_id": 1}
    out = await check(user)
    assert out["_perms"] is fs
    assert out["_matched_perm"] == "can_view_vehicles"


@pytest.mark.asyncio
async def test_any_and_singular_agree_on_the_stash(monkeypatch):
    from interfaces.api import deps
    fs = FeatureSet(can_vehicle_all=True)

    async def _fake(role, account_id, **kw):
        return fs
    monkeypatch.setattr(deps, "get_user_permissions", _fake)
    a = await deps.require_permission_any("can_view_vehicles")({"role": "fleet", "account_id": 1})
    b = await deps.require_permission("can_view_vehicles")({"role": "fleet", "account_id": 1})
    assert a["_perms"] is b["_perms"] is fs
    assert a["_matched_perm"] == b["_matched_perm"]
