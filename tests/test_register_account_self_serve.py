"""Tests for the self-serve company registration endpoint.

Covers the security/UX contract:
- Email-verify gate (no JWT until verification)
- 14-day auto-trial comp granted on creation
- Duplicate-email rejection
- Email format validation
- Response shape matches the dashboard's expected ``status: 'registered'`` flow
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
async def api_app(pg_db, monkeypatch):
    import infra.platform as _cp
    _cp._db = pg_db
    from interfaces.api.app import create_api
    app = create_api()
    return app, pg_db


class TestSelfServeRegister:
    async def test_happy_path_no_jwt_returned(self, api_app):
        """The contract: 200 OK, status=registered, NO access_token, NO auth cookie."""
        app, db = api_app
        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://test"
        ) as c:
            r = await c.post("/api/auth/register-account", json={
                "company_name": "Self Serve Co",
                "email":        "self.serve@example.com",
                "password":     "verylongpassword",
                "display_name": "Self Owner",
            })
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["status"] == "registered"
        assert body["verification_required"] is True
        assert body["email"] == "self.serve@example.com"
        assert "access_token" not in body
        # No auth cookie — pre-verification users must not be signed in.
        assert "Set-Cookie" not in r.headers or "access_token" not in r.headers.get("Set-Cookie", "")

        # Account + user actually exist
        acct = await db.get_account(body["account_id"])
        assert acct is not None

    async def test_auto_trial_comp_granted(self, api_app):
        app, db = api_app
        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://test"
        ) as c:
            r = await c.post("/api/auth/register-account", json={
                "company_name": "Trial Co",
                "email":        "trial.owner@example.com",
                "password":     "longpassword01",
            })
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["trial"] is not None
        assert body["trial"]["days"] == 14
        assert body["trial"]["expires_at"]

        # Subscription row should reflect the comp grant.
        sub = await db.get_subscription(body["account_id"])
        assert sub is not None
        assert sub.get("is_comped")
        assert sub.get("comp_reason") == "14-day auto trial"

    async def test_rejects_duplicate_email(self, api_app):
        app, _ = api_app
        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://test"
        ) as c:
            await c.post("/api/auth/register-account", json={
                "company_name": "First",
                "email":        "dup@example.com",
                "password":     "longpassword01",
            })
            r2 = await c.post("/api/auth/register-account", json={
                "company_name": "Second",
                "email":        "dup@example.com",
                "password":     "longpassword02",
            })
        assert r2.status_code == 409

    async def test_rejects_bad_email(self, api_app):
        app, _ = api_app
        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://test"
        ) as c:
            r = await c.post("/api/auth/register-account", json={
                "company_name": "Bad Email Co",
                "email":        "not-an-email",
                "password":     "longpassword01",
            })
        assert r.status_code == 422

    async def test_password_too_short_validation(self, api_app):
        app, _ = api_app
        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://test"
        ) as c:
            r = await c.post("/api/auth/register-account", json={
                "company_name": "Shorty Co",
                "email":        "shorty@example.com",
                "password":     "abc",
            })
        assert r.status_code == 422
