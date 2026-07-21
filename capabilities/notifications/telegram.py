"""Telegram channels — the two distinct paths, as separate adapters.

These are the SAME two paths the alerting pipeline already runs (docs
§5), kept fully separate here — never merge them:

  • ``telegram_dm``    — Bot → USER private chat.  PERSONAL, per-user
                         (the ``alert_prefs`` DM fanout today).
  • ``telegram_topic`` — Bot → GROUP topic.  SHARED, admin-routed
                         per-role (Forum Routing / ``resolve_alert_targets``
                         today).

Each owns only the leaf TRANSPORT (resolve the account bot, send with a
retry-once on Telegram flood control).  Orchestration that is NOT a
channel concern — DND, dedup, occurrence footers, ack records, company
scope, message rendering — stays in the caller (the notification
service / today's pipeline).  Imports only ``infra`` + the telegram lib,
so this module never depends on ``capabilities.alerting``.
"""

from __future__ import annotations

import asyncio
import logging

from .channels import (
    DeliveryResult,
    NotificationContent,
    Payload,
    Recipient,
    register_channel,
)

logger = logging.getLogger("bot.notifications")

# Severity → the emoji the pipeline already uses, so a notification-layer
# Telegram message reads the same as a live alert.  Keys are validated
# against channels.SEVERITIES by a test so the three severity dicts
# (icon / subject-prefix / rank) can't drift apart.
_SEV_ICON = {"critical": "🔴", "warning": "🟡", "info": "🔵"}

# Telegram's hard limits: 4096 for a message, 1024 for a photo caption.
_TELEGRAM_TEXT_MAX = 4096
_TELEGRAM_CAPTION_MAX = 1024


def _clamp(text: str, limit: int) -> str:
    """Truncate to Telegram's limit without splitting an HTML entity.

    The bound in ``build_digest_content`` is on RAW length, but escaping
    grows it (``&`` → ``&amp;``), so a digest full of ampersands could
    still exceed the transport cap and — because items clear only on a
    successful send — wedge that recipient's queue forever.  This is the
    final, transport-shaped safety net.  Because content is escaped with
    plain ``html.escape``, the only multi-char sequences are ``&…;``
    entities, so backing off to before a dangling ``&`` guarantees valid
    HTML that Telegram will parse."""
    if len(text) <= limit:
        return text
    cut = text[:limit - 1]              # room for the ellipsis
    amp, semi = cut.rfind("&"), cut.rfind(";")
    if amp > semi:                      # mid-entity — drop the partial entity
        cut = cut[:amp]
    return cut + "…"


def render_telegram(content: NotificationContent) -> Payload:
    """Semantic content → a Telegram HTML :class:`Payload`.

    The ONE place raw content gets escaped for Telegram — with plain
    ``html.escape`` (NOT the alerting pipeline's allow-list
    ``escape_html``): ``NotificationContent`` is documented raw plain
    text, so a literal ``<a href>`` in it must render as inert text, never
    a live link.  Escape exactly once (``html.escape`` handles ``&`` /
    ``<`` / ``>``); the ``<b>`` framing is added AFTER escaping.  Both
    Telegram channels share this: a DM and a group topic render
    identically; only the destination differs."""
    import html as _html

    icon = _SEV_ICON.get(content.severity, "")
    head = f"{icon} <b>{_html.escape(content.title)}</b>" if icon \
        else f"<b>{_html.escape(content.title)}</b>"
    parts = [head]
    if content.body:
        parts.append(_html.escape(content.body))
    if content.url:
        parts.append(_html.escape(content.url))
    limit = _TELEGRAM_CAPTION_MAX if content.photo_bytes else _TELEGRAM_TEXT_MAX
    return Payload(
        text=_clamp("\n".join(parts), limit), parse_mode="HTML",
        photo_bytes=content.photo_bytes,
    )


async def _tg_send_with_retry(send, *, what: str):
    """Run one ``bot.send_*`` coroutine factory, retrying ONCE after
    Telegram flood control (mirrors the pipeline's proven primitive).

    ``send`` is a zero-arg callable returning a FRESH coroutine so the
    retry re-issues the request instead of awaiting a spent one.  A
    second ``RetryAfter`` propagates to the caller.
    """
    from telegram.error import RetryAfter

    from infra.observability import record_alert_flood
    try:
        return await send()
    except RetryAfter as e:
        delay = float(getattr(e, "retry_after", 3)) + 0.5
        record_alert_flood("retried")
        logger.warning("Telegram flood on %s — retry in %.1fs", what, delay)
        await asyncio.sleep(delay)
        try:
            result = await send()
            record_alert_flood("delivered_after_retry")
            return result
        except RetryAfter:
            record_alert_flood("dropped")
            logger.error("Telegram flood persisted on %s — dropped", what)
            raise


def _bot_for(account_id: int, override=None):
    """The per-account bot Application (or an explicit override — e.g.
    the owner_admin aggregate cross-post uses the primary bot)."""
    if override is not None:
        return override
    from infra.bot_registry import get_app_for_account
    return get_app_for_account(account_id)


async def _send(app, *, chat_id: int, thread_id: int | None,
                payload: Payload, what: str) -> DeliveryResult:
    """Shared leaf send for both Telegram channels."""
    if app is None:
        return DeliveryResult(ok=False, error="no_bot")
    try:
        if payload.photo_bytes is not None:
            msg = await _tg_send_with_retry(
                lambda: app.bot.send_photo(
                    chat_id=chat_id, message_thread_id=thread_id,
                    photo=payload.photo_bytes, caption=payload.text,
                    parse_mode=payload.parse_mode, reply_markup=payload.markup,
                ), what=what,
            )
        else:
            msg = await _tg_send_with_retry(
                lambda: app.bot.send_message(
                    chat_id=chat_id, message_thread_id=thread_id,
                    text=payload.text, parse_mode=payload.parse_mode,
                    reply_markup=payload.markup,
                ), what=what,
            )
        return DeliveryResult(ok=True, provider_ref=str(getattr(msg, "message_id", "")))
    except Exception as e:   # transport failure — the caller logs/records
        logger.warning("Telegram %s send failed: %s", what, e)
        return DeliveryResult(ok=False, error=type(e).__name__)


class TelegramDmChannel:
    """Bot → USER private chat.  PERSONAL (per-user address)."""
    key = "telegram_dm"
    personal = True

    def render(self, recipient: Recipient, content: NotificationContent) -> Payload:
        return render_telegram(content)

    async def send(self, recipient: Recipient, payload: Payload) -> DeliveryResult:
        raw = recipient.address or recipient.id
        try:
            chat_id = int(raw)
        except (TypeError, ValueError):
            return DeliveryResult(ok=False, error="bad_telegram_id")
        return await _send(
            _bot_for(recipient.account_id), chat_id=chat_id, thread_id=None,
            payload=payload, what="dm",
        )


class TelegramTopicChannel:
    """Bot → GROUP topic.  SHARED (per-account/topic destination).

    ``address`` = ``"<chat_id>"`` or ``"<chat_id>:<thread_id>"``.  Pass
    ``sender_app`` to force a specific bot (the owner_admin aggregate
    cross-post uses the primary bot, not the persona bot)."""
    key = "telegram_topic"
    personal = False

    def render(self, recipient: Recipient, content: NotificationContent) -> Payload:
        return render_telegram(content)

    async def send(self, recipient: Recipient, payload: Payload,
                   *, sender_app=None) -> DeliveryResult:
        chat_raw, _, thread_raw = str(recipient.address).partition(":")
        try:
            chat_id = int(chat_raw)
        except (TypeError, ValueError):
            return DeliveryResult(ok=False, error="bad_chat_id")
        thread_id = int(thread_raw) if thread_raw else None
        return await _send(
            _bot_for(recipient.account_id, sender_app),
            chat_id=chat_id, thread_id=thread_id, payload=payload, what="topic",
        )


register_channel(TelegramDmChannel())
register_channel(TelegramTopicChannel())
