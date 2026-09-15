"""The invoice 4truck writes itself.

An Absolute 0 account pays nothing and is still billed: every month its
costs are computed, written down, and sent — the plan line, the trucks
above the included count, and the one line that takes the total to
zero.  A customer who is being carried should be able to see what is
being carried, and an accountant should find a document rather than an
absence.

Stripe is not involved, and that is the whole point of the arrangement:
no subscription, no card, no checkout.  What it costs is Stripe's own
reporting — these customers do not appear there at all — so the numbers
here are the only record that exists and the PDF is the only copy.

The invoice number is derived, not sequenced: ``INV-YYYYMM-<account>``
is unique per account per period, which makes the whole monthly run
idempotent — a retry writes the same row rather than a second bill.
"""

from __future__ import annotations

import io
import json
import logging
from datetime import datetime, timezone

logger = logging.getLogger(__name__)

PROVIDER = "local"


def invoice_number(account_id: int, period_start: str) -> str:
    """``INV-202609-10000001`` — the string a person quotes back.

    Derived from the period and the account rather than a sequence, so
    running the month twice produces one invoice and not two.
    """
    stamp = (period_start or "")[:7].replace("-", "") or datetime.now(timezone.utc).strftime("%Y%m")
    return f"INV-{stamp}-{int(account_id)}"


def build(*, account_id: int, account_name: str, billing: dict, discount: dict,
          period_start: str, period_end: str) -> dict:
    """One month's bill, as a dict the row and the PDF both read from.

    ``billing`` is ``compute_billing``'s answer: the plan's own price,
    the included count, the unit price of an extra truck, and how many
    trucks were actually billable.
    """
    tier = str(billing.get("tier") or "free")
    base = int(billing.get("base_cents") or 0)
    included = int(billing.get("included") or 0)
    extras = int(billing.get("extras") or 0)
    extra_unit = int(billing.get("extra_unit_cents") or 0)
    lines: list[dict] = []
    if base > 0:
        lines.append({"label": f"{tier.capitalize()} plan ({included} truck{'s' if included != 1 else ''} included)",
                      "qty": 1, "unit_cents": base, "amount_cents": base})
    if extras > 0 and extra_unit > 0:
        lines.append({"label": f"Extra truck{'s' if extras != 1 else ''}",
                      "qty": extras, "unit_cents": extra_unit,
                      "amount_cents": extras * extra_unit})
    subtotal = sum(int(line["amount_cents"]) for line in lines)
    reason = str(discount.get("reason") or "").strip()
    off_label = "Absolute 0" + (f" — {reason}" if reason else "")
    if subtotal > 0:
        lines.append({"label": off_label, "qty": 1,
                      "unit_cents": -subtotal, "amount_cents": -subtotal})
    return {
        "account_id": int(account_id),
        "account_name": account_name,
        "number": invoice_number(account_id, period_start),
        "period_start": period_start,
        "period_end": period_end,
        "lines": lines,
        "subtotal_cents": subtotal,
        "discount_cents": subtotal,
        "total_cents": 0,
        "currency": "usd",
    }


def to_row(invoice: dict) -> dict:
    """The ``billing_invoices`` fields for one local invoice."""
    return {
        "provider": PROVIDER,
        "amount_due_cents": 0,
        "amount_paid_cents": 0,
        "subtotal_cents": int(invoice["subtotal_cents"]),
        "discount_cents": int(invoice["discount_cents"]),
        "lines_json": json.dumps(invoice["lines"]),
        "currency": "usd",
        "status": "paid",          # nothing is owed, so nothing is outstanding
        "period_start": invoice["period_start"],
        "period_end": invoice["period_end"],
        "paid_at": datetime.now(timezone.utc).isoformat(),
    }


def _money(cents: int) -> str:
    sign = "-" if cents < 0 else ""
    return f"{sign}${abs(int(cents)) / 100:,.2f}"


def render_pdf(invoice: dict, *, issuer: dict | None = None) -> bytes:
    """The invoice as a PDF, in memory.

    Built rather than fetched: there is no Stripe invoice behind an
    Absolute 0 account, so this file is the document.  Side-effect free
    — the caller decides whether it is attached, streamed or stored.
    """
    from reportlab.lib import colors
    from reportlab.lib.enums import TA_RIGHT
    from reportlab.lib.pagesizes import letter
    from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
    from reportlab.lib.units import inch
    from reportlab.platypus import Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle

    issuer = issuer or {}
    buf = io.BytesIO()
    doc = SimpleDocTemplate(
        buf, pagesize=letter,
        leftMargin=0.8 * inch, rightMargin=0.8 * inch,
        topMargin=0.7 * inch, bottomMargin=0.7 * inch,
        title=f"Invoice {invoice['number']}",
        author=issuer.get("name") or "4truck",
    )
    base = getSampleStyleSheet()
    styles = {
        "h1": ParagraphStyle("h1", parent=base["Heading1"], fontSize=20, spaceAfter=2,
                             textColor=colors.HexColor("#0f172a")),
        "muted": ParagraphStyle("muted", parent=base["Normal"], fontSize=9,
                                textColor=colors.HexColor("#64748b")),
        "body": ParagraphStyle("body", parent=base["Normal"], fontSize=10,
                               textColor=colors.HexColor("#0f172a")),
        "right": ParagraphStyle("right", parent=base["Normal"], fontSize=10, alignment=TA_RIGHT),
        "total": ParagraphStyle("total", parent=base["Heading2"], fontSize=15,
                                textColor=colors.HexColor("#0f172a")),
    }
    story: list = []
    story.append(Paragraph("Invoice", styles["h1"]))
    story.append(Paragraph(issuer.get("name") or "4truck", styles["muted"]))
    story.append(Spacer(1, 16))

    period = f"{invoice['period_start'][:10]} — {invoice['period_end'][:10]}"
    meta = Table([
        ["Invoice number", invoice["number"]],
        ["Billed to", invoice.get("account_name") or f"Account #{invoice['account_id']}"],
        ["Billing period", period],
        ["Issued", datetime.now(timezone.utc).date().isoformat()],
    ], colWidths=[1.5 * inch, 4.2 * inch])
    meta.setStyle(TableStyle([
        ("FONTSIZE", (0, 0), (-1, -1), 9),
        ("TEXTCOLOR", (0, 0), (0, -1), colors.HexColor("#64748b")),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 4),
        ("TOPPADDING", (0, 0), (-1, -1), 0),
    ]))
    story.append(meta)
    story.append(Spacer(1, 18))

    rows = [["Description", "Qty", "Unit price", "Amount"]]
    for line in invoice["lines"]:
        rows.append([
            line["label"],
            str(line.get("qty") or 1),
            _money(int(line.get("unit_cents") or 0)),
            _money(int(line["amount_cents"])),
        ])
    table = Table(rows, colWidths=[3.4 * inch, 0.6 * inch, 1.1 * inch, 1.1 * inch])
    table.setStyle(TableStyle([
        ("FONTSIZE", (0, 0), (-1, -1), 9),
        ("TEXTCOLOR", (0, 0), (-1, 0), colors.HexColor("#64748b")),
        ("LINEBELOW", (0, 0), (-1, 0), 0.6, colors.HexColor("#cbd5e1")),
        ("ALIGN", (1, 0), (-1, -1), "RIGHT"),
        ("TOPPADDING", (0, 0), (-1, -1), 6),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 6),
        ("LINEBELOW", (0, 1), (-1, -2), 0.3, colors.HexColor("#e2e8f0")),
    ]))
    story.append(table)
    story.append(Spacer(1, 10))

    totals = Table([
        ["Subtotal", _money(invoice["subtotal_cents"])],
        ["Discount", _money(-invoice["discount_cents"])],
        ["Total", _money(invoice["total_cents"])],
    ], colWidths=[4.9 * inch, 1.3 * inch])
    totals.setStyle(TableStyle([
        ("FONTSIZE", (0, 0), (-1, -1), 10),
        ("ALIGN", (0, 0), (-1, -1), "RIGHT"),
        ("TEXTCOLOR", (0, 0), (-1, 1), colors.HexColor("#64748b")),
        ("LINEABOVE", (0, 2), (-1, 2), 0.6, colors.HexColor("#cbd5e1")),
        ("FONTNAME", (0, 2), (-1, 2), "Helvetica-Bold"),
        ("TOPPADDING", (0, 0), (-1, -1), 4),
    ]))
    story.append(totals)
    story.append(Spacer(1, 16))
    story.append(Paragraph("Nothing to pay. This invoice records what the "
                           "account used and what 4truck covered.", styles["muted"]))
    if issuer.get("support"):
        story.append(Spacer(1, 6))
        story.append(Paragraph(f"Questions about this invoice: {issuer['support']}", styles["muted"]))
    doc.build(story)
    return buf.getvalue()
