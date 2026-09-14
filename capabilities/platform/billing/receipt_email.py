"""The receipt 4truck sends when an invoice is paid.

Stripe sends one of its own, from Stripe's infrastructure, with a link
to a hosted page.  This is ours: the company's name on the envelope,
the words in the customer's language, and the invoice PDF attached, so
whoever files it for their bookkeeping never leaves their inbox.

One PDF, not two, and that is Stripe's shape rather than a preference:
``invoice.invoice_pdf`` is the only document the API hands out.  The
receipt Stripe's own email offers is generated inside its templates;
what an API consumer can reach is ``charge.receipt_url``, an HTML page.
So the PDF is attached and the hosted page is linked.

Off by default.  ``BILLING_RECEIPT_EMAIL=1`` turns it on, and the point
of the switch is the transition: run it beside Stripe's for a cycle,
read what customers actually receive, and only then turn Stripe's off —
two receipts for one payment is worse than either alone, and no receipt
is worse than both.
"""

from __future__ import annotations

import logging
import os
import urllib.error
import urllib.request

logger = logging.getLogger(__name__)

#: Enough for Stripe's PDF (the probe measured 21 KB) with room to
#: spare, and small enough that a surprise cannot fill a mailbox.
MAX_PDF_BYTES = 5 * 1024 * 1024
FETCH_TIMEOUT_S = 20


def enabled() -> bool:
    return (os.getenv("BILLING_RECEIPT_EMAIL") or "").strip().lower() in ("1", "true", "yes", "on")


#: The envelope. A receipt arrives from an address that says what it is,
#: and a reply goes where the customer was already told to write — the
#: Billing page prints ``billing@4truck.us`` at its foot, so a reply to a
#: receipt must land in the same mailbox or the page is lying.
#: ``SUPPORT_CONTACT`` is deliberately NOT used here: it holds a Telegram
#: handle, and a handle in a Reply-To header is a malformed header.
DEFAULT_FROM = "invoice@4truck.us"
DEFAULT_REPLY_TO = "billing@4truck.us"


def looks_like_email(value: str) -> bool:
    """Enough to keep a non-address out of a header.

    ``"@" in value`` is not enough: ``SUPPORT_CONTACT`` holds
    ``@Allen_Klein``, a Telegram handle, which passes that test and
    becomes a malformed Reply-To and an address no customer can write
    to.  A local part, one ``@``, and a dotted domain.
    """
    value = (value or "").strip()
    if value.count("@") != 1 or " " in value:
        return False
    local, _, domain = value.partition("@")
    return bool(local) and "." in domain and not domain.startswith(".") and not domain.endswith(".")


def _address(var: str, fallback: str) -> str:
    value = (os.getenv(var) or "").strip()
    return value if looks_like_email(value) else fallback


def sender() -> str:
    return _address("BILLING_INVOICE_FROM", DEFAULT_FROM)


def reply_to() -> str:
    return _address("BILLING_CONTACT_EMAIL", DEFAULT_REPLY_TO)


def fetch_pdf(url: str) -> bytes | None:
    """The invoice PDF, or None — never an exception.

    Stripe's PDF link needs no key, which is why it can be attached at
    all.  A fetch that fails must not cost the customer their email: the
    send goes out without the attachment, carrying the link instead.
    """
    url = (url or "").strip()
    if not url.startswith("https://"):
        return None
    try:
        with urllib.request.urlopen(url, timeout=FETCH_TIMEOUT_S) as resp:
            raw = resp.read(MAX_PDF_BYTES + 1)
    except (urllib.error.URLError, OSError, ValueError) as e:
        logger.warning("receipt email: could not fetch the invoice PDF (%s)", e)
        return None
    if len(raw) > MAX_PDF_BYTES:
        logger.warning("receipt email: the invoice PDF is larger than %d bytes; sending the link only", MAX_PDF_BYTES)
        return None
    if raw[:4] != b"%PDF":
        logger.warning("receipt email: what came back from %s is not a PDF", url[:60])
        return None
    return raw


def _money(cents: int, currency: str) -> str:
    amount = f"{int(cents) / 100:,.2f}"
    cur = (currency or "usd").upper()
    return f"${amount}" if cur == "USD" else f"{amount} {cur}"


def compose(*, account_name: str, number: str, amount_cents: int, currency: str,
            period: str, hosted_url: str, support: str,
            subtotal_cents: int = 0, discount_cents: int = 0) -> tuple[str, str]:
    """Subject and body.  Plain words, in the order a person reads them:
    what was paid, for what, where the rest lives.

    A discounted bill shows its arithmetic — a customer who was given
    $100 off should see the $100, not only the smaller number, or the
    gift is invisible and the receipt looks like a price change."""
    money = _money(amount_cents, currency)
    subject = f"Your 4truck receipt — {money}" + (f" (invoice {number})" if number else "")
    lines = [
        f"Hello {account_name}," if account_name else "Hello,",
        "",
        f"We received {money}. Thank you.",
        "",
    ]
    if number:
        lines.append(f"Invoice: {number}")
    if period:
        lines.append(f"Billing period: {period}")
    if discount_cents > 0 and subtotal_cents > 0:
        lines += ["",
                  f"  Amount      {_money(subtotal_cents, currency)}",
                  f"  Promotion  -{_money(discount_cents, currency)}",
                  f"  Total       {money}"]
    lines += ["", "The invoice is attached as a PDF."]
    if hosted_url:
        lines += ["", "You can also open it online — the same invoice, with the payment on it:", f"  {hosted_url}"]
    if support:
        lines += ["", f"A question about this bill? Reply here, or write to {support}."]
    return subject, "\n".join(lines)


def send(*, to: str, account_name: str, invoice: dict, support: str = "") -> bool:
    """Send the receipt for one recorded invoice.  False when it did not
    go — a missing address, a mailer that refused — and never an
    exception: the payment is recorded either way, and a webhook that
    raises is a webhook Stripe retries."""
    to = (to or "").strip()
    if to and not looks_like_email(to):
        logger.warning("receipt email: %r is not an address; invoice %s not sent",
                       to, invoice.get("provider_invoice_id"))
        return False
    if not to:
        logger.info("receipt email: invoice %s has no address to send to", invoice.get("provider_invoice_id"))
        return False
    number = str(invoice.get("provider_invoice_id") or "")
    amount = int(invoice.get("amount_paid_cents") or invoice.get("amount_due_cents") or 0)
    start, end = invoice.get("period_start") or "", invoice.get("period_end") or ""
    period = f"{start[:10]} to {end[:10]}" if start and end else ""
    contact = support if looks_like_email(support) else reply_to()
    subject, body = compose(
        account_name=account_name, number=number, amount_cents=amount,
        currency=str(invoice.get("currency") or "usd"), period=period,
        hosted_url=str(invoice.get("hosted_invoice_url") or ""), support=contact,
        subtotal_cents=int(invoice.get("subtotal_cents") or 0),
        discount_cents=int(invoice.get("discount_cents") or 0))
    pdf = fetch_pdf(str(invoice.get("invoice_pdf_url") or ""))
    attachments = [(f"4truck-invoice-{number or 'latest'}.pdf", pdf, "application/pdf")] if pdf else None
    if not pdf:
        body = body.replace("The invoice is attached as a PDF.",
                            "The invoice is on your Billing page and at the link below.")
    try:
        from capabilities.email.smtp import send_email_detailed
        return bool(send_email_detailed(
            to=to, subject=subject, body=body, attachments=attachments,
            from_address=sender(), from_name="4truck", reply_to=contact))
    except Exception:
        logger.exception("receipt email: sending for invoice %s failed", number)
        return False
