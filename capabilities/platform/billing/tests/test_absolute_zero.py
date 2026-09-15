"""Absolute 0: an account that pays nothing and is still billed.

A comp made an account free and left no trace — no invoice, no receipt,
nothing an accountant could find, because there was nothing in Stripe
to invoice.  Absolute 0 is the same price with the paperwork: every
month the costs are worked out the ordinary way, written down, and sent
— the plan line, the trucks above the included count, and one line that
takes the total to zero.

Stripe is not involved, and that is deliberate.  What these pin is
mostly the consequence of that: the invoice is OURS to write, so it
must be written once and only once, it must keep the prices it was
written with, and the PDF must exist because no Stripe hosts one.
"""

from __future__ import annotations

import json
import os

os.environ.setdefault("ENCRYPTION_KEY", "")
os.environ.setdefault("JWT_SECRET", "test-secret-32-chars-or-more-please-aaaaaaaaaaaa")

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient

from adapters.storage.account_discounts import ABSOLUTE, describe, effective_off
from capabilities.platform.billing import local_invoice as L


def pdf_text(pdf: bytes) -> str:
    """The words a customer reads, out of a reportlab PDF.

    reportlab writes its content streams ASCII85-then-Flate; asserting
    on the bytes would pass on a PDF that renders the wrong numbers.
    """
    import base64
    import re
    import zlib
    out = []
    for m in re.finditer(rb"stream\r?\n(.*?)endstream", pdf, re.S):
        raw = m.group(1).strip()
        for decode in (lambda b: zlib.decompress(base64.a85decode(b, adobe=True)),
                       zlib.decompress, lambda b: b):
            try:
                raw = decode(raw)
                break
            except Exception:
                continue
        out += [s[1:-1].decode("latin-1") for s in re.findall(rb"\((?:\\.|[^\\()])*\)", raw)]
    return " ".join(out)

BILLING = {"tier": "pro", "base_cents": 9900, "included": 10,
           "extras": 102, "extra_unit_cents": 299}
PERIOD = ("2026-09-01T00:00:00+00:00", "2026-10-01T00:00:00+00:00")


def test_the_whole_bill_comes_off():
    grant = {"kind": ABSOLUTE, "status": "active"}
    assert effective_off(grant, 40398) == 40398
    assert effective_off(grant, 0) == 0
    # what the CUSTOMER reads — the operator console keeps the internal
    # name, the bill says what happened
    assert describe(grant) == "Covered by 4truck — nothing to pay"
    assert "Absolute 0" not in describe(grant)
    # and a grant that is over takes nothing off, like any other
    assert effective_off({**grant, "status": "ended"}, 40398) == 0


def test_the_invoice_says_what_was_used_and_what_was_covered():
    inv = L.build(account_id=10000001, account_name="PREMIER TRUCKING GROUP INC",
                  billing=BILLING, discount={"reason": "partner since 2025"},
                  period_start=PERIOD[0], period_end=PERIOD[1])
    assert inv["number"] == "INV-202609-10000001"
    labels = [line["label"] for line in inv["lines"]]
    assert labels[0].startswith("Pro plan (10 trucks included)")
    assert labels[1] == "Extra trucks"
    assert labels[2] == "Covered by 4truck", \
        "the operator's private reason never reaches the invoice"
    assert [line["amount_cents"] for line in inv["lines"]] == [9900, 30498, -40398]
    assert inv["subtotal_cents"] == 40398 and inv["discount_cents"] == 40398
    assert inv["total_cents"] == 0, "nothing to pay"


def test_a_plan_with_nothing_above_the_included_count_has_no_extras_line():
    inv = L.build(account_id=7, account_name="Small Co",
                  billing={**BILLING, "extras": 0}, discount={},
                  period_start=PERIOD[0], period_end=PERIOD[1])
    assert [line["label"] for line in inv["lines"]] == [
        "Pro plan (10 trucks included)", "Covered by 4truck"]
    # a free plan owes nothing to begin with: no lines, and nothing to zero
    free = L.build(account_id=7, account_name="Free Co",
                   billing={"tier": "free", "base_cents": 0, "included": 0,
                            "extras": 0, "extra_unit_cents": 0},
                   discount={}, period_start=PERIOD[0], period_end=PERIOD[1])
    assert free["lines"] == [] and free["subtotal_cents"] == 0 and free["total_cents"] == 0


def test_the_number_is_derived_so_the_month_can_be_run_twice():
    """Idempotence is the whole defence against a second bill: the
    number comes from the period and the account, never a sequence."""
    a = L.invoice_number(10000001, PERIOD[0])
    b = L.invoice_number(10000001, "2026-09-30T23:59:59+00:00")
    assert a == b == "INV-202609-10000001"
    assert L.invoice_number(10000001, "2026-10-01T00:00:00+00:00") == "INV-202610-10000001"
    assert L.invoice_number(2, PERIOD[0]) == "INV-202609-2"


def test_the_row_keeps_the_lines_it_was_written_with():
    """A bill records what was charged at the time.  Re-deriving it from
    today's prices would rewrite history the customer already read."""
    inv = L.build(account_id=1, account_name="Co", billing=BILLING, discount={},
                  period_start=PERIOD[0], period_end=PERIOD[1])
    row = L.to_row(inv)
    assert row["provider"] == "local"
    assert row["amount_due_cents"] == 0 and row["amount_paid_cents"] == 0
    assert row["subtotal_cents"] == 40398 and row["discount_cents"] == 40398
    assert row["status"] == "paid", "nothing is outstanding"
    assert json.loads(row["lines_json"]) == inv["lines"]


def test_the_pdf_exists_because_no_stripe_hosts_one():
    inv = L.build(account_id=10000001, account_name="PREMIER TRUCKING GROUP INC",
                  billing=BILLING, discount={"reason": "partner"},
                  period_start=PERIOD[0], period_end=PERIOD[1])
    pdf = L.render_pdf(inv, issuer={"name": "ABC LEGACY LLC", "support": "billing@4truck.us"})
    assert pdf[:4] == b"%PDF" and len(pdf) > 1000
    # and it renders without an issuer, because a missing setting must
    # not cost a customer their invoice
    assert L.render_pdf(inv)[:4] == b"%PDF"


@pytest.mark.asyncio
async def test_the_month_writes_one_invoice_and_running_it_twice_writes_one(pg_db, monkeypatch):
    from capabilities.platform.billing import jobs
    import infra.platform as _ip
    db = pg_db

    class _Router:
        platform = db
    monkeypatch.setattr(_ip, "get_router", lambda: _Router())
    monkeypatch.delenv("BILLING_RECEIPT_EMAIL", raising=False)      # the email has its own tests

    acct = await db.create_account("Absolute Co", tier="pro")
    await db.get_or_create_subscription(acct.id)
    await db.update_subscription(acct.id, tier="pro", monthly_base_usd=9900,
                                 base_vehicles=10, extra_vehicle_cents=299, vehicle_count=0)
    # no grant yet: Stripe's to bill, so nothing is written here
    assert await jobs.issue_local_invoice(acct.id, period=PERIOD) is None
    assert await db.get_invoices(acct.id) == []

    row = await db.create_account_discount(acct.id, kind=ABSOLUTE, months=0,
                                           reason="partner", granted_by="operator:1")
    await db.mark_account_discount(int(row["id"]), status="active")

    out = await jobs.issue_local_invoice(acct.id, period=PERIOD)
    assert out and out["provider"] == "local" and out["amount_due_cents"] == 0
    invoices = await db.get_invoices(acct.id)
    assert len(invoices) == 1 and invoices[0]["provider_invoice_id"] == f"INV-202609-{acct.id}"
    assert invoices[0]["subtotal_cents"] == 9900, "the plan, with no trucks above the included count"

    await jobs.issue_local_invoice(acct.id, period=PERIOD)
    assert len(await db.get_invoices(acct.id)) == 1, "the same month is one bill, not two"


@pytest.mark.asyncio
async def test_an_amount_grant_is_left_to_stripe(pg_db, monkeypatch):
    """Only Absolute 0 is billed here.  Every other grant is a coupon on
    a Stripe subscription, and writing a local invoice for one would
    bill the customer twice on paper."""
    from capabilities.platform.billing import jobs
    import infra.platform as _ip
    db = pg_db

    class _Router:
        platform = db
    monkeypatch.setattr(_ip, "get_router", lambda: _Router())
    acct = await db.create_account("Coupon Co", tier="pro")
    await db.get_or_create_subscription(acct.id)
    row = await db.create_account_discount(acct.id, kind="amount", amount_off_cents=10000)
    await db.mark_account_discount(int(row["id"]), status="active")
    assert await jobs.issue_local_invoice(acct.id, period=PERIOD) is None
    assert await db.get_invoices(acct.id) == []


@pytest.mark.asyncio
async def test_the_customers_page_reads_nothing_to_pay(pg_db):
    db = pg_db
    acct = await db.create_account("Zero Co", tier="pro")
    await db.get_or_create_subscription(acct.id)
    await db.update_subscription(acct.id, tier="pro", monthly_base_usd=9900,
                                 base_vehicles=0, extra_vehicle_cents=299, vehicle_count=0)
    row = await db.create_account_discount(acct.id, kind=ABSOLUTE, reason="partner")
    await db.mark_account_discount(int(row["id"]), status="active")
    s = await db.get_billing_summary(acct.id)
    assert s["subtotal_cents"] == 9900
    assert s["discount_cents"] == 9900 and s["amount_due_cents"] == 0
    assert s["promotion_label"] == "Covered by 4truck — nothing to pay"
    assert not [i for i in s["line_items"] if i["amount_cents"] < 0], \
        "the deduction belongs under the charges, not among them"
    assert "partner" not in str(s["line_items"]), "the operator's reason is not the customer's"


# ── the operator's grant, and the customer's download ──────────────

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
async def test_granting_absolute_zero_never_reaches_the_provider(api, monkeypatch):
    """The point of Absolute 0 is that Stripe is not asked.  If the
    grant went through the provider the account would acquire a Stripe
    customer, a subscription and a row in Stripe's own reporting — the
    exact thing this discount exists to avoid."""
    c, db = api["client"], api["db"]
    acct = await db.create_account("Absolute Route Co")
    import capabilities.platform.billing as _b
    provider = _b.get_provider()
    asked = []
    monkeypatch.setattr(provider, "apply_discount",
                        lambda *a, **k: asked.append("apply"), raising=False)
    monkeypatch.setattr(provider, "remove_discount",
                        lambda *a, **k: asked.append("remove"), raising=False)

    r = await c.post(f"/api/system/accounts/{acct.id}/discount", json={
        "account_id": acct.id, "kind": ABSOLUTE, "reason": "partner since 2025"})
    assert r.status_code == 201, r.text
    got = r.json()["discount"]
    assert got["kind"] == ABSOLUTE and got["status"] == "active"
    assert got["reason"] == "partner since 2025"
    assert asked == [], "the provider was never asked"

    assert (await c.delete(f"/api/system/accounts/{acct.id}/discount")).status_code == 200
    assert asked == [], "and it is not asked to take one back either"
    assert await db.live_account_discount(acct.id) is None
    rows = await db.list_platform_audit(event="discount_granted", limit=5)
    assert rows, "an operator granting a customer a free year is written down"


@pytest.mark.asyncio
async def test_an_absolute_grant_needs_no_amount_and_rejects_a_bad_kind(api):
    c, db = api["client"], api["db"]
    acct = await db.create_account("No Amount Co")
    # an amount grant still has to carry a number, and a kind we do not
    # bill is refused before it can reach the database
    r = await c.post(f"/api/system/accounts/{acct.id}/discount", json={
        "account_id": acct.id, "kind": "amount"})
    assert r.status_code == 409 and "amount above zero" in r.json()["detail"]
    assert (await c.post(f"/api/system/accounts/{acct.id}/discount", json={
        "account_id": acct.id, "kind": "free"})).status_code == 422
    # absolute is the one kind that needs no number: it takes the lot
    assert (await c.post(f"/api/system/accounts/{acct.id}/discount", json={
        "account_id": acct.id, "kind": ABSOLUTE})).status_code == 201


@pytest_asyncio.fixture
async def customers(pg_db, monkeypatch):
    """Two accounts with one invoice each — the download asked by both."""
    from adapters.storage import Role
    from interfaces.api.auth import create_jwt
    db = pg_db
    monkeypatch.setenv("BILLING_PROVIDER", "stub")
    mine = await db.create_account("Mine Co", tier="pro")
    other = await db.create_account("Neighbour Co", tier="pro")
    mine_owner = await db.create_user(950001, mine.id, role=Role.OWNER)
    other_owner = await db.create_user(950002, other.id, role=Role.OWNER)
    for acct in (mine, other):
        inv = L.build(account_id=acct.id, account_name=acct.name, billing=BILLING,
                      discount={"reason": "partner"},
                      period_start=PERIOD[0], period_end=PERIOD[1])
        await db.record_invoice(acct.id, provider_invoice_id=inv["number"], **L.to_row(inv))
    import infra.platform as _cp
    monkeypatch.setattr(_cp, "_db", db)
    from interfaces.api.app import create_api
    async with AsyncClient(transport=ASGITransport(app=create_api()), base_url="http://testserver") as client:
        yield {"client": client, "db": db, "mine": mine, "other": other,
               "mine_hdr": {"Authorization": f"Bearer {create_jwt(mine_owner.telegram_id, mine.id, 'owner')}"},
               "other_hdr": {"Authorization": f"Bearer {create_jwt(other_owner.telegram_id, other.id, 'owner')}"}}


@pytest.mark.asyncio
async def test_the_download_is_a_pdf_and_only_of_the_callers_own_invoice(customers):
    """Invoice numbers are derived, so a neighbour's number is guessable
    by construction — the route must answer on the account, not on the
    number."""
    c = customers
    mine_no = f"INV-202609-{c['mine'].id}"
    other_no = f"INV-202609-{c['other'].id}"

    r = await c["client"].get(f"/api/billing/invoices/{mine_no}/pdf", headers=c["mine_hdr"])
    assert r.status_code == 200, r.text
    assert r.headers["content-type"].startswith("application/pdf")
    assert r.content[:4] == b"%PDF"

    r = await c["client"].get(f"/api/billing/invoices/{other_no}/pdf", headers=c["mine_hdr"])
    assert r.status_code == 404, "a guessed number is not a key to someone else's money"
    assert (await c["client"].get(f"/api/billing/invoices/{mine_no}/pdf")).status_code in (401, 403)


@pytest.mark.asyncio
async def test_the_download_is_rebuilt_from_the_lines_not_from_todays_prices(customers):
    """A customer who downloads last year's bill must see last year's
    prices.  The PDF is rebuilt from the row's own lines, so raising the
    plan price tomorrow cannot rewrite a bill already sent."""
    c, db = customers["client"], customers["db"]
    acct = customers["mine"]
    await db.get_or_create_subscription(acct.id)
    await db.update_subscription(acct.id, tier="pro", monthly_base_usd=99900,
                                 base_vehicles=0, extra_vehicle_cents=9999, vehicle_count=50)
    r = await c.get(f"/api/billing/invoices/INV-202609-{acct.id}/pdf", headers=customers["mine_hdr"])
    assert r.status_code == 200
    text = pdf_text(r.content)
    assert "102 $2.99 $304.98" in text, "the extra trucks at the price they were billed at"
    assert "$999.00" not in text and "$99.99" not in text, "today's prices never reach a bill already written"
    assert "Subtotal $403.98" in text and "Total $0.00" in text


# ── the operator writing a month that nobody billed ────────────────

def test_a_month_is_named_the_way_a_person_writes_one():
    from capabilities.platform.billing.jobs import month_window
    assert month_window("2026-09") == ("2026-09-01T00:00:00+00:00", "2026-10-01T00:00:00+00:00")
    assert month_window("2026-12") == ("2026-12-01T00:00:00+00:00", "2027-01-01T00:00:00+00:00")
    for bad in ("2026", "september", "2026-13", ""):
        with pytest.raises(ValueError):
            month_window(bad)


@pytest.mark.asyncio
async def test_the_operator_can_write_a_month_that_went_by_unbilled(api, monkeypatch):
    """An account switched to Absolute 0 today has months behind it
    nobody billed.  Waiting for the 1st would leave the customer with an
    empty Billing page and nothing to download."""
    c, db = api["client"], api["db"]
    monkeypatch.delenv("BILLING_RECEIPT_EMAIL", raising=False)
    acct = await db.create_account("Backfill Co", tier="pro")
    await db.get_or_create_subscription(acct.id)
    await db.update_subscription(acct.id, tier="pro", monthly_base_usd=50000,
                                 base_vehicles=0, extra_vehicle_cents=0, vehicle_count=0)

    # before the grant it is Stripe's to bill, and the refusal says so
    r = await c.post(f"/api/system/accounts/{acct.id}/invoice?month=2026-07")
    assert r.status_code == 409 and "Grant Absolute 0 first" in r.json()["detail"]

    assert (await c.post(f"/api/system/accounts/{acct.id}/discount", json={
        "account_id": acct.id, "kind": ABSOLUTE, "reason": "partner"})).status_code == 201

    for month in ("2026-07", "2026-08"):
        r = await c.post(f"/api/system/accounts/{acct.id}/invoice?month={month}")
        assert r.status_code == 200, r.text
        inv = r.json()["invoice"]
        assert inv["number"] == f"INV-{month.replace('-', '')}-{acct.id}"
        assert inv["amount_due_cents"] == 0 and inv["subtotal_cents"] == 50000

    assert len(await db.get_invoices(acct.id)) == 2, "two months, two invoices"
    # and asking again for a month already written returns that one
    again = await c.post(f"/api/system/accounts/{acct.id}/invoice?month=2026-07")
    assert again.status_code == 200
    assert len(await db.get_invoices(acct.id)) == 2, "never a second bill for one month"

    assert (await c.post(f"/api/system/accounts/{acct.id}/invoice?month=july")).status_code == 400
    assert (await c.post("/api/system/accounts/999999/invoice?month=2026-07")).status_code == 404


@pytest.mark.asyncio
async def test_a_past_month_is_billed_on_that_months_truck_count(api, monkeypatch):
    """Trucks are bought and sold.  Issuing July from today's registry
    would bill a month that never happened."""
    c, db = api["client"], api["db"]
    monkeypatch.delenv("BILLING_RECEIPT_EMAIL", raising=False)
    acct = await db.create_account("Fleet Changed Co", tier="pro")
    await db.get_or_create_subscription(acct.id)
    # the plan as it stands: $99, 10 trucks included, $2.99 each
    await db.update_subscription(acct.id, tier="pro", monthly_base_usd=9900,
                                 base_vehicles=10, extra_vehicle_cents=299, vehicle_count=20)
    # July, when the account ran 60
    await db.record_usage_snapshot(
        acct.id, period_start="2026-07-01T00:00:00+00:00",
        period_end="2026-08-01T00:00:00+00:00", vehicle_count=60,
        user_count=3, ai_queries=0, base_vehicles=10,
        monthly_base_cents=9900, extra_vehicle_cents=299,
        active_vehicles=60)
    row = await db.create_account_discount(acct.id, kind=ABSOLUTE, reason="partner")
    await db.mark_account_discount(int(row["id"]), status="active")

    r = await c.post(f"/api/system/accounts/{acct.id}/invoice?month=2026-07")
    assert r.status_code == 200, r.text
    july = r.json()["invoice"]
    assert july["subtotal_cents"] == 9900 + 50 * 299, "50 over the included count, as July had"

    # a month with no snapshot falls back to the registry as it stands
    # rather than failing — and never borrows another month's count
    r = await c.post(f"/api/system/accounts/{acct.id}/invoice?month=2026-06")
    assert r.status_code == 200
    june = r.json()["invoice"]
    assert june["subtotal_cents"] == 9900, "the plan, and no trucks on the registry"
    assert june["subtotal_cents"] != july["subtotal_cents"]


@pytest.mark.asyncio
async def test_backfilling_history_can_be_written_without_mailing_it(api, monkeypatch):
    """Months that closed long ago are written so the record exists, not
    to tell the customer something.  Four receipts arriving at once for
    periods nobody billed reads as a billing system in trouble."""
    c, db = api["client"], api["db"]
    monkeypatch.setenv("BILLING_RECEIPT_EMAIL", "1")
    sent: list[str] = []
    import capabilities.platform.billing.receipt_email as _re
    monkeypatch.setattr(_re, "send_local", lambda **kw: (sent.append(kw["to"]), True)[1])

    acct = await db.create_account("Quiet Backfill Co", tier="pro")
    await db.get_or_create_subscription(acct.id)
    await db.update_subscription(acct.id, tier="pro", monthly_base_usd=9900,
                                 base_vehicles=10, extra_vehicle_cents=299,
                                 billing_email="owner@quiet.com")
    row = await db.create_account_discount(acct.id, kind=ABSOLUTE, reason="partner")
    await db.mark_account_discount(int(row["id"]), status="active")

    for month in ("2026-05", "2026-06"):
        r = await c.post(f"/api/system/accounts/{acct.id}/invoice?month={month}&send=false")
        assert r.status_code == 200, r.text
    assert len(await db.get_invoices(acct.id)) == 2, "the record is there"
    assert sent == [], "and nobody was emailed about a month that closed long ago"

    # the ordinary monthly path still tells them
    r = await c.post(f"/api/system/accounts/{acct.id}/invoice?month=2026-07")
    assert r.status_code == 200
    assert sent == ["owner@quiet.com"], "a current invoice is still sent"
