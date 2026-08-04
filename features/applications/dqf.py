"""The DQF sidecar — an application that means something without us.

WHY THIS EXISTS
---------------
On a ``hybrid`` or ``gdrive`` account, an applicant's documents are written
into the CUSTOMER's own Google Drive.  That sounds like "your data is
yours", and it was only half true: the bytes were theirs, the meaning was
not.  What actually landed in their Drive was

    {COMPANY}/applications/APP-4C7A0A/
        cdlFront.jpg  cdlBack.pdf  medical.pdf  signature.png

— four files in a folder named after a reference code.  Everything that
made them a driver qualification file (name, DOB, CDL detail, employment
history, and critically the FCRA / PSP / employment-verification CONSENTS
with their disclosure version and signature) lived only in our Postgres.

If this platform disappeared tomorrow, the carrier would open their Drive
and find a CDL photo of someone they could not identify, with no record
that the applicant had consented to anything.  And DQF retention under
49 CFR 391.51 is an obligation on the CARRIER, not on us — "our vendor's
server went away" is their violation, not ours.

So each application folder now carries its own meaning:

    {COMPANY}/applications/Rodriguez, Miguel — APP-4C7A0A/
        application.pdf     the DQF cover document, printable, auditable
        application.json    the same data, machine-readable, re-importable
        documents/
            cdl-front.jpg  cdl-back.pdf  medical-card.pdf  signature.png

``documents/`` rather than the folder root because that mirrors a physical
DQF — a cover sheet plus attachments — and because it extends without
restructuring when the MVR, PSP report and annual review become uploads
rather than the checkboxes they are today.

SSN IS MASKED to its last four (owner decision).  A DQF needs to establish
identity and to tie back to the PSP / MVR queries that were run; it does
not need to republish a full SSN into a folder whose sharing we neither
audit nor revoke.  The full value stays in our encrypted column.

REGENERATED, NEVER WRITTEN ONCE.  An application changes after submit —
stage, pre-hire checks, recruiter notes — and a compliance document that
contradicts the record is worse than no document at all.  Every mutation
point rewrites it, and ``generated_at`` states when the snapshot was taken.
"""

from __future__ import annotations

import io
import json
import logging
from datetime import datetime, timezone
from typing import Any

logger = logging.getLogger(__name__)

# Which uploaded slot lands under which filename in ``documents/``.
# Human-readable and stable: a file mailed to an auditor should say what
# it is without the folder around it.
DOC_FILENAMES: dict[str, str] = {
    "cdlFront":  "cdl-front",
    "cdlBack":   "cdl-back",
    "medical":   "medical-card",
    "signature": "signature",
    "mvr":       "mvr",
    "psp":       "psp-report",
}


def mask_ssn(raw: str | None) -> str:
    """``123-45-6789`` -> ``***-**-6789``; anything unusable -> ''."""
    digits = "".join(ch for ch in str(raw or "") if ch.isdigit())
    return f"***-**-{digits[-4:]}" if len(digits) >= 4 else ""


def application_folder(first: str, last: str, reference: str) -> str:
    """``Rodriguez, Miguel — APP-4C7A0A``.

    Name first because that is what someone scanning a Drive under audit
    pressure is looking for; the reference stays so the folder still maps
    back to a row.  Falls back to the bare reference when the name is
    missing — never produces a folder that is only punctuation.
    """
    from features.work_orders.storage import sanitize_company_folder

    who = ", ".join(p for p in ((last or "").strip(), (first or "").strip()) if p)
    label = f"{who} — {reference}" if who else str(reference or "application")
    return sanitize_company_folder(label)


def _j(raw: Any) -> Any:
    """Parse a ``*_json`` column that may arrive as text or already-parsed."""
    if raw in (None, ""):
        return None
    if isinstance(raw, (dict, list)):
        return raw
    try:
        return json.loads(raw)
    except (ValueError, TypeError):
        return None


def build_manifest(
    app: dict, history: list[dict] | None = None,
    protection: dict | None = None,
) -> dict:
    """The machine-readable half — what makes the folder RE-IMPORTABLE.

    This is the portability guarantee: if the carrier ever leaves for
    another platform, or comes back to us after a restore, the data goes
    with them in a form a program can read.  A PDF alone would be a
    picture of their data, not their data.

    ``history`` comes from the activity trail; applications only started
    recording transitions recently, so an older application yields an
    empty list rather than a fabricated one.  Saying "we don't know" is
    the honest output — inventing a plausible sequence for a compliance
    record would be worse than omitting it.
    """
    return {
        "schema": "4truck.driver_application/1",
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "reference": app.get("reference"),
        "status": app.get("status"),
        "submitted_at": app.get("submitted_at"),
        "reviewed_at": app.get("reviewed_at"),
        "applicant": {
            "first_name": app.get("first_name"),
            "last_name": app.get("last_name"),
            "email": app.get("email"),
            "phone": app.get("phone"),
            "city": app.get("city"),
            "state": app.get("state"),
            "dob": app.get("dob"),
            # Masked, per the owner decision recorded in this module's
            # docstring.  The full value never leaves our encrypted column.
            "ssn_last4": mask_ssn(app.get("ssn")),
            "cdl_state": app.get("cdl_state"),
            "cdl_class": app.get("cdl_class"),
            "years_cdl": app.get("years_cdl"),
        },
        "position": _j(app.get("position_json")),
        "address_history": _j(app.get("address_history_json")),
        "employment_history": _j(app.get("employment_json")),
        "incidents": _j(app.get("incidents_json")),
        # The legally load-bearing part, and the part that used to exist
        # in exactly one place the customer did not control.
        "consents": _j(app.get("consents_json")),
        "disclosure_version": app.get("disclosure_version"),
        "signature": {
            "mode": app.get("sig_mode"),
            "name": app.get("sig_name"),
            "date": app.get("sig_date"),
        },
        "pre_hire_checks": _j(app.get("vetting_json")),
        "history": history or [],
        # Which passphrase era this folder belongs to.  Injected by the
        # writer, which is the only layer that knows the account's
        # passphrase state.
        "protection": protection or {},
    }


def _kv_rows(pairs: list[tuple[str, Any]]) -> list[list[str]]:
    """Drop empty values rather than printing a column of dashes."""
    return [[k, str(v)] for k, v in pairs if v not in (None, "", [], {})]


def render_pdf(manifest: dict) -> bytes:
    """The human-readable half — the document handed to an auditor.

    reportlab, matching capabilities/reporting/risk_summary_pdf.py; this
    is the same shape as the existing DOT binder export, which already
    established that a compliance artifact here is a self-contained
    printable rather than a screen.

    Returns b'' on failure.  A sidecar that cannot be rendered must never
    break the submit or the stage change it is attached to — the
    application is the product, the sidecar is the safety net.
    """
    try:
        from reportlab.lib import colors
        from reportlab.lib.pagesizes import letter
        from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
        from reportlab.lib.units import inch
        from reportlab.platypus import (
            SimpleDocTemplate, Paragraph, Spacer, Table, TableStyle,
        )

        buf = io.BytesIO()
        doc = SimpleDocTemplate(
            buf, pagesize=letter,
            leftMargin=0.75 * inch, rightMargin=0.75 * inch,
            topMargin=0.75 * inch, bottomMargin=0.75 * inch,
            title=f"Driver Qualification File — {manifest.get('reference')}",
        )
        styles = getSampleStyleSheet()
        h1 = ParagraphStyle("h1", parent=styles["Heading1"], fontSize=16, spaceAfter=4)
        h2 = ParagraphStyle("h2", parent=styles["Heading2"], fontSize=11, spaceBefore=12, spaceAfter=4)
        small = ParagraphStyle("small", parent=styles["Normal"], fontSize=8, textColor=colors.grey)

        a = manifest.get("applicant") or {}
        name = " ".join(p for p in (a.get("first_name"), a.get("last_name")) if p) or "Applicant"

        story: list = [
            Paragraph("Driver Qualification File", h1),
            Paragraph(
                f"{name} &nbsp;·&nbsp; {manifest.get('reference', '')} "
                f"&nbsp;·&nbsp; status: <b>{manifest.get('status', '')}</b>",
                styles["Normal"],
            ),
            Paragraph(
                f"Generated {manifest.get('generated_at', '')} — a point-in-time "
                f"export. Retained by the carrier under 49 CFR 391.51.",
                small,
            ),
            Spacer(1, 10),
        ]

        def section(title: str, rows: list[list[str]]) -> None:
            if not rows:
                return
            story.append(Paragraph(title, h2))
            t = Table(rows, colWidths=[2.1 * inch, 4.4 * inch])
            t.setStyle(TableStyle([
                ("FONTSIZE", (0, 0), (-1, -1), 9),
                ("VALIGN", (0, 0), (-1, -1), "TOP"),
                ("TEXTCOLOR", (0, 0), (0, -1), colors.grey),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 4),
                ("TOPPADDING", (0, 0), (-1, -1), 4),
                ("LINEBELOW", (0, 0), (-1, -2), 0.25, colors.lightgrey),
            ]))
            story.append(t)

        section("Applicant", _kv_rows([
            ("Name", name),
            ("Date of birth", a.get("dob")),
            ("SSN", a.get("ssn_last4")),
            ("Email", a.get("email")),
            ("Phone", a.get("phone")),
            ("Location", " ".join(p for p in (a.get("city"), a.get("state")) if p)),
            ("CDL", " ".join(p for p in (a.get("cdl_state"), a.get("cdl_class")) if p)),
            ("Years holding CDL", a.get("years_cdl")),
        ]))

        checks = manifest.get("pre_hire_checks") or {}
        if isinstance(checks, dict) and checks:
            section("Pre-hire checks", [
                [k.replace("_", " ").title(),
                 "complete" if (v.get("done") if isinstance(v, dict) else v) else "not done"]
                for k, v in sorted(checks.items())
            ])

        consents = manifest.get("consents") or {}
        if isinstance(consents, dict) and consents:
            section("Consents & disclosures", [
                [k.replace("_", " ").title(), "agreed" if v else "not agreed"]
                for k, v in sorted(consents.items())
                if not isinstance(v, (dict, list))
            ])

        sig = manifest.get("signature") or {}
        section("Signature", _kv_rows([
            ("Signed by", sig.get("name")),
            ("Method", sig.get("mode")),
            ("Date", sig.get("date")),
            ("Disclosure version", manifest.get("disclosure_version")),
        ]))

        hist = manifest.get("history") or []
        if hist:
            section("History", [
                [str(h.get("at", ""))[:19], f"{h.get('action', '')} — {h.get('actor', '')}"]
                for h in hist
            ])
        else:
            story.append(Paragraph("History", h2))
            story.append(Paragraph(
                "No recorded stage history. Applications began recording "
                "transitions after this one was submitted.", small,
            ))

        story.append(Spacer(1, 14))
        story.append(Paragraph("Uploaded documents", h2))
        story.append(Paragraph(
            "The documents supplied with this application are in the "
            "<b>documents</b> folder alongside this file.", styles["Normal"],
        ))

        # The instructions matter as much as the file.  Whoever opens this
        # in three years will not be whoever set the passphrase, and a
        # password prompt with no explanation is where a compliance
        # document stops being useful.  The PDF reader shows its own bare
        # prompt and we cannot change it — so the explanation has to be
        # here, and in README.txt, before they ever reach it.
        story.append(Spacer(1, 8))
        story.append(Paragraph("Opening the protected file", h2))
        story.append(Paragraph(
            "This applicant's Social Security Number is required on the "
            "employment application by 49 CFR 391.21(b)(2) and must be kept "
            "in this file under 49 CFR 391.51. It is stored separately, as "
            "<b>documents/ssn-protected.pdf</b>, and protected with a "
            "password so that the rest of this file can be shared without "
            "exposing it. Everywhere else, including above, it appears only "
            "as its last four digits.",
            styles["Normal"],
        ))
        story.append(Spacer(1, 6))
        _prot = manifest.get("protection") or {}
        story.append(Paragraph(
            f"<b>{stamp_line(_prot.get('set_at', ''), _prot.get('set_by', ''), bool(_prot.get('is_default')))}</b>",
            styles["Normal"],
        ))
        story.append(Spacer(1, 6))
        story.append(Paragraph(
            "The password is your organisation's DQF export passphrase. To "
            "look it up, sign in to 4truck and open <b>Workforce &rarr; "
            "Applications &rarr; DQF export</b>. An administrator with the "
            "account-wide Config permission can view it there, and can "
            "replace it with one of your own choosing.",
            styles["Normal"],
        ))
        story.append(Spacer(1, 6))
        story.append(Paragraph(
            "If you cannot reach 4truck, contact 4truck support and provide "
            "your company name together with your MC or USDOT number — "
            "support can confirm the passphrase for the account those "
            "identifiers belong to. We recommend keeping your own copy "
            "somewhere outside 4truck, so that neither route is needed.",
            styles["Normal"],
        ))

        doc.build(story)
        return buf.getvalue()
    except Exception:
        logger.exception("DQF sidecar render failed for %s", manifest.get("reference"))
        return b""


# Account setting holding the carrier's DQF passphrase, ``encrypt``ed the
# same way the Drive refresh token is.  We keep a copy ONLY so that
# regenerating a sidecar can reuse the same passphrase; the carrier knows
# it independently, which is the whole point — see render_protected_ssn.
DQF_PASSPHRASE_KEY = "dqf.export_passphrase"
# When it was last changed and by whom.  Stamped into the READABLE files,
# not only the encrypted one — see stamp_line.
DQF_PASSPHRASE_SET_AT_KEY = "dqf.passphrase_set_at"
DQF_PASSPHRASE_SET_BY_KEY = "dqf.passphrase_set_by"


def stamp_line(set_at: str = "", set_by: str = "", is_default: bool = False) -> str:
    """"Protected on 6 July 2026 by Alex Kim" — which passphrase ERA a
    file belongs to.

    Owner's idea, with one correction that decides whether it works:
    this has to appear in the files anyone can READ.  Putting it only
    inside ssn-protected.pdf would mean needing the password to learn
    which password to use, which is the problem it exists to solve.  So
    it goes in README.txt and application.pdf — legible in a Drive
    preview — and is repeated inside the protected file as confirmation
    once opened.

    Why it matters: after a rotation a carrier's storage holds files from
    two eras that look identical.  This is the only way to tell, at a
    glance, whether a given folder predates the change and therefore
    still wants the old passphrase.
    """
    if is_default:
        return (
            "Protected by the default passphrase for your company "
            "(no administrator has set one)."
        )
    if not set_at:
        return "Protected by your organisation's DQF export passphrase."
    when = str(set_at)[:10]
    who = f" by {set_by}" if set_by else ""
    return f"Protected by the passphrase set on {when}{who}."


def render_protected_ssn(
    app: dict, passphrase: str, protection: dict | None = None,
) -> bytes:
    """A password-protected PDF holding the ONE field that must not sit
    readable in a shared folder.

    49 CFR 391.21(b)(2) requires the SSN on the driver's application, and
    391.51 requires that application in the DQF — so it cannot simply be
    left out; an SSN-less export fails the audit it exists for.  But the
    same folder gets shared with insurance brokers, factoring companies
    and office staff, and a full SSN sitting readable there forever is a
    liability the carrier does not need.

    So the split: ``application.pdf`` carries ``***-**-6789`` and is safe
    to hand to anyone, and this file carries the real number behind a
    password.  Everything readable except the one thing that should not be.

    THE PASSPHRASE MUST BE THE CARRIER'S.  If we generated and held it,
    losing our server would lose the password too, and they would be left
    with a file they can never open — worse than plaintext, because it
    looks like protection and delivers nothing in exactly the moment it
    was built for.

    Returns b'' when there is no passphrase or no SSN.  Callers treat that
    as "write no file and say so in the cover PDF" — never as "write it
    unprotected".
    """
    ssn = str(app.get("ssn") or "").strip()
    if not ssn or not passphrase:
        return b""
    try:
        import io as _io

        from reportlab.lib import pdfencrypt
        from reportlab.lib.pagesizes import letter
        from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
        from reportlab.lib.units import inch
        from reportlab.platypus import SimpleDocTemplate, Paragraph, Spacer

        name = " ".join(
            p for p in (app.get("first_name"), app.get("last_name")) if p
        ) or "Applicant"
        buf = _io.BytesIO()
        doc = SimpleDocTemplate(
            buf, pagesize=letter,
            leftMargin=0.9 * inch, rightMargin=0.9 * inch,
            topMargin=0.9 * inch, bottomMargin=0.9 * inch,
            title=f"Protected identifiers — {app.get('reference', '')}",
            encrypt=pdfencrypt.StandardEncryption(
                passphrase,
                # Printable and readable once opened; copy/modify off, so
                # the number is not trivially lifted into another document.
                canPrint=1, canModify=0, canCopy=0, canAnnotate=0,
                strength=128,
            ),
        )
        st = getSampleStyleSheet()
        small = ParagraphStyle(
            "small", parent=st["Normal"], fontSize=8, textColor=(0.4, 0.4, 0.4),
        )
        doc.build([
            Paragraph("Protected identifiers", st["Heading1"]),
            Paragraph(
                f"{name} &nbsp;·&nbsp; {app.get('reference', '')}", st["Normal"],
            ),
            Spacer(1, 12),
            Paragraph(f"<b>Social security number: {ssn}</b>", st["Normal"]),
            Spacer(1, 10),
            Paragraph(
                stamp_line(
                    (protection or {}).get("set_at", ""),
                    (protection or {}).get("set_by", ""),
                    bool((protection or {}).get("is_default")),
                ),
                small,
            ),
            Spacer(1, 10),
            Paragraph(
                "Required on the driver's employment application by 49 CFR "
                "391.21(b)(2), and required in the driver qualification file "
                "by 49 CFR 391.51. Separated from application.pdf and "
                "password-protected so the rest of the file can be shared "
                "without exposing this number.",
                small,
            ),
        ])
        return buf.getvalue()
    except Exception:
        logger.exception("protected-SSN render failed for %s", app.get("reference"))
        return b""


README_TEMPLATE = """DRIVER QUALIFICATION FILE
{name} — {reference}

WHAT IS IN THIS FOLDER

  application.pdf    The driver qualification file: identity, licence,
                     employment history, consents and signature, the
                     pre-hire checks that were run, and the stage this
                     application reached. This is the document to print
                     or hand to an auditor.

  application.json   The same information in machine-readable form, so
                     the record can be imported into another system.

  documents/         The files the applicant supplied — commercial
                     driver's licence, medical examiner's certificate,
                     signature — together with the protected file below.

OPENING documents/ssn-protected.pdf

  {stamp}

  This applicant's Social Security Number is required on the employment
  application by 49 CFR 391.21(b)(2) and must be retained in this file
  under 49 CFR 391.51. It is kept in its own password-protected file so
  that everything else here can be shared without exposing it.

  The password is your organisation's DQF export passphrase.

  TO LOOK IT UP
    1. Sign in to 4truck.
    2. Open Workforce -> Applications.
    3. Select DQF export.

  An administrator with the account-wide Config permission can view it
  there, and can replace it with one of your own choosing.

  IF YOU CANNOT REACH 4TRUCK
    Contact 4truck support and provide your company name together with
    your MC or USDOT number. Support can confirm the passphrase for the
    account those identifiers belong to.

  We recommend keeping your own copy somewhere outside 4truck, so that
  neither route is needed.

  Nothing else in this folder needs a password.

Generated {generated_at} by 4truck.
"""


def render_readme(manifest: dict) -> bytes:
    """A plain-text guide, readable in the Drive preview without opening
    anything.

    The PDF explains itself, but only once you open it — and the person
    who eventually needs this folder may be an auditor, a new office
    manager, or whoever inherits the account. A .txt sitting at the top
    of the folder is the one thing that is legible at a glance, in any
    client, with no software and no login.
    """
    a = manifest.get("applicant") or {}
    name = " ".join(
        p for p in (a.get("first_name"), a.get("last_name")) if p
    ) or "Applicant"
    prot = manifest.get("protection") or {}
    return README_TEMPLATE.format(
        name=name,
        reference=manifest.get("reference", ""),
        generated_at=manifest.get("generated_at", ""),
        stamp=stamp_line(
            prot.get("set_at", ""), prot.get("set_by", ""),
            bool(prot.get("is_default")),
        ),
    ).encode("utf-8")


def default_passphrase(company) -> str:
    """The fallback password, derived from the carrier's own identifiers.

    OWNER DECISION, and I argued against it — recorded here so the next
    reader knows the trade rather than inheriting it silently.

    WHAT IT BUYS: a carrier who never opens the config card still gets a
    COMPLETE driver qualification file, SSN included, and can recover
    access from information they can prove they own — company name plus
    MC or USDOT — even if this platform is no longer running and only
    support is reachable.  That is the scenario the whole sidecar exists
    for, and a randomly generated passphrase nobody wrote down fails it.

    WHAT IT DOES NOT BUY: real secrecy.  MC and USDOT numbers are public
    (FMCSA SAFER lists them against the carrier name), and the company
    name is the parent folder.  A determined reader who knows the scheme
    can reconstruct this.  It is a barrier against CASUAL access — the
    broker or office hire who opens a shared folder and idly clicks the
    file — not against someone trying.

    Two things follow, and both are load-bearing:
      * the scheme is never printed in README.txt or the cover PDF.  Not
        because obscurity is protection, but because writing the password
        next to the file it protects removes even the casual barrier.
      * an administrator who wants real protection replaces it, and the
        config card says so.

    Deterministic on purpose: support can recompute it from the account,
    which is the recovery path this design is chosen for.
    """
    name = "".join(
        ch for ch in str(getattr(company, "display_name", "") or "")
        if ch.isalnum()
    ).upper()
    ident = (
        str(getattr(company, "mc_number", "") or "").strip()
        or str(getattr(company, "usdot_number", "") or "").strip()
    )
    ident = "".join(ch for ch in ident if ch.isalnum()).upper()
    if not name and not ident:
        # Nothing to derive from — better no file than one "protected"
        # by an empty string.
        return ""
    return f"{name}-{ident}" if ident else name
