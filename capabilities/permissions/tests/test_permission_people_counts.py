"""Headcount per role — the blast radius the Permissions page shows.

A permission toggled on a role seven people hold and the same toggle on a
role nobody holds used to look identical on screen.  The roles payload now
carries ``people`` so each tab can say how many it governs.  Pins:

  • the count is per ACCOUNT — another tenant's staff never leak into it,
  • only ACTIVE users count (a deactivated account holds nothing today),
  • it is a COUNT, never a name: this page has no business knowing who,
  • a role nobody holds is absent from the map, and the page renders the
    zero — so "not loaded" and "nobody" stay distinguishable.
"""
from __future__ import annotations

import os
import sys

os.environ.setdefault("ENCRYPTION_KEY", "test-key-32-chars-min-aaaaaaaaaaaaaaaaaaaa")
os.environ.setdefault("JWT_SECRET", "test-secret-32-chars-or-more-please-aaaaaaaaaaaa")
os.environ.setdefault("OBJECT_STORE_BACKEND", "disk")
os.environ.setdefault("OBJECT_STORE_ROOT", "/tmp/people_count_store")

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


async def _user(db, acct_id, role, email, active=True):
    from interfaces.api.auth import _hash_password
    u = await db.create_user_with_email(
        email=email, password_hash=_hash_password("password12345"),
        account_id=acct_id, role=role, display_name=role.value.title(),
    )
    if not active:
        await db.update_user(u.id, is_active=False)
    return u


class TestPeopleCounts:
    async def test_counts_are_per_account_and_active_only(self, api):
        app, db = api
        acct = await db.create_account("Counted Co")
        other = await db.create_account("Other Co")
        owner = await _user(db, acct.id, Role.OWNER, f"o.{acct.id}@x.com")
        await _user(db, acct.id, Role.FLEET, f"f1.{acct.id}@x.com")
        await _user(db, acct.id, Role.FLEET, f"f2.{acct.id}@x.com")
        # Deactivated: holds no permissions today, so it must not inflate
        # the number an owner reads before flipping a switch.
        await _user(db, acct.id, Role.FLEET, f"f3.{acct.id}@x.com", active=False)
        # A different tenant's fleet staff must never reach this count.
        await _user(db, other.id, Role.FLEET, f"x1.{other.id}@x.com")
        await _user(db, other.id, Role.FLEET, f"x2.{other.id}@x.com")

        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
            body = (await c.get(PERMS, headers=_headers(owner, acct, "owner"))).json()

        people = body["people"]
        assert people["fleet"] == 2, people
        assert people["owner"] == 1, people

    async def test_a_role_nobody_holds_is_simply_absent(self, api):
        """Absent, not zero: the page fills the zero, which keeps 'still
        loading' and 'nobody holds this' from rendering the same way."""
        app, db = api
        acct = await db.create_account("Sparse Co")
        owner = await _user(db, acct.id, Role.OWNER, f"o.{acct.id}@x.com")

        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
            body = (await c.get(PERMS, headers=_headers(owner, acct, "owner"))).json()

        assert body["people"] == {"owner": 1}, body["people"]

    async def test_it_is_a_count_and_never_a_name(self, api):
        """The Permissions page needs the blast radius, not the roster —
        a name here would put personal data on a page that never asks for
        it and cannot show it."""
        app, db = api
        acct = await db.create_account("Private Co")
        owner = await _user(db, acct.id, Role.OWNER, f"o.{acct.id}@x.com")
        await _user(db, acct.id, Role.SAFETY, f"sam.smith.{acct.id}@x.com")

        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
            body = (await c.get(PERMS, headers=_headers(owner, acct, "owner"))).json()

        assert all(isinstance(v, int) for v in body["people"].values())
        assert "sam.smith" not in str(body["people"])

    async def test_a_failed_count_omits_the_key_rather_than_sending_zero(
            self, api, monkeypatch):
        """A count is decoration; the matrix is the page.  But the failure
        must be TELLABLE: the caller is themselves an active user, so an
        empty map is never a real answer — sent as {} the page would fill
        every tab with a confident 0 and dim them, claiming a toggle
        affects nobody at the one moment nobody knows.  Absent = unknown.
        """
        app, db = api
        acct = await db.create_account("Fragile Co")
        owner = await _user(db, acct.id, Role.OWNER, f"o.{acct.id}@x.com")

        async def boom(_account_id):
            raise RuntimeError("count query exploded")

        monkeypatch.setattr(db, "count_users_by_role", boom, raising=True)
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
            r = await c.get(PERMS, headers=_headers(owner, acct, "owner"))

        assert r.status_code == 200
        body = r.json()
        assert "people" not in body, "an absent count must not read as zero people"
        assert body["current"]["owner"]["can_manage_permissions"] is True

    async def test_a_permissions_failure_still_fails_loudly(self, api, monkeypatch):
        """The swallow above must stay wrapped around the COUNT alone.  If
        it ever widened to cover the permission resolution, this endpoint
        would answer 200 with a half-empty matrix — an owner editing rights
        that aren't the real ones."""
        app, db = api
        acct = await db.create_account("Loud Co")
        owner = await _user(db, acct.id, Role.OWNER, f"o.{acct.id}@x.com")

        import capabilities.permissions.router as prouter

        async def boom(*a, **kw):
            raise RuntimeError("permission resolution exploded")

        monkeypatch.setattr(prouter, "get_account_permissions", boom, raising=True)
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
            try:
                r = await c.get(PERMS, headers=_headers(owner, acct, "owner"))
            except RuntimeError:
                return          # propagated past the app — loud enough
        assert r.status_code >= 500, (
            "a permissions failure must never be swallowed into a 200")
