"""Tests for the operator-driven account creation endpoint.

Covers:
- POST /system/accounts happy path (creates account + owner)
- Duplicate-email rejection
- Optional comp grant applied in same call
- Password vs invite onboarding modes
- Pydantic validation guards
"""
from __future__ import annotations

import os
import sys

os.environ.setdefault("ENCRYPTION_KEY", "")
os.environ.setdefault("JWT_SECRET", "test-secret-32-chars-or-more-please-aaaaaaaaaaaa")
sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

import pytest_asyncio
from httpx import AsyncClient, ASGITransport


@pytest_asyncio.fixture
async def api_setup(pg_db, monkeypatch):
    """Boots the API with the pg_db as platform DB; stubs the operator gate."""
    monkeypatch.setenv("SYSTEM_OWNER_IDS", "12345")

    import infra.platform as _cp
    _cp._db = pg_db

    from interfaces.api.app import create_api
    app = create_api()

    # Operator gate stub — endpoint reads telegram_id from the dep return.
    from interfaces.api import deps as _deps
    async def _fake_operator():
        return {"telegram_id": 12345, "role": "system_owner", "account_id": 0}
    app.dependency_overrides[_deps.require_system_owner] = _fake_operator

    return app, pg_db


class TestOperatorCreateAccount:
    async def test_happy_path_with_invite(self, api_setup):
        app, db = api_setup
        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://test"
        ) as c:
            r = await c.post("/api/system/accounts", json={
                "company_name": "Beta Trucks LLC",
                "owner_email":  "beta.owner@example.com",
                "owner_display_name": "Beta Owner",
            })
        assert r.status_code == 201, r.text
        body = r.json()
        assert body["account_id"]
        assert body["owner_email"] == "beta.owner@example.com"
        assert body["provisioned_password"] is False
        assert body["comp"] is None
        acct = await db.get_account(body["account_id"])
        assert acct is not None
        assert acct.name == "Beta Trucks LLC"

    async def test_happy_path_with_password(self, api_setup):
        app, _ = api_setup
        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://test"
        ) as c:
            r = await c.post("/api/system/accounts", json={
                "company_name": "Gamma Logistics",
                "owner_email":  "gamma.owner@example.com",
                "password":     "supersecret123",
            })
        assert r.status_code == 201, r.text
        assert r.json()["provisioned_password"] is True

    async def test_optional_comp_grant_applied(self, api_setup):
        app, db = api_setup
        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://test"
        ) as c:
            r = await c.post("/api/system/accounts", json={
                "company_name": "Comped Carriers",
                "owner_email":  "comped.owner@example.com",
                "grant_comp":   {"months": 6, "reason": "Sales handoff"},
            })
        assert r.status_code == 201, r.text
        body = r.json()
        assert body["comp"] is not None
        assert body["comp"]["months"] == 6
        sub = await db.get_subscription(body["account_id"])
        assert sub is not None
        assert sub.get("is_comped")

    async def test_rejects_duplicate_email(self, api_setup):
        app, _ = api_setup
        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://test"
        ) as c:
            await c.post("/api/system/accounts", json={
                "company_name": "First Co",
                "owner_email":  "shared@example.com",
            })
            r2 = await c.post("/api/system/accounts", json={
                "company_name": "Second Co",
                "owner_email":  "shared@example.com",
            })
        assert r2.status_code == 409, r2.text

    async def test_rejects_bad_email(self, api_setup):
        app, _ = api_setup
        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://test"
        ) as c:
            r = await c.post("/api/system/accounts", json={
                "company_name": "Bad Email Co",
                "owner_email":  "not-an-email",
            })
        assert r.status_code == 422

    async def test_password_too_short_validation(self, api_setup):
        app, _ = api_setup
        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://test"
        ) as c:
            r = await c.post("/api/system/accounts", json={
                "company_name": "Shorty Co",
                "owner_email":  "shorty@example.com",
                "password":     "abc",
            })
        # Pydantic enforces min_length=8 → 422
        assert r.status_code == 422

    async def test_comp_months_clamped_by_pydantic(self, api_setup):
        app, _ = api_setup
        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://test"
        ) as c:
            r = await c.post("/api/system/accounts", json={
                "company_name": "Crazy Comp Co",
                "owner_email":  "crazy@example.com",
                "grant_comp":   {"months": 200, "reason": "test"},
            })
        # months has le=24 in the schema.
        assert r.status_code == 422
