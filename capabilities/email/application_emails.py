"""Recruiting emails — the ones addressed to APPLICANTS and to previous
employers.

The recruiter-facing "a new application landed" mail used to live here.
It moved to the notifications capability, which renders it from the same
semantic content as the in-app and Telegram notices and adds the
unsubscribe this hand-rolled sender never had (features/applications/
notifications.py).  What stays here is correspondence with people who
are not platform users, and so have no notification preferences.

Follows the auth_emails / lifecycle_emails contract — returns ``True`` on
relay hand-off, ``False`` when SMTP is unconfigured or the send raised;
callers treat email as best-effort and never fail the business action on
a mail error.

Subjects and bodies are templated.  The ONE piece of caller-supplied
text that reaches a header is the carrier-intake sender name, which is
length-capped and control-character-stripped by
features/carrier_directory/service.resolve_sender_name before it gets
here, and CRLF-stripped again by ``send_email``.  Anything else added to
a Subject must clear the same bar.
"""
from __future__ import annotations

import html
import logging

from capabilities.email.auth_emails import _company_name
from capabilities.email.lifecycle_emails import _shell
from capabilities.email.smtp import is_email_configured, send_email

logger = logging.getLogger(__name__)


def send_application_received_email(
    *, to: str, applicant_name: str, carrier_name: str, reference: str,
    status_url: str,
) -> bool:
    """Confirm to the APPLICANT that their application landed.

    Everything else in this module talks to recruiters or previous
    employers.  The person who just handed over ten years of employment
    history, an SSN and three signatures got nothing — and the reference
    number, the only key to the status page, existed on one screen and was
    then unrecoverable.  This is the receipt.
    """
    if not is_email_configured() or not to:
        return False
    who = html.escape(carrier_name or _company_name())
    safe_ref = html.escape(reference or "")
    safe_name = html.escape((applicant_name or "").strip())
    greeting = f"Hi {safe_name}," if safe_name else "Hi,"
    inner = (
        f'<h2 style="margin:0 0 12px;font-size:18px">We received your application</h2>'
        f'<p style="margin:0 0 8px">{greeting}</p>'
        f'<p style="margin:0 0 16px">Your driver application to <b>{who}</b> is in. '
        f"Keep this reference — it's how you check on it:</p>"
        f'<p style="margin:0 0 16px;font-size:20px;font-family:monospace;'
        f'letter-spacing:1px"><b>{safe_ref}</b></p>'
        f'<p style="margin:0 0 24px">'
        f'<a href="{html.escape(status_url)}" style="background:#2563eb;color:#fff;'
        f"text-decoration:none;padding:10px 18px;border-radius:8px;"
        f'display:inline-block;font-size:14px">Check your status</a></p>'
        f'<p style="margin:0;color:#6b7280;font-size:13px">You&rsquo;ll need this '
        f"reference and the email address you applied with. We&rsquo;ll be in "
        f"touch about next steps.</p>"
    )
    try:
        return send_email(
            to=to,
            subject=f"We received your application — {reference}",
            body=(
                f"{('Hi ' + (applicant_name or '').strip() + ',') if applicant_name else 'Hi,'}\n\n"
                f"Your driver application to {carrier_name or _company_name()} is in.\n\n"
                f"Your reference number: {reference}\n\n"
                f"Check your status here: {status_url}\n"
                "You'll need this reference and the email address you applied "
                "with.\n\nWe'll be in touch about next steps.\n"
            ),
            html_body=_shell(inner),
            extra_headers={"Auto-Submitted": "auto-generated"},
        )
    except Exception as e:  # best-effort — never fail the submission
        logger.debug("send_application_received_email to %s failed: %s", to, e)
        return False


def send_resume_link_email(
    *, to: str, carrier_name: str, resume_url: str, reminder: bool = False,
) -> bool:
    """Send an applicant the link to continue their saved application.

    Two triggers share the template: the applicant's own "Save & finish
    later" and a recruiter's manual reminder nudge — only the opening line
    differs.  The link alone doesn't open the draft (the applicant re-enters
    their email on the resume page), so a forwarded email exposes nothing.
    """
    if not is_email_configured() or not to:
        return False
    safe_carrier = html.escape(carrier_name or _company_name())
    lead = (
        "Your driver application is waiting — pick up right where you left off."
        if not reminder else
        "Just a reminder — your driver application is still waiting for you."
    )
    inner = (
        f'<h2 style="margin:0 0 12px;font-size:18px">Finish your application with '
        f"{safe_carrier}</h2>"
        f'<p style="margin:0 0 16px">{lead}</p>'
        f'<p style="margin:0 0 24px">'
        f'<a href="{html.escape(resume_url)}" style="background:#2563eb;color:#fff;'
        f"text-decoration:none;padding:10px 18px;border-radius:8px;"
        f'display:inline-block;font-size:14px">Continue my application</a></p>'
        f'<p style="margin:0;color:#6b7280;font-size:13px">For your security, '
        f"you'll confirm your email address on that page before your saved "
        f"answers open. The link expires after 14 days of inactivity.</p>"
    )
    try:
        return send_email(
            to=to,
            subject=f"Finish your application with {carrier_name or _company_name()}",
            body=(
                f"{lead}\n\nContinue here: {resume_url}\n\n"
                "You'll confirm your email address on that page before your "
                "saved answers open. The link expires after 14 days of inactivity.\n"
            ),
            html_body=_shell(inner),
            extra_headers={"Auto-Submitted": "auto-generated"},
        )
    except Exception as e:  # best-effort
        logger.debug("send_resume_link_email to %s failed: %s", to, e)
        return False


def send_verification_request_email(
    *, to: str, carrier_name: str, driver_name: str, reply_to: str,
    pdf_bytes: bytes,
) -> bool:
    """Email a §391.23 safety-performance-history request to a driver's
    previous employer, with the fill-in request PDF (which carries the
    driver's signed release) attached.  Replies go to the requesting
    carrier's compliance address, not to us."""
    if not is_email_configured() or not to:
        return False
    safe_carrier = html.escape(carrier_name or _company_name())
    safe_driver = html.escape(driver_name or "a driver applicant")
    inner = (
        f'<h2 style="margin:0 0 12px;font-size:18px">Safety performance history '
        f"request — {safe_driver}</h2>"
        f'<p style="margin:0 0 8px">{safe_carrier} is considering '
        f"<b>{safe_driver}</b> for a driving position and is required to "
        f"investigate the driver's safety performance history with previous "
        f"DOT-regulated employers (49 CFR §391.23).</p>"
        f'<p style="margin:0 0 16px">The attached request includes the '
        f"driver's signed release. Please complete and return it within "
        f"30 days by replying to this email.</p>"
        f'<p style="margin:0;color:#6b7280;font-size:13px">Confidential — '
        f"contains personal data. If you received this in error, please "
        f"delete it and notify the sender.</p>"
    )
    try:
        return send_email(
            to=to,
            subject=f"Safety performance history request (49 CFR §391.23) — {driver_name}",
            body=(
                f"{carrier_name or _company_name()} is required to investigate the safety "
                f"performance history of {driver_name} (49 CFR §391.23).\n\n"
                "The attached request includes the driver's signed release. "
                "Please complete and return it within 30 days by replying to "
                "this email.\n"
            ),
            html_body=_shell(inner),
            reply_to=reply_to or None,
            attachments=[("safety_history_request.pdf", pdf_bytes, "application/pdf")],
        )
    except Exception as e:  # best-effort
        logger.debug("send_verification_request_email to %s failed: %s", to, e)
        return False


def send_carrier_intake_email(
    *, to: str, carrier_name: str, agency_name: str, intake_url: str,
    expires_days: int, prefilled: bool = False, reply_to: str = "",
    field_count: int = 0, reminder: bool = False,
) -> bool:
    """Invite an external carrier to fill in their own profile sheet
    (requirements, pay, benefits) via the tokenized public link a
    recruiting manager minted for them.

    ``agency_name`` is the RESOLVED sender name from
    features/carrier_directory/service.resolve_sender_name and may be ''
    — the sender is entitled to stay unnamed.  Blank must NOT be filled
    in with the product name or the tenant's registered name: it drops
    the naming clause entirely, matching the public page word for word.

    ``prefilled=True`` (the profile already holds real content) adds an
    honest "review and complete the rest" line that lowers the perceived
    effort — it MUST stay conditional so an empty sheet never claims to
    be partly done.

    ``reply_to`` is the inviting manager's address.  An unnamed sender is a
    legitimate privacy choice; an unnamed sender with no reply path is a
    dead end for a business we just asked for pay data, so this should
    always be supplied.

    ``field_count`` states the real size of the form.  Do NOT replace it
    with a wall-clock estimate: the honest shape of the ask is "N short
    fields, all optional", and a minutes figure the reader disproves by
    scrolling costs more trust than it buys."""
    if not is_email_configured() or not to:
        return False
    sender = (agency_name or "").strip()
    safe_carrier = html.escape(carrier_name or "your company")
    safe_agency = html.escape(sender)
    lead = "Reminder: complete" if reminder else "Complete"
    subject = (
        f"{lead} your carrier profile for {sender}" if sender
        else f"{lead} your carrier profile"
    )
    # Named: "<Sender> recruits drivers for <Carrier> and would like…"
    # Unnamed: the same ask, with no one named on either end of it.
    lede_html = (
        f"{safe_agency} recruits drivers for <b>{safe_carrier}</b> and would "
        f"like your hiring requirements, pay and benefits straight from the "
        f"source"
        if sender else
        f"A recruiting partner is presenting <b>{safe_carrier}</b> to driver "
        f"candidates and would like your hiring requirements, pay and "
        f"benefits straight from the source"
    )
    lede_text = (
        f"{sender} recruits drivers for {carrier_name or 'your company'} and "
        f"would like your hiring requirements, pay and benefits straight from "
        f"the source."
        if sender else
        f"A recruiting partner is presenting {carrier_name or 'your company'} "
        f"to driver candidates and would like your hiring requirements, pay "
        f"and benefits straight from the source."
    )
    prefilled_html = (
        " Some fields are already filled from our records &mdash; you only "
        "need to review and complete the rest."
        if prefilled else ""
    )
    prefilled_text = (
        " Some fields are already filled from our records — you only need "
        "to review and complete the rest."
        if prefilled else ""
    )
    # Honest shape of the ask, not a minutes figure the reader disproves
    # by scrolling.  Every field really is optional and a partial sheet
    # really is accepted, so say exactly that.
    size = (
        f"About {int(field_count)} short fields, most one line."
        if field_count else "Most fields are one line."
    )
    # The sharing truth: the token is bearer auth with no recipient
    # identity, so we cannot promise privacy — we can only tell them how
    # it actually behaves. Mirrors the wording on the form itself.
    sharing = (
        "Anyone who opens this link can see and change these answers, and "
        "the newest submission replaces the previous one — forward it only "
        "inside your company."
    )
    reply_html = (
        '<p style="margin:0 0 8px;color:#6b7280;font-size:13px">Questions? '
        "Reply to this email and it reaches the recruiter who sent it.</p>"
        if reply_to else ""
    )
    reply_text = (
        "\nQuestions? Reply to this email and it reaches the recruiter who "
        "sent it.\n" if reply_to else ""
    )
    inner = (
        f'<h2 style="margin:0 0 12px;font-size:18px">{html.escape(subject)}</h2>'
        f'<p style="margin:0 0 8px">{lede_html} — so recruiters present your '
        f"company accurately to driver candidates.</p>"
        f'<p style="margin:0 0 16px">{size} Every field is optional and you '
        f"can send a partial sheet.{prefilled_html} "
        f"You can come back and revise your answers any time while the link "
        f"is active.</p>"
        f'<p style="margin:0 0 24px">'
        f'<a href="{html.escape(intake_url)}" style="background:#2563eb;color:#fff;'
        f"text-decoration:none;padding:10px 18px;border-radius:8px;"
        f'display:inline-block;font-size:14px">Fill in our carrier profile</a></p>'
        f"{reply_html}"
        f'<p style="margin:0;color:#6b7280;font-size:13px">{sharing} '
        f"It expires in {int(expires_days)} days.</p>"
    )
    try:
        return send_email(
            to=to,
            subject=subject,
            body=(
                f"{lede_text}"
                f"{prefilled_text}\n\n"
                f"{size} Every field is optional and you can send a partial "
                f"sheet.\n\n"
                f"Fill in your profile here: {intake_url}\n"
                f"{reply_text}\n"
                f"{sharing} It expires in {int(expires_days)} days.\n"
            ),
            html_body=_shell(inner),
            reply_to=reply_to or None,
            extra_headers={"Auto-Submitted": "auto-generated"},
        )
    except Exception as e:  # best-effort
        logger.debug("send_carrier_intake_email to %s failed: %s", to, e)
        return False


def send_carrier_intake_expiring_email(
    *, to: str, carrier_name: str, days_left: int, contact_email: str,
    profile_url: str,
) -> bool:
    """Warn a recruiting manager that a carrier's fill link is about to
    lapse unanswered — while chasing it is still possible.

    Sent once per link by the nightly sweep.  Deliberately states the
    consequence rather than just the date: an expired link is the state
    where the manager finds out from the carrier, not from us."""
    if not is_email_configured() or not to:
        return False
    safe_carrier = html.escape(carrier_name or "A carrier")
    when = "today" if days_left <= 0 else f"in {int(days_left)} day{'' if days_left == 1 else 's'}"
    sent_to = (
        f" It was sent to {html.escape(contact_email)}." if contact_email else
        " No email address was recorded for it."
    )
    inner = (
        f'<h2 style="margin:0 0 12px;font-size:18px">Carrier fill link expires {html.escape(when)}</h2>'
        f'<p style="margin:0 0 16px"><b>{safe_carrier}</b> has not filled in '
        f"their profile sheet, and their link expires {html.escape(when)}."
        f"{sent_to} Once it lapses the link stops working and they would "
        f"need a new one.</p>"
        f'<p style="margin:0 0 24px">'
        f'<a href="{html.escape(profile_url)}" style="background:#2563eb;color:#fff;'
        f"text-decoration:none;padding:10px 18px;border-radius:8px;"
        f'display:inline-block;font-size:14px">Open carrier profile</a></p>'
    )
    try:
        return send_email(
            to=to,
            subject=f"Carrier fill link expires {when} — {carrier_name or 'carrier'}",
            body=(
                f"{carrier_name or 'A carrier'} has not filled in their profile "
                f"sheet, and their link expires {when}.{sent_to}\n\n"
                f"Open the profile: {profile_url}\n"
            ),
            html_body=_shell(inner),
            extra_headers={"Auto-Submitted": "auto-generated"},
        )
    except Exception as e:  # best-effort
        logger.debug("send_carrier_intake_expiring_email to %s failed: %s", to, e)
        return False


def send_carrier_intake_submitted_email(
    *, to: str, carrier_name: str, profile_url: str,
    filled_count: int = 0, total_count: int = 0, revision: bool = False,
) -> bool:
    """Tell a recruiting manager that a carrier filled in their profile
    sheet and it is waiting for review.

    ``filled_count``/``total_count`` and ``revision`` exist because this
    mail used to be byte-identical whether the carrier answered 3 fields
    or 70, and identical for a first fill and a fourth revision — so a
    manager could not tell an substantive submission from a typo fix
    without opening the profile."""
    if not is_email_configured() or not to:
        return False
    safe_carrier = html.escape(carrier_name or "A carrier")
    what = "revised" if revision else "completed"
    scale = (
        f" They answered {int(filled_count)} of {int(total_count)} fields."
        if total_count else ""
    )
    inner = (
        f'<h2 style="margin:0 0 12px;font-size:18px">Carrier profile '
        f'{"revised" if revision else "filled in"}</h2>'
        f'<p style="margin:0 0 16px"><b>{safe_carrier}</b> {what} their '
        f"profile sheet through the invite link.{html.escape(scale)} Review "
        f"their answers, then save or use Mark reviewed to clear the flag.</p>"
        f'<p style="margin:0 0 24px">'
        f'<a href="{html.escape(profile_url)}" style="background:#2563eb;color:#fff;'
        f"text-decoration:none;padding:10px 18px;border-radius:8px;"
        f'display:inline-block;font-size:14px">Review carrier profile</a></p>'
    )
    try:
        return send_email(
            to=to,
            subject=(
                f"Carrier profile {'revised' if revision else 'filled in'} — "
                f"{carrier_name or 'carrier'}"
            ),
            body=(
                f"{carrier_name or 'A carrier'} {what} their profile sheet "
                f"through the invite link.{scale}\n\nReview it: {profile_url}\n"
            ),
            html_body=_shell(inner),
            extra_headers={"Auto-Submitted": "auto-generated"},
        )
    except Exception as e:  # best-effort
        logger.debug("send_carrier_intake_submitted_email to %s failed: %s", to, e)
        return False


def send_link_expiring_email(
    *, to: str, link_label: str, days_left: int, apply_url: str,
    manage_url: str,
) -> bool:
    """Warn a recruiter that a recruiting link is about to lapse.

    Sent once per link by the nightly sweep.  Before this, nobody was told:
    the recruiter learned from an applicant reporting a dead URL, and
    anyone part-way through the form had already lost their work.
    """
    if not is_email_configured() or not to:
        return False
    when = "today" if days_left <= 0 else f"in {int(days_left)} day{'' if days_left == 1 else 's'}"
    name = html.escape(link_label or "an application link")
    inner = (
        f'<h2 style="margin:0 0 12px;font-size:18px">Application link expires '
        f"{html.escape(when)}</h2>"
        f'<p style="margin:0 0 16px">Your recruiting link <b>{name}</b> stops '
        f"accepting applications {html.escape(when)}. Anyone part-way through "
        f"the form when it lapses loses what they entered, so extend it now if "
        f"the posting is still open.</p>"
        f'<p style="margin:0 0 8px;color:#6b7280;font-size:13px">'
        f"{html.escape(apply_url)}</p>"
        f'<p style="margin:0 0 24px">'
        f'<a href="{html.escape(manage_url)}" style="background:#2563eb;color:#fff;'
        f"text-decoration:none;padding:10px 18px;border-radius:8px;"
        f'display:inline-block;font-size:14px">Extend or replace it</a></p>'
    )
    try:
        return send_email(
            to=to,
            subject=f"Application link expires {when} — {link_label or 'untitled link'}",
            body=(
                f"Your recruiting link \"{link_label or 'untitled link'}\" stops "
                f"accepting applications {when}. Anyone part-way through the "
                f"form when it lapses loses what they entered.\n\n"
                f"Link: {apply_url}\n"
                f"Extend or replace it: {manage_url}\n"
            ),
            html_body=_shell(inner),
            extra_headers={"Auto-Submitted": "auto-generated"},
        )
    except Exception as e:  # best-effort
        logger.debug("send_link_expiring_email to %s failed: %s", to, e)
        return False
