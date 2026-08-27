"""Per-tier permission editing — each tier stored + edited INDEPENDENTLY.

The two-level Role→Tier matrix stores the senior tier under a ``{role}__manager``
key.  Editing Standard admin must never move Full admin, and vice-versa.  A
full-admin user (admin + is_manager) resolves the senior tier's own stored row.
"""
from __future__ import annotations

import os
import sys

os.environ.setdefault("ENCRYPTION_KEY", "test-key-32-chars-min-aaaaaaaaaaaaaaaaaaaa")
os.environ.setdefault("JWT_SECRET", "test-secret-32-chars-or-more-please-aaaaaaaaaaaa")
os.environ.setdefault("OBJECT_STORE_BACKEND", "disk")
os.environ.setdefault("OBJECT_STORE_ROOT", "/tmp/tier_test_store")

import pytest_asyncio
from httpx import AsyncClient, ASGITransport

from adapters.storage import Role

PERMS = "/api/admin/permissions/roles"


@pytest_asyncio.fixture
async def api(pg_db):
    import infra.platform as cp
    cp._db = pg_db
    from interfaces.api.app import create_api
    return create_api(), pg_db


def _headers(db_user, acct, role: str):
    from interfaces.api.auth import create_jwt
    return {"Authorization": f"Bearer {create_jwt(db_user.telegram_id or 0, acct.id, role, user_id=db_user.id)}"}


class TestPermissionTiers:
    async def test_senior_tier_is_independent(self, api):
        app, db = api
        from interfaces.api.auth import _hash_password
        acct = await db.create_account("Tier Co")
        owner = await db.create_user_with_email(
            email="o@x.com", password_hash=_hash_password("pw"),
            account_id=acct.id, role=Role.OWNER,
        )
        ho = _headers(owner, acct, "owner")
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
            # Baseline: Full admin seeds from base + grants (has can_manage_permissions);
            # Standard admin does not.
            d = (await c.get(PERMS, headers=ho)).json()
            assert d["current"]["admin"]["can_manage_permissions"] is False
            assert d["current"]["admin__manager"]["can_manage_permissions"] is True
            assert "admin" in d["tiers"] and d["tiers"]["admin"]["senior_label"] == "Full admin"

            # Edit STANDARD admin: grant a flag it lacks (companies — NOT a tier grant).
            r = await c.put(PERMS, headers=ho, json={"role": "admin", "permissions": {"can_manage_companies": True}})
            assert r.status_code == 200, r.text
            # Edit FULL admin (senior): revoke can_manage_permissions + keep companies off.
            r = await c.put(PERMS, headers=ho, json={"role": "admin", "tier": "senior",
                                                     "permissions": {"can_manage_permissions": False, "can_manage_companies": False}})
            assert r.status_code == 200, r.text

            d2 = (await c.get(PERMS, headers=ho)).json()
            # Standard admin kept ITS edit; Full admin unaffected by it (same flag, different value).
            assert d2["current"]["admin"]["can_manage_companies"] is True
            assert d2["current"]["admin__manager"]["can_manage_companies"] is False
            # Full admin's own edit applied; Standard admin unaffected by it.
            assert d2["current"]["admin__manager"]["can_manage_permissions"] is False
            assert d2["current"]["admin"]["can_manage_permissions"] is False

    async def test_full_admin_user_resolves_senior_row(self, api):
        app, db = api
        from interfaces.api.auth import create_jwt, _hash_password
        from capabilities.permissions.roles import get_user_permissions
        acct = await db.create_account("Tier Co2")
        owner = await db.create_user_with_email(
            email="o2@x.com", password_hash=_hash_password("pw"),
            account_id=acct.id, role=Role.OWNER,
        )
        ho = _headers(owner, acct, "owner")
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
            # Owner revokes can_manage_permissions from the FULL admin tier.
            await c.put(PERMS, headers=ho, json={"role": "admin", "tier": "senior",
                                                 "permissions": {"can_manage_permissions": False}})
        # A full-admin user (admin + is_manager) resolves the senior tier's stored row.
        full = await get_user_permissions(Role.ADMIN, acct.id, is_manager=True)
        std = await get_user_permissions(Role.ADMIN, acct.id, is_manager=False)
        assert full.can_manage_permissions is False          # the owner's senior-tier edit
        assert full.can_manage_integrations is True           # still has other grants
        assert std.can_manage_permissions is False            # standard never had it
        assert std.can_manage_integrations is False           # standard base unchanged

    async def test_co_owner_independent_from_primary(self, api):
        app, db = api
        from interfaces.api.auth import _hash_password
        from capabilities.permissions.roles import get_user_permissions
        acct = await db.create_account("Owner Split Co")
        owner = await db.create_user_with_email(
            email="o4@x.com", password_hash=_hash_password("pw"),
            account_id=acct.id, role=Role.OWNER,
        )
        ho = _headers(owner, acct, "owner")
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
            d = (await c.get(PERMS, headers=ho)).json()
            # Both owner tiers start full (co-owner seeded from the full owner).
            assert d["current"]["owner"]["can_manage_billing"] is True
            assert d["current"]["owner__co"]["can_manage_billing"] is True
            # Restrict the CO-OWNER: revoke billing (co-owner is NOT protected).
            r = await c.put(PERMS, headers=ho, json={"role": "owner", "tier": "co",
                                                     "permissions": {"can_manage_billing": False}})
            assert r.status_code == 200, r.text
            d2 = (await c.get(PERMS, headers=ho)).json()
            # Co-owner lost billing; the PRIMARY owner still has it (independent +
            # escape-hatch-protected) — this is the reported bug, now fixed.
            assert d2["current"]["owner__co"]["can_manage_billing"] is False
            assert d2["current"]["owner"]["can_manage_billing"] is True
        # Enforcement: a co-owner USER resolves owner__co (no billing); the
        # primary owner keeps it.
        co = await get_user_permissions(Role.OWNER, acct.id, is_primary_owner=False)
        prim = await get_user_permissions(Role.OWNER, acct.id, is_primary_owner=True)
        assert co.can_manage_billing is False
        assert prim.can_manage_billing is True
