"""What a subscription records as its price is what Stripe invoices.

The plan row is the catalog the customer sees; the Stripe Price behind
the row's id is what is charged.  When the two differ the webhook
records Stripe's amount and says so; when Stripe's price is not the
shape our columns mean (yearly, decimal, another currency) or could not
be read, the row answers.  The trucks included are always the row's.
"""

import logging
import os

os.environ.setdefault("ENCRYPTION_KEY", "")

import pytest

from capabilities.platform.billing.stripe_client import StripeBillingProvider

P = StripeBillingProvider


def _item(item_id, price_id, amount, interval="month", count=1, currency="usd"):
    return {"id": item_id, "price": {"id": price_id, "unit_amount": amount, "currency": currency,
                                     "recurring": {"interval": interval, "interval_count": count}}}


def test_only_a_plain_monthly_usd_amount_is_taken_from_stripe():
    ok = _item("si_1", "price_b", 5900)["price"]
    slot = {"unit_amount": 5900, "interval": "month", "interval_count": 1, "currency": "usd"}
    assert P._stripe_monthly_usd(slot) == 5900
    assert P._stripe_monthly_usd({**slot, "interval": "year"}) is None
    assert P._stripe_monthly_usd({**slot, "interval_count": 3}) is None
    assert P._stripe_monthly_usd({**slot, "currency": "eur"}) is None
    assert P._stripe_monthly_usd({**slot, "unit_amount": None}) is None       # tiered / decimal price
    assert P._stripe_monthly_usd({**slot, "unit_amount": "5900"}) is None
    assert P._stripe_monthly_usd({**slot, "unit_amount": True}) is None
    assert P._stripe_monthly_usd({**slot, "unit_amount": -1}) is None
    assert P._stripe_monthly_usd({}) is None
    assert ok["unit_amount"] == 5900


def test_items_are_matched_to_their_slots_with_their_prices(monkeypatch):
    monkeypatch.setenv("STRIPE_PRICE_EXTRA_VEHICLE", "price_extra")
    sub = {"items": {"data": [_item("si_x", "price_extra", 349), _item("si_b", "price_base", 5900)]}}
    slots = P._extract_items(sub)
    assert slots["base"]["id"] == "si_b" and slots["base"]["unit_amount"] == 5900
    assert slots["extra"]["id"] == "si_x" and slots["extra"]["unit_amount"] == 349
    assert P._extract_item_ids(sub) == ("si_b", "si_x")
    assert P._extract_items({})["base"] == {"id": ""}


def test_stripes_amount_wins_the_row_fills_in_and_drift_is_said_out_loud(caplog):
    pricing = {"base_vehicles": 10, "monthly_base_cents": 4900, "extra_vehicle_cents": 299}
    slots = {"base": {"id": "si_b", "unit_amount": 5900, "interval": "month", "interval_count": 1, "currency": "usd"},
             "extra": {"id": "si_x", "unit_amount": 299, "interval": "month", "interval_count": 1, "currency": "usd"}}
    with caplog.at_level(logging.WARNING, logger="capabilities.platform.billing.stripe_client"):
        out = P._priced_updates("starter", pricing, slots)
    assert out == {"base_vehicles": 10, "monthly_base_usd": 5900, "extra_vehicle_cents": 299}
    assert any("plan price drift" in r.message and "slot=base" in r.message for r in caplog.records)
    assert not any("slot=extra" in r.message for r in caplog.records)
    # a yearly base price and no extras item → the row answers, trucks stay the row's
    yearly = {"base": {**slots["base"], "interval": "year"}, "extra": {"id": ""}}
    assert P._priced_updates("starter", pricing, yearly) == {"base_vehicles": 10, "monthly_base_usd": 4900, "extra_vehicle_cents": 299}
    assert P._priced_updates("starter", pricing, {}) == {"base_vehicles": 10, "monthly_base_usd": 4900, "extra_vehicle_cents": 299}


def _fake_stripe(event, retrieved=None, raise_retrieve=False):
    class _S:
        class Webhook:
            @staticmethod
            def construct_event(p, s, k): return event
        class error:  # noqa: N801
            class SignatureVerificationError(Exception): ...
        class Subscription:
            @staticmethod
            def retrieve(sub_id, expand=None):
                if raise_retrieve:
                    raise RuntimeError("stripe down")
                return retrieved
    return _S


@pytest.mark.asyncio
async def test_checkout_records_stripes_prices_and_the_rows_trucks(db, monkeypatch):
    acct = await db.create_account("Stripe Prices Co")
    await db.get_or_create_subscription(acct.id)
    monkeypatch.setenv("STRIPE_WEBHOOK_SECRET", "whsec_test")
    monkeypatch.setenv("STRIPE_PRICE_EXTRA_VEHICLE", "price_extra")
    event = {"id": "evt_prices_1", "type": "checkout.session.completed",
             "data": {"object": {"id": "cs_1", "customer": "cus_1", "subscription": "sub_1",
                                 "metadata": {"account_id": str(acct.id), "tier": "starter"}}}}
    retrieved = {"items": {"data": [_item("si_b", "price_base", 5900), _item("si_x", "price_extra", 349)]}}
    monkeypatch.setattr("capabilities.platform.billing.stripe_client._stripe", lambda: _fake_stripe(event, retrieved))
    await StripeBillingProvider().handle_webhook(b"{}", "sig", db)
    sub = await db.get_subscription(acct.id)
    assert (sub["tier"], sub["monthly_base_usd"], sub["extra_vehicle_cents"], sub["base_vehicles"]) == ("starter", 5900, 349, 10)
    assert (sub["provider_base_item_id"], sub["provider_extra_item_id"]) == ("si_b", "si_x")
    assert (await db.get_account(acct.id)).tier == "starter"


@pytest.mark.asyncio
async def test_when_stripe_cannot_be_read_the_row_answers_and_updated_backfills(db, monkeypatch):
    acct = await db.create_account("Stripe Backfill Co")
    await db.get_or_create_subscription(acct.id)
    monkeypatch.setenv("STRIPE_WEBHOOK_SECRET", "whsec_test")
    monkeypatch.setenv("STRIPE_PRICE_EXTRA_VEHICLE", "price_extra")
    event = {"id": "evt_prices_2", "type": "checkout.session.completed",
             "data": {"object": {"id": "cs_2", "customer": "cus_2", "subscription": "sub_2",
                                 "metadata": {"account_id": str(acct.id), "tier": "pro"}}}}
    monkeypatch.setattr("capabilities.platform.billing.stripe_client._stripe", lambda: _fake_stripe(event, raise_retrieve=True))
    await StripeBillingProvider().handle_webhook(b"{}", "sig", db)
    sub = await db.get_subscription(acct.id)
    assert (sub["monthly_base_usd"], sub["extra_vehicle_cents"], sub["base_vehicles"]) == (9900, 299, 10)   # the row
    assert not sub["provider_extra_item_id"]
    # the next subscription.updated carries the items: ids and Stripe's prices are backfilled once
    await db.update_subscription(acct.id, provider_subscription_id="sub_2", provider_customer_id="cus_2")
    upd = {"id": "evt_prices_3", "type": "customer.subscription.updated",
           "data": {"object": {"id": "sub_2", "customer": "cus_2", "status": "active",
                               "items": {"data": [_item("si_b2", "price_base", 10900), _item("si_x2", "price_extra", 299)]}}}}
    monkeypatch.setattr("capabilities.platform.billing.stripe_client._stripe", lambda: _fake_stripe(upd))
    await StripeBillingProvider().handle_webhook(b"{}", "sig", db)
    sub = await db.get_subscription(acct.id)
    assert (sub["provider_base_item_id"], sub["provider_extra_item_id"]) == ("si_b2", "si_x2")
    assert (sub["monthly_base_usd"], sub["extra_vehicle_cents"], sub["base_vehicles"]) == (10900, 299, 10)
    # a later update with a different price does NOT refresh a row that has its ids
    upd2 = {**upd, "id": "evt_prices_4",
            "data": {"object": {**upd["data"]["object"], "items": {"data": [_item("si_b3", "price_base", 100)]}}}}
    monkeypatch.setattr("capabilities.platform.billing.stripe_client._stripe", lambda: _fake_stripe(upd2))
    await StripeBillingProvider().handle_webhook(b"{}", "sig", db)
    sub = await db.get_subscription(acct.id)
    assert (sub["provider_base_item_id"], sub["monthly_base_usd"]) == ("si_b2", 10900)
