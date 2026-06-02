"""Email templates + senders for account-security flows.

Three messages live here: password-reset link, email-verification link,
and the "your account was just locked after N failed login attempts"
heads-up.  Each one composes a plain-text body + an HTML body and
hands them to the shared ``send_email`` shim.

Why a dedicated module
----------------------
- Keeps copy + branding in one file so future redesigns touch one
  surface, not three call sites.
- Insulates the auth routes from string-formatting and HTML escaping.
- Makes the public URLs (where the link points) configurable via a
  single env var so dev/staging/prod can each have their own dashboard
  origin without code changes.
"""

from __future__ import annotations

import html
import logging
import os
from typing import Optional

from capabilities.notifications.email import is_email_configured, send_email

logger = logging.getLogger(__name__)


# Where the dashboard lives — used to compose the link inside the
# email body.  Defaults to the production apex; deployments override
# via env so a staging mail doesn't ship a prod link.
def _dashboard_base() -> str:
    return (os.getenv("DASHBOARD_BASE_URL") or "https://dash.4truck.us").rstrip("/")


def _company_name() -> str:
    return os.getenv("EMAIL_BRAND_NAME") or "4truck"


# ── Reset password ──────────────────────────────────────────────────


def send_password_reset_email(
    *, to: str, token: str, recipient_name: str = "",
) -> bool:
    """Compose + send the password-reset email.

    Returns ``True`` when handed off to the relay, ``False`` when
    SMTP isn't configured or the send raised.  The caller is
    responsible for the auth-route response either way — we never
    want a transient email failure to leak the existence (or not) of
    an email address to a probing attacker.
    """
    if not is_email_configured():
        logger.info(
            "Password reset email NOT sent (SMTP not configured) — "
            "token for %s: visit %s/reset-password?token=%s",
            to, _dashboard_base(), token,
        )
        return False

    brand = _company_name()
    base = _dashboard_base()
    reset_url = f"{base}/reset-password?token={token}"
    greeting = f"Hi {html.escape(recipient_name)}," if recipient_name else "Hi,"

    subject = f"{brand}: reset your password"
    text_body = (
        f"{greeting}\n\n"
        f"We received a request to reset the password for your {brand} account.\n"
        f"To choose a new password, open this link in your browser:\n\n"
        f"{reset_url}\n\n"
        "The link expires in 1 hour and can only be used once.\n\n"
        "If you didn't request this, you can safely ignore this email — "
        "your password won't change.\n\n"
        f"— The {brand} team"
    )
    html_body = f"""\
<!doctype html>
<html><body style="font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',Roboto,sans-serif;max-width:560px;margin:24px auto;color:#1f2937">
<p style="font-size:15px">{greeting}</p>
<p style="font-size:15px">We received a request to reset the password for your <strong>{html.escape(brand)}</strong> account.</p>
<p style="margin:28px 0">
  <a href="{html.escape(reset_url)}"
     style="background:#111827;color:#fff;text-decoration:none;padding:12px 22px;border-radius:8px;display:inline-block;font-weight:600">
    Choose a new password
  </a>
</p>
<p style="font-size:13px;color:#6b7280">Or paste this URL into your browser:<br>
<span style="word-break:break-all">{html.escape(reset_url)}</span></p>
<p style="font-size:13px;color:#6b7280">The link expires in 1 hour and can only be used once.</p>
<p style="font-size:13px;color:#6b7280">If you didn't request this, you can safely ignore this email — your password won't change.</p>
<p style="font-size:13px;color:#9ca3af;margin-top:32px">— The {html.escape(brand)} team</p>
</body></html>"""
    return send_email(to=to, subject=subject, body=text_body, html_body=html_body)


# ── Verify email ────────────────────────────────────────────────────


def send_verification_email(
    *, to: str, token: str, recipient_name: str = "",
) -> bool:
    """Compose + send the email-verification message.

    Sent on signup and on any later "resend verification" request.
    The link redirects the user to the dashboard once redeemed; the
    backend flips ``users.email_verified=1`` and clears the lock that
    prevented login.
    """
    if not is_email_configured():
        logger.info(
            "Verification email NOT sent (SMTP not configured) — "
            "token for %s: visit %s/verify-email?token=%s",
            to, _dashboard_base(), token,
        )
        return False

    brand = _company_name()
    base = _dashboard_base()
    verify_url = f"{base}/verify-email?token={token}"
    greeting = f"Welcome to {brand}, {html.escape(recipient_name)}!" if recipient_name else f"Welcome to {brand}!"

    subject = f"Verify your email for {brand}"
    text_body = (
        f"{greeting}\n\n"
        f"Confirm your email so you can sign in and start using {brand}:\n\n"
        f"{verify_url}\n\n"
        "The link expires in 24 hours.  If you didn't create this account, "
        "you can safely ignore this email.\n\n"
        f"— The {brand} team"
    )
    html_body = f"""\
<!doctype html>
<html><body style="font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',Roboto,sans-serif;max-width:560px;margin:24px auto;color:#1f2937">
<p style="font-size:17px;font-weight:600">{greeting}</p>
<p style="font-size:15px">Confirm your email address so you can sign in and start using <strong>{html.escape(brand)}</strong>.</p>
<p style="margin:28px 0">
  <a href="{html.escape(verify_url)}"
     style="background:#111827;color:#fff;text-decoration:none;padding:12px 22px;border-radius:8px;display:inline-block;font-weight:600">
    Verify my email
  </a>
</p>
<p style="font-size:13px;color:#6b7280">Or paste this URL into your browser:<br>
<span style="word-break:break-all">{html.escape(verify_url)}</span></p>
<p style="font-size:13px;color:#6b7280">The link expires in 24 hours.  If you didn't create this account, you can safely ignore this email.</p>
<p style="font-size:13px;color:#9ca3af;margin-top:32px">— The {html.escape(brand)} team</p>
</body></html>"""
    return send_email(to=to, subject=subject, body=text_body, html_body=html_body)


# ── Lockout notification ────────────────────────────────────────────


def send_lockout_notice(
    *, to: str, ip: Optional[str] = None,
    lock_minutes: int = 15, recipient_name: str = "",
) -> bool:
    """Heads-up email when an account hits the lockout threshold.

    Sent right after the 5th consecutive failed-login attempt.  The
    goal is to alert the legitimate account holder that someone may
    be trying to break in — they can then change their password
    proactively even before the lock window expires.
    """
    if not is_email_configured():
        logger.info(
            "Lockout notice NOT sent (SMTP not configured) — to %s, lock %d min",
            to, lock_minutes,
        )
        return False

    brand = _company_name()
    greeting = f"Hi {html.escape(recipient_name)}," if recipient_name else "Hi,"
    ip_note = f" The attempts came from IP <code>{html.escape(ip)}</code>." if ip else ""

    subject = f"{brand}: your account was temporarily locked"
    text_body = (
        f"{greeting}\n\n"
        f"We saw 5 failed login attempts on your {brand} account and "
        f"temporarily locked it for {lock_minutes} minutes.\n\n"
        "If that was you (typo, forgotten password), wait it out or use the "
        "\"Forgot password\" link on the login page.\n\n"
        "If it wasn't you, change your password as soon as the lock clears — "
        "someone may be trying to guess it.\n\n"
        f"— The {brand} team"
    )
    html_body = f"""\
<!doctype html>
<html><body style="font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',Roboto,sans-serif;max-width:560px;margin:24px auto;color:#1f2937">
<p style="font-size:15px">{greeting}</p>
<p style="font-size:15px">We saw <strong>5 failed login attempts</strong> on your <strong>{html.escape(brand)}</strong> account
and temporarily locked it for <strong>{lock_minutes} minutes</strong>.{ip_note}</p>
<p style="font-size:14px">If that was you (typo, forgotten password) — wait it out or use the
&ldquo;Forgot password&rdquo; link on the login page.</p>
<p style="font-size:14px">If it wasn't you, change your password as soon as the lock clears — someone may be trying to guess it.</p>
<p style="font-size:13px;color:#9ca3af;margin-top:32px">— The {html.escape(brand)} team</p>
</body></html>"""
    return send_email(to=to, subject=subject, body=text_body, html_body=html_body)
