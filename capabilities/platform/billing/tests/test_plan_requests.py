"""Asking for a plan that is not sold self-serve.

Enterprise is offered at no price, which in this product means "talk to
us" — and its button used to do nothing at all. The customer who wanted
the biggest thing we sell had nowhere to say so, and the operator never
learned they had asked.

What these pin: the request is RECORDED before anyone is notified (a
failed Telegram send must not lose it), pressing twice joins rather than
duplicates, a priced plan is refused because it has a checkout, and the
case number is something a person can quote.
"""

from __future__ import annotations

import pytest

from adapters.storage.plan_requests import case_number


def test_a_case_number_is_quotable_and_month_stamped():
    assert case_number(7, "2026-09-13T06:00:00+00:00") == "4T-202609-0007"
    assert case_number(1234, "2026-12-01T00:00:00+00:00") == "4T-202612-1234"


@pytest.mark.asyncio
async def test_a_request_is_recorded_with_a_case_number(pg_db):
    db = pg_db
    acct = await db.create_account("BigFleetCo")
    req = await db.create_plan_request(
        acct.id, "enterprise", requested_by=1,
        contact_email="ops@bigfleet.example", note="200 trucks, three yards")
    assert req["joined"] is False
    assert req["case_number"].startswith("4T-")
    assert req["status"] == "open"
    assert req["note"] == "200 trucks, three yards"


@pytest.mark.asyncio
async def test_pressing_twice_joins_the_request_already_open(pg_db):
    """Two case numbers for one conversation is worse than none."""
    db = pg_db
    acct = await db.create_account("TwiceCo")
    first = await db.create_plan_request(acct.id, "enterprise", note="first")
    again = await db.create_plan_request(acct.id, "enterprise", note="second")
    assert again["joined"] is True
    assert again["case_number"] == first["case_number"]
    assert again["note"] == "first", "the first message is the one kept"
    assert len(await db.list_plan_requests()) == 1


@pytest.mark.asyncio
async def test_closing_a_request_lets_the_account_ask_again(pg_db):
    """A conversation that ended should not block the next one."""
    db = pg_db
    acct = await db.create_account("AgainCo")
    first = await db.create_plan_request(acct.id, "enterprise", note="spring")
    assert await db.set_plan_request_status(int(first["id"]), "closed", actor="tg:1")
    second = await db.create_plan_request(acct.id, "enterprise", note="autumn")
    assert second["joined"] is False
    assert second["case_number"] != first["case_number"]


@pytest.mark.asyncio
async def test_the_operator_queue_counts_only_what_is_open(pg_db):
    db = pg_db
    a = await db.create_account("QueueA")
    b = await db.create_account("QueueB")
    r1 = await db.create_plan_request(a.id, "enterprise")
    await db.create_plan_request(b.id, "enterprise")
    assert await db.count_open_plan_requests() == 2
    await db.set_plan_request_status(int(r1["id"]), "contacted", actor="tg:1")
    assert await db.count_open_plan_requests() == 1
    assert len(await db.list_plan_requests(status="contacted")) == 1


@pytest.mark.asyncio
async def test_an_account_sees_its_own_open_requests_but_not_closed_ones(pg_db):
    db = pg_db
    acct = await db.create_account("MineCo")
    req = await db.create_plan_request(acct.id, "enterprise")
    assert [r["case_number"] for r in await db.plan_requests_for_account(acct.id)] \
        == [req["case_number"]]
    await db.set_plan_request_status(int(req["id"]), "closed", actor="tg:1")
    assert await db.plan_requests_for_account(acct.id) == []


def test_a_bad_status_is_refused_rather_than_written():
    """The column is not free-form: a typo would make a request vanish
    from every query that filters by status."""
    import asyncio

    from adapters.storage.plan_requests import PlanRequestsMixin

    class _Store(PlanRequestsMixin):
        _db = None

    with pytest.raises(ValueError):
        asyncio.run(_Store().set_plan_request_status(1, "archived"))


# ── the notification half ─────────────────────────────────────────

def test_the_sales_email_is_optional_and_says_so(monkeypatch):
    """A platform whose mail is not configured must still not swallow a
    customer's request — the row is already written by then."""
    from capabilities.platform.billing import plan_requests as notify
    monkeypatch.delenv("SALES_EMAIL", raising=False)
    assert notify.sales_inbox() == ""
    assert notify.email_sales({"case_number": "4T-1", "tier": "enterprise"}, "Co") is False


def test_the_email_carries_what_a_reply_needs(monkeypatch):
    from capabilities.platform.billing import plan_requests as notify
    monkeypatch.setenv("SALES_EMAIL", "sales@4truck.us")
    sent: dict = {}
    monkeypatch.setattr("capabilities.email.smtp.send_email",
                        lambda **kw: sent.update(kw) or True)
    ok = notify.email_sales(
        {"case_number": "4T-202609-0007", "tier": "enterprise", "account_id": 10000001,
         "contact_email": "ops@bigfleet.example", "note": "200 trucks"},
        "Big Fleet Co")
    assert ok and sent["to"] == "sales@4truck.us"
    assert "4T-202609-0007" in sent["subject"]
    # Press Reply and you are writing to the customer, not to no-reply@.
    assert sent["reply_to"] == "ops@bigfleet.example"
    for needed in ("Big Fleet Co", "enterprise", "ops@bigfleet.example", "200 trucks"):
        assert needed in sent["body"], needed


@pytest.mark.asyncio
async def test_no_operator_to_notify_is_logged_not_raised():
    """The request is already recorded; a notification that cannot go
    out must not undo it."""
    from capabilities.platform.billing import plan_requests as notify
    assert await notify.notify_operators(10000001, {"case_number": "4T-1"}, "Co") == 0


# ── what the person who asked hears back ──────────────────────────

def test_the_asker_is_acknowledged_with_the_number_to_quote(monkeypatch):
    """A request that vanishes into a form looks exactly like one that
    was never sent, and the next thing the customer does is ask again."""
    from capabilities.platform.billing import plan_requests as notify
    monkeypatch.setenv("SALES_EMAIL", "sales@4truck.us")
    sent: dict = {}
    monkeypatch.setattr("capabilities.email.smtp.send_email",
                        lambda **kw: sent.update(kw) or True)
    ok = notify.email_customer(
        {"case_number": "4T-202609-0007", "tier": "enterprise",
         "contact_email": "ops@bigfleet.example", "note": "200 trucks"},
        "Big Fleet Co")
    assert ok and sent["to"] == "ops@bigfleet.example"
    assert "4T-202609-0007" in sent["subject"]
    assert "4T-202609-0007" in sent["body"]
    assert "200 trucks" in sent["body"], "they should see we have the right thing"
    # Replying to the acknowledgement must reach a person, not no-reply@.
    assert sent["reply_to"] == "sales@4truck.us"


def test_nobody_is_emailed_when_no_address_was_given(monkeypatch):
    from capabilities.platform.billing import plan_requests as notify
    called: list = []
    monkeypatch.setattr("capabilities.email.smtp.send_email",
                        lambda **kw: called.append(kw) or True)
    assert notify.email_customer({"case_number": "4T-1", "tier": "enterprise"}, "Co") is False
    assert called == []


def test_the_acknowledgement_falls_back_to_the_reply_address(monkeypatch):
    """Without a sales inbox the customer still needs somewhere to reply."""
    from capabilities.platform.billing import plan_requests as notify
    monkeypatch.delenv("SALES_EMAIL", raising=False)
    monkeypatch.setenv("SMTP_FROM_REPLY_TO", "support@4truck.us")
    sent: dict = {}
    monkeypatch.setattr("capabilities.email.smtp.send_email",
                        lambda **kw: sent.update(kw) or True)
    notify.email_customer(
        {"case_number": "4T-1", "tier": "enterprise", "contact_email": "a@b.example"},
        "Co")
    assert sent["reply_to"] == "support@4truck.us"
