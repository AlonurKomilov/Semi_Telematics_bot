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
