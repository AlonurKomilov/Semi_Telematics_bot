"""A price break for one account, for a bounded time.

Until now the only relief was a comp: a local 100% flag Stripe was
never told about — which is why a comped customer received no invoice
and no receipt, there being nothing in Stripe to invoice.  A discount
is a Stripe Coupon on the account's SUBSCRIPTION instead, so the
reduced amount is what the card is charged and the minus line prints
itself on the invoice, the PDF and the receipt.

What these pin, each because it is a way money goes wrong:
one live grant per account (two coupons on one subscription is a bill
nobody can predict); the customer's page shows the number Stripe will
charge by, clamped the way Stripe clamps; a grant Stripe refuses leaves
no half-live row; a revoke closes the row even when Stripe has already
ended the coupon; and the daily sweep believes Stripe's clock, not ours.
"""

from __future__ import annotations

import os

os.environ.setdefault("ENCRYPTION_KEY", "")
os.environ.setdefault("JWT_SECRET", "test-secret-32-chars-or-more-please-aaaaaaaaaaaa")

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient

from adapters.storage.account_discounts import describe, effective_off
from capabilities.platform.billing import discounts as D


# ── the arithmetic a customer reads ───────────────────────────────

def test_what_comes_off_a_bill_is_clamped_the_way_stripe_clamps():
    """Stripe makes the invoice zero and does not carry the remainder;
    the page must show the same, or it promises a credit that will
    never arrive."""
    amount = {"kind": "amount", "amount_off_cents": 10000, "status": "active"}
    assert effective_off(amount, 45900) == 10000
    assert effective_off(amount, 4900) == 4900, "never more than the bill"
    assert effective_off(amount, 0) == 0
    pct = {"kind": "percent", "percent_off": 20, "status": "active"}
    assert effective_off(pct, 45900) == 9180
    assert effective_off({"kind": "percent", "percent_off": 100, "status": "active"}, 45900) == 45900
    # a grant that is over takes nothing off
    assert effective_off({**amount, "status": "ended"}, 45900) == 0
    assert effective_off({**amount, "status": "revoked"}, 45900) == 0
    assert effective_off(None, 45900) == 0


def test_the_grant_says_itself_in_the_words_on_the_bill():
    assert describe({"kind": "amount", "amount_off_cents": 10000}) == "$100.00 off"
    assert describe({"kind": "percent", "percent_off": 20}) == "20% off"
    assert describe(None) == ""


# ── what goes to Stripe ───────────────────────────────────────────

def test_the_coupon_says_how_long_and_carries_the_reason():
    """``name`` is what a customer reads beside the minus sign."""
    three = D.coupon_params({"id": 7, "account_id": 1, "kind": "amount",
                             "amount_off_cents": 10000, "months": 3, "reason": "thank you"})
    assert three["amount_off"] == 10000 and three["currency"] == "usd"
    assert three["duration"] == "repeating" and three["duration_in_months"] == 3
    assert three["name"] == "Promotion — thank you"
    assert three["metadata"] == {"account_id": "1", "discount_id": "7"}
    forever = D.coupon_params({"id": 8, "account_id": 1, "kind": "percent",
                              "percent_off": 20, "months": 0, "reason": ""})
    assert forever["percent_off"] == 20.0 and forever["duration"] == "forever"
    assert "currency" not in forever, "Stripe refuses a currency on a percent coupon"
    assert forever["name"] == "Promotion"


def test_a_checkout_carries_a_pending_grant_and_never_an_empty_list():
    """An empty ``discounts`` CLEARS on a Subscription.modify and is
    refused by Checkout, so the caller must send the key or nothing."""
    assert D.checkout_discounts({"stripe_coupon_id": "co_1"}) == [{"coupon": "co_1"}]
    assert D.checkout_discounts({"stripe_coupon_id": ""}) == []
    assert D.checkout_discounts(None) == []


def test_the_coupon_behind_a_discount_is_read_from_source_not_from_coupon():
    """Probed against a live sandbox subscription on 2026-09-14: a
    Discount has no ``coupon`` field on this API version — reading one
    raises KeyError.  It carries ``source = {"type": "coupon",
    "coupon": "<id>"}``, and the id is a string unless the nested path
    was expanded."""
    assert D._coupon_id_of({"source": {"type": "coupon", "coupon": "co_1"}}) == "co_1"
    assert D._coupon_id_of({"source": {"type": "coupon", "coupon": {"id": "co_1"}}}) == "co_1"
    assert D._coupon_id_of({"source": {}}) == ""
    assert D._coupon_id_of({}) == ""


class _FakeStripe:
    """Enough Stripe to apply and read a discount, in the shape the
    sandbox actually returned."""

    def __init__(self, *, applies=True):
        self.calls: list = []
        fake = self
        self.state: dict = {"id": "sub_1", "discounts": []}

        class Coupon:
            @staticmethod
            def create(**kw):
                fake.calls.append(("Coupon.create", kw))
                return {"id": "co_made"}

        class Subscription:
            @staticmethod
            def modify(sid, **kw):
                fake.calls.append(("Subscription.modify", sid, kw))
                if applies:
                    fake.state = {**fake.state, "discounts": [{
                        "id": "di_1", "start": 1789384241, "end": 1797246641,
                        "source": {"type": "coupon", "coupon": kw["discounts"][0]["coupon"]}}]}
                return dict(fake.state)

            @staticmethod
            def retrieve(sid, expand=None):
                fake.calls.append(("Subscription.retrieve", sid))
                return dict(fake.state)

            @staticmethod
            def delete_discount(sid):
                fake.calls.append(("Subscription.delete_discount", sid))
                fake.state = {**fake.state, "discounts": []}
                return {"deleted": True}

        self.Coupon, self.Subscription = Coupon, Subscription


def test_applying_reads_back_what_stripe_holds_and_takes_its_dates():
    st = _FakeStripe()
    out = D.apply_to_subscription(st, subscription_id="sub_1", discount={
        "id": 7, "account_id": 1, "kind": "amount", "amount_off_cents": 10000,
        "months": 3, "reason": "thank you"})
    assert out["coupon_id"] == "co_made" and out["discount_id"] == "di_1"
    # Stripe's own start and end, not our arithmetic: a repeating coupon
    # runs on the calendar from the moment it is applied
    assert out["starts_at"].startswith("2026-09-14") and out["ends_at"].startswith("2026-12-14")
    names = [c[0] for c in st.calls]
    assert names == ["Coupon.create", "Subscription.modify", "Subscription.retrieve"]
    assert st.calls[1][2]["discounts"] == [{"coupon": "co_made"}]
    assert "items" not in st.calls[1][2], "a discount is not a price change"


def test_a_modify_stripe_did_not_honour_is_an_error_not_a_recorded_lie():
    st = _FakeStripe(applies=False)
    with pytest.raises(RuntimeError, match="did not put the discount"):
        D.apply_to_subscription(st, subscription_id="sub_1", discount={
            "id": 7, "account_id": 1, "kind": "percent", "percent_off": 20, "months": 0})


def test_removing_a_discount_stripe_already_ended_is_not_an_error():
    st = _FakeStripe()
    D.apply_to_subscription(st, subscription_id="sub_1", discount={
        "id": 7, "account_id": 1, "kind": "amount", "amount_off_cents": 100, "months": 1})
    assert D.remove_from_subscription(st, subscription_id="sub_1", discount_id="di_1") is True
    assert D.read_back(st, subscription_id="sub_1", discount_id="di_1") is None

    class _Gone:
        class Subscription:
            @staticmethod
            def delete_discount(sid):
                raise RuntimeError("No such discount")
    assert D.remove_from_subscription(_Gone, subscription_id="sub_1", discount_id="di_1") is False
    assert D.remove_from_subscription(st, subscription_id="", discount_id="di_1") is False


# ── the store ─────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_one_live_grant_per_account(pg_db):
    """Two coupons on one subscription is a bill neither operator can
    predict, so the second grant is refused with the sentence that
    fixes it."""
    db = pg_db
    acct = await db.create_account("Discount Co")
    row = await db.create_account_discount(acct.id, kind="amount", amount_off_cents=10000,
                                           months=3, reason="thank you", granted_by="operator:1")
    assert row["status"] == "pending" and row["amount_off_cents"] == 10000
    with pytest.raises(ValueError, match="revoke it before granting another"):
        await db.create_account_discount(acct.id, kind="percent", percent_off=20)
    # once it is closed, another may be given
    await db.mark_account_discount(int(row["id"]), status="revoked")
    assert await db.live_account_discount(acct.id) is None
    again = await db.create_account_discount(acct.id, kind="percent", percent_off=20)
    assert again["kind"] == "percent"
    assert len(await db.account_discount_history(acct.id)) == 2


@pytest.mark.asyncio
async def test_a_grant_must_be_a_real_amount(pg_db):
    db = pg_db
    acct = await db.create_account("Bad Grant Co")
    for kwargs in ({"kind": "amount", "amount_off_cents": 0},
                   {"kind": "percent", "percent_off": 0},
                   {"kind": "percent", "percent_off": 101},
                   {"kind": "nonsense"}):
        with pytest.raises(ValueError):
            await db.create_account_discount(acct.id, **kwargs)
    assert await db.live_account_discount(acct.id) is None


@pytest.mark.asyncio
async def test_the_customers_page_shows_the_number_stripe_will_charge_by(pg_db):
    db = pg_db
    acct = await db.create_account("Page Co", tier="pro")
    await db.get_or_create_subscription(acct.id)
    await db.update_subscription(acct.id, tier="pro", monthly_base_usd=45900,
                                 base_vehicles=0, extra_vehicle_cents=0, vehicle_count=0)
    plain = await db.get_billing_summary(acct.id)
    assert plain["subtotal_cents"] == 45900 and plain["amount_due_cents"] == 45900
    assert plain["discount_cents"] == 0 and plain["promotion_label"] == ""

    row = await db.create_account_discount(acct.id, kind="amount", amount_off_cents=10000,
                                           months=3, reason="onboarding help", granted_by="operator:1")
    await db.mark_account_discount(int(row["id"]), status="active", ends_at="2026-12-14T11:10:41+00:00")
    with_promo = await db.get_billing_summary(acct.id)
    assert with_promo["subtotal_cents"] == 45900
    assert with_promo["discount_cents"] == 10000
    assert with_promo["amount_due_cents"] == 35900, "the $359 the card is charged"
    assert with_promo["promotion_label"] == "$100.00 off"
    assert with_promo["promotion_until"].startswith("2026-12-14")
    # the deduction is stated ONCE, under the charges — line_items are
    # what the account is charged for, and the operator's private reason
    # never reaches the customer's bill
    assert not [i for i in with_promo["line_items"] if i["amount_cents"] < 0]
    assert "onboarding help" not in str(with_promo["line_items"])
    assert "onboarding help" not in with_promo["promotion_label"]


@pytest.mark.asyncio
async def test_a_comped_account_is_still_free_and_no_promotion_is_stacked_on_it(pg_db):
    """Comp is 100% already; a promotion beside it would print two
    discount lines for one bill."""
    db = pg_db
    acct = await db.create_account("Comped Co", tier="pro")
    await db.get_or_create_subscription(acct.id)
    await db.update_subscription(acct.id, monthly_base_usd=45900, base_vehicles=0,
                                 extra_vehicle_cents=0, vehicle_count=0)
    await db.grant_comp(acct.id, expires_at="2027-01-01T00:00:00+00:00", reason="partner")
    row = await db.create_account_discount(acct.id, kind="amount", amount_off_cents=10000)
    await db.mark_account_discount(int(row["id"]), status="active")
    s = await db.get_billing_summary(acct.id)
    assert s["amount_due_cents"] == 0 and s["discount_cents"] == 45900
    assert s["promotion_label"] == "", "one discount line, and it is the comp's"
    assert len([i for i in s["line_items"] if i["amount_cents"] < 0]) == 1


# ── the operator's doors ──────────────────────────────────────────

@pytest_asyncio.fixture
async def api(pg_db, monkeypatch):
    monkeypatch.setenv("SYSTEM_OWNER_IDS", "12345")
    monkeypatch.setenv("BILLING_PROVIDER", "stub")
    import capabilities.platform.billing as _b
    monkeypatch.setattr(_b, "_provider", None)
    import infra.platform as _cp
    monkeypatch.setattr(_cp, "_db", pg_db)
    from interfaces.api.app import create_api
    app = create_api()
    from interfaces.api import deps as _deps
    async def _operator():
        return {"telegram_id": 12345, "sub": 12345, "role": "system_owner", "account_id": 0}
    app.dependency_overrides[_deps.require_system_owner] = _operator
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
        yield {"client": c, "db": pg_db}


@pytest.mark.asyncio
async def test_the_operator_grants_reads_and_revokes(api):
    c, db = api["client"], api["db"]
    acct = await db.create_account("Route Co")
    r = await c.post(f"/api/system/accounts/{acct.id}/discount", json={
        "account_id": acct.id, "kind": "amount", "amount_off_cents": 10000,
        "months": 3, "reason": "onboarding help"})
    assert r.status_code == 201, r.text
    got = r.json()["discount"]
    assert got["amount_off_cents"] == 10000 and got["status"] == "active"   # the stub applies at once
    assert got["reason"] == "onboarding help" and got["granted_by"].startswith("operator:")

    read = (await c.get(f"/api/system/accounts/{acct.id}/discount")).json()
    assert read["discount"]["id"] == got["id"] and len(read["history"]) == 1

    # a second grant is refused with the sentence that fixes it
    again = await c.post(f"/api/system/accounts/{acct.id}/discount", json={
        "account_id": acct.id, "kind": "percent", "percent_off": 20})
    assert again.status_code == 409 and "revoke it" in again.json()["detail"]

    assert (await c.delete(f"/api/system/accounts/{acct.id}/discount")).status_code == 200
    assert (await c.get(f"/api/system/accounts/{acct.id}/discount")).json()["discount"] is None
    assert (await c.delete(f"/api/system/accounts/{acct.id}/discount")).status_code == 404
    rows = await db.list_platform_audit(event="discount_granted", limit=5)
    assert rows and str(acct.id) in str(rows[0]["account_id"])


@pytest.mark.asyncio
async def test_a_comped_account_is_refused_a_discount_with_the_reason(api):
    c, db = api["client"], api["db"]
    acct = await db.create_account("Comped Route Co")
    await db.get_or_create_subscription(acct.id)
    await db.grant_comp(acct.id, expires_at="2027-01-01T00:00:00+00:00", reason="partner")
    r = await c.post(f"/api/system/accounts/{acct.id}/discount", json={
        "account_id": acct.id, "kind": "amount", "amount_off_cents": 10000})
    assert r.status_code == 409 and "already pays nothing" in r.json()["detail"]
    assert await db.live_account_discount(acct.id) is None


@pytest.mark.asyncio
async def test_a_provider_that_refuses_leaves_no_half_live_grant(api):
    """A pending row nobody can see is worse than no row: the operator
    would be told it worked and the customer would be charged in full."""
    c, db = api["client"], api["db"]
    acct = await db.create_account("Refused Co")
    import capabilities.platform.billing as _b
    provider = _b.get_provider()
    real_apply = provider.apply_discount
    async def _refuse(account_id, database, discount):
        raise RuntimeError("Stripe said no")
    provider.apply_discount = _refuse
    r = await c.post(f"/api/system/accounts/{acct.id}/discount", json={
        "account_id": acct.id, "kind": "amount", "amount_off_cents": 10000})
    assert r.status_code == 502 and "refused the discount" in r.json()["detail"]
    assert await db.live_account_discount(acct.id) is None, "the row was rolled back"
    # and the operator may try again
    provider.apply_discount = real_apply
    assert (await c.post(f"/api/system/accounts/{acct.id}/discount", json={
        "account_id": acct.id, "kind": "amount", "amount_off_cents": 10000})).status_code == 201
