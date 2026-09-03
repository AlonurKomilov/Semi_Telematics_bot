"""Effective-permission delivery gates (§9d closed 2026-07-27).

The Board's visibility filter resolves the user's EFFECTIVE per-account
FeatureSet; until this change the delivery gates used static role
seeds, so a matrix-customized account could SEE one thing and RECEIVE
another.  These tests pin the owner decision: permissions are the
single truth for receiving too —

* owner REVOKES a feature → its personal DMs stop, even with the
  toggle on;
* owner GRANTS beyond the role seed → delivery (and the toggle UIs)
  follow;
* the resolver failing falls back to the static seed gate (a perms
  hiccup must never silence delivery), per-tier.
"""

from dataclasses import replace
from types import SimpleNamespace

import pytest

import capabilities.permissions.roles as roles_mod
from capabilities.permissions.roles import ROLE_PERMISSIONS, Role
from capabilities.alerting.relevance import (
    alert_types_for_role,
    alert_types_for_user,
    filter_users_by_alert_access,
)


def _user(uid, role, account_id=1, **kw):
    return SimpleNamespace(
        id=uid, account_id=account_id, role=role,
        is_manager=kw.get("is_manager", False),
        is_primary_owner=kw.get("is_primary_owner", False),
    )


@pytest.fixture
def perms_store(monkeypatch):
    """Fake per-account overrides: {(account_id, role_str): FeatureSet}.
    Falls through to the role seed when no override is stored."""
    store: dict = {}

    async def fake_get_user_permissions(role, account_id, *,
                                        is_manager=False,
                                        is_primary_owner=False,
                                        company_id=None):
        r = role.value if hasattr(role, "value") else str(role)
        key = (account_id, r)
        if key in store:
            return store[key]
        return ROLE_PERMISSIONS[Role(r)]

    monkeypatch.setattr(roles_mod, "get_user_permissions",
                        fake_get_user_permissions)
    return store


class TestRevoke:
    @pytest.mark.asyncio
    async def test_revoked_feature_stops_delivery(self, perms_store):
        # Safety's seed allows events; this account's owner revoked it.
        seed = ROLE_PERMISSIONS[Role.SAFETY]
        perms_store[(1, "safety")] = replace(
            seed, can_view_events=False)
        users = [_user(10, "safety")]
        assert await filter_users_by_alert_access(users, "events") == []

    @pytest.mark.asyncio
    async def test_toggle_ui_hides_revoked_type(self, perms_store):
        seed = ROLE_PERMISSIONS[Role.SAFETY]
        perms_store[(1, "safety")] = replace(
            seed, can_view_events=False)
        types = await alert_types_for_user("safety", 1)
        assert "events" not in types


class TestGrant:
    @pytest.mark.asyncio
    async def test_granted_feature_starts_delivery(self, perms_store):
        # Accounting's seed denies events; this owner granted it.
        assert "events" not in alert_types_for_role("accounting")
        seed = ROLE_PERMISSIONS[Role.ACCOUNTING]
        perms_store[(1, "accounting")] = replace(seed, can_view_events=True)
        users = [_user(20, "accounting")]
        kept = await filter_users_by_alert_access(users, "events")
        assert [u.id for u in kept] == [20]

    @pytest.mark.asyncio
    async def test_toggle_ui_shows_granted_type(self, perms_store):
        seed = ROLE_PERMISSIONS[Role.ACCOUNTING]
        perms_store[(1, "accounting")] = replace(seed, can_view_events=True)
        types = await alert_types_for_user("accounting", 1)
        assert "events" in types


class TestAgreementAndFallback:
    @pytest.mark.asyncio
    async def test_delivery_agrees_with_board_gate(self, perms_store):
        """One SSOT: for any effective set, the delivery filter and the
        Board's perms_allow_alert_type must agree on every typed alert."""
        from capabilities.alerting.relevance import (
            ALERT_TYPE_REQUIRED_PERM, perms_allow_alert_type)
        seed = ROLE_PERMISSIONS[Role.DISPATCHER]
        eff = replace(seed, can_view_parking=False, can_view_faults=True)
        perms_store[(1, "dispatcher")] = eff
        for atype in ALERT_TYPE_REQUIRED_PERM:
            delivered = await filter_users_by_alert_access(
                [_user(30, "dispatcher")], atype)
            assert bool(delivered) == perms_allow_alert_type(eff, atype), atype

    @pytest.mark.asyncio
    async def test_resolver_failure_falls_back_to_static(self, monkeypatch):
        async def boom(*a, **kw):
            raise RuntimeError("perms storage down")
        monkeypatch.setattr(roles_mod, "get_user_permissions", boom)
        # Static seed gates keep delivery alive: safety keeps events,
        # accounting (no seed grant) stays excluded.
        kept = await filter_users_by_alert_access(
            [_user(10, "safety"), _user(20, "accounting")], "events")
        assert [u.id for u in kept] == [10]
        # Toggle UI falls back to the static list the same way.
        assert await alert_types_for_user("safety", 1) == \
            alert_types_for_role("safety")

    @pytest.mark.asyncio
    async def test_tier_resolution_memoized_per_role(self, monkeypatch):
        calls = []

        async def counting(role, account_id, **kw):
            calls.append((account_id, str(role)))
            return ROLE_PERMISSIONS[Role.SAFETY]
        monkeypatch.setattr(roles_mod, "get_user_permissions", counting)
        users = [_user(i, "safety") for i in range(25)]
        await filter_users_by_alert_access(users, "events")
        assert len(calls) == 1  # one resolve for 25 same-tier users
