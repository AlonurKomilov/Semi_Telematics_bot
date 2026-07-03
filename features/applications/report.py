"""Applications feature — reporting contribution (the DQ-packet PDF).

Co-located with the feature (like ``features/cameras/report.py``): the
reporting *hub* (``capabilities/reporting``) owns the shared infra, while a
feature owns its own report generator.  This one is self-contained — it
builds the PDF straight from reportlab, no hub ``pdf_base`` needed.

The retainable §391.51 Driver Qualification record.  Renders a submitted
application (the full ``get_driver_application`` dict)
into a printable Driver Qualification File packet: identity, CDL, 10-yr
employment, accident/violation history, the signed FMCSA authorizations,
the pre-hire vetting checklist, and the applicant's signature.  Pure
function of its inputs (no DB / network) so it's trivially testable.
"""
from __future__ import annotations

import html
import io
from typing import Any, Optional

from reportlab.lib import colors
from reportlab.lib.pagesizes import letter
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from reportlab.lib.units import inch
from reportlab.platypus import (
    SimpleDocTemplate, Paragraph, Spacer, Table, TableStyle, Image, KeepTogether,
)

_INK = colors.HexColor("#1f2937")
_MUTED = colors.HexColor("#6b7280")
_BAR = colors.HexColor("#1e3a5f")
_LINE = colors.HexColor("#e5e7eb")


def _esc(v) -> str:
    return html.escape(str(v if v is not None else "")).strip()


def _styles():
    s = getSampleStyleSheet()
    s.add(ParagraphStyle("DQTitle", parent=s["Title"], fontSize=16, textColor=_INK, spaceAfter=2))
    s.add(ParagraphStyle("DQSub", parent=s["Normal"], fontSize=9, textColor=_MUTED, spaceAfter=10))
    s.add(ParagraphStyle("DQSection", parent=s["Heading2"], fontSize=11, textColor=colors.white,
                         backColor=_BAR, leading=16, spaceBefore=12, spaceAfter=6,
                         leftIndent=6, borderPadding=(3, 4, 3, 4)))
    s.add(ParagraphStyle("DQBody", parent=s["Normal"], fontSize=9, textColor=_INK, leading=13))
    s.add(ParagraphStyle("DQSmall", parent=s["Normal"], fontSize=7.5, textColor=_MUTED, leading=10))
    return s


def _kv(rows: list[tuple[str, Any]], styles) -> Table:
    data = [[Paragraph(f"<b>{_esc(k)}</b>", styles["DQBody"]), Paragraph(_esc(v) or "—", styles["DQBody"])]
            for k, v in rows]
    t = Table(data, colWidths=[1.7 * inch, 4.8 * inch])
    t.setStyle(TableStyle([
        ("VALIGN", (0, 0), (-1, -1), "TOP"),
        ("LINEBELOW", (0, 0), (-1, -2), 0.4, _LINE),
        ("TOPPADDING", (0, 0), (-1, -1), 3), ("BOTTOMPADDING", (0, 0), (-1, -1), 3),
    ]))
    return t


def _section(story, styles, title: str):
    story.append(Paragraph(_esc(title), styles["DQSection"]))


def _yn(v) -> str:
    return "Yes" if v in (True, "yes", "Yes", 1, "true") else ("No" if v in (False, "no", "No", 0) else _esc(v))


def build_dq_packet_pdf(
    app: dict, *, account_name: str = "", signature_png: Optional[bytes] = None,
    generated_at: str = "", carrier_name: str = "", carrier_mc: str = "",
    carrier_dot: str = "", verifications: Optional[list] = None,
) -> io.BytesIO:
    """Build the application packet PDF.  Returns a seek-0 BytesIO."""
    styles = _styles()
    buf = io.BytesIO()
    doc = SimpleDocTemplate(
        buf, pagesize=letter, topMargin=0.6 * inch, bottomMargin=0.6 * inch,
        leftMargin=0.7 * inch, rightMargin=0.7 * inch,
        title=f"Driver Application {app.get('reference', '')}",
    )
    story: list = []
    P = app.get("personal") or {}
    name = f"{P.get('first', '')} {P.get('middle', '')} {P.get('last', '')}".replace("  ", " ").strip()

    # Hiring carrier identity (§391.51): prefer the specific sub-company
    # the applicant applied to (legal name + MC#/USDOT#); fall back to the
    # account name for generic / pre-branding links.
    carrier_bits: list[str] = []
    if carrier_name:
        carrier_bits.append(_esc(carrier_name))
        if carrier_mc:
            carrier_bits.append(f"MC {_esc(carrier_mc)}")
        if carrier_dot:
            carrier_bits.append(f"USDOT {_esc(carrier_dot)}")
    header_org = " · ".join(carrier_bits) if carrier_bits else _esc(account_name)

    story.append(Paragraph("Driver Application — Qualification File", styles["DQTitle"]))
    story.append(Paragraph(
        f"{header_org} · Reference {_esc(app.get('reference'))} · "
        f"Status {_esc(app.get('status'))} · Submitted {_esc((app.get('submitted_at') or '')[:10])}",
        styles["DQSub"]))

    # Applicant identity
    em = P.get("emergency") or {}
    emergency = " · ".join(x for x in [em.get("name"), em.get("phone"), em.get("relationship")] if x)
    _section(story, styles, "Applicant")
    story.append(_kv([
        ("Name", name), ("Date of birth", app.get("dob")), ("SSN", app.get("ssn")),
        ("Phone", app.get("phone") or P.get("phone")), ("Email", app.get("email") or P.get("email")),
        ("Address", ", ".join(x for x in [P.get("addr1"), P.get("addr2"), P.get("city"),
                                          P.get("state"), P.get("zip")] if x)),
        ("Years at address", P.get("yearsAtAddr")),
        ("Emergency contact", emergency),
    ], styles))

    # Address history
    hist = app.get("address_history") or []
    if hist:
        _section(story, styles, "Residence history — last 3 years")
        story.append(_kv([(f"{a.get('from', '?')}–{a.get('to', '?')}",
                           ", ".join(x for x in [a.get("addr1"), a.get("city"), a.get("state"), a.get("zip")] if x))
                          for a in hist], styles))

    # CDL
    c = app.get("cdl") or {}
    end = c.get("endorsements") or {}
    _section(story, styles, "Commercial Driver's License")
    story.append(_kv([
        ("CDL number", c.get("number")), ("State", c.get("state")), ("Class", c.get("class")),
        ("Expiration", c.get("exp")), ("Restrictions", c.get("restrictions")),
        ("Endorsements", ", ".join(k for k, v in end.items() if v) or "None"),
    ], styles))

    # Experience
    x = app.get("experience") or {}
    _section(story, styles, "Driving experience")
    story.append(_kv([
        ("Years CDL", x.get("yearsCdl")),
        ("Equipment", ", ".join(x.get("equipment") or [])),
        ("Regions", ", ".join(x.get("regions") or [])),
        ("Preferred role", x.get("preferredRole")),
    ], styles))

    # Employment (10-year history) — §391.21(b).  An applicant may attest to
    # no employment in the window; capture that + their account of the time.
    work = app.get("work") or {}
    jobs = app.get("employment") or []
    if str(work.get("employed")) == "no":
        _section(story, styles, "Employment history")
        story.append(Paragraph("Applicant attests to no employment (incl. self-employment) in the past 10 years.", styles["DQBody"]))
        if work.get("explain"):
            story.append(Paragraph(f"<b>Account for this period:</b> {_esc(work.get('explain'))}", styles["DQBody"]))
    elif jobs:
        _section(story, styles, f"Employment history ({len(jobs)})")
        rows = [[Paragraph("<b>Employer</b>", styles["DQSmall"]), Paragraph("<b>Dates</b>", styles["DQSmall"]),
                 Paragraph("<b>FMCSA</b>", styles["DQSmall"]), Paragraph("<b>Reason / gap</b>", styles["DQSmall"])]]
        for j in jobs:
            dates = f"{j.get('from', '?')} → {'present' if j.get('current') else j.get('to', '?')}"
            rg = " / ".join(x for x in [j.get("reason"), j.get("gapExplanation")] if x)
            rows.append([
                Paragraph(_esc(j.get("company")), styles["DQSmall"]),
                Paragraph(_esc(dates), styles["DQSmall"]),
                Paragraph(_yn(j.get("fmcsa")), styles["DQSmall"]),
                Paragraph(_esc(rg), styles["DQSmall"]),
            ])
        t = Table(rows, colWidths=[2.3 * inch, 1.6 * inch, 0.6 * inch, 2.0 * inch])
        t.setStyle(TableStyle([
            ("VALIGN", (0, 0), (-1, -1), "TOP"), ("GRID", (0, 0), (-1, -1), 0.4, _LINE),
            ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#f3f4f6")),
            ("TOPPADDING", (0, 0), (-1, -1), 3), ("BOTTOMPADDING", (0, 0), (-1, -1), 3),
        ]))
        story.append(t)
    else:
        _section(story, styles, "Employment history")
        story.append(Paragraph("None provided.", styles["DQBody"]))

    # Incidents
    inc = app.get("incidents") or {}
    _section(story, styles, "Accidents & violations — last 3 years")
    story.append(_kv([
        ("Accidents?", _yn(inc.get("hasAccidents"))), ("Violations?", _yn(inc.get("hasViolations"))),
        ("Suspensions?", _yn(inc.get("hasSuspensions"))), ("License denial?", _yn(inc.get("hasDenial"))),
        ("Accidents on record", str(len(inc.get("accidents") or []))),
        ("Violations on record", str(len(inc.get("violations") or []))),
    ], styles))

    # Consents — the signed FMCSA authorizations
    cons = app.get("consents") or {}
    _section(story, styles, "Authorizations & certification")
    consent_rows = [
        ("PSP Disclosure & Authorization", cons.get("psp")), ("Motor Vehicle Record", cons.get("mvr")),
        ("Drug & Alcohol Clearinghouse", cons.get("clearinghouse")),
        ("Consumer report / FCRA disclosure", cons.get("fcra")),
        ("Employee Verification (49 CFR §391.23)", cons.get("employment_verification")),
        ("Pre-employment drug screen", cons.get("drug")),
        ("Truthful & complete certification", cons.get("truthful")),
    ]
    story.append(_kv([(k, "✓ Authorized" if v else "✗ Not given") for k, v in consent_rows], styles))
    story.append(Paragraph(
        f"Disclosure version {_esc(app.get('disclosure_version'))} · "
        f"signed by {_esc(cons.get('sigName') or '(drawn)')} on {_esc(cons.get('sigDate'))}",
        styles["DQSmall"]))

    # §391.23 safety-history investigation — per-employer request trail.
    # The attempts/dates ARE the good-faith documentation §391.23(c)(2)
    # expects when a previous employer never responds.
    if verifications:
        _section(story, styles, "Safety-history investigation (§391.23)")
        _status = {"pending": "Not sent", "sent": "Sent — awaiting response",
                   "received": "Response received", "no_response": "No response (good-faith attempts documented)"}
        story.append(_kv([
            (v.get("employer_name") or f"Employer #{v.get('employer_index', '?')}",
             f"{_status.get(str(v.get('status')), v.get('status'))} · "
             f"{v.get('attempts', 0)} attempt(s)"
             + (f" · last sent {str(v.get('sent_at'))[:10]} to {v.get('employer_email')}" if v.get("sent_at") else "")
             + (f" · responded {str(v.get('responded_at'))[:10]}" if v.get("responded_at") else ""))
            for v in verifications
        ], styles))

    # Pre-hire vetting checklist
    vet = app.get("vetting") or {}
    _section(story, styles, "Pre-hire vetting")
    story.append(_kv([
        ("PSP query", "✓ Completed" if (vet.get("psp") or {}).get("done") else "Pending"),
        ("MVR pulled", "✓ Completed" if (vet.get("mvr") or {}).get("done") else "Pending"),
        ("Clearinghouse query", "✓ Completed" if (vet.get("clearinghouse") or {}).get("done") else "Pending"),
        ("Drug screen", "✓ Completed" if (vet.get("drug") or {}).get("done") else "Pending"),
        ("Background check", "✓ Completed" if (vet.get("background") or {}).get("done") else "Pending"),
    ], styles))

    # Signature
    _section(story, styles, "Applicant signature")
    sig_block: list = []
    if signature_png:
        try:
            sig_block.append(Image(io.BytesIO(signature_png), width=2.4 * inch, height=0.9 * inch, kind="proportional"))
        except Exception:
            sig_block.append(Paragraph("(signature on file)", styles["DQBody"]))
    elif cons.get("sigName"):
        sig_block.append(Paragraph(f"<i>{_esc(cons.get('sigName'))}</i>", styles["DQTitle"]))
    sig_block.append(Paragraph(
        f"Signed {_esc(cons.get('sigDate'))} · X{'_' * 40}", styles["DQSmall"]))
    story.append(KeepTogether(sig_block))

    story.append(Spacer(1, 16))
    story.append(Paragraph(
        f"Generated {_esc(generated_at[:19])}. Retain per 49 CFR §391.51. "
        f"Confidential — contains personal data.", styles["DQSmall"]))

    doc.build(story)
    buf.seek(0)
    return buf
