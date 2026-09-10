"""The customer's plan catalog comes from the plan table, and a plan
change reaches the resolver at once.

``GET /billing/plans`` lists every public plan plus the account's own;
a hidden plan cannot be bought by name; the stub checkout — the same
path Stripe's webhook takes — invalidates the account's permissions so
the new plan's mask answers the next request, not the next TTL.
"""

import os

os.environ.setdefault("ENCRYPTION_KEY", "")

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient

from adapters.storage import Role
from interfaces.api.auth import create_jwt


@pytest_asyncio.fixture
async def app(pg_db, monkeypatch):
    db = pg_db
    monkeypatch.setenv("BILLING_PROVIDER", "stub")
    acct = await db.create_account("Catalog Co")               # free
    owner = await db.create_user(930001, acct.id, role=Role.OWNER)
    import infra.platform as _cp
    monkeypatch.setattr(_cp, "_db", db)
    from interfaces.api.app import create_api
    transport = ASGITransport(app=create_api())
    async with AsyncClient(transport=transport, base_url="http://testserver") as client:
        yield {"client": client, "db": db, "acct": acct,
               "hdr": {"Authorization": f"Bearer {create_jwt(owner.telegram_id, acct.id, 'owner')}"}}


@pytest.mark.asyncio
async def test_the_catalog_is_the_public_plans_plus_the_current_one(app):
    r = await app["client"].get("/api/billing/plans", headers=app["hdr"])
    assert r.status_code == 200, r.text
    body = r.json()
    tiers = [p["tier"] for p in body["plans"]]
    assert tiers == ["free", "starter", "pro"], tiers          # free = current (hidden), then public by sort
    by = {p["tier"]: p for p in body["plans"]}
    assert by["free"]["current"] is True and by["free"]["public"] is False
    assert by["starter"]["price_monthly_cents"] == 4900 and by["starter"]["everything"] is True
    assert by["pro"]["included"] and "maintenance" in by["pro"]["included"]
    # the quota shown is the one enforced (interfaces/api/deps.py): the config table until a row sets one
    from infra.config import QUOTA_MAX_COMPANIES, QUOTA_MAX_USERS
    assert by["pro"]["quotas"] == {"max_users": QUOTA_MAX_USERS["pro"], "max_companies": QUOTA_MAX_COMPANIES["pro"]}
    assert by["starter"]["quotas"] == {"max_users": QUOTA_MAX_USERS["starter"], "max_companies": QUOTA_MAX_COMPANIES["starter"]}
    await app["db"].upsert_plan("pro", label="Pro", included=["*"], quotas={"max_users": 42})
    from capabilities.permissions.plans import invalidate_plans as _inv
    _inv()
    by2 = {p["tier"]: p for p in (await app["client"].get("/api/billing/plans", headers=app["hdr"])).json()["plans"]}
    assert by2["pro"]["quotas"] == {"max_users": 42, "max_companies": QUOTA_MAX_COMPANIES["pro"]}
    assert body["current_tier"] == "free"
    # a narrowed public plan lists what it includes; a hidden plan is not offered
    from capabilities.permissions.plans import EXCLUDABLE, invalidate_plans
    await app["db"].upsert_plan("starter", label="Starter", included=[i for i in EXCLUDABLE if i != "maintenance"])
    await app["db"].upsert_plan("pro", label="Pro", included=["*"], public=False)
    invalidate_plans()
    body = (await app["client"].get("/api/billing/plans", headers=app["hdr"])).json()
    by = {p["tier"]: p for p in body["plans"]}
    assert "pro" not in by
    assert by["starter"]["everything"] is False and "maintenance" not in by["starter"]["included"] and "vehicles" in by["starter"]["included"]


@pytest.mark.asyncio
async def test_a_hidden_plan_cannot_be_bought_by_name(app):
    await app["db"].upsert_plan("pro", label="Pro", included=["*"], public=False)
    r = await app["client"].post("/api/billing/checkout", headers=app["hdr"], json={"tier": "pro"})
    assert r.status_code == 400 and "not available" in r.json()["detail"]
    assert (await app["db"].get_account(app["acct"].id)).tier == "free"


@pytest.mark.asyncio
async def test_a_plan_change_reaches_the_resolver_at_once(app):
    """Pro leaves Maintenance out; the account is on Free (everything).
    After checkout to Pro the very next resolve answers for Pro."""
    from capabilities.permissions.plans import EXCLUDABLE, invalidate_plans
    from capabilities.permissions.roles import get_account_permissions
    await app["db"].upsert_plan("pro", label="Pro", included=[i for i in EXCLUDABLE if i != "maintenance"], public=True)
    invalidate_plans()
    assert (await get_account_permissions(Role.OWNER, app["acct"].id)).can_view_maintenance   # cached now
    r = await app["client"].post("/api/billing/checkout", headers=app["hdr"], json={"tier": "pro"})
    assert r.status_code == 200, r.text
    assert (await app["db"].get_account(app["acct"].id)).tier == "pro"
    assert not (await get_account_permissions(Role.OWNER, app["acct"].id)).can_view_maintenance
    # and the subscription recorded the row's prices
    sub = await app["db"].get_subscription(app["acct"].id)
    assert sub["monthly_base_usd"] == 9900 and sub["base_vehicles"] == 10
