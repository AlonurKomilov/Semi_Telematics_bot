"""Shared fixtures for the Semi Telematics Bot test suite."""

from __future__ import annotations

import os
import sys
import tempfile

# Ensure encryption is not active during tests (unless explicitly tested)
os.environ.setdefault("ENCRYPTION_KEY", "")

import pytest
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
