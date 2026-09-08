"""Notifications is a service granted per role (can_view_notifications).
Withheld, nothing reaches the person on any channel — broadcast or
targeted — except a mandatory (security / billing) notice, which passes
the way it passes a mute.  The API doors close on the same verb."""

from __future__ import annotations

import os
import re

os.environ.setdefault("ENCRYPTION_KEY", "")

import pytest

from capabilities.permissions import roles
from capabilities.permissions.roles import FeatureSet
from capabilities.notifications import service as svc
from tests._repo import REPO


class _DB:
    """Two users: 1 (owner) holds the service, 2 (dispatcher) does not."""
    def __init__(self):
        self.tiers = {1: ("owner", False, True, 7), 2: ("dispatcher", False, False, 7)}

    async def get_roles_for_users(self, ids):
        return {i: self.tiers[i][0] for i in ids if i in self.tiers}

    async def get_permission_tiers_for_users(self, ids):
        return {i: self.tiers[i] for i in ids if i in self.tiers}


def _perms_by_role(monkeypatch):
    async def fake(role, account_id, is_manager=False, is_primary_owner=False, company_id=None):
        r = role.value if hasattr(role, "value") else role
        return FeatureSet(can_view_notifications=(r == "owner"))
    monkeypatch.setattr(roles, "get_user_permissions", fake)


def _subs():
    return [
        {"recipient_type": "user", "recipient_id": "1"},
        {"recipient_type": "user", "recipient_id": "2"},
        {"recipient_type": "topic", "recipient_id": "-100"},   # a shared topic has no role
    ]


@pytest.mark.asyncio
async def test_a_broadcast_skips_the_role_the_service_is_withheld_from(monkeypatch):
    _perms_by_role(monkeypatch)
    kept = await svc._filter_recipients(_DB(), _subs(), None, None)
    assert [s["recipient_id"] for s in kept] == ["1", "-100"]


@pytest.mark.asyncio
async def test_a_mandatory_broadcast_passes_the_gate(monkeypatch):
    _perms_by_role(monkeypatch)
    kept = await svc._filter_recipients(_DB(), _subs(), None, None, service_gate=False)
    assert [s["recipient_id"] for s in kept] == ["1", "2", "-100"]


@pytest.mark.asyncio
async def test_the_targeted_path_asks_the_same_door(monkeypatch):
    _perms_by_role(monkeypatch)
    assert await svc._service_held(_DB(), 7, 1) is True
    assert await svc._service_held(_DB(), 7, 2) is False


@pytest.mark.asyncio
async def test_an_unanswerable_gate_keeps_the_recipient(monkeypatch):
    async def boom(*a, **k):
        raise RuntimeError("resolver down")
    monkeypatch.setattr(roles, "get_user_permissions", boom)
    kept = await svc._filter_recipients(_DB(), _subs(), None, None)
    assert len(kept) == 3
    assert await svc._service_held(_DB(), 7, 2) is True


def test_every_signed_in_door_of_the_api_asks_the_verb():
    """The public token doors (verify / unsubscribe) stay open; every
    other endpoint in the notifications router is behind the service."""
    src = open(os.path.join(REPO, "capabilities/notifications/router.py"), encoding="utf-8").read()
    assert "Depends(get_current_user)" not in src
    assert len(re.findall(r'require_permission\("can_view_notifications"\)', src)) >= 12
