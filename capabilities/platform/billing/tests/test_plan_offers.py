"""A hidden plan opened to one account.

After a Contact-Sales conversation the operator agrees terms with ONE
customer.  That plan is an ordinary hidden row; an offer row is what
puts it on that account's Billing page and lets that account pay.

What these pin, because each is a way money or visibility goes wrong:
the door opens for the offered account and NOBODY else (the customer
list, both checkouts, the plan-switch path); the gate is asked BEFORE a
live subscriber's in-place switch, so a Stripe customer cannot slip past
it; an offer moves no tier and no subscription; an operator answering a
case cannot attach it to a different account; and under Stripe a private
plan needs a Price of its own, because the webhook reads the tier back
from the Price.
"""

from __future__ import annotations

import os

os.environ.setdefault("ENCRYPTION_KEY", "")
os.environ.setdefault("JWT_SECRET", "test-secret-32-chars-or-more-please-aaaaaaaaaaaa")

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient

from capabilities.platform.billing.offers import offer_refusal, purchasable_plan
from capabilities.platform.billing.stripe_client import StripeBillingProvider
from capabilities.platform.billing.stub import StubBillingProvider


def _item(item_id, price_id, amount):
    return {"id": item_id, "price": {"id": price_id, "unit_amount": amount, "currency": "usd",
                                     "recurring": {"interval": "month", "interval_count": 1}}}


class _FakeStripe:
    def __init__(self, live_sub):
        self.calls = []; fake = self; self.state = dict(live_sub)
        class error:  # noqa: N801
            class StripeError(Exception): ...
        class Subscription:
            @staticmethod
            def retrieve(sid, expand=None): fake.calls.append(("retrieve", sid)); return dict(fake.state)
            @staticmethod
            def modify(sid, **kw):
                fake.calls.append(("modify", sid, kw))
                base = kw["items"][0]
                fake.state = {**fake.state, "items": {"data": [_item(base["id"], base["price"], 50000)]}}
                return dict(fake.state)
        class checkout:  # noqa: N801
            class Session:
                @staticmethod
                def create(**kw): fake.calls.append(("Session.create", kw)); return {"url": "https://stripe/checkout", "id": "cs_1"}
        class Customer:
            @staticmethod
            def create(**kw): fake.calls.append(("Customer.create", kw)); return {"id": "cus_new"}
        self.Subscription, self.checkout, self.Customer, self.error = Subscription, checkout, Customer, error


async def _private_plan(db, tier="premier"):
    await db.upsert_plan(tier, label="Premier", included=["*"], price_monthly_cents=50000,
                         base_vehicles=60, stripe_price_id="price_premier", public=False)


# ── the store ──────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_an_offer_is_one_row_and_revoking_removes_it(pg_db):
    db = pg_db
    await _private_plan(db)
    acct = await db.create_account("Premier Trucking")
    first = await db.offer_plan("premier", acct.id, request_id=None, created_by="operator:1")
    again = await db.offer_plan("premier", acct.id, created_by="operator:2")
    assert first["created"] is True and again["created"] is False
    assert again["created_by"] == "operator:1", "the second press did not overwrite the first"
    assert await db.plan_offered_to("premier", acct.id)
    assert await db.offered_tiers_for_account(acct.id) == ["premier"]
    assert await db.revoke_plan_offer("premier", acct.id) is True
    assert await db.revoke_plan_offer("premier", acct.id) is False
    assert not await db.plan_offered_to("premier", acct.id)


# ── the one gate ───────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_the_gate_opens_for_the_offered_account_and_nobody_else(pg_db):
    db = pg_db
    await _private_plan(db)
    await db.upsert_plan("pro", label="Pro", included=["*"], price_monthly_cents=4900, public=True)
    mine = await db.create_account("Offered Co")
    other = await db.create_account("Other Co")
    await db.offer_plan("premier", mine.id, created_by="operator:1")

    assert (await purchasable_plan(db, "pro", other.id))["tier"] == "pro", "public is for everyone"
    assert (await purchasable_plan(db, "premier", mine.id))["tier"] == "premier"
    assert await purchasable_plan(db, "premier", other.id) is None, "hidden stays hidden for the rest"
    assert await purchasable_plan(db, "nonesuch", mine.id) is None
    await db.revoke_plan_offer("premier", mine.id)
    assert await purchasable_plan(db, "premier", mine.id) is None, "revoking closes the door"


@pytest.mark.asyncio
async def test_a_live_stripe_subscriber_is_refused_before_the_switch_is_attempted(db, monkeypatch):
    """The gate must sit BEFORE the in-place switch: a Stripe customer
    asking for a hidden plan by name gets the same refusal as a new one,
    and Stripe is never asked to move anything."""
    monkeypatch.setenv("STRIPE_PRICE_EXTRA_VEHICLE", "price_extra")
    await _private_plan(db)
    await db.upsert_plan("starter", label="Starter", included=["*"], stripe_price_id="price_starter", public=True)
    acct = await db.create_account("Live Co", tier="starter")
    await db.get_or_create_subscription(acct.id)
    await db.update_subscription(acct.id, tier="starter", provider="stripe", provider_customer_id="cus_1",
                                 provider_subscription_id="sub_live", status="active",
                                 provider_base_price_id="price_starter")
    live = {"id": "sub_live", "status": "active", "current_period_end": 1800000000,
            "items": {"data": [_item("si_base", "price_starter", 4900)]}}
    fake = _FakeStripe(live)
    monkeypatch.setattr("capabilities.platform.billing.stripe_client._stripe", lambda: fake)
    provider = StripeBillingProvider()

    with pytest.raises(ValueError, match="not available"):
        await provider.create_checkout_session(acct.id, db, "premier", "https://s", "https://c")
    assert not [c for c in fake.calls if c[0] == "modify"], "Stripe was asked to move a plan it may not sell"

    await db.offer_plan("premier", acct.id, created_by="operator:1")
    out = await provider.create_checkout_session(acct.id, db, "premier", "https://s", "https://c")
    assert [c for c in fake.calls if c[0] == "modify"], "with the offer, the live subscriber switches in place"
    assert (await db.get_subscription(acct.id))["tier"] == "premier"
    assert out.get("switched") or out.get("url"), out


@pytest.mark.asyncio
async def test_the_stub_checkout_asks_the_same_gate(pg_db):
    db = pg_db
    await _private_plan(db)
    mine = await db.create_account("Stub Mine")
    other = await db.create_account("Stub Other")
    await db.offer_plan("premier", mine.id, created_by="operator:1")
    provider = StubBillingProvider()
    with pytest.raises(ValueError, match="not available"):
        await provider.create_checkout_session(other.id, db, "premier", "https://s", "https://c")
    await provider.create_checkout_session(mine.id, db, "premier", "https://s", "https://c")
    assert (await db.get_account(mine.id)).tier == "premier"
    assert (await db.get_account(other.id)).tier != "premier"


@pytest.mark.asyncio
async def test_an_offer_moves_no_tier_and_no_subscription(pg_db):
    """The offer is a door, not a move.  What the customer pays is
    decided at checkout; until then nothing about the account changes."""
    db = pg_db
    await _private_plan(db)
    acct = await db.create_account("Untouched Co", tier="starter")
    await db.get_or_create_subscription(acct.id)
    before = await db.get_subscription(acct.id)
    await db.offer_plan("premier", acct.id, created_by="operator:1")
    assert (await db.get_account(acct.id)).tier == "starter"
    assert (await db.get_subscription(acct.id))["tier"] == before["tier"]


# ── what may be offered ───────────────────────────────────────────

def test_a_public_plan_or_a_shared_stripe_price_cannot_be_offered():
    hidden = {"tier": "premier", "public": 0, "stripe_price_id": "price_p", "price_monthly_cents": 50000}
    assert "comp" in offer_refusal({**hidden, "price_monthly_cents": 0}, [], provider="stripe"), \
        "a $0 plan is a comp, and the refusal must say where comps are granted"
    assert offer_refusal({**hidden, "public": 1}, [], provider="stripe"), "public: nothing to offer"
    assert "Stripe price" in offer_refusal({**hidden, "stripe_price_id": ""}, [], provider="stripe")
    twin = {"tier": "pro", "public": 1, "stripe_price_id": "price_p"}
    assert "shares" in offer_refusal(hidden, [hidden, twin], provider="stripe")
    assert offer_refusal(hidden, [hidden], provider="stripe") == ""
    assert offer_refusal({**hidden, "stripe_price_id": ""}, [], provider="stub") == "", \
        "the stub charges nothing, so it needs no Price"


# ── the operator's routes ─────────────────────────────────────────

@pytest_asyncio.fixture
async def api(pg_db, monkeypatch):
    monkeypatch.setenv("SYSTEM_OWNER_IDS", "12345")
    import infra.platform as _cp
    _cp._db = pg_db
    from interfaces.api.app import create_api
    app = create_api()
    from interfaces.api import deps as _deps
    async def _operator():
        return {"telegram_id": 12345, "sub": 12345, "role": "system_owner", "account_id": 0}
    app.dependency_overrides[_deps.require_system_owner] = _operator
    return app, pg_db


@pytest.mark.asyncio
async def test_the_operator_offers_a_plan_and_the_case_moves_to_contacted(api):
    app, db = api
    await _private_plan(db)
    acct = await db.create_account("Premier Trucking Group")
    req = await db.create_plan_request(acct.id, "enterprise", contact_email="adam@premier.example",
                                       note="40 companies")
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
        r = await c.post("/api/system/plans/premier/offers",
                         json={"account_id": acct.id, "request_id": int(req["id"])})
        assert r.status_code == 201, r.text
        body = r.json()
        assert body["created"] is True and body["case_number"] == req["case_number"]
        again = await c.post("/api/system/plans/premier/offers", json={"account_id": acct.id})
        assert again.status_code == 201 and again.json()["created"] is False
        plans = (await c.get("/api/system/plans")).json()["plans"]
    premier = next(p for p in plans if p["tier"] == "premier")
    assert [o["account_id"] for o in premier["offered_to"]] == [acct.id]
    assert premier["offered_to"][0]["account_name"] == "Premier Trucking Group"
    assert (await db.get_plan_request(int(req["id"])))["status"] == "contacted"


@pytest.mark.asyncio
async def test_a_case_cannot_be_answered_with_an_offer_to_another_account(api):
    app, db = api
    await _private_plan(db)
    asker = await db.create_account("Asker Co")
    stranger = await db.create_account("Stranger Co")
    req = await db.create_plan_request(asker.id, "enterprise", contact_email="a@asker.example")
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
        r = await c.post("/api/system/plans/premier/offers",
                         json={"account_id": stranger.id, "request_id": int(req["id"])})
    assert r.status_code == 400, r.text
    assert not await db.plan_offered_to("premier", stranger.id)
    assert (await db.get_plan_request(int(req["id"])))["status"] == "open"


@pytest.mark.asyncio
async def test_a_public_plan_is_refused_and_a_revoke_closes_the_door(api):
    app, db = api
    await db.upsert_plan("pro", label="Pro", included=["*"], price_monthly_cents=4900, public=True)
    await _private_plan(db)
    acct = await db.create_account("Revoke Co")
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
        r = await c.post("/api/system/plans/pro/offers", json={"account_id": acct.id})
        assert r.status_code == 409, r.text
        assert (await c.post("/api/system/plans/nonesuch/offers", json={"account_id": acct.id})).status_code == 404
        assert (await c.post("/api/system/plans/premier/offers", json={"account_id": 987654321})).status_code == 404
        assert (await c.post("/api/system/plans/premier/offers", json={"account_id": acct.id})).status_code == 201
        assert await db.plan_offered_to("premier", acct.id)
        assert (await c.delete(f"/api/system/plans/premier/offers/{acct.id}")).status_code == 200
        assert (await c.delete(f"/api/system/plans/premier/offers/{acct.id}")).status_code == 404
    assert not await db.plan_offered_to("premier", acct.id)


# ── the customer's page ───────────────────────────────────────────

@pytest_asyncio.fixture
async def customers(pg_db, monkeypatch):
    """Two accounts, two owners, one API — the same list asked by each."""
    from adapters.storage import Role
    from interfaces.api.auth import create_jwt
    db = pg_db
    monkeypatch.setenv("BILLING_PROVIDER", "stub")
    await _private_plan(db)
    await db.upsert_plan("pro", label="Pro", included=["*"], price_monthly_cents=4900, public=True)
    mine = await db.create_account("Offered Co")
    other = await db.create_account("Neighbour Co")
    mine_owner = await db.create_user(940001, mine.id, role=Role.OWNER)
    other_owner = await db.create_user(940002, other.id, role=Role.OWNER)
    await db.offer_plan("premier", mine.id, created_by="operator:1")
    import infra.platform as _cp
    monkeypatch.setattr(_cp, "_db", db)
    from interfaces.api.app import create_api
    async with AsyncClient(transport=ASGITransport(app=create_api()), base_url="http://testserver") as client:
        yield {
            "client": client, "mine": mine, "other": other,
            "mine_hdr": {"Authorization": f"Bearer {create_jwt(mine_owner.telegram_id, mine.id, 'owner')}"},
            "other_hdr": {"Authorization": f"Bearer {create_jwt(other_owner.telegram_id, other.id, 'owner')}"},
        }


@pytest.mark.asyncio
async def test_the_offered_account_sees_its_plan_and_the_neighbour_does_not(customers):
    c = customers
    mine = {p["tier"]: p for p in (await c["client"].get("/api/billing/plans", headers=c["mine_hdr"])).json()["plans"]}
    other = {p["tier"]: p for p in (await c["client"].get("/api/billing/plans", headers=c["other_hdr"])).json()["plans"]}
    assert "premier" in mine and mine["premier"]["offered"] is True and mine["premier"]["public"] is False
    assert "premier" not in other, "an offer to one account is invisible to the next"
    assert mine["pro"]["offered"] is False, "a public plan is not an offer"
    # and the wire never says who ELSE a plan was offered to
    assert "offered_to" not in mine["premier"]


@pytest.mark.asyncio
async def test_the_neighbour_cannot_check_out_the_offered_plan_by_name(customers):
    c = customers
    r = await c["client"].post("/api/billing/checkout", json={"tier": "premier"}, headers=c["other_hdr"])
    assert r.status_code == 400, r.text
    assert "not available" in r.json()["detail"]
    r = await c["client"].post("/api/billing/checkout", json={"tier": "premier"}, headers=c["mine_hdr"])
    assert r.status_code == 200, r.text


# ── the two ways an offer row could outlive its meaning ────────────

@pytest.mark.asyncio
async def test_making_a_plan_public_withdraws_its_private_offers(api):
    """The trap this closes: a plan goes public, the offer rows stay
    inert behind it, and the day someone hides it again those accounts
    silently get it back — a door nobody remembers opening."""
    app, db = api
    await _private_plan(db)
    acct = await db.create_account("Premier Trucking Group")
    await db.offer_plan("premier", acct.id, created_by="operator:1")
    plan = await db.get_plan("premier")
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
        r = await c.put("/api/system/plans/premier", json={
            "label": plan["label"], "included": ["*"], "quotas": {},
            "price_monthly_cents": plan["price_monthly_cents"],
            "base_vehicles": plan["base_vehicles"], "extra_vehicle_cents": 0,
            "public": True, "sort": 0, "trial_default": False,
        })
        assert r.status_code == 200, r.text
        assert r.json()["offers_withdrawn"] == 1
        assert r.json()["plan"]["offered_to"] == []
    assert not await db.plan_offered_to("premier", acct.id)

    # and hiding it again does NOT bring the old offer back
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
        r = await c.put("/api/system/plans/premier", json={
            "label": plan["label"], "included": ["*"], "quotas": {},
            "price_monthly_cents": plan["price_monthly_cents"],
            "base_vehicles": plan["base_vehicles"], "extra_vehicle_cents": 0,
            "public": False, "sort": 0, "trial_default": False,
        })
        assert r.status_code == 200 and r.json()["offers_withdrawn"] == 0
    assert await purchasable_plan(db, "premier", acct.id) is None


@pytest.mark.asyncio
async def test_purging_an_account_takes_its_offers_with_it(pg_db):
    """The purge finds tenant tables by their ``account_id`` column, so a
    new table joins the sweep by having one — this pins that plan_offers
    does, rather than leaving a row pointing at an account that is gone."""
    db = pg_db
    await _private_plan(db)
    doomed = await db.create_account("Closing Down LLC")
    kept = await db.create_account("Still Here LLC")
    await db.offer_plan("premier", doomed.id, created_by="operator:1")
    await db.offer_plan("premier", kept.id, created_by="operator:1")
    await db.purge_account_data(doomed.id)
    assert not await db.plan_offered_to("premier", doomed.id)
    assert await db.plan_offered_to("premier", kept.id), "the purge took only its own"


def test_every_checkout_asks_the_one_gate_rather_than_reading_the_flag():
    """The rule that must not fork.

    Two providers already answer "may this account buy this plan", and a
    third (or a new route) is one file away.  Written inline as
    ``if not plan["public"]`` it is right until the day offers exist —
    which is today.  So every ``create_checkout_session`` must ASK
    purchasable_plan; a provider that reads the flag itself fails here
    with the name of the file to fix.
    """
    import ast
    from pathlib import Path

    billing = Path(__file__).resolve().parents[1]
    offenders = []
    for path in sorted(billing.glob("*.py")):
        tree = ast.parse(path.read_text())
        for node in ast.walk(tree):
            if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                continue
            if node.name != "create_checkout_session":
                continue
            body = ast.get_source_segment(path.read_text(), node) or ""
            if "..." in body and len(body) < 400:
                continue                      # the Protocol's stub, not an answer
            if "purchasable_plan" not in body:
                offenders.append(f"{path.name}:{node.lineno} {node.name}")
    assert not offenders, (
        "these decide who may buy a plan without asking offers.purchasable_plan:\n    "
        + "\n    ".join(offenders))
