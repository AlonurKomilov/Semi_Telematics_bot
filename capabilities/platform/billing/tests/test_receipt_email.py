"""The receipt 4truck sends when an invoice is paid.

Stripe sends its own; this is ours, with the invoice PDF attached so
whoever files it never leaves their inbox.  What these pin is mostly
what must NOT happen: a payment is already recorded by the time this
runs, so nothing here may raise, and nothing here may cost the customer
their email — a PDF that will not download is sent as a link, an
address we do not have is logged and dropped, a mailer that refuses is
a False and not a crash.

One PDF and not two is Stripe's shape, not a preference:
``invoice.invoice_pdf`` is the only document the API hands out.
"""

from __future__ import annotations

import os

os.environ.setdefault("ENCRYPTION_KEY", "")

import pytest

from capabilities.platform.billing import receipt_email as R

INVOICE = {
    "provider_invoice_id": "in_1", "amount_paid_cents": 76098, "amount_due_cents": 76098,
    "currency": "usd", "period_start": "2026-09-14T00:00:00+00:00",
    "period_end": "2026-10-14T00:00:00+00:00",
    "hosted_invoice_url": "https://stripe/i/1", "invoice_pdf_url": "https://stripe/i/1.pdf",
}


@pytest.fixture
def sent(monkeypatch):
    """Capture what reaches the mailer."""
    box: list[dict] = []
    def _send(**kw):
        box.append(kw)
        return True
    import capabilities.email.smtp as smtp
    monkeypatch.setattr(smtp, "send_email_detailed", _send)
    return box


def test_off_until_it_is_turned_on(monkeypatch):
    monkeypatch.delenv("BILLING_RECEIPT_EMAIL", raising=False)
    assert R.enabled() is False
    for value in ("1", "true", "YES", "on"):
        monkeypatch.setenv("BILLING_RECEIPT_EMAIL", value)
        assert R.enabled() is True, value
    monkeypatch.setenv("BILLING_RECEIPT_EMAIL", "0")
    assert R.enabled() is False


def test_the_words_say_what_was_paid_and_for_what():
    subject, body = R.compose(
        account_name="Premier Trucking Group", number="in_1", amount_cents=76098,
        currency="usd", period="2026-09-14 to 2026-10-14",
        hosted_url="https://stripe/i/1", support="support@4truck.us")
    assert subject == "Your 4truck receipt — $760.98 (invoice in_1)"
    assert "We received $760.98" in body and "Premier Trucking Group" in body
    assert "2026-09-14 to 2026-10-14" in body
    assert "attached as a PDF" in body and "https://stripe/i/1" in body
    assert "support@4truck.us" in body
    # a non-USD amount keeps its currency rather than pretending to be dollars
    _, eur = R.compose(account_name="X", number="", amount_cents=5000, currency="eur",
                       period="", hosted_url="", support="")
    assert "50.00 EUR" in eur


def test_the_pdf_is_attached_with_a_name_a_person_can_file(sent, monkeypatch):
    monkeypatch.setattr(R, "fetch_pdf", lambda url: b"%PDF-1.7 fake")
    assert R.send(to="adam@premier.example", account_name="Premier", invoice=INVOICE,
                  support="support@4truck.us") is True
    kw = sent[0]
    assert kw["to"] == "adam@premier.example" and kw["reply_to"] == "support@4truck.us"
    name, blob, mime = kw["attachments"][0]
    assert name == "4truck-invoice-in_1.pdf" and blob == b"%PDF-1.7 fake" and mime == "application/pdf"
    assert "attached as a PDF" in kw["body"]


def test_a_pdf_that_will_not_download_still_sends_the_receipt(sent, monkeypatch):
    """The payment happened.  A fetch that fails must cost the customer
    the attachment, not the email."""
    monkeypatch.setattr(R, "fetch_pdf", lambda url: None)
    assert R.send(to="a@b.example", account_name="Co", invoice=INVOICE) is True
    kw = sent[0]
    assert kw["attachments"] is None
    assert "attached as a PDF" not in kw["body"] and "Billing page" in kw["body"]
    assert "https://stripe/i/1" in kw["body"], "the link is what is left to offer"


def test_no_address_and_a_refusing_mailer_are_both_a_quiet_false(sent, monkeypatch):
    monkeypatch.setattr(R, "fetch_pdf", lambda url: b"%PDF-x")
    assert R.send(to="  ", account_name="Co", invoice=INVOICE) is False
    assert sent == [], "nothing was attempted"
    import capabilities.email.smtp as smtp
    def _boom(**kw):
        raise RuntimeError("relay down")
    monkeypatch.setattr(smtp, "send_email_detailed", _boom)
    assert R.send(to="a@b.example", account_name="Co", invoice=INVOICE) is False


@pytest.mark.parametrize("payload,why", [
    (b"<html>not a pdf", "what came back is not a PDF"),
    (b"%PDF" + b"x" * (R.MAX_PDF_BYTES + 10), "bigger than the cap"),
])
def test_fetch_refuses_anything_that_is_not_a_sane_pdf(monkeypatch, payload, why):
    class _Resp:
        def read(self, n=None): return payload[:n] if n else payload
        def __enter__(self): return self
        def __exit__(self, *a): return False
    monkeypatch.setattr(R.urllib.request, "urlopen", lambda url, timeout=0: _Resp())
    assert R.fetch_pdf("https://stripe/x.pdf") is None, why


def test_fetch_refuses_a_url_that_is_not_https_and_survives_a_dead_host(monkeypatch):
    assert R.fetch_pdf("http://stripe/x.pdf") is None
    assert R.fetch_pdf("") is None
    def _boom(url, timeout=0):
        raise OSError("no route to host")
    monkeypatch.setattr(R.urllib.request, "urlopen", _boom)
    assert R.fetch_pdf("https://stripe/x.pdf") is None
