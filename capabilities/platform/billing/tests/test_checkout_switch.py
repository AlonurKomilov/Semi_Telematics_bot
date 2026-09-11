"""An account Stripe already bills never checks out twice: "Upgrade"
switches the live subscription's base item in place, prorated, and our
rows follow at once.  A new account still gets a Checkout Session."""

import os

os.environ.setdefault("ENCRYPTION_KEY", "")

import pytest

from capabilities.platform.billing.stripe_client import StripeBillingProvider


def _item(item_id, price_id, amount):
    return {"id": item_id, "price": {"id": price_id, "unit_amount": amount, "currency": "usd",
                                     "recurring": {"interval": "month", "interval_count": 1}}}


class _FakeStripe:
    """Stripe with a real subscription STATE: modify changes it, retrieve
    reads it — so a cached replay (modify returning the old state) can be
    told apart from a move that happened."""
    def __init__(self, live_sub, *, stale_replay=False):
        self.calls = []; fake = self; self.state = dict(live_sub); self.raise_on_modify = None
        class error:  # noqa: N801
            class StripeError(Exception): ...
        class Subscription:
            @staticmethod
            def retrieve(sid, expand=None): fake.calls.append(("retrieve", sid)); return dict(fake.state)
            @staticmethod
            def modify(sid, **kw):
                fake.calls.append(("modify", sid, kw))
                if fake.raise_on_modify:
                    raise fake.raise_on_modify
                base = kw["items"][0]
                if stale_replay:            # Stripe hands back its cached answer and moves nothing
                    return dict(fake.state)
                fake.state = {**fake.state, "items": {"data": [_item(base["id"], base["price"], 9900), _item("si_x", "price_extra", 299)]}}
                return dict(fake.state)
        class checkout:  # noqa: N801
            class Session:
                @staticmethod
                def create(**kw): fake.calls.append(("Session.create", kw)); return {"url": "https://stripe/checkout", "id": "cs_1"}
        class Customer:
            @staticmethod
            def create(**kw): fake.calls.append(("Customer.create", kw)); return {"id": "cus_new"}
        self.Subscription, self.checkout, self.Customer, self.error = Subscription, checkout, Customer, error


@pytest.mark.asyncio
async def test_a_live_subscriber_switches_in_place_and_a_new_account_checks_out(db, monkeypatch):
    monkeypatch.setenv("STRIPE_PRICE_EXTRA_VEHICLE", "price_extra")
    await db.upsert_plan("pro", label="Pro", included=["*"], stripe_price_id="price_pro", public=True)
    await db.upsert_plan("starter", label="Starter", included=["*"], stripe_price_id="price_starter", public=True)
    acct = await db.create_account("Switch Co", tier="starter")
    await db.get_or_create_subscription(acct.id)
    await db.update_subscription(acct.id, tier="starter", provider="stripe", provider_customer_id="cus_1",
                                 provider_subscription_id="sub_live", status="active", provider_base_price_id="price_starter")
    live = {"id": "sub_live", "status": "active", "current_period_end": 1800000000,
            "items": {"data": [_item("si_base", "price_starter", 4900), _item("si_x", "price_extra", 299)]}}
    fake = _FakeStripe(live)
    monkeypatch.setattr("capabilities.platform.billing.stripe_client._stripe", lambda: fake)
    out = await StripeBillingProvider().create_checkout_session(
        account_id=acct.id, db=db, tier="pro", success_url="https://dash/billing?success=1", cancel_url="https://dash/billing")
    assert out["switched"] is True and out["url"] == "https://dash/billing?success=1"
    names = [c[0] for c in fake.calls]
    assert "Session.create" not in names and names.count("modify") == 1          # never a second subscription
    kw = [c for c in fake.calls if c[0] == "modify"][0][2]
    assert kw["items"] == [{"id": "si_base", "price": "price_pro", "quantity": 1}]
    assert kw["proration_behavior"] == "create_prorations" and kw["metadata"]["tier"] == "pro"
    sub = await db.get_subscription(acct.id)
    assert (sub["tier"], sub["provider_base_price_id"], sub["monthly_base_usd"]) == ("pro", "price_pro", 9900)
    assert (await db.get_account(acct.id)).tier == "pro"
    # the same plan again is refused, not re-charged
    with pytest.raises(ValueError, match="already on"):
        await StripeBillingProvider().create_checkout_session(
            account_id=acct.id, db=db, tier="pro", success_url="u", cancel_url="c")
    # a fresh account (no live subscription) gets a Checkout Session as before
    fresh = await db.create_account("Fresh Co")
    out2 = await StripeBillingProvider().create_checkout_session(
        account_id=fresh.id, db=db, tier="pro", success_url="u", cancel_url="c")
    assert out2["url"] == "https://stripe/checkout" and [c[0] for c in fake.calls][-1] == "Session.create"
    session_kw = fake.calls[-1][1]
    assert session_kw["line_items"][0] == {"price": "price_pro", "quantity": 1}
    assert session_kw["success_url"] == "u"


async def _live_account(db, name, sub_id, tg=None):
    acct = await db.create_account(name, tier="starter")
    await db.get_or_create_subscription(acct.id)
    await db.update_subscription(acct.id, tier="starter", provider="stripe", provider_customer_id="cus_1",
                                 provider_subscription_id=sub_id, status="active", provider_base_price_id="price_starter")
    return acct


def _live(sub_id="sub_live", base="price_starter"):
    return {"id": sub_id, "status": "active", "current_period_end": 1800000000,
            "items": {"data": [_item("si_base", base, 4900), _item("si_x", "price_extra", 299)]}}


@pytest.mark.asyncio
async def test_each_switch_is_its_own_stripe_action_and_a_stale_replay_writes_nothing(db, monkeypatch):
    monkeypatch.setenv("STRIPE_PRICE_EXTRA_VEHICLE", "price_extra")
    await db.upsert_plan("pro", label="Pro", included=["*"], stripe_price_id="price_pro", public=True)
    await db.upsert_plan("starter", label="Starter", included=["*"], stripe_price_id="price_starter", public=True)
    # A→B, B→A, A→B on one day: three different idempotency keys, never the first one replayed
    acct = await _live_account(db, "Flip Co", "sub_flip")
    fake = _FakeStripe(_live("sub_flip"))
    monkeypatch.setattr("capabilities.platform.billing.stripe_client._stripe", lambda: fake)
    P = StripeBillingProvider()
    for tier in ("pro", "starter", "pro"):
        await P.create_checkout_session(account_id=acct.id, db=db, tier=tier, success_url="u", cancel_url="c")
    keys = [c[2]["idempotency_key"] for c in fake.calls if c[0] == "modify"]
    assert len(keys) == 3 and len(set(keys)) == 3 and all(k.startswith(f"switch:{acct.id}:sub_flip:") for k in keys)
    assert (await db.get_subscription(acct.id))["tier"] == "pro"
    # a replay that moves nothing: Stripe's fresh state still says the old price → refused, rows untouched
    acct2 = await _live_account(db, "Stale Co", "sub_stale")
    stale = _FakeStripe(_live("sub_stale"), stale_replay=True)
    monkeypatch.setattr("capabilities.platform.billing.stripe_client._stripe", lambda: stale)
    from capabilities.platform.billing.provider import ProviderError
    with pytest.raises(ProviderError, match="did not move"):
        await P.create_checkout_session(account_id=acct2.id, db=db, tier="pro", success_url="u", cancel_url="c")
    assert (await db.get_subscription(acct2.id))["tier"] == "starter" and (await db.get_account(acct2.id)).tier == "starter"
    # Stripe refusing outright is a named provider error, not a bare exception
    acct3 = await _live_account(db, "Down Co", "sub_down")
    down = _FakeStripe(_live("sub_down"))
    down.raise_on_modify = down.error.StripeError("rate limited")          # its OWN error class, as Stripe's would be
    monkeypatch.setattr("capabilities.platform.billing.stripe_client._stripe", lambda: down)
    with pytest.raises(ProviderError, match="rate limited"):
        await P.create_checkout_session(account_id=acct3.id, db=db, tier="pro", success_url="u", cancel_url="c")


@pytest.mark.asyncio
async def test_the_webhook_reconciles_a_subscription_stripe_moved_to_a_plan_our_row_does_not_say(db, monkeypatch):
    """The in-app switch's local write failed after Stripe moved (or a
    rollout died mid-write): subscription.updated carries the base item on
    Pro's Price while our row says Starter → the row, the account and the
    resolver follow Stripe."""
    monkeypatch.setenv("STRIPE_WEBHOOK_SECRET", "whsec_test")
    monkeypatch.setenv("STRIPE_PRICE_EXTRA_VEHICLE", "price_extra")
    await db.upsert_plan("pro", label="Pro", included=["*"], stripe_price_id="price_pro", public=True)
    await db.upsert_plan("starter", label="Starter", included=["*"], stripe_price_id="price_starter", public=True)
    acct = await _live_account(db, "Reconcile Co", "sub_rec")
    event = {"id": "evt_rec_1", "type": "customer.subscription.updated",
             "data": {"object": {"id": "sub_rec", "customer": "cus_1", "status": "active",
                                 "items": {"data": [_item("si_base", "price_pro", 9900), _item("si_x", "price_extra", 299)]}}}}
    class _S:
        class Webhook:
            @staticmethod
            def construct_event(p, s, k): return event
        class error:  # noqa: N801
            class SignatureVerificationError(Exception): ...
    monkeypatch.setattr("capabilities.platform.billing.stripe_client._stripe", lambda: _S)
    out = await StripeBillingProvider().handle_webhook(b"{}", "sig", db)
    assert out.get("reconciled_to") == "pro"
    sub = await db.get_subscription(acct.id)
    assert (sub["tier"], sub["provider_base_price_id"], sub["monthly_base_usd"]) == ("pro", "price_pro", 9900)
    assert (await db.get_account(acct.id)).tier == "pro"
    # a row already on the Price's plan is not "reconciled" again — the event just updates status/period
    event["id"] = "evt_rec_2"
    out = await StripeBillingProvider().handle_webhook(b"{}", "sig", db)
    assert "reconciled_to" not in out


def test_the_customer_returns_to_the_dashboard_billing_page(monkeypatch):
    from capabilities.platform.billing.router import _dashboard_url
    for k in ("AUTH_BASE_URL", "DASHBOARD_BASE_URL", "APP_BASE_URL"):
        monkeypatch.delenv(k, raising=False)
    assert _dashboard_url() == "https://dash.4truck.us"
    monkeypatch.setenv("APP_BASE_URL", "https://4truck.us/")
    assert _dashboard_url() == "https://4truck.us"
    monkeypatch.setenv("AUTH_BASE_URL", "https://dash.example/")
    assert _dashboard_url() == "https://dash.example"
