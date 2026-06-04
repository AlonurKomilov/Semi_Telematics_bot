"""SMTP-based email transport for alert fallbacks.

Stdlib only — no SDK dependency.  The whole module is a no-op when
``SMTP_HOST`` is unset, so deploys without an SMTP relay keep working
unchanged.  Operators that want the email fallback set ``SMTP_HOST``,
``SMTP_PORT`` (defaults to 587), ``SMTP_USER``, ``SMTP_PASS``,
``SMTP_FROM``, and optionally ``SMTP_FROM_NAME`` (display name shown
to recipients — defaults to ``4truck``) and ``SMTP_REPLY_TO`` (a
monitored mailbox the recipient's "Reply" button targets — recommended
when ``SMTP_FROM`` is a no-reply alias, since one-way senders hurt
deliverability reputation).

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
from email.utils import formataddr, parseaddr
from typing import Iterable, Optional, Tuple

# Resend's per-message limit is 40 MB; SES is 10 MB; SendGrid is 30 MB.
# Cap below the lowest common ceiling so a Scheduled-Reports PDF
# delivery via email never silently bounces.  Callers that hit this
# limit should fall back to the Telegram channel and surface a
# "view-on-dashboard" link in the email body.
MAX_ATTACHMENT_BYTES = 9 * 1024 * 1024  # 9 MB — leaves headroom under SES's 10 MB

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
    attachments: Optional[Iterable[Tuple[str, bytes, str]]] = None,
) -> bool:
    """Send a single email via SMTP.

    Returns ``True`` when the message was handed off to the relay,
    ``False`` when configuration is missing or the send raised.
    Never propagates SMTP errors — alert paths must continue even if
    the relay is down.

    ``body`` is plain text (required); ``html_body`` is optional and
    becomes the alternative ``text/html`` part when set.

    ``attachments`` is an optional iterable of ``(filename, payload,
    mimetype)`` triples; mimetype is a full ``"application/pdf"``-style
    string parsed into ``maintype``/``subtype``.  Any single attachment
    larger than ``MAX_ATTACHMENT_BYTES`` is dropped (with a warning) —
    Scheduled-Reports callers should fall back to the Telegram channel
    when they get ``False`` from this function for a large PDF.
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

    # Wrap the From header with a display name so recipients see
    # "4truck" in their inbox column instead of the "noreply" local-part.
    # Operators with a non-default brand override via SMTP_FROM_NAME.
    # If SMTP_FROM already contains a display-name component
    # (``"Brand" <addr>``), respect that and skip the wrap.
    parsed_name, parsed_addr = parseaddr(sender)
    if parsed_name:
        from_header = sender
    else:
        from_name = _env("SMTP_FROM_NAME", "4truck") or ""
        addr = parsed_addr or sender
        from_header = formataddr((from_name, addr)) if from_name else addr

    msg = EmailMessage()
    msg["From"] = from_header
    msg["To"] = to
    msg["Subject"] = subject
    # Reply-To lets recipients reach a real mailbox even when the From
    # address is a no-reply alias.  Without it, Gmail/Outlook flag the
    # message as one-way and penalise sender reputation.
    reply_to = _env("SMTP_REPLY_TO")
    if reply_to:
        msg["Reply-To"] = reply_to
    msg.set_content(body)
    if html_body:
        msg.add_alternative(html_body, subtype="html")

    # Attachments — added AFTER the body/alternative so the multipart
    # tree comes out as ``multipart/mixed → (multipart/alternative,
    # attachment*)``, which Gmail/Outlook render correctly with the
    # body inline and attachments listed below.
    if attachments:
        for filename, payload, mimetype in attachments:
            if not payload:
                continue
            if len(payload) > MAX_ATTACHMENT_BYTES:
                logger.warning(
                    "send_email: dropping oversized attachment %r "
                    "(%d bytes > %d limit)",
                    filename, len(payload), MAX_ATTACHMENT_BYTES,
                )
                return False
            maintype, _, subtype = mimetype.partition("/")
            msg.add_attachment(
                payload,
                maintype=maintype or "application",
                subtype=subtype or "octet-stream",
                filename=filename,
            )

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
