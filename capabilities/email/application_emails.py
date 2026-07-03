"""Recruiting notification emails.

Currently one sender: a new driver application landed and needs review.
Follows the auth_emails / lifecycle_emails contract — returns ``True`` on
relay hand-off, ``False`` when SMTP is unconfigured or the send raised;
callers treat email as best-effort and never fail the business action on
a mail error.  The Subject + body are fully templated (no applicant- or
recruiter-supplied text reaches headers) to avoid header injection.
"""
from __future__ import annotations

import html
import logging

from capabilities.email.auth_emails import _company_name
from capabilities.email.lifecycle_emails import _shell
from capabilities.email.smtp import is_email_configured, send_email

logger = logging.getLogger(__name__)


def send_new_application_email(
    *, to: str, account_name: str, applicant_name: str, reference: str,
    review_url: str,
) -> bool:
    """Notify a recruiter that a new driver application was submitted."""
    if not is_email_configured() or not to:
        return False
    brand = _company_name()
    safe_applicant = html.escape(applicant_name or "A driver")
    safe_account = html.escape(account_name or brand)
    safe_ref = html.escape(reference or "")
    inner = (
        f'<h2 style="margin:0 0 12px;font-size:18px">New driver application</h2>'
        f'<p style="margin:0 0 8px"><b>{safe_applicant}</b> submitted an application'
        f" to {safe_account}.</p>"
        f'<p style="margin:0 0 16px;color:#6b7280;font-size:14px">Reference '
        f"<b>{safe_ref}</b></p>"
        f'<p style="margin:0 0 24px">'
        f'<a href="{html.escape(review_url)}" style="background:#2563eb;color:#fff;'
        f"text-decoration:none;padding:10px 18px;border-radius:8px;"
        f'display:inline-block;font-size:14px">Review application</a></p>'
    )
    try:
        return send_email(
            to=to,
            subject=f"New driver application — {applicant_name or 'applicant'}",
            body=(
                f"{applicant_name or 'A driver'} submitted an application to "
                f"{account_name or brand} (ref {reference}).\n\nReview it: {review_url}\n"
            ),
            html_body=_shell(inner),
            extra_headers={"Auto-Submitted": "auto-generated"},
        )
    except Exception as e:  # best-effort — never break the submission
        logger.debug("send_new_application_email to %s failed: %s", to, e)
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
