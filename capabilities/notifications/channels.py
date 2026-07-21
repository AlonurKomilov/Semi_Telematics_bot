"""Notification channels — the delivery-transport contract + registry.

This is the CROSS-CUTTING notification layer (docs/architecture/
notifications.md).  It sits BELOW the event sources: alerts (and, later,
other events) resolve recipients + preferences and hand each delivery to
a ``Channel`` here.  This module imports only ``infra`` + the transport
libs — never ``capabilities.alerting`` — so alerting can call INTO it
without a cycle.

Two recipient scopes, one axis (docs §4):
  • ``personal`` channel  → a per-USER address   (my Telegram DM, my email)
  • shared channel        → a per-ACCOUNT/topic destination (team group)

A channel is a pluggable adapter behind one ``send`` interface; adding
SMS later = one new registered channel, zero changes to the event layer.
(Registry pattern, idiomatic here — tools, ImportTargets, artifacts,
undo recipes all use it.)

Phase 1a scope: the contract + registry + the two Telegram transport
channels.  ``Payload`` is transport-level (a message the caller already
rendered).  A SEMANTIC payload + per-channel ``render()`` lands with the
first channel that renders differently (Email, Phase 4).  The live
``capabilities/alerting`` pipeline is intentionally NOT rewired yet —
that is a separate, tested step once the preference matrix exists.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Awaitable, Protocol, runtime_checkable

# recipient scope — 'user' is personal; 'account'/'topic' are shared.
RecipientType = str  # 'user' | 'account' | 'topic'

# Canonical severity vocabulary, ORDERED low→high.  One SSOT so the
# per-transport severity maps (Telegram icon, email subject prefix,
# digest rank) can't drift; a test asserts each keys only these.
SEVERITIES: tuple[str, ...] = ("info", "warning", "critical")
SEVERITY_RANK: dict[str, int] = {s: i for i, s in enumerate(SEVERITIES)}


@dataclass
class Recipient:
    """Who a notification is delivered to.

    ``account_id`` scopes every recipient (per-account bots, tenant
    data).  ``address`` is the channel-specific destination:
    ``telegram_dm`` → the user's telegram_id; ``telegram_topic`` →
    ``"<chat_id>"`` or ``"<chat_id>:<thread_id>"``; email → the address;
    sms → E.164.  ``id`` is the stable recipient key (user_id / topic id).
    """
    account_id: int
    type: RecipientType
    id: str
    address: str = ""
    locale: str = "en"


@dataclass
class NotificationContent:
    """The SEMANTIC message — what happened, not how any one channel shows
    it.  This is the seam every channel binds to (docs §5): ``render()``
    turns it into a channel-specific :class:`Payload`.

    ``title`` and ``body`` are RAW, UNESCAPED plain text.  Escaping /
    markup happens exactly once, inside each channel's ``render`` — so
    the same content becomes escaped Telegram HTML, a multipart email
    (subject from ``title``), or one day a ≤160-char SMS, with no lossy
    round-trip through another channel's already-rendered string.

    ``severity`` is a plain string ("critical"/"warning"/"info") — the
    alerting layer's enum maps to it at the call site, keeping alerting's
    types on alerting's side of the seam.  ``meta`` carries optional
    channel hints (e.g. a Telegram keyboard spec) without polluting the
    semantic core.
    """
    title: str
    body: str = ""
    alert_type: str = ""
    severity: str = "info"
    url: str = ""
    photo_bytes: bytes | None = None
    meta: dict = field(default_factory=dict)


@dataclass
class Payload:
    """A transport-level message — the OUTPUT of a channel's ``render``.
    ``parse_mode`` / ``photo_bytes`` / ``markup`` / ``subject`` are
    honored by channels that support them and ignored by those that
    don't (email uses ``subject`` + ``extra['html']``; telegram ignores
    ``subject``; email/sms ignore ``markup``)."""
    text: str
    parse_mode: str = "HTML"
    subject: str = ""           # email subject line; ignored by chat channels
    photo_bytes: bytes | None = None
    markup: Any = None          # channel-specific reply markup (opaque here)
    extra: dict = field(default_factory=dict)


@dataclass
class DeliveryResult:
    ok: bool
    error: str = ""
    provider_ref: str = ""      # e.g. Telegram message_id


@runtime_checkable
class Channel(Protocol):
    """A delivery transport.  ``key`` is the stable id used by prefs +
    the registry; ``personal`` splits per-user channels from shared
    destinations.

    Two responsibilities, kept separate so the semantic layer never
    speaks a transport's dialect:
      • ``render`` turns :class:`NotificationContent` into this channel's
        :class:`Payload` (escape, format, subject, short-form …).
      • ``send`` pushes a rendered ``Payload`` to a recipient.
    """
    key: str
    personal: bool

    def render(self, recipient: Recipient, content: NotificationContent) -> Payload:
        ...

    def send(self, recipient: Recipient, payload: Payload) -> Awaitable[DeliveryResult]:
        ...


# ── Registry ─────────────────────────────────────────────────────────

_CHANNELS: dict[str, Channel] = {}


def register_channel(channel: Channel) -> Channel:
    """Register a channel by its ``key`` (last registration wins, so a
    deployment can override an adapter)."""
    _CHANNELS[channel.key] = channel
    return channel


def get_channel(key: str) -> Channel | None:
    return _CHANNELS.get(key)


def list_channels() -> list[Channel]:
    return list(_CHANNELS.values())


def personal_channels() -> list[Channel]:
    """Channels addressed to a single user (my DM / my email / …)."""
    return [c for c in _CHANNELS.values() if c.personal]


def shared_channels() -> list[Channel]:
    """Channels addressed to a shared destination (team group / distro)."""
    return [c for c in _CHANNELS.values() if not c.personal]
