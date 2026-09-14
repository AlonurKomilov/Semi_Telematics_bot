"""Everything that comes back from Stripe is read the same way.

The SDK's objects are not dicts.  ``StripeObject`` implements ``[...]``
and attribute access and nothing else — ``obj.get`` raises
``AttributeError: get``.  Every fake in this package is a plain dict,
which does have ``.get``, so handlers written with ``.get`` passed the
whole suite and raised against real Stripe:

  * the webhook dropped EVERY event — a customer paid and their plan
    never moved, because ``event.get("id")`` was the first line;
  * ``Roll out price`` refused to run (``_check_price``);
  * a live subscriber's plan switch crashed (``_extract_items``);
  * the daily quantity sync could not read the current quantity.

The fake here is faithful instead of convenient: attributes and
subscripts work, ``.get`` does not.  Anything that reads a Stripe
answer must go through ``_field``, and these say so at each of the four
doors the bug came through.
"""

from __future__ import annotations

import os

os.environ.setdefault("ENCRYPTION_KEY", "")

import pytest

from capabilities.platform.billing import rollout as R
from capabilities.platform.billing.stripe_client import StripeBillingProvider, _field


class SO:
    """A Stripe SDK object, as faithfully as this matters: ``[...]`` and
    attributes, and NO ``.get`` — ``__getattr__`` turns any unknown name
    into AttributeError, which is exactly how ``.get`` disappears."""

    def __init__(self, **data):
        object.__setattr__(self, "_data", {
            k: (SO(**v) if isinstance(v, dict) else
                [SO(**i) if isinstance(i, dict) else i for i in v] if isinstance(v, list) else v)
            for k, v in data.items()})

    def __getitem__(self, k):
        return self._data[k]                      # KeyError when absent, like the SDK

    def __getattr__(self, k):
        try:
            return object.__getattribute__(self, "_data")[k]
        except KeyError as e:
            raise AttributeError(k) from e

    def __contains__(self, k):
        return k in self._data

    def __iter__(self):
        return iter(self._data)


def test_the_fake_is_faithful():
    o = SO(id="x", nested={"a": 1})
    assert o["id"] == "x" and o.id == "x" and o.nested.a == 1
    with pytest.raises(AttributeError):
        o.get                                     # the whole point
    with pytest.raises(KeyError):
        o["nope"]


def test_field_reads_a_stripe_object_and_a_dict_alike():
    for obj in (SO(id="x", n=None), {"id": "x", "n": None}):
        assert _field(obj, "id") == "x"
        assert _field(obj, "missing", "fallback") == "fallback"
        # a field Stripe sent as null reads as None, exactly as dict.get
        # would — callers keep saying ``or {}`` / ``or ""`` themselves
        assert _field(obj, "n", "fallback") is None


def test_the_items_of_a_real_subscription_split_into_base_and_extras():
    """``_extract_items`` used ``item.get('price')`` — the door the live
    subscriber's plan switch crashed through."""
    sub = SO(id="sub_1", status="active", items={"data": [
        SO(id="si_x", quantity=12, price=SO(id="price_x", unit_amount=499, currency="usd",
                                            recurring=SO(interval="month", interval_count=1),
                                            metadata=SO(kind="extra", tier="gold"))),
        SO(id="si_b", quantity=1, price=SO(id="price_gold", unit_amount=15000, currency="usd",
                                           recurring=SO(interval="month", interval_count=1),
                                           metadata=SO(tier="gold"))),
    ]})
    slots = StripeBillingProvider._extract_items(sub)
    assert slots["base"]["id"] == "si_b" and slots["base"]["unit_amount"] == 15000
    assert slots["extra"]["id"] == "si_x" and slots["extra"]["quantity"] == 12
    assert slots["base"]["interval"] == "month" and slots["base"]["currency"] == "usd"


def test_the_rollouts_price_check_reads_a_real_price():
    """``_check_price`` used ``p.get('active', True)`` — the door
    Roll out price crashed through."""
    class _S:
        class Price:
            @staticmethod
            def retrieve(_id):
                return SO(id="price_pro", active=True, unit_amount=9900, currency="usd",
                          recurring=SO(interval="month", interval_count=1))
    R._check_price(_S, "price_pro", 9900)                       # no raise = the read worked
    with pytest.raises(R.RolloutRefused, match="save the plan again"):
        R._check_price(_S, "price_pro", 12900)


@pytest.mark.asyncio
async def test_a_rollout_moves_a_real_subscription():
    """``_move_one`` used ``s.get('status')`` on the retrieved object."""
    state = {"sub_1": SO(id="sub_1", status="active", current_period_end=1800000000,
                         items={"data": [SO(id="si_b", quantity=1,
                                            price=SO(id="price_old", unit_amount=4900, currency="usd",
                                                     recurring=SO(interval="month", interval_count=1)))]})}

    class _S:
        class Subscription:
            @staticmethod
            def retrieve(sid, expand=None): return state[sid]
            @staticmethod
            def modify(sid, **kw):
                state[sid] = SO(id=sid, status="active", current_period_end=1800000000,
                                items={"data": [SO(id="si_b", quantity=1,
                                                   price=SO(id=kw["items"][0]["price"], unit_amount=9900,
                                                            currency="usd",
                                                            recurring=SO(interval="month", interval_count=1)))]})
                return state[sid]

    class _DB:
        def __init__(self): self.updates = []
        async def update_subscription(self, account_id, **f): self.updates.append((account_id, f))

    db = _DB()
    outcome, effective = await R._move_one(_S, db, 1, "sub_1", "price_new")
    assert outcome == "changed" and effective == "1800000000"
    assert db.updates[-1][1]["provider_base_price_id"] == "price_new"
    # a subscription Stripe has already ended is reconciled, not moved
    state["sub_1"] = SO(id="sub_1", status="canceled", items={"data": []})
    assert (await R._move_one(_S, db, 1, "sub_1", "price_new"))[0] == "skipped_status"


@pytest.mark.asyncio
async def test_the_webhook_reads_a_real_event(db, monkeypatch):
    """The first line of the handler was ``event.get("id", "")`` — so a
    real event raised before anything else could happen, and every
    completed checkout was dropped."""
    monkeypatch.setenv("STRIPE_WEBHOOK_SECRET", "whsec_x")
    await db.upsert_plan("pro", label="Pro", included=["*"], price_monthly_cents=9900,
                         stripe_price_id="price_pro", public=True)
    acct = await db.create_account("Webhook Co", tier="free")
    await db.get_or_create_subscription(acct.id)

    event = SO(id="evt_1", type="checkout.session.completed", data={"object": SO(
        id="cs_1", customer="cus_1", subscription="",
        metadata=SO(account_id=str(acct.id), tier="pro"))})

    class _S:
        class Webhook:
            @staticmethod
            def construct_event(payload, sig, secret): return event
        class error:  # noqa: N801
            class SignatureVerificationError(Exception): ...
    monkeypatch.setattr("capabilities.platform.billing.stripe_client._stripe", lambda: _S)

    out = await StripeBillingProvider().handle_webhook(b"{}", "sig", db)
    assert out["handled"] is True and out["event_type"] == "checkout.session.completed", out
    assert (await db.get_account(acct.id)).tier == "pro", "the customer paid and their plan moved"


def test_an_invoice_payload_projects_from_a_real_object():
    """``_invoice_fields`` read thirteen fields with ``.get`` — so every
    invoice.payment_succeeded raised before a row was written, and a
    paying customer's billing history stayed empty."""
    invoice = SO(id="in_1", subscription="sub_1", customer="cus_1",
                 amount_due=35398, amount_paid=35398, currency="USD", status="paid",
                 hosted_invoice_url="https://stripe/i", invoice_pdf="https://stripe/p",
                 period_start=1800000000, period_end=1802592000,
                 status_transitions=SO(paid_at=1800000001),
                 lines={"data": [SO(id="il_1", period=SO(start=1800000000, end=1802592000))]})
    out = StripeBillingProvider._invoice_fields(invoice)
    assert out["provider_invoice_id"] == "in_1"
    assert (out["amount_due_cents"], out["amount_paid_cents"]) == (35398, 35398)
    assert out["currency"] == "usd" and out["status"] == "paid"
    assert out["provider_subscription_id"] == "sub_1" and out["provider_customer_id"] == "cus_1"
    assert out["period_start"].startswith("20") and out["paid_at"].startswith("20")
    assert out["hosted_invoice_url"] == "https://stripe/i"


@pytest.mark.asyncio
async def test_a_paid_invoice_sends_our_receipt_and_a_failure_never_reaches_stripe(db, monkeypatch):
    """The webhook records the invoice, then sends our receipt.  The
    order matters: an exception in the send would make Stripe retry the
    whole event and the invoice would be recorded twice."""
    monkeypatch.setenv("STRIPE_WEBHOOK_SECRET", "whsec_x")
    monkeypatch.setenv("BILLING_RECEIPT_EMAIL", "1")
    acct = await db.create_account("Receipt Co")
    await db.get_or_create_subscription(acct.id)
    await db.update_subscription(acct.id, provider="stripe", provider_customer_id="cus_1",
                                 billing_email="fallback@co.example")

    event = SO(id="evt_2", type="invoice.payment_succeeded", data={"object": SO(
        id="in_9", customer="cus_1", subscription="", customer_email="adam@co.example",
        amount_due=76098, amount_paid=76098, currency="usd", status="paid",
        hosted_invoice_url="https://stripe/i/9", invoice_pdf="https://stripe/i/9.pdf",
        period_start=1800000000, period_end=1802592000,
        status_transitions=SO(paid_at=1800000001), lines={"data": []},
        metadata=SO(account_id=str(acct.id)))})

    class _S:
        class Webhook:
            @staticmethod
            def construct_event(p, s, k): return event
        class error:  # noqa: N801
            class SignatureVerificationError(Exception): ...
    monkeypatch.setattr("capabilities.platform.billing.stripe_client._stripe", lambda: _S)

    seen: list[dict] = []
    from capabilities.platform.billing import receipt_email as R
    monkeypatch.setattr(R, "fetch_pdf", lambda url: b"%PDF-1.7 x")
    import capabilities.email.smtp as smtp
    monkeypatch.setattr(smtp, "send_email_detailed", lambda **kw: seen.append(kw) or True)

    out = await StripeBillingProvider().handle_webhook(b"{}", "sig", db)
    assert out["handled"] is True
    assert [i["provider_invoice_id"] for i in await db.get_invoices(acct.id)] == ["in_9"]
    assert len(seen) == 1 and seen[0]["to"] == "adam@co.example", "Stripe's own address wins over ours"
    assert seen[0]["attachments"][0][0] == "4truck-invoice-in_9.pdf"

    # a mailer that explodes must not take the webhook with it
    seen.clear()
    def _boom(**kw):
        raise RuntimeError("relay down")
    monkeypatch.setattr(smtp, "send_email_detailed", _boom)
    event2 = SO(id="evt_3", type="invoice.payment_succeeded", data={"object": SO(
        id="in_10", customer="cus_1", subscription="", customer_email="adam@co.example",
        amount_due=100, amount_paid=100, currency="usd", status="paid",
        hosted_invoice_url="", invoice_pdf="", period_start=1800000000, period_end=1802592000,
        status_transitions=SO(paid_at=1800000001), lines={"data": []},
        metadata=SO(account_id=str(acct.id)))})
    class _S2(_S):
        class Webhook:
            @staticmethod
            def construct_event(p, s, k): return event2
    monkeypatch.setattr("capabilities.platform.billing.stripe_client._stripe", lambda: _S2)
    out2 = await StripeBillingProvider().handle_webhook(b"{}", "sig", db)
    assert out2["handled"] is True, "the webhook answered Stripe anyway"
    assert len(await db.get_invoices(acct.id)) == 2

    # The inner layer above absorbs a mailer that refuses.  This is the
    # OUTER one's own reason: a read the send path needs — the account's
    # name for the greeting — failing before any mail is attempted.
    # Without the wrapper the webhook raises, Stripe retries the event,
    # and the invoice is recorded a second time.
    async def _dead_read(_account_id):
        raise RuntimeError("platform db gone")
    real_get_account = db.get_account
    db.get_account = _dead_read
    monkeypatch.setattr(smtp, "send_email_detailed", lambda **kw: seen.append(kw) or True)
    seen.clear()
    event_db = SO(id="evt_5", type="invoice.payment_succeeded", data={"object": SO(
        id="in_12", customer="cus_1", subscription="", customer_email="adam@co.example",
        amount_due=100, amount_paid=100, currency="usd", status="paid",
        hosted_invoice_url="", invoice_pdf="", period_start=1800000000, period_end=1802592000,
        status_transitions=SO(paid_at=1800000001), lines={"data": []},
        metadata=SO(account_id=str(acct.id)))})
    class _S4(_S):
        class Webhook:
            @staticmethod
            def construct_event(p, s, k): return event_db
    monkeypatch.setattr("capabilities.platform.billing.stripe_client._stripe", lambda: _S4)
    out3 = await StripeBillingProvider().handle_webhook(b"{}", "sig", db)
    assert out3["handled"] is True, "a broken read in the receipt path must not reach Stripe"
    assert len(await db.get_invoices(acct.id)) == 3 and seen == []
    db.get_account = real_get_account

    # and with the switch off, nothing is sent at all
    monkeypatch.delenv("BILLING_RECEIPT_EMAIL")
    monkeypatch.setattr(smtp, "send_email_detailed", lambda **kw: seen.append(kw) or True)
    event3 = SO(id="evt_4", type="invoice.payment_succeeded", data={"object": SO(
        id="in_11", customer="cus_1", subscription="", customer_email="adam@co.example",
        amount_due=100, amount_paid=100, currency="usd", status="paid",
        hosted_invoice_url="", invoice_pdf="", period_start=1800000000, period_end=1802592000,
        status_transitions=SO(paid_at=1800000001), lines={"data": []},
        metadata=SO(account_id=str(acct.id)))})
    class _S3(_S):
        class Webhook:
            @staticmethod
            def construct_event(p, s, k): return event3
    monkeypatch.setattr("capabilities.platform.billing.stripe_client._stripe", lambda: _S3)
    await StripeBillingProvider().handle_webhook(b"{}", "sig", db)
    assert seen == []
