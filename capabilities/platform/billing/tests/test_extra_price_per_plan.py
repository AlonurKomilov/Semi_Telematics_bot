"""Each plan's per-truck extra is billed at the plan's own Price.

One env-wide Price used to serve every plan: the Plans page said Gold
bills $4.99 per extra truck, the customer page said $4.99, our own
snapshots said $4.99 — and Stripe charged the env Price's $2.99.  The
row and the invoice disagreed, which is the lie the plan table exists
to end.

What these pin: checkout attaches the plan's extras Price and refuses
a plan that bills extra trucks but holds none (never someone else's
amount); the in-place switch moves base and extras in ONE modify;
opening a missing extras line uses the subscription's plan; the stub
provider makes nothing.
"""

import os

os.environ.setdefault("ENCRYPTION_KEY", "")

import pytest

from capabilities.platform.billing.stripe_client import StripeBillingProvider, _plan_extras_price
from capabilities.platform.billing.stub import StubBillingProvider


def _item(item_id, price_id, amount, quantity=None, meta=None):
    it = {"id": item_id, "price": {"id": price_id, "unit_amount": amount, "currency": "usd",
                                   "recurring": {"interval": "month", "interval_count": 1}}}
    if quantity is not None:
        it["quantity"] = quantity
    if meta:
        it["price"]["metadata"] = meta
    return it


class _FakeStripe:
    def __init__(self, live_sub=None):
        self.calls = []; fake = self; self.state = dict(live_sub or {})
        class error:  # noqa: N801
            class StripeError(Exception): ...
        class Subscription:
            @staticmethod
            def retrieve(sid, expand=None): fake.calls.append(("retrieve", sid)); return dict(fake.state)
            @staticmethod
            def modify(sid, **kw):
                fake.calls.append(("modify", sid, kw))
                moved = {it["id"]: it["price"] for it in kw["items"]}
                data = []
                for it in fake.state["items"]["data"]:
                    if it["id"] in moved:
                        new = dict(it); new["price"] = {**it["price"], "id": moved[it["id"]]}
                        if moved[it["id"]].startswith("price_x"):
                            new["price"]["metadata"] = {"kind": "extra"}
                        data.append(new)
                    else:
                        data.append(it)
                fake.state = {**fake.state, "items": {"data": data}}
                return dict(fake.state)
        class SubscriptionItem:
            @staticmethod
            def create(**kw): fake.calls.append(("SubscriptionItem.create", kw)); return {"id": "si_opened"}
        class checkout:  # noqa: N801
            class Session:
                @staticmethod
                def create(**kw): fake.calls.append(("Session.create", kw)); return {"url": "https://stripe/checkout", "id": "cs_1"}
        class Customer:
            @staticmethod
            def create(**kw): fake.calls.append(("Customer.create", kw)); return {"id": "cus_new"}
        self.Subscription, self.SubscriptionItem, self.checkout, self.Customer, self.error = \
            Subscription, SubscriptionItem, checkout, Customer, error


def test_the_plans_own_extras_price_or_a_refusal_never_someone_elses():
    assert _plan_extras_price({"tier": "gold", "extra_vehicle_cents": 499, "stripe_extra_price_id": "price_x_gold"}) == "price_x_gold"
    assert _plan_extras_price({"tier": "free", "extra_vehicle_cents": 0, "stripe_extra_price_id": ""}) == ""
    with pytest.raises(ValueError, match="no Stripe price for it yet"):
        _plan_extras_price({"tier": "gold", "extra_vehicle_cents": 499, "stripe_extra_price_id": ""})


@pytest.mark.asyncio
async def test_checkout_sends_the_plans_extras_price_and_refuses_a_plan_without_one(db, monkeypatch):
    monkeypatch.setenv("STRIPE_PRICE_EXTRA_VEHICLE", "price_env")          # must NOT be what goes out
    await db.upsert_plan("gold", label="Gold", included=["*"], price_monthly_cents=15000, base_vehicles=0,
                         extra_vehicle_cents=499, stripe_price_id="price_gold", stripe_extra_price_id="price_x_gold",
                         public=True)
    acct = await db.create_account("Gold Co")
    monkeypatch.setattr("adapters.storage.billing.BillingMixin.compute_billing",
                        lambda self, account_id: _billing(account_id), raising=False)
    fake = _FakeStripe()
    monkeypatch.setattr("capabilities.platform.billing.stripe_client._stripe", lambda: fake)
    out = await StripeBillingProvider().create_checkout_session(acct.id, db, "gold", "https://s", "https://c")
    assert out["url"]
    session = [c for c in fake.calls if c[0] == "Session.create"][0][1]
    assert session["line_items"] == [{"price": "price_gold", "quantity": 1},
                                     {"price": "price_x_gold", "quantity": 7}]
    # a plan that bills extra trucks and holds no Price is refused before Stripe is asked
    await db.upsert_plan("silver", label="Silver", included=["*"], price_monthly_cents=9900, base_vehicles=0,
                         extra_vehicle_cents=399, stripe_price_id="price_silver", public=True)
    n = len(fake.calls)
    with pytest.raises(ValueError, match="no Stripe price for it yet"):
        await StripeBillingProvider().create_checkout_session(acct.id, db, "silver", "https://s", "https://c")
    assert len(fake.calls) == n, "Stripe was not asked"


async def _billing(account_id):
    return {"extras": 7, "active_vehicles": 7, "base_vehicles": 0}


@pytest.mark.asyncio
async def test_the_switch_moves_base_and_extras_in_one_prorated_call(db, monkeypatch):
    monkeypatch.setenv("STRIPE_PRICE_EXTRA_VEHICLE", "price_env")
    await db.upsert_plan("starter", label="Starter", included=["*"], price_monthly_cents=4900, base_vehicles=10,
                         extra_vehicle_cents=299, stripe_price_id="price_starter", stripe_extra_price_id="price_x_starter", public=True)
    await db.upsert_plan("gold", label="Gold", included=["*"], price_monthly_cents=15000, base_vehicles=0,
                         extra_vehicle_cents=499, stripe_price_id="price_gold", stripe_extra_price_id="price_x_gold", public=True)
    acct = await db.create_account("Switch Co", tier="starter")
    await db.get_or_create_subscription(acct.id)
    await db.update_subscription(acct.id, tier="starter", provider="stripe", provider_customer_id="cus_1",
                                 provider_subscription_id="sub_live", status="active", provider_base_price_id="price_starter")
    live = {"id": "sub_live", "status": "active", "current_period_end": 1800000000,
            "items": {"data": [_item("si_base", "price_starter", 4900),
                               _item("si_x", "price_env", 299, quantity=40)]}}      # extras still on the env Price
    fake = _FakeStripe(live)
    monkeypatch.setattr("capabilities.platform.billing.stripe_client._stripe", lambda: fake)
    out = await StripeBillingProvider().create_checkout_session(acct.id, db, "gold", "https://dash/billing", "https://c")
    assert out["switched"] is True
    mods = [c for c in fake.calls if c[0] == "modify"]
    assert len(mods) == 1, "one modify: one proration set, never a moment with base on Gold and trucks priced like Starter"
    assert mods[0][2]["items"] == [{"id": "si_base", "price": "price_gold", "quantity": 1},
                                   {"id": "si_x", "price": "price_x_gold", "quantity": 40}]
    assert mods[0][2]["proration_behavior"] == "create_prorations"
    sub = await db.get_subscription(acct.id)
    assert sub["tier"] == "gold" and sub["provider_base_price_id"] == "price_gold"


@pytest.mark.asyncio
async def test_opening_the_extras_line_uses_the_subscriptions_plan(db, monkeypatch):
    monkeypatch.setenv("STRIPE_PRICE_EXTRA_VEHICLE", "price_env")
    await db.upsert_plan("gold", label="Gold", included=["*"], price_monthly_cents=15000, base_vehicles=0,
                         extra_vehicle_cents=499, stripe_price_id="price_gold", stripe_extra_price_id="price_x_gold", public=True)
    acct = await db.create_account("Open Co", tier="gold")
    await db.get_or_create_subscription(acct.id)
    await db.update_subscription(acct.id, tier="gold", provider="stripe", provider_customer_id="cus_1",
                                 provider_subscription_id="sub_live", status="active")
    monkeypatch.setattr("adapters.storage.billing.BillingMixin.compute_billing",
                        lambda self, account_id: _billing(account_id), raising=False)
    fake = _FakeStripe()
    monkeypatch.setattr("capabilities.platform.billing.stripe_client._stripe", lambda: fake)
    out = await StripeBillingProvider()._open_extras_item(acct.id, db, await db.get_subscription(acct.id))
    assert out["after"] == 7
    opened = [c for c in fake.calls if c[0] == "SubscriptionItem.create"][0][1]
    assert opened["price"] == "price_x_gold" and opened["quantity"] == 7
    # the plan bills extras but holds no Price: nothing is opened at a wrong amount
    await db.upsert_plan("gold", label="Gold", included=["*"], stripe_extra_price_id="")
    n = len(fake.calls)
    out = await StripeBillingProvider()._open_extras_item(acct.id, db, await db.get_subscription(acct.id))
    assert out["skipped"] == "no_extras_price" and len(fake.calls) == n


@pytest.mark.asyncio
async def test_the_stub_makes_no_extras_price():
    assert await StubBillingProvider().create_extra_price(tier="gold", label="Gold", cents=499, before={}) == {"skipped": "stub"}
