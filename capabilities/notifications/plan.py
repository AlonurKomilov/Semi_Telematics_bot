"""Delivery plans — one hand-off from a source to every destination.

The last seam of the alert-delivery migration
(capabilities/alerting/docs/alert-dm-migration.md): a source (alerting today,
any domain tomorrow) builds a PLAN — pure data, zero transport — and
the spine executes all of it:

  • ``shared`` targets: resolved team destinations (a Telegram group
    topic today, a Slack channel someday) — sent through the shared
    channel with the source's sender hint and per-target prefix,
    recorded in the delivery ledger so later ``update_delivery`` edits
    (ack stamps, reminders) reach the team copies too.
  • ``personal`` fanouts: delegated to :func:`service.dispatch` — the
    matrix decides recipients, quiet hours defer, the ledger records.

The source keeps every DECISION (who, where, what text, which
severity); the plan carries only the outcome of those decisions.  The
executor returns per-target results so the source can keep its own
state (ack rows, drift/failure policy) without ever touching the
transport.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field

from capabilities.notifications.channels import (
    DeliveryResult,
    Recipient,
    get_channel,
)
from capabilities.notifications.service import dispatch

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class Target:
    """One resolved SHARED destination.

    ``address`` is channel-specific ("<chat_id>" or
    "<chat_id>:<thread_id>" for Telegram).  ``id`` is the stable ledger
    key for this destination (persona slug, topic key); defaults to the
    address.  ``sender_hint`` names the persona whose Sub bot should
    send ("" = the account's primary bot).  ``prefix_html`` is a
    PRE-RENDERED HTML fragment (on-shift @-mentions) applied AFTER the
    channel's escaping render — the one place raw HTML legitimately
    crosses, because a mention link must stay live.
    ``content_index`` picks which of the plan's contents this target
    shows (group text differs from DM text).
    """
    channel: str
    address: str
    id: str = ""
    sender_hint: str = ""
    prefix_html: str = ""
    content_index: int = 0
    # Thread the send as a REPLY to an earlier message in the same
    # destination (resolve receipts under their original alert).  The
    # channel falls back to a plain send when the target message is
    # gone — a receipt must land even if the alert was deleted.
    reply_to_message_id: "int | None" = None


@dataclass(frozen=True)
class PersonalSend:
    """One personal fanout: which channels, which content, and the
    source's opaque recipient predicate (company scope, truck scope,
    AI-note cohort)."""
    channels: tuple = ("telegram_dm",)
    content_index: int = 0
    recipient_filter: object = None     # (user_id, role) -> bool, or None


@dataclass
class DeliveryPlan:
    correlation_key: str = ""
    contents: list = field(default_factory=list)   # list[NotificationContent]
    shared: list = field(default_factory=list)     # list[Target]
    personal: list = field(default_factory=list)   # list[PersonalSend]


@dataclass
class PlanResult:
    """Per-target outcomes the source reacts to (ack rows from handles,
    drift/failure policy) — results, never transport."""
    shared: list = field(default_factory=list)     # list[(Target, DeliveryResult)]
    personal: list = field(default_factory=list)   # flat list[DeliveryResult]

    @property
    def any_shared_ok(self) -> bool:
        return any(r.ok for _, r in self.shared)


async def deliver(db, account_id: int, plan: DeliveryPlan) -> PlanResult:
    """Execute one plan: shared targets first (team surfaces are the
    primary destination), then the personal fanouts.

    Failure isolation per destination: one dead group never blocks the
    next target or the personal fanout.  Ledger rows are written only
    for successful sends WITH a handle AND a correlation key — same
    rule as the personal path.
    """
    result = PlanResult()
    for target in plan.shared:
        res = await _send_shared(db, account_id, plan, target)
        result.shared.append((target, res))
    for ps in plan.personal:
        try:
            content = plan.contents[ps.content_index]
            result.personal.extend(await dispatch(
                db, account_id, content,
                channels=ps.channels,
                recipient_filter=ps.recipient_filter,
                correlation_key=plan.correlation_key,
            ))
        except Exception as e:
            logger.error("plan personal fanout failed acct=%d: %s",
                         account_id, e, exc_info=True)
            result.personal.append(DeliveryResult(ok=False, error="exception"))
    return result


async def _send_shared(db, account_id: int, plan: DeliveryPlan,
                       target: Target) -> DeliveryResult:
    channel = get_channel(target.channel)
    if channel is None:
        logger.warning("plan: unknown shared channel %r", target.channel)
        return DeliveryResult(ok=False, error="unknown_channel")
    try:
        content = plan.contents[target.content_index]
    except IndexError:
        return DeliveryResult(ok=False, error="bad_content_index")
    if plan.correlation_key:
        content.meta.setdefault("correlation_key", plan.correlation_key)
    rcpt = Recipient(
        account_id=account_id, type="topic",
        id=str(target.id or target.address), address=target.address,
    )
    try:
        payload = channel.render(rcpt, content)
        if target.reply_to_message_id:
            payload.extra["reply_to_message_id"] = target.reply_to_message_id
        if target.prefix_html:
            # Applied post-render: the prefix is already-safe HTML the
            # source built (mention links), never user text.
            payload.text = f"{target.prefix_html}\n{payload.text}"
        # Channels that route by persona declare accepts_sender_hint
        # (getattr pattern, like intrinsic/supports_edit).
        if getattr(channel, "accepts_sender_hint", False):
            res = await channel.send(rcpt, payload,
                                     sender_hint=target.sender_hint)
        else:
            res = await channel.send(rcpt, payload)
    except Exception as e:
        logger.warning("plan shared send failed (%s %s): %s",
                       target.channel, target.address, e)
        return DeliveryResult(ok=False, error=type(e).__name__)

    if plan.correlation_key and res.ok and res.handle:
        try:
            await db.record_notification_delivery(
                account_id, channel=target.channel, recipient_type="topic",
                recipient_id=rcpt.id, category=content.category,
                correlation_key=plan.correlation_key, handle=res.handle,
            )
        except Exception as e:
            logger.error("plan: ledger write failed (%s): %s",
                         target.channel, e)
    return res
