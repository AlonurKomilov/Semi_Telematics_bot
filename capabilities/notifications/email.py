"""SMTP-based email transport for alert fallbacks.

Stdlib only — no SDK dependency.  The whole module is a no-op when
``SMTP_HOST`` is unset, so deploys without an SMTP relay keep working
unchanged.  Operators that want the email fallback set ``SMTP_HOST``,
``SMTP_PORT`` (defaults to 587), ``SMTP_USER``, ``SMTP_PASS``,
``SMTP_FROM``.

Why stdlib instead of an SDK: SES / SendGrid / Postmark all speak SMTP
and reading credentials from env is the lowest-friction integration
path.  When the operator outgrows SMTP, swap this module for an SDK
without touching callers — the ``send_email`` signature stays.
"""

from __future__ import annotations

import logging
import os
import smtplib
from email.message import EmailMessage
from typing import Optional

logger = logging.getLogger(__name__)


def _env(key: str, default: Optional[str] = None) -> Optional[str]:
    """Resolve an env var, treating empty string the same as unset."""
    v = os.environ.get(key, default)
    return v if v else default


def is_email_configured() -> bool:
    """``True`` when SMTP credentials are present in the environment.

    Callers can short-circuit composition work when this returns
    False — no point building the HTML body of an email no one will
    ever receive.
    """
    return bool(_env("SMTP_HOST") and _env("SMTP_FROM"))


def send_email(
    *,
    to: str,
    subject: str,
    body: str,
    html_body: Optional[str] = None,
) -> bool:
    """Send a single email via SMTP.

    Returns ``True`` when the message was handed off to the relay,
    ``False`` when configuration is missing or the send raised.
    Never propagates SMTP errors — alert paths must continue even if
    the relay is down.

    ``body`` is plain text (required); ``html_body`` is optional and
    becomes the alternative ``text/html`` part when set.
    """
    if not is_email_configured():
        # Down-graded to debug — the no-op state is the default in
        # dev, and we don't want every scheduler tick to spam INFO.
        logger.debug("send_email skipped: SMTP not configured")
        return False
    if not to or "@" not in to:
        logger.debug("send_email skipped: invalid 'to' address: %r", to)
        return False

    host = _env("SMTP_HOST") or ""
    port = int(_env("SMTP_PORT", "587") or "587")
    user = _env("SMTP_USER")
    password = _env("SMTP_PASS")
    sender = _env("SMTP_FROM") or user or ""
    use_tls = (_env("SMTP_USE_TLS", "1") or "1") not in ("0", "false", "False")

    msg = EmailMessage()
    msg["From"] = sender
    msg["To"] = to
    msg["Subject"] = subject
    msg.set_content(body)
    if html_body:
        msg.add_alternative(html_body, subtype="html")

    try:
        # 30s timeout keeps the scheduler tick bounded even if the
        # relay hangs — we'd rather fail one notification than block
        # the whole overdue-marker job.
        with smtplib.SMTP(host, port, timeout=30) as smtp:
            if use_tls:
                smtp.starttls()
            if user and password:
                smtp.login(user, password)
            smtp.send_message(msg)
        return True
    except Exception as e:
        # Best-effort: log + swallow.  The Telegram path that called
        # us either already succeeded or already failed; the email
        # outcome doesn't change the scheduler's exit status.
        logger.warning("send_email to %s failed: %s", to, e)
        return False
