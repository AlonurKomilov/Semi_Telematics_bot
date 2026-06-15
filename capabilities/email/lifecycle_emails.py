"""Account-lifecycle notification emails.

Suspend / unsuspend (operator-driven) and the owner self-delete flow
(verification code → confirmed → cancelled → purge warning).  All
senders follow the auth_emails contract: return ``True`` on relay
hand-off, ``False`` when SMTP is unconfigured or the send raised —
callers treat email as best-effort and never fail the business action
on a mail error.
"""
from __future__ import annotations

import html
import logging

from capabilities.email.auth_emails import _auth_base, _company_name
from capabilities.email.smtp import is_email_configured, send_email

logger = logging.getLogger(__name__)


def _shell(inner_html: str) -> str:
    return (
        '<!doctype html><html><body style="font-family:-apple-system,'
        "BlinkMacSystemFont,'Segoe UI',Roboto,sans-serif;max-width:560px;"
        'margin:24px auto;color:#1f2937">'
        f"{inner_html}"
        f'<p style="font-size:13px;color:#9ca3af;margin-top:32px">— The '
        f"{html.escape(_company_name())} team</p></body></html>"
    )


def send_account_suspended_email(
    *, to: str, account_name: str, reason: str = "",
) -> bool:
    """Notify the account contact that an operator suspended the account."""
    if not is_email_configured():
        logger.info("suspend email NOT sent (SMTP unconfigured) for %s", to)
        return False
    brand = _company_name()
    why = f"\n\nReason: {reason}" if reason else ""
    subject = f"{brand}: your account has been suspended"
    text = (
        f"Hi,\n\nYour {brand} account \"{account_name}\" has been suspended "
        f"by the platform team. Logins are disabled while the suspension "
        f"is active.{why}\n\n"
        f"If you believe this is a mistake, reply to this email or contact "
        f"support and we'll review it.\n"
    )
    inner = (
        f'<p style="font-size:15px">Hi,</p>'
        f'<p style="font-size:15px">Your <strong>{html.escape(brand)}</strong> account '
        f'"<strong>{html.escape(account_name)}</strong>" has been suspended by the '
        f"platform team. Logins are disabled while the suspension is active.</p>"
        + (f'<p style="font-size:14px;color:#6b7280">Reason: {html.escape(reason)}</p>' if reason else "")
        + '<p style="font-size:14px">If you believe this is a mistake, reply to '
          "this email or contact support and we'll review it.</p>"
    )
    return send_email(to=to, subject=subject, body=text, html_body=_shell(inner))


def send_account_unsuspended_email(*, to: str, account_name: str) -> bool:
    if not is_email_configured():
        return False
    brand = _company_name()
    subject = f"{brand}: your account has been reactivated"
    text = (
        f"Hi,\n\nGood news — your {brand} account \"{account_name}\" has been "
        f"reactivated. You can sign in again at {_auth_base()}/login.\n"
    )
    inner = (
        f'<p style="font-size:15px">Hi,</p>'
        f'<p style="font-size:15px">Good news — your <strong>{html.escape(brand)}</strong> '
        f'account "<strong>{html.escape(account_name)}</strong>" has been reactivated.</p>'
        f'<p style="margin:24px 0"><a href="{html.escape(_auth_base())}/login" '
        'style="background:#111827;color:#fff;text-decoration:none;padding:12px 22px;'
        'border-radius:8px;display:inline-block;font-weight:600">Sign in</a></p>'
    )
    return send_email(to=to, subject=subject, body=text, html_body=_shell(inner))


def send_deletion_code_email(
    *, to: str, code: str, account_name: str, recipient_name: str = "",
) -> bool:
    """The 6-digit confirmation code for owner-initiated account deletion."""
    if not is_email_configured():
        logger.info(
            "deletion code email NOT sent (SMTP unconfigured) — code for %s: %s",
            to, code,
        )
        return False
    brand = _company_name()
    greeting = f"Hi {recipient_name}," if recipient_name else "Hi,"
    subject = f"{brand}: confirm account deletion — code {code}"
    text = (
        f"{greeting}\n\n"
        f"You asked to delete the {brand} account \"{account_name}\".\n\n"
        f"Confirmation code: {code}\n\n"
        "Enter this code on the deletion screen to confirm. The code "
        "expires in 15 minutes.\n\n"
        "After confirmation the account is deactivated immediately. Your "
        "data is then retained for 90 days — motor carriers are subject "
        "to FMCSA recordkeeping requirements, and the window also lets "
        "you restore the account if this is a mistake. After 90 days "
        "everything is permanently erased from 4truck. Files in your own "
        "Google Drive are not affected.\n\n"
        "If you did NOT request this, change your password right away.\n"
    )
    inner = (
        f'<p style="font-size:15px">{html.escape(greeting)}</p>'
        f'<p style="font-size:15px">You asked to delete the <strong>{html.escape(brand)}</strong> '
        f'account "<strong>{html.escape(account_name)}</strong>".</p>'
        f'<p style="font-size:28px;letter-spacing:6px;font-weight:700;'
        f'background:#f3f4f6;padding:14px 20px;border-radius:8px;'
        f'display:inline-block">{html.escape(code)}</p>'
        '<p style="font-size:13px;color:#6b7280">The code expires in 15 minutes.</p>'
        '<p style="font-size:14px">After confirmation the account is deactivated '
        "immediately. Your data is then retained for <strong>90 days</strong> — "
        "motor carriers are subject to FMCSA recordkeeping requirements, and the "
        "window also lets you restore the account if this is a mistake. After 90 "
        "days everything is permanently erased from 4truck. Files in your own "
        "Google Drive are not affected.</p>"
        '<p style="font-size:14px;color:#b91c1c">If you did NOT request this, '
        "change your password right away.</p>"
    )
    return send_email(to=to, subject=subject, body=text, html_body=_shell(inner))


def send_deletion_confirmed_email(
    *, to: str, account_name: str, purge_at: str,
) -> bool:
    if not is_email_configured():
        return False
    brand = _company_name()
    purge_date = (purge_at or "")[:10]
    subject = f"{brand}: account deletion scheduled"
    text = (
        f"Hi,\n\nThe {brand} account \"{account_name}\" is now deactivated and "
        f"scheduled for permanent erasure on {purge_date}.\n\n"
        "Why not erased immediately? Motor carriers are subject to FMCSA "
        "recordkeeping requirements (driver qualification files, inspection "
        "and maintenance records). The 90-day retention window protects your "
        "company's compliance position and lets you recover from an "
        "accidental deletion. Export any records you need before the date "
        "above — after the purge, recovery is impossible. Files in your own "
        "Google Drive are not affected.\n\n"
        f"Changed your mind? Sign in at {_auth_base()}/login before that date "
        "and choose \"Reactivate account\" — everything will be restored.\n"
    )
    inner = (
        f'<p style="font-size:15px">Hi,</p>'
        f'<p style="font-size:15px">The <strong>{html.escape(brand)}</strong> account '
        f'"<strong>{html.escape(account_name)}</strong>" is now deactivated and scheduled '
        f"for permanent erasure on <strong>{html.escape(purge_date)}</strong>.</p>"
        '<p style="font-size:14px"><strong>Why not erased immediately?</strong> Motor '
        "carriers are subject to FMCSA recordkeeping requirements (driver qualification "
        "files, inspection and maintenance records). The 90-day retention window protects "
        "your company's compliance position and lets you recover from an accidental "
        "deletion. Export any records you need before the date above — after the purge, "
        "recovery is impossible. Files in your own Google Drive are not affected.</p>"
        f'<p style="font-size:14px">Changed your mind? Sign in before that date and '
        'choose "Reactivate account" — everything will be restored.</p>'
        f'<p style="margin:24px 0"><a href="{html.escape(_auth_base())}/login" '
        'style="background:#111827;color:#fff;text-decoration:none;padding:12px 22px;'
        'border-radius:8px;display:inline-block;font-weight:600">Sign in</a></p>'
    )
    return send_email(to=to, subject=subject, body=text, html_body=_shell(inner))


def send_deletion_cancelled_email(*, to: str, account_name: str) -> bool:
    if not is_email_configured():
        return False
    brand = _company_name()
    subject = f"{brand}: account deletion cancelled"
    text = (
        f"Hi,\n\nDeletion of the {brand} account \"{account_name}\" has been "
        "cancelled. The account is active again and nothing was removed.\n"
    )
    inner = (
        f'<p style="font-size:15px">Hi,</p>'
        f'<p style="font-size:15px">Deletion of the <strong>{html.escape(brand)}</strong> '
        f'account "<strong>{html.escape(account_name)}</strong>" has been cancelled. '
        "The account is active again and nothing was removed.</p>"
    )
    return send_email(to=to, subject=subject, body=text, html_body=_shell(inner))


def send_purge_warning_email(
    *, to: str, account_name: str, purge_at: str,
) -> bool:
    """Last-chance reminder ~7 days before the hard purge."""
    if not is_email_configured():
        return False
    brand = _company_name()
    purge_date = (purge_at or "")[:10]
    subject = f"{brand}: account will be permanently erased on {purge_date}"
    text = (
        f"Hi,\n\nThis is a final reminder: the {brand} account "
        f"\"{account_name}\" will be permanently erased on {purge_date}.\n\n"
        f"To keep the account, sign in at {_auth_base()}/login and choose "
        "\"Reactivate account\" before that date. After the purge, recovery "
        "is impossible.\n\n"
        "Reminder: FMCSA recordkeeping obligations (driver qualification "
        "files, inspection and maintenance records) remain with your company "
        "after the purge — export anything you still need NOW. Files in "
        "your own Google Drive are not affected.\n"
    )
    inner = (
        f'<p style="font-size:15px">Hi,</p>'
        f'<p style="font-size:15px">Final reminder: the <strong>{html.escape(brand)}</strong> '
        f'account "<strong>{html.escape(account_name)}</strong>" will be permanently erased '
        f"on <strong>{html.escape(purge_date)}</strong>.</p>"
        f'<p style="font-size:14px">To keep the account, sign in and choose '
        '"Reactivate account" before that date. After the purge, recovery is impossible.</p>'
        f'<p style="margin:24px 0"><a href="{html.escape(_auth_base())}/login" '
        'style="background:#b91c1c;color:#fff;text-decoration:none;padding:12px 22px;'
        'border-radius:8px;display:inline-block;font-weight:600">Reactivate account</a></p>'
    )
    return send_email(to=to, subject=subject, body=text, html_body=_shell(inner))
