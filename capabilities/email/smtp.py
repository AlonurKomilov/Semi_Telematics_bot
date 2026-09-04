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

import email.utils
import logging
import os
import smtplib
from email.message import EmailMessage
from email.utils import formataddr, parseaddr
from typing import Iterable, Mapping, Optional, Tuple


def _strip_crlf(value: Optional[str]) -> Optional[str]:
    """Defensive header sanitization — refuse CR/LF in any value that
    will land in an SMTP header.  Python's ``email.message`` already
    refuses raw CRLF when serialising, but a hostile env var
    (``SMTP_FROM_NAME='Acme\\r\\nBcc: attacker@…'``) or a future caller
    that passes operator-supplied text into ``from_name`` / ``reply_to``
    would otherwise raise ValueError at send time.  Stripping here
    fails open (we send without the malicious header) instead of
    crashing the whole send."""
    if value is None:
        return None
    return value.replace("\r", "").replace("\n", "").strip() or None

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


def send_email_detailed(
    *,
    to: str,
    subject: str,
    body: str,
    html_body: Optional[str] = None,
    attachments: Optional[Iterable[Tuple[str, bytes, str]]] = None,
    from_address: Optional[str] = None,
    from_name: Optional[str] = None,
    reply_to: Optional[str] = None,
    extra_headers: Optional[Mapping[str, str]] = None,
) -> bool:
    """Send a single email via SMTP.

    Returns ``True`` when the message was handed off to the relay,
    ``False`` when configuration is missing or the send raised.
    Never propagates SMTP errors — alert paths must continue even if
    the relay is down.

    Per-call overrides (introduced for the invite-email channel so
    invite emails ship from ``invites@4truck.us`` while auth emails
    keep using ``noreply@4truck.us``):

      from_address  — overrides SMTP_FROM for THIS send only.
                      Fall back: SMTP_FROM (existing behaviour).
      from_name     — overrides the SMTP_FROM_NAME display name.
                      Fall back: SMTP_FROM_NAME (existing default).
      reply_to      — overrides SMTP_REPLY_TO for THIS send only.
                      Fall back: SMTP_REPLY_TO (existing default).
      extra_headers — additional headers like ``List-Unsubscribe``
                      or ``Auto-Submitted``.  Sanitized for CRLF; an
                      injected newline in a value drops just that
                      header rather than crashing the send.

    All three string-typed overrides are CRLF-stripped via
    ``_strip_crlf`` — defence-in-depth against header injection from
    operator-supplied or env-var inputs (the env vars themselves are
    already validated by the deploy, but the new ``from_name``
    parameter accepts code-supplied display names that a future
    refactor might pipe through from caller input).

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
        return False, "email_not_configured"
    if not to or "@" not in to:
        logger.debug("send_email skipped: invalid 'to' address: %r", to)
        return False, "bad_email"

    host = _env("SMTP_HOST") or ""
    port = int(_env("SMTP_PORT", "587") or "587")
    user = _env("SMTP_USER")
    password = _env("SMTP_PASS")
    # Per-call from_address wins over the env default.  Both go
    # through the same display-name wrap below so the inbox column
    # always shows a friendly name.
    sender = _strip_crlf(from_address) or _env("SMTP_FROM") or user or ""
    use_tls = (_env("SMTP_USE_TLS", "1") or "1") not in ("0", "false", "False")

    # Wrap the From header with a display name so recipients see
    # "4truck" in their inbox column instead of the "noreply" local-part.
    # Operators with a non-default brand override via SMTP_FROM_NAME.
    # If SMTP_FROM already contains a display-name component
    # (``"Brand" <addr>``), respect that and skip the wrap.
    # Per-call ``from_name`` wins over the env default — lets the
    # invite-email send identify itself as "4truck Invites" without
    # affecting "4truck" on auth emails sharing the same process.
    parsed_name, parsed_addr = parseaddr(sender)
    if parsed_name:
        from_header = sender
    else:
        effective_name = (
            _strip_crlf(from_name) or _env("SMTP_FROM_NAME", "4truck") or ""
        )
        addr = parsed_addr or sender
        from_header = formataddr((effective_name, addr)) if effective_name else addr

    msg = EmailMessage()
    msg["From"] = from_header
    # Strip CR/LF from the recipient too — the only header that wasn't,
    # and an unstripped newline here would otherwise raise mid-send. CR/LF
    # is never valid in an address, so stripping can't harm a real one.
    msg["To"] = _strip_crlf(to) or to
    msg["Subject"] = _strip_crlf(subject) or subject
    # Message-ID gives Outlook / Exchange Online a stable identity to
    # group retries by and avoids 'missing Message-ID' as a soft
    # spam-score signal.  Date is auto-added by EmailMessage but we
    # set it explicitly so the timestamp is deterministic UTC, not
    # whatever localtime the relay happens to live in.
    # Derive the domain strictly from parsed_addr (parseaddr-stripped
    # bare address); rpartitioning the raw sender string would catch
    # a trailing '>' from a `"Brand" <addr@host>` form and produce a
    # syntactically invalid Message-ID that strict ESPs reject.
    sender_domain = parsed_addr.rpartition("@")[-1] if parsed_addr else "localhost"
    msg["Message-ID"] = email.utils.make_msgid(domain=sender_domain)
    msg["Date"] = email.utils.formatdate(localtime=False)
    # Reply-To lets recipients reach a real mailbox even when the From
    # address is a no-reply alias.  Without it, Gmail/Outlook flag the
    # message as one-way and penalise sender reputation.
    effective_reply_to = _strip_crlf(reply_to) or _env("SMTP_REPLY_TO")
    if effective_reply_to:
        msg["Reply-To"] = effective_reply_to
    # extra_headers — sanitize value-by-value; a CRLF-injected entry
    # is dropped rather than crashing the send.  Used by the invite
    # email for List-Unsubscribe / Auto-Submitted.
    if extra_headers:
        for hk, hv in extra_headers.items():
            clean = _strip_crlf(hv)
            if clean is None:
                logger.warning("send_email: dropped extra header %r (CRLF in value)", hk)
                continue
            msg[hk] = clean
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
                return False, "attachment_too_large"
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
        return True, ""
    except Exception as e:
        # Best-effort for the bool callers: log + swallow.  But the
        # REASON now travels, because "mailbox does not exist" and "the
        # relay timed out" need opposite responses — one should stop
        # trying and tell the person, the other should try again.
        logger.warning("send_email to %s failed: %s", to, e)
        return False, f"{type(e).__name__}: {e}"[:200]


def send_email(**kwargs) -> bool:
    """Backwards-compatible wrapper: did it go out?

    Every existing caller wants a yes/no and nothing more.  Callers that
    must act on WHY it failed — the notification channel, which stops
    retrying a dead mailbox and tells its owner — use
    ``send_email_detailed``.
    """
    ok, _reason = send_email_detailed(**kwargs)
    return ok
