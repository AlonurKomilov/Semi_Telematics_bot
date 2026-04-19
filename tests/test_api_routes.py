"""API route integration tests — auth flow, permission enforcement, data scoping."""

from __future__ import annotations

import os
import sys

# Ensure encryption is not active during tests
os.environ.setdefault("ENCRYPTION_KEY", "")

import pytest
import pytest_asyncio

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

from httpx import ASGITransport, AsyncClient

from database import Database, Role
from api.auth import create_jwt, JWT_SECRET


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest_asyncio.fixture
async def db_and_app(tmp_path):
    """Provide a test DB, seed it, and return (db, FastAPI app, tokens)."""
    db_path = str(tmp_path / "test.db")
    database = Database(db_path)
    await database.initialize()

    # Seed two accounts
    acct_a = await database.create_account("Tenant A")
    acct_b = await database.create_account("Tenant B")
    await database.add_company(acct_a.id, "COMPA", "key_a", "Company A")
    await database.add_company(acct_b.id, "COMPB", "key_b", "Company B")
    owner_a = await database.create_user(1001, acct_a.id, role=Role.OWNER)
    owner_b = await database.create_user(2001, acct_b.id, role=Role.OWNER)
    driver_a = await database.create_user(1002, acct_a.id, role=Role.DRIVER)
    await database.add_maintenance_task(
        acct_a.id, "COMPA", "Truck-A1", "oil_change", "Oil change",
    )

    # Tokens
    token_a = create_jwt(owner_a.telegram_id, acct_a.id, "owner")
    token_b = create_jwt(owner_b.telegram_id, acct_b.id, "owner")
    token_driver = create_jwt(driver_a.telegram_id, acct_a.id, "driver")

    # Patch core.platform so api/deps.py and bot.state lazy proxy resolve correctly
    import core.platform as _cp
    from database.tenant_router import LegacyRouter
    _old_router = _cp._router
    _old_cp_db = _cp._db
    _cp._router = LegacyRouter(database)
    _cp._db = database

    # Import app after patching
    from api.app import create_api
    app = create_api()

    yield {
        "db": database,
        "app": app,
        "acct_a": acct_a,
        "acct_b": acct_b,
        "token_a": token_a,
        "token_b": token_b,
        "token_driver": token_driver,
    }

    _cp._router = _old_router
    _cp._db = _old_cp_db
    await database.close()


def _headers(token: str) -> dict:
    return {"Authorization": f"Bearer {token}"}


# ---------------------------------------------------------------------------
# Auth tests
# ---------------------------------------------------------------------------

class TestAuth:
    async def test_no_token_returns_422(self, db_and_app):
        app = db_and_app["app"]
        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://test"
        ) as client:
            r = await client.get("/api/user/me")
            assert r.status_code == 422  # Missing Authorization header

    async def test_bad_token_returns_401(self, db_and_app):
        app = db_and_app["app"]
        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://test"
        ) as client:
            r = await client.get(
                "/api/user/me", headers={"Authorization": "Bearer invalid.token.here"}
            )
            assert r.status_code == 401

    async def test_valid_token_returns_user(self, db_and_app):
        app = db_and_app["app"]
        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://test"
        ) as client:
            r = await client.get("/api/user/me", headers=_headers(db_and_app["token_a"]))
            assert r.status_code == 200
            data = r.json()
            assert data["role"] == "owner"

    async def test_refresh_token(self, db_and_app):
        app = db_and_app["app"]
        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://test"
        ) as client:
            r = await client.post(
                "/api/auth/refresh", headers=_headers(db_and_app["token_a"])
            )
            assert r.status_code == 200
            data = r.json()
            assert "access_token" in data
            assert data["user"]["role"] == "owner"


# ---------------------------------------------------------------------------
# Permission tests
# ---------------------------------------------------------------------------

class TestPermissions:
    async def test_driver_cannot_manage_companies(self, db_and_app):
        app = db_and_app["app"]
        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://test"
        ) as client:
            r = await client.get(
                "/api/admin/companies",
                headers=_headers(db_and_app["token_driver"]),
            )
            assert r.status_code == 403

    async def test_owner_can_list_companies(self, db_and_app):
        app = db_and_app["app"]
        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://test"
        ) as client:
            r = await client.get(
                "/api/admin/companies",
                headers=_headers(db_and_app["token_a"]),
            )
            assert r.status_code == 200
            data = r.json()
            # Should only see tenant A's companies
            assert data["count"] == 1
            assert data["companies"][0]["code"] == "COMPA"


# ---------------------------------------------------------------------------
# Cross-tenant isolation — API level
# ---------------------------------------------------------------------------

class TestCrossTenantAPI:
    async def test_tenant_b_cannot_see_tenant_a_maintenance(self, db_and_app):
        app = db_and_app["app"]
        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://test"
        ) as client:
            r = await client.get(
                "/api/maintenance/tasks",
                headers=_headers(db_and_app["token_b"]),
            )
            assert r.status_code == 200
            data = r.json()
            assert data["count"] == 0  # B has no maintenance tasks

    async def test_tenant_b_cannot_delete_tenant_a_company(self, db_and_app):
        app = db_and_app["app"]
        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://test"
        ) as client:
            # Company ID 1 belongs to tenant A
            r = await client.delete(
                "/api/admin/companies/1",
                headers=_headers(db_and_app["token_b"]),
            )
            assert r.status_code == 404  # Not found because scoped

    async def test_tenant_a_can_delete_own_company(self, db_and_app):
        app = db_and_app["app"]
        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://test"
        ) as client:
            r = await client.delete(
                "/api/admin/companies/1",
                headers=_headers(db_and_app["token_a"]),
            )
            assert r.status_code == 200


# ---------------------------------------------------------------------------
# Maintenance CRUD
# ---------------------------------------------------------------------------

class TestMaintenanceAPI:
    async def test_create_and_get_task(self, db_and_app):
        app = db_and_app["app"]
        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://test"
        ) as client:
            r = await client.post(
                "/api/maintenance/tasks",
                headers=_headers(db_and_app["token_a"]),
                json={
                    "vehicle_name": "TruckNew",
                    "task_type": "brake_check",
                    "description": "Check brakes",
                },
            )
            assert r.status_code == 200
            task_id = r.json()["id"]

            r = await client.get(
                f"/api/maintenance/tasks/{task_id}",
                headers=_headers(db_and_app["token_a"]),
            )
            assert r.status_code == 200
            assert r.json()["task_type"] == "brake_check"

    async def test_tenant_b_cannot_get_tenant_a_task(self, db_and_app):
        app = db_and_app["app"]
        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://test"
        ) as client:
            # Task 1 belongs to tenant A
            r = await client.get(
                "/api/maintenance/tasks/1",
                headers=_headers(db_and_app["token_b"]),
            )
            assert r.status_code == 404
