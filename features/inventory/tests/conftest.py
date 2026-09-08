"""Fixtures for the inventory suites."""

from __future__ import annotations

import pytest_asyncio


@pytest_asyncio.fixture
async def api_app(pg_db):
    """The real API against a scratch database — the same shape the auth
    suites use, so a route test sees the app as it is actually built."""
    import infra.platform as _cp
    _cp._db = pg_db
    from interfaces.api.app import create_api
    return create_api(), pg_db
