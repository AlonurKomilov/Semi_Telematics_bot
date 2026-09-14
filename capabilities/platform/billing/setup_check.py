"""Is the Stripe wiring real, or only present? — what the console asks.

Env presence is not wiring.  A live key with a Price made in the sandbox,
or ``STRIPE_PRICE_EXTRA_VEHICLE`` holding a Product id (``prod_…``) where
a Price id belongs, both read as "set" — and the second one fails
SILENTLY: every subscription goes out single-line and no extra truck is
ever billed.  That is the failure this module exists to catch, because
nothing else in the chain says a word about it.

So these checks ask Stripe.  Each one is best-effort: a Stripe outage
leaves a check ``unknown``, never an exception the console has to render
as a broken page.  Nothing here writes; it is safe to run on every page
open.
"""

from __future__ import annotations

import asyncio
import logging
import os
from urllib.parse import urlparse

logger = logging.getLogger(__name__)

#: What the webhook endpoint must be subscribed to for the billing chain
#: to close.  The same list as docs/runbooks/stripe-go-live.md.
REQUIRED_EVENTS = (
    "checkout.session.completed",
    "customer.subscription.updated",
    "customer.subscription.deleted",
    "invoice.payment_succeeded",
    "invoice.payment_failed",
)

#: The paths this API answers webhooks on — the provider-named one and
#: the alias.  Matched on the URL's tail so the same endpoint counts on
#: api.4truck.us (no prefix) and on the hosts that mount under /api.
WEBHOOK_PATHS = ("/billing/stripe/webhook", "/billing/webhook")

_OK, _PROBLEM, _UNKNOWN = "ok", "problem", "unknown"


def _check(id_: str, label: str, state: str, note: str) -> dict:
    return {"id": id_, "label": label, "state": state, "note": note}


def stripe_mode() -> str:
    """Which Stripe mode the configured key belongs to.

    Read from the key's own prefix, so it is right even when Stripe is
    unreachable — and it is the difference between a dry run and a real
    charge, which the operator must never have to guess.
    """
    key = (os.getenv("STRIPE_SECRET_KEY") or "").strip()
    if key.startswith(("sk_live_", "rk_live_")):
        return "live"
    if key.startswith(("sk_test_", "rk_test_")):
        return "test"
    return "unknown"


def _check_account(stripe, mode: str) -> dict:
    try:
        acct = stripe.Account.retrieve()
    except Exception as exc:
        return _check("secret_key", "Secret key", _PROBLEM,
                      f"Stripe refused the key ({type(exc).__name__}). Check STRIPE_SECRET_KEY.")
    name = ""
    try:
        name = (acct.settings.dashboard.display_name or "").strip()
    except Exception:
        # A restricted key may not carry settings; the id alone still
        # identifies the account, which is what the operator is checking.
        name = ""
    where = f"{acct.id}" + (f" · {name}" if name else "")
    return _check("secret_key", "Secret key", _OK, f"{mode} mode · {where}")


def _check_extras_price(stripe, mode: str) -> dict:
    label = "Per-extra-truck price"
    pid = (os.getenv("STRIPE_PRICE_EXTRA_VEHICLE") or "").strip()
    if not pid:
        # each plan carries its own extras Price now (checked per plan
        # below); the env one only recognises subscriptions made before
        return _check("extras_price", label, _OK,
                      "No env-wide extras price — each plan carries its own (see Stripe price per plan).")
    if pid.startswith("prod_"):
        return _check("extras_price", label, _PROBLEM,
                      f"{pid} is a Product id, not a Price id. Open the product in "
                      "Stripe and copy the price_… id underneath it.")
    if not pid.startswith("price_"):
        return _check("extras_price", label, _PROBLEM,
                      f"{pid} is not a Price id (those start with price_).")
    try:
        price = stripe.Price.retrieve(pid)
    except Exception as exc:
        return _check("extras_price", label, _PROBLEM,
                      f"Stripe does not know {pid} in {mode} mode ({type(exc).__name__}). "
                      "Price ids do not carry between test and live.")
    wrong = []
    if not price.active:
        wrong.append("archived")
    if price.type != "recurring" or not price.recurring or price.recurring.interval != "month":
        wrong.append("not monthly recurring")
    if price.currency != "usd":
        wrong.append(f"in {price.currency.upper()}, not USD")
    if price.billing_scheme != "per_unit":
        wrong.append("not per-unit (the quantity is the truck count)")
    if price.livemode != (mode == "live"):
        wrong.append("from the other Stripe mode")
    amount = f"${(price.unit_amount or 0) / 100:.2f}/month"
    if wrong:
        return _check("extras_price", label, _PROBLEM, f"{amount} — but it is {', '.join(wrong)}.")
    return _check("extras_price", label, _OK,
                  f"{amount} per extra truck, per unit — legacy: subscriptions made before "
                  "per-plan extras prices; the rollout moves them")


def _check_invoices(stripe, mode: str) -> dict:
    """What a billed customer actually receives.

    Two things are readable and one is not, and the note says which.
    The Stripe account's support email is what a receipt tells the
    customer to reply to — without it the receipt is anonymous.  A
    recorded invoice must carry the two links the Billing page offers,
    the hosted receipt and the PDF, or those buttons are dead.  The
    receipt EMAIL itself is a dashboard switch the API does not expose
    (``settings.invoices`` carries tax ids and nothing about mail), so
    the note names it as the one thing to confirm by hand, once.
    """
    from capabilities.platform.billing.stripe_client import _field
    label = "Invoices and receipts"
    try:
        acct = stripe.Account.retrieve()
    except Exception as exc:
        return _check("invoices", label, _UNKNOWN,
                      f"Could not read the account ({type(exc).__name__}).")
    support = str(_field(_field(acct, "business_profile", {}) or {}, "support_email", "") or "").strip()
    if not support:
        return _check("invoices", label, _PROBLEM,
                      "The Stripe account has no support email, so every receipt reaches the "
                      "customer with nobody to reply to. Stripe → Settings → Business details.")
    try:
        recent = _field(stripe.Invoice.list(limit=1), "data", []) or []
    except Exception as exc:
        return _check("invoices", label, _UNKNOWN,
                      f"Could not list invoices ({type(exc).__name__}).")
    manual = ("Stripe's own receipt email is a dashboard switch the API cannot read — "
              "confirm Settings → Customer emails → Successful payments once.")
    if not recent:
        return _check("invoices", label, _OK,
                      f"No invoice in {mode} mode yet — the first is written when a subscription "
                      f"bills. {manual}")
    inv = recent[0]
    missing = [name for name, key in (("receipt link", "hosted_invoice_url"), ("PDF", "invoice_pdf"))
               if not str(_field(inv, key, "") or "").strip()]
    if missing:
        return _check("invoices", label, _PROBLEM,
                      f"The latest invoice carries no {' and no '.join(missing)} — "
                      "the Billing page's download buttons would be dead.")
    return _check("invoices", label, _OK,
                  f"The latest invoice carries a receipt link and a PDF; replies go to {support}. {manual}")


def _check_receipt_email(latest: dict | None) -> dict:
    """Does 4truck's own receipt email work — not "is it configured".

    Configuration is half an answer; the other half is whether the last
    invoice's receipt actually went out, which only the invoice row
    knows.  So: off is stated plainly (Stripe's is the receipt then);
    on with no invoice yet says what it will do; on with an invoice says
    whether that one was sent, and when it was not, that is a problem —
    the customer paid and heard nothing from us.
    """
    from capabilities.platform.billing import receipt_email
    label = "Receipt email (ours)"
    if not receipt_email.enabled():
        return _check("receipt_email", label, _OK,
                      "Off — Stripe's own receipt is what a customer gets. "
                      "BILLING_RECEIPT_EMAIL=1 adds ours, with the invoice PDF attached.")
    host = (os.getenv("SMTP_HOST") or "").strip()
    envelope = f"from {receipt_email.sender()}, replies to {receipt_email.reply_to()}"
    if not host:
        return _check("receipt_email", label, _PROBLEM,
                      "On, but SMTP_HOST is empty — nothing can be sent, and a paying "
                      "customer would hear nothing from us.")
    if not latest:
        return _check("receipt_email", label, _OK,
                      f"On, nothing billed yet — the first paid invoice sends one, {envelope}.")
    when = str((latest.get("receipt_emailed_at") or "")).strip()
    number = str(latest.get("provider_invoice_id") or "")
    if when:
        return _check("receipt_email", label, _OK,
                      f"The receipt for {number} went out {when[:19].replace('T', ' ')}, {envelope}.")
    return _check("receipt_email", label, _PROBLEM,
                  f"The latest invoice ({number}) has no receipt from us — it was paid and the "
                  "customer heard nothing. See the API log for the reason.")


def _check_webhook(stripe) -> dict:
    label = "Webhook endpoint"
    secret = bool((os.getenv("STRIPE_WEBHOOK_SECRET") or "").strip())
    try:
        endpoints = list(stripe.WebhookEndpoint.list(limit=100).auto_paging_iter())
    except Exception as exc:
        return _check("webhook", label, _UNKNOWN,
                      f"Could not list endpoints ({type(exc).__name__})."
                      + ("" if secret else " STRIPE_WEBHOOK_SECRET is also empty."))
    ours = [e for e in endpoints
            if any(urlparse(e.url).path.endswith(p) for p in WEBHOOK_PATHS)]
    if not ours:
        return _check("webhook", label, _PROBLEM,
                      "No endpoint in this Stripe mode points at "
                      f"{WEBHOOK_PATHS[0]} — checkouts complete in Stripe and "
                      "nothing reaches 4truck.")
    live = [e for e in ours if e.status == "enabled"]
    if not live:
        return _check("webhook", label, _PROBLEM,
                      f"The endpoint at {urlparse(ours[0].url).path} is disabled.")
    ep = live[0]
    if not secret:
        return _check("webhook", label, _PROBLEM,
                      "The endpoint exists but STRIPE_WEBHOOK_SECRET is empty — "
                      "every delivery is refused unsigned (HTTP 400).")
    subscribed = set(ep.enabled_events)
    missing = [e for e in REQUIRED_EVENTS if e not in subscribed and "*" not in subscribed]
    if missing:
        return _check("webhook", label, _PROBLEM,
                      f"{urlparse(ep.url).path} — not subscribed to {', '.join(missing)}.")
    return _check("webhook", label, _OK,
                  f"{urlparse(ep.url).path} · {len(REQUIRED_EVENTS)} events · signed")


def _check_return_url() -> dict:
    """Ask the return URL itself, rather than trusting that a set
    variable is a working address.

    A customer who has just paid is the worst person to hand a 404, and
    an origin that does not serve the dashboard gives exactly that: the
    apex answered 404 for /billing while three base-URL variables were
    all set.  So this fetches the page Stripe will send them to.
    """
    from capabilities.platform.billing.urls import return_origin, return_origin_source
    origin = return_origin()
    named = return_origin_source()
    where = f"{origin}/billing" + (f" ({named})" if named else " (built-in default)")
    import urllib.error
    import urllib.request
    # A named agent, because the edge answers 403 to the default
    # Python one — the probe would then report the customer's page as
    # broken when a browser loads it fine.
    req = urllib.request.Request(
        f"{origin}/billing", method="GET",
        headers={"User-Agent": "4truck-setup-check/1.0"},
    )
    try:
        with urllib.request.urlopen(req, timeout=5) as resp:
            code = resp.status
    except urllib.error.HTTPError as exc:
        code = exc.code
    except Exception as exc:
        return _check("return_url", "Return address", _UNKNOWN,
                      f"{where} — could not be reached from this server "
                      f"({type(exc).__name__}).")
    if code in (401, 403):
        # The host is there and the edge refused US, not a customer.
        return _check("return_url", "Return address", _UNKNOWN,
                      f"{where} — the host answered HTTP {code} to this check "
                      "(bot protection); open it in a browser to be sure.")
    if code >= 400:
        return _check("return_url", "Return address", _PROBLEM,
                      f"{where} answers HTTP {code} — a customer who has just paid "
                      "lands on an error page. Point DASHBOARD_BASE_URL at the host "
                      "that serves the dashboard.")
    return _check("return_url", "Return address", _OK, f"{where} · HTTP {code}")


#: What the sandbox→live switch leaves behind when its SQL step is
#: skipped: plan rows still naming the other mode's Product and Prices.
#: A Save then asks live Stripe to hang a Price on a sandbox Product,
#: which it does not know — the operator sees a failure, not the cause.
OTHER_MODE_HINT = ("belong to the other Stripe mode — run the sandbox→live SQL "
                   "(docs/runbooks/stripe-go-live.md, 4b step 2), then press Create Stripe price again")


def _price_mode_problem(stripe, price_id: str, mode: str) -> str:
    """'' when *price_id* is a Price of this mode; else what is wrong."""
    try:
        price = stripe.Price.retrieve(price_id)
    except Exception as exc:
        # "No such price" is what the other mode's id looks like from here
        return "unknown here" if "No such" in str(exc) else f"unreadable ({type(exc).__name__})"
    if bool(getattr(price, "livemode", False)) != (mode == "live"):
        return "from the other mode"
    return ""


def _check_plan_prices(plans: list[dict], mode: str, stripe=None) -> dict:
    """Every plan a customer can buy needs its own Stripe Price.

    A priced plan whose row has no ``stripe_price_id`` is the step people
    skip: the plan is public, and its checkout falls back to the
    ``STRIPE_PRICE_<TIER>`` env or refuses.  Ids do not carry between
    modes, so this also catches the sandbox ids left behind after a dry
    run.
    """
    label = "Stripe price per plan"
    sellable = [p for p in plans
                if p.get("public") and int(p.get("price_monthly_cents") or 0) > 0]
    if not sellable:
        return _check("plan_prices", label, _PROBLEM,
                      "No plan is both shown to all customers and priced — "
                      "the Billing page has nothing to sell.")
    without = [p["tier"] for p in sellable if not (p.get("stripe_price_id") or "").strip()]
    if without:
        return _check("plan_prices", label, _PROBLEM,
                      f"No Stripe price yet for {', '.join(sorted(without))} — "
                      "press Create Stripe price on each of them below.")
    # a plan that bills extra trucks needs the extras Price too, or its
    # checkout refuses rather than bill at another plan's amount
    no_extra = [p["tier"] for p in sellable
                if int(p.get("extra_vehicle_cents") or 0) > 0
                and not (p.get("stripe_extra_price_id") or "").strip()]
    if no_extra:
        return _check("plan_prices", label, _PROBLEM,
                      f"No Stripe price for the extra truck on {', '.join(sorted(no_extra))} — "
                      "press Create Stripe price on each of them below.")
    # an extras Price from before the extras Product: it sits on the
    # plan's own Product, and Stripe names a bill's line after its
    # Product, so the checkout page prints the plan's name twice
    shared = [p["tier"] for p in sellable
              if (p.get("stripe_extra_price_id") or "").strip()
              and not (p.get("stripe_extra_product_id") or "").strip()]
    if shared:
        return _check("plan_prices", label, _PROBLEM,
                      f"The extra truck on {', '.join(sorted(shared))} shares the plan's Stripe product, "
                      "so a bill prints the plan's name twice — press Update Stripe price on each.")
    # with a key in hand, ask Stripe whether each id is a Price of THIS
    # mode — the ids a dry run leaves behind are the ones this catches
    if stripe is not None:
        wrong = []
        for p in sellable:
            for pid in ((p.get("stripe_price_id") or "").strip(), (p.get("stripe_extra_price_id") or "").strip()):
                if pid and _price_mode_problem(stripe, pid, mode):
                    wrong.append(p["tier"]); break
        if wrong:
            return _check("plan_prices", label, _PROBLEM,
                          f"The Stripe prices on {', '.join(sorted(wrong))} {OTHER_MODE_HINT}.")
    return _check("plan_prices", label, _OK,
                  f"{len(sellable)} public plan{'s' if len(sellable) != 1 else ''} "
                  f"carry a {mode}-mode Stripe price")


def _run_checks(plans: list[dict], latest_invoice: dict | None = None) -> list[dict]:
    """The blocking half — Stripe's SDK is synchronous."""
    mode = stripe_mode()
    key = (os.getenv("STRIPE_SECRET_KEY") or "").strip()
    if not key:
        return [
            _check("secret_key", "Secret key", _PROBLEM,
                   "STRIPE_SECRET_KEY is empty — the API cannot reach Stripe."),
            _check("extras_price", "Per-extra-truck price", _UNKNOWN, "Needs a key to check."),
            _check("webhook", "Webhook endpoint", _UNKNOWN, "Needs a key to check."),
            _check("invoices", "Invoices and receipts", _UNKNOWN, "Needs a key to check."),
            _check_receipt_email(latest_invoice),
            _check_return_url(),
            _check_plan_prices(plans, mode),
        ]
    try:
        import stripe
        stripe.api_key = key
    except ImportError:
        return [_check("secret_key", "Secret key", _PROBLEM,
                       "The stripe package is not installed on this server.")]
    return [
        _check_account(stripe, mode),
        _check_extras_price(stripe, mode),
        _check_webhook(stripe),
        _check_invoices(stripe, mode),
        _check_receipt_email(latest_invoice),
        _check_return_url(),
        _check_plan_prices(plans, mode, stripe),
    ]


async def check_stripe_setup(plans: list[dict], *, provider: str, latest_invoice: dict | None = None) -> dict:
    """What the console shows under "Payment wiring".

    ``provider`` is the configured ``BILLING_PROVIDER``: on ``stub`` the
    checks still run (an operator preparing the switch wants to see them
    go green BEFORE flipping it), and the answer simply says that nothing
    is charged yet.
    """
    try:
        checks = await asyncio.to_thread(_run_checks, plans, latest_invoice)
    except Exception:
        logger.exception("stripe setup check failed")
        checks = [_check("secret_key", "Secret key", _UNKNOWN,
                         "The check itself failed — see the API log.")]
    return {
        "provider": provider,
        "mode": stripe_mode(),
        "checks": checks,
        "ok": all(c["state"] == _OK for c in checks),
    }
