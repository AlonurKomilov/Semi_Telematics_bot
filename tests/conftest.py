"""Shared fixtures for the Semi Telematics Bot test suite."""

from __future__ import annotations

import os
import sys

# Ensure encryption is not active during tests (unless explicitly tested)
os.environ.setdefault("ENCRYPTION_KEY", "")

import pytest_asyncio

# Ensure project root is on sys.path
sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

from database import Database, Role


@pytest_asyncio.fixture
async def db(tmp_path):
    """Provide an initialised in-memory-like Database (temp file)."""
    db_path = str(tmp_path / "test.db")
    database = Database(db_path, pool_size=1)
    await database.initialize()
    yield database
    await database.close()


@pytest_asyncio.fixture
async def seeded_db(db: Database):
    """Database pre-loaded with one account, one company, and one owner user."""
    account = await db.create_account("Test Fleet Co")
    company = await db.add_company(
        account_id=account.id,
        code="TFC",
        samsara_api_key="samsara_api_test_key_123",
        display_name="Test Fleet",
    )
    owner = await db.create_user(
        telegram_id=100001,
        account_id=account.id,
        role=Role.OWNER,
    )
    return {
        "db": db,
        "account": account,
        "company": company,
        "owner": owner,
    }


# ── Core infrastructure fixtures ─────────────────────────────────

@pytest_asyncio.fixture
async def core_platform(tmp_path):
    """Wire core.platform with a temp DB — use for tests that need the platform layer.

    Yields the Database instance.  Restores original core.platform state on teardown.
    """
    import core.platform as _cp
    from database.tenant_router import LegacyRouter

    db_path = str(tmp_path / "core_platform_test.db")
    database = Database(db_path, pool_size=1)
    await database.initialize()

    _old_db = _cp._db
    _old_router = _cp._router
    _cp._db = database
    _cp._router = LegacyRouter(database)

    yield database

    _cp._db = _old_db
    _cp._router = _old_router
    await database.close()


@pytest_asyncio.fixture
async def tenant_registry(core_platform):
    """Provide a TenantRegistry backed by the test core_platform."""
    from core.registry import TenantRegistry

    registry = TenantRegistry()
    yield registry
    await registry.close_all()
