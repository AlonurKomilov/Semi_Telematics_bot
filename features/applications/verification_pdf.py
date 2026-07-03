"""Safety Performance History Request (49 CFR §391.23) — the PDF a carrier
emails to a driver's prior DOT-regulated employer.

Page 1 is the fill-in request form (employment confirmation, the §391.23(d)
accident questions, the §391.23(e)/§40.25 drug & alcohol questions); page 2
is the driver's release authorization backed by the e-signature captured on
the apply form.  Shares the DQ packet's styles so both documents read as one
family.  SSN deliberately appears as LAST-4 ONLY — full SSNs don't belong in
email attachments.
"""
from __future__ import annotations

import io
from datetime import datetime, timezone
from typing import Optional

from reportlab.lib.pagesizes import letter
from reportlab.lib.units import inch
from reportlab.platypus import (
    Image, KeepTogether, Paragraph, SimpleDocTemplate, Spacer,
)

from features.applications.report import _esc, _kv, _section, _styles

_BLANK = "_" * 46


def _q(story, styles, text: str) -> None:
    story.append(Paragraph(_esc(text), styles["DQBody"]))
    story.append(Paragraph(f"Answer: {_BLANK}", styles["DQBody"]))
    story.append(Spacer(1, 6))


def build_verification_request_pdf(
    app: dict, employer: dict, *,
    carrier_name: str, carrier_mc: str = "", carrier_dot: str = "",
    carrier_address: str = "", carrier_phone: str = "", carrier_email: str = "",
    signature_png: Optional[bytes] = None,
) -> io.BytesIO:
    """Render the request for ONE prior employer of ONE application."""
    styles = _styles()
    buf = io.BytesIO()
    doc = SimpleDocTemplate(
        buf, pagesize=letter, topMargin=0.6 * inch, bottomMargin=0.6 * inch,
        leftMargin=0.7 * inch, rightMargin=0.7 * inch,
        title="Safety Performance History Request",
    )
    story: list = []
    p = app.get("personal") or {}
    c = app.get("cdl") or {}
    cons = app.get("consents") or {}
    driver_name = f"{p.get('first', '')} {p.get('last', '')}".strip()
    ssn = str(app.get("ssn") or p.get("ssn") or "")
    ssn_last4 = ("***-**-" + ssn.replace("-", "")[-4:]) if len(ssn.replace("-", "")) >= 4 else "—"
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")

    story.append(Paragraph("Safety Performance History Request", styles["DQTitle"]))
    story.append(Paragraph(
        "Investigation of driver safety performance history under 49 CFR "
        "§391.23(d)/(e) and §40.25.  Please complete and return within 30 days.",
        styles["DQSub"]))

    _section(story, styles, "Requesting (prospective) carrier")
    story.append(_kv([
        ("Carrier", carrier_name), ("MC / USDOT", f"{carrier_mc or '—'} / {carrier_dot or '—'}"),
        ("Address", carrier_address), ("Phone", carrier_phone),
        ("Return response to", carrier_email), ("Request date", today),
    ], styles))

    _section(story, styles, "Previous employer (addressee)")
    story.append(_kv([
        ("Company", employer.get("company")),
        ("Location", ", ".join(x for x in [employer.get("city"), employer.get("state")] if x)),
        ("Phone (as reported)", employer.get("phone")),
    ], styles))

    _section(story, styles, "Driver identification")
    story.append(_kv([
        ("Name", driver_name), ("Date of birth", app.get("dob") or p.get("dob")),
        ("SSN", ssn_last4),
        ("CDL", f"{c.get('number') or '—'} ({c.get('state') or '—'}, Class {c.get('class') or '—'})"),
        ("Employment period claimed",
         f"{employer.get('from') or '?'} → {'present' if employer.get('current') else employer.get('to') or '?'}"),
        ("Position claimed", employer.get("position")),
    ], styles))

    _section(story, styles, "1 · Employment verification")
    _q(story, styles, "Did this person work for you? If yes, confirm the dates of employment and position held.")
    _q(story, styles, "Did the driver operate a commercial motor vehicle subject to FMCSA regulations?")

    _section(story, styles, "2 · Accident history — §391.23(d)")
    story.append(Paragraph(_esc(
        "List any accidents involving this driver in the three years preceding "
        "this request that are required to be recorded under §390.15(b), and any "
        "other accidents you retain under your internal policy (date, location, "
        "injuries/fatalities, hazardous-material spill).  Write NONE if none."),
        styles["DQBody"]))
    for _ in range(3):
        story.append(Paragraph(_BLANK + "  " + _BLANK[:24], styles["DQBody"]))
        story.append(Spacer(1, 4))

    _section(story, styles, "3 · Drug & alcohol history — §391.23(e), §40.25")
    _q(story, styles, "In the past three years, did the driver violate the DOT drug and alcohol prohibitions of 49 CFR Part 382 / Part 40?")
    _q(story, styles, "If yes: did the driver complete the return-to-duty process (SAP evaluation, return-to-duty test, follow-up testing)?")
    _q(story, styles, "Did the driver refuse a DOT drug or alcohol test in the past three years?")

    story.append(Spacer(1, 8))
    story.append(Paragraph(_esc(
        "Completed by (name/title): " + _BLANK + "    Date: " + "_" * 14),
        styles["DQBody"]))

    # ── Page 2: the driver's release authorization ──────────────────
    story.append(Spacer(1, 24))
    _section(story, styles, "Driver authorization for release")
    story.append(Paragraph(_esc(
        f"I, {driver_name or '(applicant)'}, hereby authorize the release of my "
        f"safety performance history — including employment verification, "
        f"accident history, and drug and alcohol testing records under 49 CFR "
        f"§391.23 and §40.321(b) — by my previous employer(s) to "
        f"{carrier_name or 'the requesting carrier'}."), styles["DQBody"]))
    story.append(Spacer(1, 8))
    sig_block: list = []
    if signature_png:
        try:
            sig_block.append(Image(io.BytesIO(signature_png),
                                   width=2.4 * inch, height=0.9 * inch, kind="proportional"))
        except Exception:
            sig_block.append(Paragraph("(signature on file)", styles["DQBody"]))
    elif cons.get("sigName"):
        sig_block.append(Paragraph(f"<i>{_esc(cons.get('sigName'))}</i>", styles["DQTitle"]))
    sig_block.append(Paragraph(
        f"Signed electronically {_esc(cons.get('sigDate'))} on the driver's "
        f"employment application (disclosure v{_esc(app.get('disclosure_version') or cons.get('disclosureVersion') or '—')}).",
        styles["DQSmall"]))
    story.append(KeepTogether(sig_block))

    story.append(Spacer(1, 16))
    story.append(Paragraph(
        "Confidential — contains personal data.  The requesting carrier retains "
        "this investigation per 49 CFR §391.53.", styles["DQSmall"]))

    doc.build(story)
    buf.seek(0)
    return buf
