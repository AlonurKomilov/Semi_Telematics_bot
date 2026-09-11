"""The login page's trial promise comes from the plan table, through
the public config — not from copy of its own."""

import os

os.environ.setdefault("ENCRYPTION_KEY", "")

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient


@pytest_asyncio.fixture
async def client(pg_db, monkeypatch):
    import infra.platform as _cp
    monkeypatch.setattr(_cp, "_db", pg_db)
    from interfaces.api.app import create_api
    async with AsyncClient(transport=ASGITransport(app=create_api()), base_url="http://testserver") as c:
        yield c, pg_db


@pytest.mark.asyncio
async def test_the_public_config_says_which_trial_a_signup_gets_or_none(client):
    c, db = client
    r = await c.get("/api/auth/config")
    assert r.status_code == 200, r.text
    assert r.json()["trial"] == {"days": 14, "plan": "pro", "plan_label": "Pro"}      # the seed's flag
    await db.upsert_plan("starter", label="Starter", included=["*"], trial_default=True)
    assert (await c.get("/api/auth/config")).json()["trial"]["plan"] == "starter"
    await db.upsert_plan("starter", label="Starter", included=["*"], trial_default=False)
    assert (await c.get("/api/auth/config")).json()["trial"] is None
