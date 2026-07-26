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
    parts = []
    if content.title:
        parts.append(f"{icon} <b>{_html.escape(content.title)}</b>" if icon
                     else f"<b>{_html.escape(content.title)}</b>")
    if content.body:
        parts.append(_html.escape(content.body))
    if content.url:
        parts.append(_html.escape(content.url))
    limit = _TELEGRAM_CAPTION_MAX \
        if (content.photo_bytes or content.document_bytes
            or content.video_url) else _TELEGRAM_TEXT_MAX
    return Payload(
        text=_clamp("\n".join(parts), limit), parse_mode="HTML",
        photo_bytes=content.photo_bytes,
        video_url=content.video_url,
        document_bytes=content.document_bytes,
        document_name=content.document_name,
        markup=_action_markup(content),
    )


def _action_markup(content: NotificationContent):
    """``content.actions`` (+ optional ``meta["tg_buttons"]`` rows) → an
    inline keyboard, or None.

    ACTIONS need a routing address: the dispatch-time ``correlation_key``
    (stamped into ``content.meta`` by the service).  No key, or a key too
    long for Telegram's 64-byte callback cap → the action row is dropped
    (a message without buttons beats one with dead buttons).

    ``meta["tg_buttons"]`` is the source-provided EXTRA rows — plain
    specs ``[[{"text", "url"|"callback_data"}]]`` the source already
    routes itself (deep links, its own bot callbacks).  The spine just
    renders them under the action row; malformed specs are skipped, not
    fatal."""
    from telegram import InlineKeyboardButton, InlineKeyboardMarkup

    rows: list = []
    if content.actions:
        correlation_key = content.meta.get("correlation_key", "")
        if not correlation_key:
            logger.warning("actions without correlation_key — dropped (%s)",
                           content.category)
        else:
            from .actions import build_action_callback_data
            buttons = []
            for a in content.actions:
                data = build_action_callback_data(
                    correlation_key, str(a.get("id", "")))
                if data is None or not a.get("label"):
                    buttons = []
                    break
                buttons.append(
                    InlineKeyboardButton(str(a["label"]), callback_data=data))
            if buttons:
                rows.append(buttons)
    for spec_row in content.meta.get("tg_buttons") or []:
        buttons = []
        for spec in spec_row or []:
            text = str(spec.get("text", "") or "")
            url = spec.get("url")
            cb = spec.get("callback_data")
            if not text or (not url and not cb):
                continue
            if cb is not None and len(str(cb).encode("utf-8")) > 64:
                continue
            buttons.append(
                InlineKeyboardButton(text, url=url, callback_data=cb))
        if buttons:
            rows.append(buttons)
    return InlineKeyboardMarkup(rows) if rows else None


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
        if payload.document_bytes is not None:
            msg = await _tg_send_with_retry(
                lambda: app.bot.send_document(
                    chat_id=chat_id, message_thread_id=thread_id,
                    document=payload.document_bytes,
                    filename=payload.document_name or "report.pdf",
                    caption=_clamp(payload.text, _TELEGRAM_CAPTION_MAX),
                    parse_mode=payload.parse_mode, reply_markup=payload.markup,
                ), what=what,
            )
        elif payload.video_url:
            msg = await _tg_send_with_retry(
                lambda: app.bot.send_video(
                    chat_id=chat_id, message_thread_id=thread_id,
                    video=payload.video_url,
                    caption=_clamp(payload.text, _TELEGRAM_CAPTION_MAX),
                    parse_mode=payload.parse_mode, reply_markup=payload.markup,
                ), what=what,
            )
        elif payload.photo_bytes is not None:
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
        message_id = getattr(msg, "message_id", None)
        # The edit handle: everything ``_edit`` needs to find this exact
        # message again.  ``kind`` decides text-vs-caption edit later.
        handle: dict = {}
        if message_id is not None:
            kind = ("document" if payload.document_bytes is not None
                    else "video" if payload.video_url
                    else "photo" if payload.photo_bytes is not None else "text")
            handle = {"chat_id": chat_id, "message_id": int(message_id),
                      "kind": kind}
            if thread_id is not None:
                handle["thread_id"] = thread_id
        return DeliveryResult(ok=True, provider_ref=str(message_id or ""),
                              handle=handle)
    except Exception as e:   # transport failure — the caller logs/records
        logger.warning("Telegram %s send failed: %s", what, e)
        # error carries the MESSAGE too — sources react to specific
        # Telegram rejections ("topic deleted" → drift handling).
        return DeliveryResult(ok=False, error=f"{type(e).__name__}: {e}"[:200])


async def _edit(app, *, handle: dict, payload: Payload, what: str) -> DeliveryResult:
    """Shared leaf edit — mutate a previously sent message in place, given
    the ``handle`` that message's ``_send`` returned.

    ``kind`` picks the right Telegram verb: photo and document messages
    only accept ``edit_message_caption``; a text message only
    ``edit_message_text``.
    Telegram's "message is not modified" rejection is treated as success —
    the message already shows the desired content, which is all an edit
    promises (the pipeline's ack-swap makes the same call)."""
    if app is None:
        return DeliveryResult(ok=False, error="no_bot")
    chat_id, message_id = handle.get("chat_id"), handle.get("message_id")
    if not chat_id or not message_id:
        return DeliveryResult(ok=False, error="bad_handle")
    try:
        if handle.get("kind") in ("photo", "video", "document"):
            await _tg_send_with_retry(
                lambda: app.bot.edit_message_caption(
                    chat_id=chat_id, message_id=message_id,
                    caption=payload.text, parse_mode=payload.parse_mode,
                    reply_markup=payload.markup,
                ), what=what,
            )
        else:
            await _tg_send_with_retry(
                lambda: app.bot.edit_message_text(
                    chat_id=chat_id, message_id=message_id,
                    text=payload.text, parse_mode=payload.parse_mode,
                    reply_markup=payload.markup,
                ), what=what,
            )
        return DeliveryResult(ok=True, provider_ref=str(message_id),
                              handle=dict(handle))
    except Exception as e:
        if "not modified" in str(e).lower():
            return DeliveryResult(ok=True, provider_ref=str(message_id),
                                  handle=dict(handle))
        logger.warning("Telegram %s edit failed: %s", what, e)
        return DeliveryResult(ok=False, error=f"{type(e).__name__}: {e}"[:200])


class TelegramDmChannel:
    """Bot → USER private chat.  PERSONAL (per-user address)."""
    key = "telegram_dm"
    personal = True
    supports_edit = True
    respects_quiet_hours = True     # a DM buzzes the phone

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

    async def edit(self, recipient: Recipient, handle: dict,
                   payload: Payload) -> DeliveryResult:
        return await _edit(
            _bot_for(recipient.account_id), handle=handle, payload=payload,
            what="dm-edit",
        )


# Per-destination locks: keep multi-part sends to ONE (chat, thread)
# serialized so concurrent alerts can't interleave their messages in a
# topic.  Transport-level concern, so the locks live with the channel.
_DEST_LOCKS: dict[tuple[int, "int | None"], asyncio.Lock] = {}


def _dest_lock(chat_id: int, thread_id: "int | None") -> asyncio.Lock:
    key = (chat_id, thread_id)
    lock = _DEST_LOCKS.get(key)
    if lock is None:
        lock = asyncio.Lock()
        _DEST_LOCKS[key] = lock
    return lock


def _resolve_sender(account_id: int, sender_hint: str, override=None):
    """The bot that posts to a shared destination: an explicit override,
    else the hinted persona's Sub bot (fail-open to the primary — an
    attached-but-down Sub bot must never eat a team post), else the
    account's primary bot."""
    if override is not None:
        return override
    if sender_hint:
        from infra.bot_registry import get_sender_for_persona
        app = get_sender_for_persona(account_id, sender_hint)
        if app is not None:
            return app
    return _bot_for(account_id)


class TelegramTopicChannel:
    """Bot → GROUP topic.  SHARED (per-account/topic destination).

    ``address`` = ``"<chat_id>"`` or ``"<chat_id>:<thread_id>"``.
    ``sender_hint`` names the persona whose Sub bot should post (plan
    targets); ``sender_app`` forces an explicit bot.  The chosen sender
    is recorded in the handle — Telegram only lets the AUTHOR bot edit
    a message, so edits re-resolve the same sender."""
    key = "telegram_topic"
    personal = False
    supports_edit = True
    accepts_sender_hint = True

    def render(self, recipient: Recipient, content: NotificationContent) -> Payload:
        return render_telegram(content)

    async def send(self, recipient: Recipient, payload: Payload,
                   *, sender_app=None, sender_hint: str = "") -> DeliveryResult:
        chat_raw, _, thread_raw = str(recipient.address).partition(":")
        try:
            chat_id = int(chat_raw)
        except (TypeError, ValueError):
            return DeliveryResult(ok=False, error="bad_chat_id")
        thread_id = int(thread_raw) if thread_raw else None
        app = _resolve_sender(recipient.account_id, sender_hint, sender_app)
        async with _dest_lock(chat_id, thread_id):
            res = await _send(
                app, chat_id=chat_id, thread_id=thread_id,
                payload=payload, what="topic",
            )
        if res.ok and res.handle and sender_hint:
            res.handle["sender"] = sender_hint
        return res

    async def edit(self, recipient: Recipient, handle: dict, payload: Payload,
                   *, sender_app=None) -> DeliveryResult:
        return await _edit(
            _resolve_sender(recipient.account_id,
                            str(handle.get("sender") or ""), sender_app),
            handle=handle, payload=payload, what="topic-edit",
        )


register_channel(TelegramDmChannel())
register_channel(TelegramTopicChannel())
