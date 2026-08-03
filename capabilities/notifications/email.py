"""Email channel — the first adapter that renders DIFFERENTLY from chat.

This is why the semantic ``NotificationContent`` + per-channel ``render``
seam exists (docs §5).  A chat message is one HTML string; an email is a
subject + a text/plain part + a text/html part + a CAN-SPAM unsubscribe.
None of those can be recovered from a Telegram-shaped string without loss,
so email renders straight from the raw content instead.

Transport reuses ``capabilities/email`` (``send_email`` — stdlib SMTP or
the Resend HTTP path, chosen by env).  ``send_email`` is blocking, so the
async ``send`` runs it in a thread, matching the invite-email path.

Scope (4a): render + send + a working unsubscribe LINK (to the
preferences page) + the RFC 8058 ``List-Unsubscribe`` header.  The
one-click POST endpoint + address verification live in 4b — until an
address is verified, ``get_notification_subscribers`` returns no email
recipient, so this channel is inert-but-tested today (same posture the
digest queue shipped in).
"""

from __future__ import annotations

import logging
import os

from .channels import (
    DeliveryResult, NotificationContent, Payload, Recipient, parse_action,
)

logger = logging.getLogger("bot.notifications")

_SEV_SUBJECT_PREFIX = {"critical": "[Critical] ", "warning": "[Warning] "}


def _base_url() -> str:
    """Where the dashboard lives — same env convention as the auth
    emails (``AUTH_BASE_URL`` → ``DASHBOARD_BASE_URL`` → apex)."""
    value = (
        os.getenv("AUTH_BASE_URL")
        or os.getenv("DASHBOARD_BASE_URL")
        or "https://4truck.us"
    )
    return value.rstrip("/")


def _unsubscribe_url(recipient: Recipient) -> str:
    """Per-recipient signed one-click unsubscribe link — the target the
    ``List-Unsubscribe-Post`` header points at.  Turns this channel off
    without a login (4b)."""
    from capabilities.notifications.lifecycle import unsubscribe_url
    return unsubscribe_url(
        recipient.account_id, recipient.type, recipient.id, "email")


def _brand() -> str:
    return os.getenv("EMAIL_BRAND_NAME") or "4truck"


def _action(content: NotificationContent) -> tuple[str, str] | None:
    """The declared call-to-action as ``(label, ABSOLUTE url)``.

    Validity is ``channels.parse_action`` — one definition of the
    relative-only rule for every renderer.  Email is the one that has to
    absolutise the result, because a mail client has no origin to resolve
    a relative path against.
    """
    parsed = parse_action(content.meta)
    if parsed is None:
        return None
    label, url = parsed
    return label, f"{_base_url()}{url}"


class EmailChannel:
    """Personal email delivery.  ``recipient.address`` is the verified
    email address (verification is 4b)."""

    key = "email"
    personal = True

    # ── render: semantic content → an email Payload ──────────────────

    def render(self, recipient: Recipient, content: NotificationContent) -> Payload:
        """Build subject + text/plain + text/html from RAW content.  HTML
        escaping happens HERE, exactly once (``html.escape``), so the same
        content that becomes Telegram HTML elsewhere becomes a safe email
        body here with no double-escaping.

        A source that declares ``meta["action"]`` gets a labelled button
        instead of a naked link — the one thing a feature's own template
        used to do that the generic renderer could not."""
        import html as _html

        prefix = _SEV_SUBJECT_PREFIX.get(content.severity, "")
        subject = f"{prefix}{content.title}".strip()

        unsub = _unsubscribe_url(recipient)
        brand = _brand()

        action = _action(content)

        # text/plain — the required part; every client can show it.  The
        # action becomes "Label: <url>" here; text/plain has no buttons,
        # but naming the destination still beats a bare address.
        text_lines = [content.title]
        if content.body:
            text_lines += ["", content.body]
        if action:
            text_lines += ["", f"{action[0]}: {action[1]}"]
        elif content.url:
            text_lines += ["", content.url]
        text_lines += [
            "", "—",
            f"You're receiving this from {brand} because you enabled email "
            "notifications.",
            f"Manage or turn off: {unsub}",
        ]
        text_body = "\n".join(text_lines)

        # text/html — escape raw fields, keep newlines as <br>.
        safe_title = _html.escape(content.title)
        safe_body = _html.escape(content.body).replace("\n", "<br>")
        if action:
            # A labelled button: what the old hand-rolled recruiting
            # template had and a bare URL doesn't — you read what the
            # click DOES instead of parsing an address.  Inline styles
            # only; mail clients strip <style> blocks.
            link_html = (
                f'<p style="margin:16px 0"><a href="{_html.escape(action[1])}" '
                f'style="background:#2563eb;color:#ffffff;text-decoration:none;'
                f'padding:10px 18px;border-radius:8px;display:inline-block;'
                f'font-size:14px;font-weight:600">{_html.escape(action[0])}</a></p>'
            )
        elif content.url:
            link_html = (
                f'<p><a href="{_html.escape(content.url)}">'
                f'{_html.escape(content.url)}</a></p>'
            )
        else:
            link_html = ""
        html_body = (
            f'<div style="font-family:system-ui,Arial,sans-serif;font-size:14px;'
            f'color:#111827;line-height:1.5">'
            f"<p style=\"font-size:16px;font-weight:600\">{safe_title}</p>"
            f"{'<p>' + safe_body + '</p>' if content.body else ''}"
            f"{link_html}"
            f'<hr style="border:none;border-top:1px solid #e5e7eb;margin:16px 0">'
            f'<p style="font-size:11px;color:#6b7280">'
            f"You're receiving this from {_html.escape(brand)} because you enabled "
            f"email notifications. "
            f'<a href="{_html.escape(unsub)}" style="color:#6b7280">'
            f"Manage or turn these off.</a></p></div>"
        )

        return Payload(
            text=text_body, subject=subject, parse_mode="",
            extra={
                "html": html_body,
                "list_unsubscribe": f"<{unsub}>",
            },
        )

    # ── send: hand the rendered Payload to the SMTP/Resend transport ──

    async def send(self, recipient: Recipient, payload: Payload) -> DeliveryResult:
        import asyncio

        from capabilities.email import is_email_configured, send_email

        to = (recipient.address or "").strip()
        if not to or "@" not in to:
            return DeliveryResult(ok=False, error="bad_email")
        if not is_email_configured():
            return DeliveryResult(ok=False, error="email_not_configured")

        # RFC 8058 List-Unsubscribe (+ One-Click POST) — mirrors the
        # invite email.  The header value is set by render() in ``extra``.
        headers = {"Auto-Submitted": "auto-generated"}
        lu = payload.extra.get("list_unsubscribe")
        if lu:
            headers["List-Unsubscribe"] = lu
            headers["List-Unsubscribe-Post"] = "List-Unsubscribe=One-Click"

        try:
            ok = await asyncio.to_thread(
                lambda: send_email(
                    to=to,
                    subject=payload.subject or "Notification",
                    body=payload.text,
                    html_body=payload.extra.get("html"),
                    extra_headers=headers,
                )
            )
        except Exception as e:                    # transport must never crash fan-out
            logger.warning("email send failed for %s: %s", recipient.id, e)
            return DeliveryResult(ok=False, error=type(e).__name__)
        return DeliveryResult(ok=bool(ok), error="" if ok else "send_failed")
