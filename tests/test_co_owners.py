"""Co-owners: a protected PRIMARY owner + promotable co-owners.

Covers the whole feature end-to-end:
  * signup owner is the primary owner (is_primary_owner);
  * promote guards (primary-only, password, target must be Admin);
  * promote confirm applies Admin → co-owner (role=owner, is_primary_owner=0);
  * demote co-owner → Admin; co-owners can't manage owners;
  * the primary owner is immutable (role change / deactivate / demote);
  * destructive account actions are primary-owner only.
"""
from __future__ import annotations

import os
import sys

os.environ.setdefault("ENCRYPTION_KEY", "test-key-32-chars-min-aaaaaaaaaaaaaaaaaaaa")
os.environ.setdefault("JWT_SECRET", "test-secret-32-chars-or-more-please-aaaaaaaaaaaa")
os.environ.setdefault("OBJECT_STORE_BACKEND", "disk")
os.environ.setdefault("OBJECT_STORE_ROOT", "/tmp/co_owner_test_store")
sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

import pytest_asyncio
from httpx import AsyncClient, ASGITransport

from adapters.storage import Role

PW = "correct-horse-battery-staple"
API = "/api/admin/users"


@pytest_asyncio.fixture
async def api(pg_db):
    import infra.platform as cp
    cp._db = pg_db
    from interfaces.api.app import create_api
    return create_api(), pg_db


def _headers(db_user, acct, role: str):
    from interfaces.api.auth import create_jwt
    token = create_jwt(db_user.telegram_id or 0, acct.id, role, user_id=db_user.id)
    return {"Authorization": f"Bearer {token}"}


async def _setup(db):
    """Primary owner (with a password + email) + an Admin, same account."""
    from interfaces.api.auth import _hash_password
    acct = await db.create_account("Co Owner Co")
    owner = await db.create_user_with_email(
        email="owner@x.com", password_hash=_hash_password(PW),
        account_id=acct.id, role=Role.OWNER, display_name="Prim",
    )
    admin = await db.create_user_with_email(
        email="admin@x.com", password_hash=_hash_password(PW),
        account_id=acct.id, role=Role.ADMIN, display_name="Ada",
    )
    return acct, owner, admin


class TestCoOwners:
    async def test_signup_owner_is_primary(self, api):
        _, db = api
        _, owner, admin = await _setup(db)
        assert owner.is_primary_owner is True
        assert admin.is_primary_owner is False

    async def test_promotion_code_db_layer(self, api):
        _, db = api
        acct, owner, admin = await _setup(db)
        code = await db.create_owner_promotion_code(acct.id, owner.id, admin.id)
        # wrong initiator → None
        assert await db.consume_owner_promotion_code(acct.id, admin.id, code) is None
        # wrong code → None
        code2 = await db.create_owner_promotion_code(acct.id, owner.id, admin.id)
        assert await db.consume_owner_promotion_code(acct.id, owner.id, "000000") is None
        # correct → returns target, and is single-use
        assert await db.consume_owner_promotion_code(acct.id, owner.id, code2) == admin.id
        assert await db.consume_owner_promotion_code(acct.id, owner.id, code2) is None

    async def test_promote_guards(self, api):
        app, db = api
        acct, owner, admin = await _setup(db)
        ho = _headers(owner, acct, "owner")
        ha = _headers(admin, acct, "admin")
        drv = await db.create_user(999001, acct.id, role=Role.DRIVER)
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
            # a non-primary (admin) can't promote
            r = await c.post(f"{API}/{admin.id}/promote-owner", headers=ha, json={"password": PW})
            assert r.status_code == 403
            # wrong password
            r = await c.post(f"{API}/{admin.id}/promote-owner", headers=ho, json={"password": "nope"})
            assert r.status_code == 403
            # target must be an Admin (a driver can't be promoted)
            r = await c.post(f"{API}/{drv.id}/promote-owner", headers=ho, json={"password": PW})
            assert r.status_code == 400
            # valid → code sent
            r = await c.post(f"{API}/{admin.id}/promote-owner", headers=ho, json={"password": PW})
            assert r.status_code == 200, r.text
            assert r.json()["status"] == "code_sent"

    async def test_promote_confirm_then_demote(self, api):
        app, db = api
        acct, owner, admin = await _setup(db)
        ho = _headers(owner, acct, "owner")
        # Email is unconfigured in tests, so mint the code directly.
        code = await db.create_owner_promotion_code(acct.id, owner.id, admin.id)
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
            # wrong code
            r = await c.post(f"{API}/{admin.id}/promote-owner/confirm", headers=ho, json={"code": "000000"})
            assert r.status_code == 400
            # right code → co-owner
            code = await db.create_owner_promotion_code(acct.id, owner.id, admin.id)
            r = await c.post(f"{API}/{admin.id}/promote-owner/confirm", headers=ho, json={"code": code})
            assert r.status_code == 200, r.text
            promoted = await db.get_user(admin.id)
            assert promoted.role == Role.OWNER and promoted.is_primary_owner is False

            # a co-owner is NOT primary → can't manage owners
            hco = _headers(promoted, acct, "owner")
            r = await c.post(f"{API}/{owner.id}/demote-owner", headers=hco, json={"password": PW})
            assert r.status_code == 403

            # the primary owner demotes the co-owner → Admin
            r = await c.post(f"{API}/{admin.id}/demote-owner", headers=ho, json={"password": PW})
            assert r.status_code == 200, r.text
            assert (await db.get_user(admin.id)).role == Role.ADMIN

    async def test_primary_owner_is_immutable(self, api):
        app, db = api
        acct, owner, admin = await _setup(db)
        ho = _headers(owner, acct, "owner")
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
            # can't change the primary owner's role
            r = await c.put(f"{API}/{owner.id}/role", headers=ho, json={"role": "admin"})
            assert r.status_code == 403
            # can't deactivate the primary owner
            r = await c.put(f"{API}/{owner.id}/status", headers=ho, json={"is_active": False})
            assert r.status_code == 403
            # can't "demote" the primary owner (they aren't a co-owner)
            r = await c.post(f"{API}/{owner.id}/demote-owner", headers=ho, json={"password": PW})
            assert r.status_code == 403

    async def test_destructive_account_actions_primary_only(self, api):
        app, db = api
        acct, owner, admin = await _setup(db)
        ho = _headers(owner, acct, "owner")
        code = await db.create_owner_promotion_code(acct.id, owner.id, admin.id)
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
            await c.post(f"{API}/{admin.id}/promote-owner/confirm", headers=ho, json={"code": code})
            co = await db.get_user(admin.id)
            hco = _headers(co, acct, "owner")
            # a co-owner can't start account deletion (primary-only)
            r = await c.post("/api/user/account/delete/request", headers=hco)
            assert r.status_code == 403
