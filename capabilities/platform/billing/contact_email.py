"""The two messages that move a customer's billing address.

One asks the person who requested the change to prove they are the
owner; the other asks the new address to prove it exists and is theirs.
They are deliberately different shapes — a code you type back, a link
you open — because they are answered by different people in the
ordinary case: an owner starts the change, and the accountant whose
inbox the bills will land in finishes it.

Both fail quietly when mail is not configured, and say so in the log
with the code, so a developer on a laptop is not locked out of their
own flow.
"""

from __future__ import annotations

import html
import logging
import urllib.parse

from capabilities.email.auth_emails import _auth_base, _company_name
from capabilities.email.lifecycle_emails import _shell
from capabilities.email.smtp import is_email_configured, send_email

logger = logging.getLogger(__name__)


def confirm_link(token: str) -> str:
    """The URL the new address opens to finish the change.

    Points at the API rather than the dashboard: whoever opens it may
    have no login here at all — an accountant, a bookkeeper — and a
    link that lands on a sign-in wall proves nothing and confirms
    nothing.
    """
    base = _auth_base()
    return f"{base}/api/billing/email/confirm?token={urllib.parse.quote(token)}"


def send_change_code(
    *, to: str, code: str, account_name: str, new_email: str,
    recipient_name: str = "",
) -> bool:
    """The six-digit code, to the address of whoever asked.

    Names the address being moved TO, because that is the detail that
    lets the reader spot a change they did not start — a code alone
    would tell them something is happening but not what.
    """
    if not is_email_configured():
        logger.info(
            "billing-email change code NOT sent (SMTP unconfigured) — code for %s: %s",
            to, code)
        return False
    brand = _company_name()
    greeting = f"Hi {recipient_name}," if recipient_name else "Hi,"
    subject = f"{brand}: confirm the billing contact change — code {code}"
    text = (
        f"{greeting}\n\n"
        f"You asked to send the bills for the {brand} account "
        f"\"{account_name}\" to:\n\n"
        f"    {new_email}\n\n"
        f"Confirmation code: {code}\n\n"
        "Enter this code on the Billing page to continue. The code expires "
        "in 15 minutes.\n\n"
        "Nothing changes yet. After the code, we email that new address a "
        "link — the bills move only once somebody there opens it, so a "
        "typo simply never takes effect.\n\n"
        "If you did NOT ask for this, ignore this email and change your "
        "password: the request cannot finish without this code.\n"
    )
    inner = (
        f'<p style="font-size:15px">{html.escape(greeting)}</p>'
        f'<p style="font-size:15px">You asked to send the bills for the '
        f'<strong>{html.escape(brand)}</strong> account '
        f'"<strong>{html.escape(account_name)}</strong>" to '
        f'<strong>{html.escape(new_email)}</strong>.</p>'
        f'<p style="font-size:28px;letter-spacing:6px;font-weight:700;'
        f'background:#f3f4f6;padding:14px 20px;border-radius:8px;'
        f'display:inline-block">{html.escape(code)}</p>'
        '<p style="font-size:13px;color:#6b7280">The code expires in 15 minutes.</p>'
        '<p style="font-size:14px">Nothing changes yet. After the code, we email '
        "that new address a link — the bills move only once somebody there opens "
        "it, so a typo simply never takes effect.</p>"
        '<p style="font-size:14px;color:#b91c1c">If you did NOT ask for this, '
        "ignore this email and change your password: the request cannot finish "
        "without this code.</p>"
    )
    return send_email(to=to, subject=subject, body=text, html_body=_shell(inner))


def send_confirm_link(
    *, to: str, token: str, account_name: str, old_email: str = "",
) -> bool:
    """The one-time link, to the address the bills would move to.

    Written for a stranger: the reader may never have heard of us, so
    it says who is asking, what will arrive here, and what to do if
    they were not expecting it.
    """
    if not is_email_configured():
        logger.info(
            "billing-email confirm link NOT sent (SMTP unconfigured) — link for %s: %s",
            to, confirm_link(token))
        return False
    brand = _company_name()
    link = confirm_link(token)
    moving_from = f" It is currently sent to {old_email}." if old_email else ""
    subject = f"{brand}: confirm this address for {account_name}'s bills"
    text = (
        "Hi,\n\n"
        f"{account_name} asked {brand} to send their invoices and payment "
        f"receipts to this address.{moving_from}\n\n"
        "Confirm that this address is right:\n\n"
        f"    {link}\n\n"
        "The link works once and expires in 48 hours. Until it is opened, "
        "nothing changes and the bills keep going where they go today.\n\n"
        "If you were not expecting this, do nothing. The request expires on "
        "its own and no mail will be sent here.\n"
    )
    inner = (
        '<p style="font-size:15px">Hi,</p>'
        f'<p style="font-size:15px"><strong>{html.escape(account_name)}</strong> '
        f'asked {html.escape(brand)} to send their invoices and payment receipts '
        f'to this address.{html.escape(moving_from)}</p>'
        f'<p><a href="{html.escape(link)}" style="display:inline-block;'
        f'background:#2563eb;color:#fff;font-size:15px;font-weight:600;'
        f'padding:12px 22px;border-radius:8px;text-decoration:none">'
        f'Confirm this address</a></p>'
        '<p style="font-size:13px;color:#6b7280">The link works once and expires '
        "in 48 hours. Until it is opened, nothing changes and the bills keep "
        "going where they go today.</p>"
        '<p style="font-size:14px">If you were not expecting this, do nothing. '
        "The request expires on its own and no mail will be sent here.</p>"
    )
    return send_email(to=to, subject=subject, body=text, html_body=_shell(inner))


__all__ = ["confirm_link", "send_change_code", "send_confirm_link"]
