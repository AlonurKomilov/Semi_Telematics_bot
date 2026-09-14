"""The console's wiring check answers from Stripe, not from the environment.

Two failures cost money and neither is visible to an env-presence check:
a Price id from the other Stripe mode, and a Product id (``prod_…``)
pasted where a Price id belongs — the second bills no extra truck at
all, silently, because the subscription simply goes out single-line.
The owner hit exactly that one on 2026-09-11.

A third is not about Stripe at all: the address a paying customer is
returned to. It was the apex, which answers 404 for /billing, so the
customer's reward for paying was an error page.
"""

from __future__ import annotations

import pytest

from tests._repo import REPO

from capabilities.platform.billing import setup_check


class _Price:
    def __init__(self, **kw):
        self.active = kw.get("active", True)
        self.type = kw.get("type", "recurring")
        self.currency = kw.get("currency", "usd")
        self.billing_scheme = kw.get("billing_scheme", "per_unit")
        self.livemode = kw.get("livemode", False)
        self.unit_amount = kw.get("unit_amount", 299)
        self.recurring = kw.get("recurring", type("R", (), {"interval": "month"})())


def _state(checks, id_):
    return next(c["state"] for c in checks if c["id"] == id_)


def _note(checks, id_):
    return next(c["note"] for c in checks if c["id"] == id_)


@pytest.fixture
def stripe_env(monkeypatch):
    monkeypatch.setenv("STRIPE_SECRET_KEY", "sk_test_x")
    monkeypatch.setenv("STRIPE_WEBHOOK_SECRET", "whsec_x")
    monkeypatch.setenv("STRIPE_PRICE_EXTRA_VEHICLE", "price_x")
    monkeypatch.setenv("DASHBOARD_BASE_URL", "https://dash.example")
    for k in ("AUTH_BASE_URL", "APP_BASE_URL"):
        monkeypatch.delenv(k, raising=False)


def test_the_mode_comes_from_the_key_so_it_is_right_without_stripe(monkeypatch):
    monkeypatch.setenv("STRIPE_SECRET_KEY", "sk_live_abc")
    assert setup_check.stripe_mode() == "live"
    monkeypatch.setenv("STRIPE_SECRET_KEY", "sk_test_abc")
    assert setup_check.stripe_mode() == "test"
    monkeypatch.setenv("STRIPE_SECRET_KEY", "")
    assert setup_check.stripe_mode() == "unknown"


# ── the extras price ──────────────────────────────────────────────

def test_a_product_id_where_a_price_belongs_is_named_exactly(monkeypatch, stripe_env):
    """The silent one: everything looks configured and no extra truck bills."""
    monkeypatch.setenv("STRIPE_PRICE_EXTRA_VEHICLE", "prod_VExzhOQI2PHykM")
    check = setup_check._check_extras_price(object(), "test")
    assert check["state"] == "problem"
    assert "is a Product id, not a Price id" in check["note"]
    assert "price_" in check["note"], "the note must say what to copy instead"


def test_a_price_from_the_other_mode_is_refused(monkeypatch, stripe_env):
    class _S:
        class Price:
            @staticmethod
            def retrieve(_id): return _Price(livemode=True)   # a live Price…
    check = setup_check._check_extras_price(_S, "test")        # …under a test key
    assert check["state"] == "problem"
    assert "other Stripe mode" in check["note"]


def test_a_wrongly_shaped_price_says_which_way(monkeypatch, stripe_env):
    class _S:
        class Price:
            @staticmethod
            def retrieve(_id):
                return _Price(currency="eur", billing_scheme="tiered", active=False)
    check = setup_check._check_extras_price(_S, "test")
    assert check["state"] == "problem"
    for expected in ("archived", "EUR", "per-unit"):
        assert expected in check["note"]


def test_a_good_price_passes_and_says_the_amount(monkeypatch, stripe_env):
    class _S:
        class Price:
            @staticmethod
            def retrieve(_id): return _Price()
    check = setup_check._check_extras_price(_S, "test")
    assert check["state"] == "ok" and "$2.99/month" in check["note"]


# ── the webhook ───────────────────────────────────────────────────

class _Endpoint:
    def __init__(self, url, events=None, status="enabled"):
        self.url = url
        self.status = status
        self.enabled_events = list(events if events is not None else setup_check.REQUIRED_EVENTS)


def _stripe_with(endpoints):
    class _S:
        class WebhookEndpoint:
            @staticmethod
            def list(limit=100):
                return type("L", (), {"auto_paging_iter": staticmethod(lambda: iter(endpoints))})()
    return _S


def test_an_endpoint_on_either_path_counts(stripe_env):
    for path in setup_check.WEBHOOK_PATHS:
        check = setup_check._check_webhook(_stripe_with([_Endpoint(f"https://api.4truck.us{path}")]))
        assert check["state"] == "ok", path
    # and the same route mounted under /api on the dashboard host
    check = setup_check._check_webhook(
        _stripe_with([_Endpoint("https://dash.4truck.us/api/billing/stripe/webhook")]))
    assert check["state"] == "ok"


def test_an_endpoint_pointing_somewhere_else_is_not_ours(stripe_env):
    check = setup_check._check_webhook(_stripe_with([_Endpoint("https://example.com/hooks")]))
    assert check["state"] == "problem"
    assert "nothing reaches 4truck" in check["note"]


def test_missing_events_are_named(stripe_env):
    short = [e for e in setup_check.REQUIRED_EVENTS if e != "invoice.payment_failed"]
    check = setup_check._check_webhook(
        _stripe_with([_Endpoint("https://api.4truck.us/billing/stripe/webhook", short)]))
    assert check["state"] == "problem" and "invoice.payment_failed" in check["note"]


def test_an_endpoint_without_a_signing_secret_is_refused_at_the_door(monkeypatch, stripe_env):
    monkeypatch.setenv("STRIPE_WEBHOOK_SECRET", "")
    check = setup_check._check_webhook(
        _stripe_with([_Endpoint("https://api.4truck.us/billing/stripe/webhook")]))
    assert check["state"] == "problem" and "400" in check["note"]


# ── the return address ────────────────────────────────────────────

def test_an_origin_that_404s_is_a_problem_not_a_detail(monkeypatch, stripe_env):
    import urllib.error

    def _raise(*_a, **_kw):
        raise urllib.error.HTTPError("u", 404, "Not Found", None, None)
    monkeypatch.setattr("urllib.request.urlopen", _raise)
    check = setup_check._check_return_url()
    assert check["state"] == "problem"
    assert "404" in check["note"] and "just paid" in check["note"]


def test_an_edge_refusing_the_checker_is_unknown_not_broken(monkeypatch, stripe_env):
    """Cloudflare answers 403 to this check and 200 to a browser; calling
    the customer's page broken would send the operator chasing nothing."""
    import urllib.error

    def _raise(*_a, **_kw):
        raise urllib.error.HTTPError("u", 403, "Forbidden", None, None)
    monkeypatch.setattr("urllib.request.urlopen", _raise)
    assert setup_check._check_return_url()["state"] == "unknown"


# ── the plans ─────────────────────────────────────────────────────

def test_a_priced_offered_plan_without_a_stripe_price_is_named(stripe_env):
    rows = [
        {"tier": "pro", "public": 1, "price_monthly_cents": 9900, "stripe_price_id": ""},
        {"tier": "starter", "public": 1, "price_monthly_cents": 4900, "stripe_price_id": "price_s"},
        {"tier": "free", "public": 0, "price_monthly_cents": 0, "stripe_price_id": ""},
    ]
    check = setup_check._check_plan_prices(rows, "test")
    assert check["state"] == "problem"
    assert "pro" in check["note"] and "starter" not in check["note"]
    # The note names the BUTTON the operator will look for, and the two
    # have to move together: the button was renamed from "Save" to
    # "Create Stripe price" for a priced plan carrying no price yet, and
    # for one commit the card still said "press Save".
    plans_page = (REPO / "interfaces" / "system_dashboard" / "src" / "pages" / "Plans.tsx").read_text()
    label = "Create Stripe price"
    assert label in check["note"], "the note must name the act that fixes it"
    assert f"'{label}'" in plans_page, (
        f"the note tells the operator to press {label!r} and no button on the Plans page says it")


def test_a_plan_price_from_the_other_mode_is_named_with_the_runbook_step(stripe_env):
    """The state a skipped sandbox→live SQL step leaves: the rows still
    name sandbox Prices, live Stripe does not know them, and every
    Create Stripe price fails.  With a key in hand the card asks Stripe
    and says so — and says which step fixes it."""
    class _S:
        class Price:
            @staticmethod
            def retrieve(pid):
                if pid == "price_sandbox_pro":
                    raise RuntimeError("No such price: 'price_sandbox_pro'")     # live key, sandbox id
                return _Price(livemode=False)                                  # a test-mode Price…
    rows = [
        {"tier": "pro", "public": 1, "price_monthly_cents": 9900, "stripe_price_id": "price_sandbox_pro",
         "extra_vehicle_cents": 299, "stripe_extra_price_id": "price_sandbox_pro_x",
         "stripe_extra_product_id": "prod_sandbox_pro_x"},
        {"tier": "starter", "public": 1, "price_monthly_cents": 4900, "stripe_price_id": "price_s",
         "extra_vehicle_cents": 0},
    ]
    check = setup_check._check_plan_prices(rows, "live", _S)                # …under a LIVE key
    assert check["state"] == "problem"
    assert "pro" in check["note"] and "starter" in check["note"]
    assert "other Stripe mode" in check["note"] and "4b step 2" in check["note"]
    # without a key nothing is asked of Stripe and the ids pass on presence alone, as before
    assert setup_check._check_plan_prices(rows, "live")["state"] == "ok"


def test_an_extras_price_sharing_the_plans_product_is_named(stripe_env):
    """Stripe names a bill's line after its Product.  An extras Price
    made before the extras Product sits on the plan's own, so the
    checkout page printed the plan's name twice — and the card has to
    say so before the operator goes looking for a disabled button."""
    rows = [{"tier": "gold", "public": 1, "price_monthly_cents": 15000, "stripe_price_id": "price_g",
             "extra_vehicle_cents": 499, "stripe_extra_price_id": "price_gx", "stripe_extra_product_id": ""}]
    check = setup_check._check_plan_prices(rows, "live")
    assert check["state"] == "problem"
    assert "gold" in check["note"] and "twice" in check["note"]
    # The note names a button, so the button has to exist AND be
    # pressable for this shape — a card that says "press Update Stripe
    # price" beside a disabled "No changes" is worse than silence, and
    # that is exactly the state this arrived in.
    plans_page = (REPO / "interfaces" / "system_dashboard" / "src" / "pages" / "Plans.tsx").read_text()
    assert "Update Stripe price" in check["note"] and "'Update Stripe price'" in plans_page
    assert "missingPrice || staleExtraPrice" in plans_page, (
        "the Plans page must ARM Save on the same shape the card names — "
        "the button is what the note tells the operator to press")
    # and that term must be computed from the same two fields this check
    # reads, not stubbed: the card naming a button nobody can press is
    # the state this arrived in, and it looked fine from either side
    arming = plans_page.split("const staleExtraPrice", 1)[1].split(";", 1)[0]
    for field in ("stripe_extra_price_id", "stripe_extra_product_id", "extra_vehicle_cents"):
        assert field in arming, f"staleExtraPrice ignores {field}: {arming.strip()[:80]}"
    # once it has its own Product the card is quiet again
    fixed = [{**rows[0], "stripe_extra_product_id": "prod_gx"}]
    assert setup_check._check_plan_prices(fixed, "live")["state"] == "ok"


def test_nothing_to_sell_is_itself_the_problem(stripe_env):
    rows = [{"tier": "free", "public": 1, "price_monthly_cents": 0, "stripe_price_id": ""}]
    assert setup_check._check_plan_prices(rows, "test")["state"] == "problem"


# ── the whole answer ──────────────────────────────────────────────

@pytest.mark.asyncio
async def test_without_a_key_the_answer_is_honest_rather_than_empty(monkeypatch):
    monkeypatch.setenv("STRIPE_SECRET_KEY", "")
    monkeypatch.setenv("DASHBOARD_BASE_URL", "https://dash.example")
    monkeypatch.setattr("urllib.request.urlopen", lambda *_a, **_kw: (_ for _ in ()).throw(OSError("no net")))
    out = await setup_check.check_stripe_setup([], provider="stub")
    assert out["provider"] == "stub" and out["ok"] is False
    assert _state(out["checks"], "secret_key") == "problem"
    assert _state(out["checks"], "extras_price") == "unknown"


@pytest.mark.asyncio
async def test_a_broken_check_never_breaks_the_page(monkeypatch):
    monkeypatch.setattr(setup_check, "_run_checks", lambda _p: (_ for _ in ()).throw(RuntimeError("boom")))
    out = await setup_check.check_stripe_setup([], provider="stripe")
    assert out["ok"] is False and out["checks"][0]["state"] == "unknown"


# ── what a billed customer receives ───────────────────────────────

def _acct(support_email=None):
    class _A:
        business_profile = {"support_email": support_email}
    return _A()


def _inv(hosted="https://stripe/i", pdf="https://stripe/p"):
    class _I:
        hosted_invoice_url = hosted
        invoice_pdf = pdf
    return _I()


def test_a_receipt_with_nobody_to_reply_to_is_a_problem(stripe_env):
    """Stripe puts the account's support email on every receipt.  This
    live account had none, so each receipt would reach a customer
    anonymous."""
    class _S:
        Account = type("A", (), {"retrieve": staticmethod(lambda: _acct(None))})
    check = setup_check._check_invoices(_S, "live")
    assert check["state"] == "problem"
    assert "support email" in check["note"] and "Business details" in check["note"]


def test_the_note_names_the_one_switch_the_api_cannot_read(stripe_env):
    """``settings.invoices`` carries tax ids and nothing about mail, so
    whether Stripe emails a receipt at all is only knowable by looking.
    The note has to say that rather than imply it was checked."""
    class _S:
        Account = type("A", (), {"retrieve": staticmethod(lambda: _acct("help@4truck.us"))})
        Invoice = type("I", (), {"list": staticmethod(lambda limit=1: {"data": []})})
    check = setup_check._check_invoices(_S, "live")
    assert check["state"] == "ok"
    assert "Customer emails" in check["note"] and "cannot read" in check["note"]


def test_an_invoice_without_its_links_would_leave_dead_buttons(stripe_env):
    """The Billing page offers both, from the row the webhook stored."""
    class _S:
        Account = type("A", (), {"retrieve": staticmethod(lambda: _acct("help@4truck.us"))})
        Invoice = type("I", (), {"list": staticmethod(lambda limit=1: {"data": [_inv(pdf="")]})})
    check = setup_check._check_invoices(_S, "live")
    assert check["state"] == "problem" and "PDF" in check["note"]

    class _Ok:
        Account = type("A", (), {"retrieve": staticmethod(lambda: _acct("help@4truck.us"))})
        Invoice = type("I", (), {"list": staticmethod(lambda limit=1: {"data": [_inv()]})})
    good = setup_check._check_invoices(_Ok, "live")
    assert good["state"] == "ok" and "help@4truck.us" in good["note"]
    # and the page really does offer both, or the note promises what it cannot
    page = (REPO / "interfaces" / "dashboard" / "src" / "features" / "billing" / "Billing.tsx").read_text()
    assert "invoice_pdf_url" in page and "hosted_invoice_url" in page
    assert page.count("target=\"_blank\" rel=\"noopener noreferrer\"") >= 2


# ── our receipt service, on the same card as the rest ─────────────

def test_the_card_says_whether_our_receipt_actually_went_out(monkeypatch):
    """Configuration is half an answer.  The other half is the last
    invoice: paid, and did the customer hear from us."""
    monkeypatch.setenv("SMTP_HOST", "smtp.resend.com")
    monkeypatch.delenv("BILLING_RECEIPT_EMAIL", raising=False)
    off = setup_check._check_receipt_email(None)
    assert off["state"] == "ok" and "Off" in off["note"] and "Stripe's own" in off["note"]

    monkeypatch.setenv("BILLING_RECEIPT_EMAIL", "1")
    fresh = setup_check._check_receipt_email(None)
    assert fresh["state"] == "ok" and "nothing billed yet" in fresh["note"]
    assert "invoice@4truck.us" in fresh["note"] and "billing@4truck.us" in fresh["note"]

    sent = setup_check._check_receipt_email(
        {"provider_invoice_id": "in_9", "receipt_emailed_at": "2026-09-14T05:30:00+00:00"})
    assert sent["state"] == "ok" and "in_9" in sent["note"] and "2026-09-14 05:30:00" in sent["note"]

    # paid, and we said nothing — the state the operator must see
    silent = setup_check._check_receipt_email({"provider_invoice_id": "in_9", "receipt_emailed_at": None})
    assert silent["state"] == "problem" and "heard nothing" in silent["note"]

    monkeypatch.setenv("SMTP_HOST", "")
    assert setup_check._check_receipt_email(None)["state"] == "problem"
