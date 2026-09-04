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

    ``category`` is what the notification is ABOUT, namespaced by source
    (``alert.faults`` / ``team.invite_accepted``) — it selects subscribers
    and the audience gate (see ``categories.py``).  ``alert_type`` is a
    DEPRECATED alias kept during the alert-type→category sweep: a caller
    that still passes ``alert_type=`` gets mapped to ``category``, and
    ``.alert_type`` always mirrors ``.category`` on read.

    ``severity`` is a plain string ("critical"/"warning"/"info").  ``meta``
    carries optional channel hints (e.g. a Telegram keyboard spec) without
    polluting the semantic core.

    ``actions`` are SEMANTIC buttons — ``[{"id": "ack", "label": "✅
    Acknowledge"}]``.  Channels that can render interaction (Telegram
    inline keyboards) show them; channels that can't (email) ignore them.
    A press is routed back to the SOURCE's registered handler
    (``actions.py``: handler key = ``{source}.{action_id}``, source taken
    from the correlation_key namespace) — the spine renders and routes,
    the source decides what the press means.  Actions require a
    ``correlation_key`` at dispatch time (that's the routing address);
    without one they are dropped with a warning.
    """
    title: str
    body: str = ""
    category: str = ""
    severity: str = "info"
    url: str = ""
    photo_bytes: bytes | None = None
    # Optional media/attachment. Channels that can carry them do
    # (Telegram video/document, one day email attachments); channels
    # that can't ignore them.  ``video_url`` is a provider-hosted URL
    # (Telegram fetches it server-side).
    video_url: str = ""
    document_bytes: bytes | None = None
    document_name: str = ""
    meta: dict = field(default_factory=dict)
    actions: list = field(default_factory=list)
    alert_type: str = ""            # deprecated alias for `category`

    def __post_init__(self) -> None:
        # Accept the old keyword; keep the two mirrored so `.alert_type`
        # reads back == `.category` for any remaining consumer.
        if self.alert_type and not self.category:
            self.category = self.alert_type
        self.alert_type = self.category


def parse_action(meta: dict) -> tuple[str, str] | None:
    """``meta["action"]`` → ``(label, relative_url)``, or None if unusable.

    The CALL-TO-ACTION a source declares once — the in-app inbox row draws
    it as an inline button, the email channel as a real one.  This is the
    single definition of what makes one valid, because it is a security
    rule and a rule enforced in two places drifts: the url must be a
    same-origin RELATIVE path, so a stored notice can never become an
    off-site redirect, and never a ``javascript:`` href.

    ``//host`` is rejected (scheme-relative) and so is any backslash —
    browsers normalise a leading ``/\\`` to ``//`` in real navigations,
    which would defeat the relative-only rule.  The label is capped so a
    long one can't blow out a row or a subject line.
    """
    raw = meta.get("action") if isinstance(meta, dict) else None
    if not isinstance(raw, dict):
        return None
    label = str(raw.get("label", "") or "").strip()
    url = str(raw.get("url", "") or "")
    if (not label or not url.startswith("/")
            or url.startswith("//") or "\\" in url):
        return None
    return label[:40], url


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
    video_url: str = ""
    document_bytes: bytes | None = None
    document_name: str = ""
    markup: Any = None          # channel-specific reply markup (opaque here)
    extra: dict = field(default_factory=dict)


# Failures a retry can never fix.
#
# The flush loop kept EVERY failed batch for the next hour, which is right
# for a network blip and wrong for an address that no longer exists: five
# users' Telegram answered "Chat not found" 1,493 times between 2026-08-14
# and 2026-09-04, once an hour, while their notifications sat undelivered
# until the 14-day retention sweep quietly deleted them.  Nobody was ever
# told — not the recipient, not the operator.
#
# Matched against ``DeliveryResult.error``, which channels format as
# "ExceptionName: message".  Substrings rather than exception types: the
# service layer must not import a transport's dialect, which is the same
# one-way boundary that keeps the notifications core from importing
# alerting.
_PERMANENT_MARKERS = (
    "chat not found",          # never started the bot, or a stale chat id
    "bot was blocked",         # the user blocked it
    "user is deactivated",     # the Telegram account is gone
    "chat_id is empty",
    "bad_handle",              # our own: the stored address is unusable
    "peer_id_invalid",
    "recipient rejected",      # SMTP 550-class: the mailbox does not exist
    "mailbox unavailable",
    "no such user",
    # Our own: the stored address is not an address.  Retrying cannot
    # make it parse, and the owner is the only one who can fix it.
    "bad_email",
)


# The human name of a transport, for copy the RECIPIENT reads.
#
# The dashboard keeps its own map (features/alerts/_shared/channels.tsx)
# because it labels controls; this one exists because the server now
# writes a sentence a person reads ("Telegram is disconnected") and
# "telegram_dm" is not that sentence.  Unknown keys fall back to the
# de-underscored key rather than raising — a new channel gets a plain
# name, never a crash in a notice about something already broken.
_CHANNEL_LABELS = {
    "telegram_dm": "Telegram",
    "telegram_topic": "the Telegram group",
    "email": "Email",
    "web_push": "Push notifications",
    "in_app": "The bell",
}


def channel_label(key: str) -> str:
    """A transport's name as a person would say it."""
    return _CHANNEL_LABELS.get(key, (key or "").replace("_", " ") or "That channel")


def is_permanent_failure(error: str | None) -> bool:
    """True when retrying this send can only ever fail again.

    Deliberately CONSERVATIVE: anything unrecognised is transient, so an
    unfamiliar error keeps its messages queued rather than discarding
    them.  Dropping a notification that would have arrived is the worse
    mistake of the two, and the retention sweep bounds the queue anyway.
    """
    if not error:
        return False
    low = error.lower()
    return any(m in low for m in _PERMANENT_MARKERS)


@dataclass
class DeliveryResult:
    """Outcome of one ``send``/``edit``.

    ``handle`` is the channel-specific address of the SENT message —
    everything the channel itself needs to find it again for a later
    ``edit()`` (Telegram: ``{"chat_id", "message_id", "thread_id?",
    "kind"}``).  Opaque to everyone but the emitting channel; empty when
    the channel can't edit (email) or the send failed.  The service
    layer persists it in the ``notification_deliveries`` ledger keyed by
    the caller's ``correlation_key`` — that's how a source updates a
    delivered message later ("reminder 2/4", "✅ acked") without ever
    speaking the transport's dialect (docs/architecture/
    alert-dm-migration.md).
    """
    ok: bool
    error: str = ""
    provider_ref: str = ""      # e.g. Telegram message_id
    handle: dict = field(default_factory=dict)


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

    A channel may additionally declare ``intrinsic = True`` (read via
    ``getattr(..., "intrinsic", False)``, so existing channels need no
    change): it has NO external address to connect or verify — it is
    always available for every user (the in-app inbox).  The service
    layer skips the connection check for intrinsic channels and resolves
    broadcast recipients opt-OUT (all eligible users minus explicit
    mutes) instead of opt-in, because a passive record nobody pre-enables
    would otherwise stay empty forever.

    A channel may also declare ``supports_edit = True`` (same getattr
    pattern) and provide::

        async def edit(self, recipient, handle, payload) -> DeliveryResult

    to mutate an ALREADY-DELIVERED message in place, given the ``handle``
    its own earlier ``send`` returned.  ``update_delivery()`` in the
    service layer is the only caller; channels without the flag are
    silently skipped there (an email can't be edited — the source decides
    whether to send a fresh notice instead).

    Finally, ``respects_quiet_hours = True`` (same getattr pattern) marks
    a channel as one that DISTURBS — buzzes a phone — so the dispatch
    layer defers its non-critical sends while the recipient's registered
    quiet-hours rule says "quiet" (``quiet_hours.py``).  The inbox is
    silent by nature and email rides its own cadence system, so neither
    sets it.
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


def channel_ready(ch: "Channel", conn: dict | None,
                  device_count: int = 0) -> bool:
    """Can this channel actually deliver to this person RIGHT NOW?

    One definition, because the answer is asked in three places that must
    not drift: the preferences matrix greys a column with it, the send
    path refuses with it, and the trigger list states delivery with it.
    A surface that says "this reaches you by email" while the send path
    would skip email is not a cosmetic disagreement — it is the product
    promising something it will not do.

    Each channel's rule is its own: the in-app bell is intrinsic and
    always ready, web push needs a subscribed device, everything else
    needs a verified address.  All of them need their master switch.
    """
    if getattr(ch, "intrinsic", False):
        return True                     # the bell: nothing to connect
    master = bool(conn.get("enabled_master")) if conn else True
    if ch.key == "web_push":
        return device_count > 0 and master
    return bool(conn and conn.get("verified")) and master


def get_channel(key: str) -> Channel | None:
    return _CHANNELS.get(key)


def list_channels() -> list[Channel]:
    """Every registered channel — NOTE this includes the intrinsic
    ``in_app`` inbox, so a ``dispatch()``/``notify_user()`` call with
    ``channels=None`` writes inbox rows too.  A source that must NOT
    surface in the bell (e.g. alerts, which have their own feed) passes an
    explicit ``channels=`` list."""
    return list(_CHANNELS.values())


def personal_channels() -> list[Channel]:
    """Channels addressed to a single user (my DM / my email / …)."""
    return [c for c in _CHANNELS.values() if c.personal]


def shared_channels() -> list[Channel]:
    """Channels addressed to a shared destination (team group / distro)."""
    return [c for c in _CHANNELS.values() if not c.personal]
