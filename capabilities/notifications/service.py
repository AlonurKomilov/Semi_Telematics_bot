"""Dispatch — the fan-out that turns one event into N deliveries.

This is the layer an event source calls (docs/architecture/
notifications.md §9).  It answers two questions per channel and nothing
else:

  1. WHO wants this?     → the preference matrix (``get_notification_subscribers``)
  2. WHEN do they want it?  → the per-rule ``cadence``

``immediate`` hands the payload straight to the channel.  Any batched
cadence buffers it in ``notification_digest_queue``, and the scheduled
flush later sends ONE grouped summary per (recipient, channel).  That
buffer is what makes Email viable: a fleet generating hundreds of alerts
a week would make per-alert email unusable, so the digest is a
prerequisite for the Email channel, not a nice-to-have.

Telegram keeps ``immediate`` — a DM that arrives an hour late is worse
than no DM.  Nothing here is wired into the live alerting pipeline yet;
it is the path Email (and every later channel) will take.
"""

from __future__ import annotations

import logging
from collections import defaultdict
from typing import Any, Callable, Iterable

from capabilities.notifications.categories import (
    BROADCAST,
    TARGETED,
    get_category,
)
from capabilities.notifications.channels import (
    SEVERITY_RANK,
    DeliveryResult,
    NotificationContent,
    Recipient,
    get_channel,
    list_channels,
)

logger = logging.getLogger(__name__)

IMMEDIATE = "immediate"

# Severity rank (SSOT: channels.SEVERITY_RANK) — the digest envelope takes
# the MAX of its items so a batch containing a critical still reads as
# critical (drives the email subject prefix, etc.).
_SEV_RANK = SEVERITY_RANK

# Batched cadences and the flush job that drains each.  Adding a cadence
# = one entry here + one scheduler job; the queue itself is generic.
# SSOT — mirrored (deliberately, to keep adapters/ off capabilities/) by
# _VALID_CADENCES in adapters/storage/notification_prefs.py; a test pins
# the two together.
DIGEST_CADENCES = ("hourly", "daily")

# Per-digest caps.  BOTH matter: a digest that exceeds the transport's
# message limit is rejected forever, and since items clear only on a
# successful send that would wedge the recipient's queue permanently
# (every later item piling up behind the one that can't go out).  The
# overflow is always COUNTED, never silently dropped.
MAX_DIGEST_LINES = 40
MAX_DIGEST_CHARS = 3500        # Telegram's hard cap is 4096 — leave headroom


async def dispatch(
    db: Any,
    account_id: int,
    content: NotificationContent,
    *,
    channels: Iterable[str] | None = None,
    recipient_filter: Callable[[int, str | None], bool] | None = None,
    correlation_key: str = "",
) -> list[DeliveryResult]:
    """Deliver one semantic event to every subscriber on every channel.

    Each channel RENDERS ``content`` its own way (escape once, at render
    time) — Telegram HTML, an email subject+body, later a short SMS — then
    sends the result.  ``content`` stays RAW: it must NOT be pre-escaped,
    since ``escape_html`` is not idempotent for ``&`` and rendering is
    where escaping belongs.  ``channels`` restricts the fan-out (default:
    every registered channel).  ``content.category`` selects subscribers.

    BROADCAST only — a targeted category (``team.invite_accepted``) must go
    through :func:`notify_user`, not here; passing one is a programming
    error and raises.  Category audience (role eligibility) is applied here
    (moved out of the storage query): a user recipient whose CURRENT role
    isn't eligible for the category is dropped, so a stale pref left after a
    role change can't keep firing.

    ``recipient_filter`` is a caller-supplied visibility gate applied to
    every user-type recipient — ``(user_id, role) -> keep``.  It's how a
    source layers on scoping the notification core must stay ignorant of:
    alerting passes a company-scope predicate here so a user restricted to
    Company A is never emailed about Company B's vehicle, WITHOUT the
    notification core ever learning what a "company" is.  Applied in the
    same pass as the audience gate (before digest enqueue, so both cadences
    are covered) and FAIL-OPEN: a predicate that raises keeps the recipient.
    Non-user (shared) recipients are never filtered — parity with the
    Telegram path, where shared group topics aren't per-user gated.

    ``correlation_key`` is the source's stable name for this logical event
    (e.g. ``alert:{history_id}``).  When set, each successful immediate
    send that returns an edit ``handle`` is recorded in the
    ``notification_deliveries`` ledger so :func:`update_delivery` can later
    mutate the delivered messages in place.  Empty (the default) keeps the
    ledger untouched — fire-and-forget events don't pay for memory they
    never use.
    """
    cat = get_category(content.category)
    if cat is not None and cat.kind == TARGETED:
        raise ValueError(
            f"dispatch() is broadcast-only; {content.category!r} is targeted "
            "— use notify_user()")
    audience = cat.audience if cat is not None else None
    if correlation_key:
        # Renderers need the routing address for action buttons.
        content.meta.setdefault("correlation_key", correlation_key)

    keys = list(channels) if channels is not None else [c.key for c in list_channels()]
    line = (content.title or content.body).split("\n", 1)[0].strip()
    results: list[DeliveryResult] = []

    for key in keys:
        channel = get_channel(key)
        if channel is None:
            logger.warning("dispatch: unknown channel %r", key)
            continue
        try:
            if getattr(channel, "intrinsic", False):
                # Intrinsic (in-app inbox): no address to opt in with, so
                # broadcast is OPT-OUT — every active user minus explicit
                # mutes.  MANDATORY categories ignore mutes entirely (a
                # notice whose toggle is locked in the UI must not be
                # silenceable by a leftover pref row).  Rows come back in
                # the same shape as the opt-in query, with cadence forced
                # immediate, and the audience + recipient_filter pass
                # below applies unchanged.
                subs = await db.get_optout_subscribers(
                    account_id, content.category, key,
                    ignore_mutes=bool(cat and cat.mandatory))
            else:
                subs = await db.get_notification_subscribers(
                    account_id, content.category, key)
        except Exception as e:                      # a broken channel query
            logger.error("dispatch: subscriber lookup failed (%s): %s", key, e)
            continue

        subs = await _filter_recipients(db, subs, audience, recipient_filter)

        for s in subs:
            cadence = s.get("cadence") or IMMEDIATE
            if cadence != IMMEDIATE and cadence not in DIGEST_CADENCES:
                # No flush job drains an unknown cadence, so enqueueing
                # would bury the notification forever.  Deliver instead —
                # a too-eager send beats a silent black hole.
                logger.warning("dispatch: unknown cadence %r (%s) — sending now",
                               cadence, key)
                cadence = IMMEDIATE
            if cadence != IMMEDIATE:
                try:
                    await db.enqueue_digest_item(
                        account_id, s["recipient_type"], s["recipient_id"],
                        key, cadence, content.category, line,
                        s.get("address", ""), severity=content.severity,
                    )
                except Exception as e:
                    logger.error("dispatch: enqueue failed (%s): %s", key, e)
                continue
            rcpt = Recipient(
                account_id=account_id, type=s["recipient_type"],
                id=str(s["recipient_id"]), address=s.get("address", ""),
            )
            try:
                payload = channel.render(rcpt, content)
                res = await channel.send(rcpt, payload)
                results.append(res)
                await _record_delivery(db, rcpt, key, content.category,
                                       correlation_key, res)
            except Exception as e:                  # one bad address ≠ dead fan-out
                logger.error("dispatch: send failed (%s): %s", key, e)
                results.append(DeliveryResult(ok=False, error="exception"))
    return results


async def _record_delivery(
    db: Any, rcpt: Recipient, channel_key: str, category: str,
    correlation_key: str, res: DeliveryResult,
) -> None:
    """Ledger write for one successful send — only when the caller named
    the event (``correlation_key``) and the channel returned an edit
    ``handle``.  Best-effort: a ledger failure must never sink a delivery
    that already happened."""
    if not correlation_key or not res.ok or not res.handle:
        return
    try:
        await db.record_notification_delivery(
            rcpt.account_id, channel=channel_key, recipient_type=rcpt.type,
            recipient_id=rcpt.id, category=category,
            correlation_key=correlation_key, handle=res.handle,
        )
    except Exception as e:
        logger.error("dispatch: delivery ledger write failed (%s): %s",
                     channel_key, e)


async def _filter_recipients(db, subs, audience, recipient_filter) -> list[dict]:
    """Drop broadcast ``user`` recipients failing the category's role
    ``audience`` and/or a caller-supplied ``recipient_filter`` — in ONE
    role-fetch pass.  ``audience`` is ``None`` for categories with no role
    gate (deliver to all) or unregistered categories (permissive);
    ``recipient_filter`` is ``None`` when the caller layers on no extra
    scoping.  Non-user (shared) recipients are never filtered.

    The predicate is FAIL-OPEN: if it raises, the recipient is kept — a
    scoping bug must not silently swallow notifications."""
    if audience is None and recipient_filter is None:
        return subs
    ids = [int(s["recipient_id"]) for s in subs
           if s["recipient_type"] == "user"
           and str(s["recipient_id"]).lstrip("-").isdigit()]
    roles = await db.get_roles_for_users(ids) if ids else {}

    def keep(s: dict) -> bool:
        if s["recipient_type"] != "user":
            return True                        # shared topics have no role
        rid = str(s["recipient_id"])
        uid = int(rid) if rid.lstrip("-").isdigit() else None
        role = roles.get(uid) if uid is not None else None
        if audience is not None and not (role is not None and audience(role)):
            return False
        if recipient_filter is not None and uid is not None:
            try:
                if not recipient_filter(uid, role):
                    return False
            except Exception as e:             # a scoping bug must not mute
                logger.error(
                    "dispatch: recipient_filter raised for %s: %s", uid, e)
        return True

    return [s for s in subs if keep(s)]


async def notify_user(
    db: Any,
    account_id: int,
    user_id: int,
    content: NotificationContent,
    *,
    channels: Iterable[str] | None = None,
    correlation_key: str = "",
) -> list[DeliveryResult]:
    """Deliver a TARGETED notification to ONE user (the actor / affected
    person) — e.g. ``team.invite_accepted``.

    OPT-OUT, unlike broadcast: it fires on every channel the user has
    connected UNLESS they explicitly muted this category (an ``enabled=0``
    pref row).  A ``mandatory`` category (security/billing) ignores the
    mute.  This is the only honest default for account-activity — nobody
    pre-creates a pref row for "your invite was accepted", so an opt-IN
    model would silently never fire.

    Rejects broadcast categories (use :func:`dispatch`).  Immediate only —
    a targeted notice is a direct reply; batching it makes no sense.
    """
    cat = get_category(content.category)
    if cat is not None and cat.kind == BROADCAST:
        raise ValueError(
            f"notify_user() is targeted-only; {content.category!r} is "
            "broadcast — use dispatch()")
    mandatory = bool(cat and cat.mandatory)
    if correlation_key:
        content.meta.setdefault("correlation_key", correlation_key)
    keys = list(channels) if channels is not None else [c.key for c in list_channels()]
    results: list[DeliveryResult] = []

    for key in keys:
        channel = get_channel(key)
        if channel is None or not getattr(channel, "personal", False):
            continue                           # targeted = personal channels only
        if getattr(channel, "intrinsic", False):
            # Intrinsic (in-app inbox): nothing to connect or verify —
            # always deliverable.  Mutes / mandatory still apply below.
            conn = {"address": ""}
        else:
            conn = await db.get_notification_channel(
                account_id, "user", user_id, key)
            if not conn or not conn.get("verified") or not conn.get("enabled_master"):
                continue                       # nothing connected to deliver to
        if not mandatory:
            prefs = await db.get_pref_categories(account_id, "user", user_id, key)
            # Opt-out with the SPECIFIC row winning over the '*' blanket, in
            # both directions — so "I muted everything but re-enabled X" and
            # "I allow everything but muted X" both behave as the user set
            # them, no "turned it back on but still muted" surprise.
            specific = prefs.get(content.category)      # None | True | False
            if specific is False:
                continue                                # explicit mute of X
            if specific is None and prefs.get("*") is False:
                continue                                # blanket mute, no override
        rcpt = Recipient(account_id=account_id, type="user", id=str(user_id),
                         address=conn.get("address", ""))
        try:
            res = await channel.send(rcpt, channel.render(rcpt, content))
            results.append(res)
            await _record_delivery(db, rcpt, key, content.category,
                                   correlation_key, res)
        except Exception as e:
            logger.error("notify_user: send failed (%s): %s", key, e)
            results.append(DeliveryResult(ok=False, error="exception"))
    return results


async def update_delivery(
    db: Any,
    account_id: int,
    correlation_key: str,
    content: NotificationContent,
    *,
    channels: Iterable[str] | None = None,
    clear: bool = False,
) -> list[DeliveryResult]:
    """Edit every recorded delivery of one logical event IN PLACE.

    This is the generic "the thing I told you about has changed" verb: the
    source renders the NEW content and every message the ledger remembers
    (``dispatch``/``notify_user`` with the same ``correlation_key``) is
    edited via its channel's ``edit()``.  Channels that can't edit
    (``supports_edit`` unset — email) are skipped silently: an immutable
    transport's message stays as sent, and the source decides whether that
    event deserves a fresh notice instead.

    ``clear=True`` drops the ledger rows afterwards — the event's FINAL
    edit ("🟢 resolved"), after which nothing should update it again.
    Rows are kept on partial failure so a later retry can finish the job.
    """
    try:
        rows = await db.get_notification_deliveries(account_id, correlation_key)
    except Exception as e:
        logger.error("update_delivery: ledger read failed (%s): %s",
                     correlation_key, e)
        return []
    wanted = set(channels) if channels is not None else None
    results: list[DeliveryResult] = []
    all_ok = True
    for row in rows:
        key = row["channel"]
        if wanted is not None and key not in wanted:
            continue
        channel = get_channel(key)
        if channel is None or not getattr(channel, "supports_edit", False):
            continue
        rcpt = Recipient(
            account_id=account_id, type=row["recipient_type"],
            id=str(row["recipient_id"]),
        )
        try:
            payload = channel.render(rcpt, content)
            res = await channel.edit(rcpt, row.get("handle") or {}, payload)
        except Exception as e:
            logger.error("update_delivery: edit failed (%s): %s", key, e)
            res = DeliveryResult(ok=False, error="exception")
        results.append(res)
        if not res.ok:
            all_ok = False
    if clear and all_ok:
        try:
            await db.clear_notification_deliveries(account_id, correlation_key)
        except Exception as e:
            logger.error("update_delivery: ledger clear failed (%s): %s",
                         correlation_key, e)
    return results


def build_digest_content(items: list[dict], cadence: str) -> NotificationContent:
    """Group a recipient's buffered items into ONE semantic digest.

    Returns RAW content (title + bullet body) that each channel renders
    its own way — so a digest becomes escaped Telegram HTML or a multipart
    email through the SAME ``render`` path as a single event, no second
    renderer.  The body is bounded by BOTH a line cap and a conservative
    char cap: a digest over the transport's message limit would be
    rejected on every run, and since items clear only on success that
    would wedge the recipient's queue.  The overflow is COUNTED, never
    silently dropped.  (The char cap is Telegram-shaped for now; a
    per-channel limit is the SMS-day refinement.)
    """
    label = {"hourly": "Hourly", "daily": "Daily"}.get(cadence, cadence.title())
    n = len(items)
    by_cat: dict[str, int] = defaultdict(int)
    sev = "info"
    for it in items:
        by_cat[it["category"]] += 1
        if _SEV_RANK.get(it.get("severity", "info"), 0) > _SEV_RANK.get(sev, 0):
            sev = it.get("severity", "info")
    # Display the bare topic (strip the source namespace: alert.faults → faults).
    counts = ", ".join(
        f"{c} {t.split('.', 1)[-1].replace('_', ' ')}"
        for t, c in sorted(by_cat.items())
    )
    title = f"{label} summary — {n} notification{'s' if n != 1 else ''}"

    body_lines: list[str] = []
    if counts:
        body_lines.append(counts)
        body_lines.append("")
    used = len(title) + sum(len(x) + 1 for x in body_lines)
    shown = 0
    for it in items[:MAX_DIGEST_LINES]:
        line = f"• {it['summary']}"
        if used + len(line) + 1 > MAX_DIGEST_CHARS - 40:   # reserve the tail
            break
        body_lines.append(line)
        used += len(line) + 1
        shown += 1
    if n > shown:
        body_lines.append(f"…and {n - shown} more")
    return NotificationContent(
        title=title, body="\n".join(body_lines),
        category="digest", severity=sev,
    )


async def flush_digests(db: Any, cadence: str, *, limit: int = 2000) -> int:
    """Send every buffered item for ``cadence`` as one message per
    (recipient, channel).  Returns the number of digests sent.

    Items are cleared ONLY after a successful send, so a transport
    outage re-sends on the next run instead of silently losing alerts.
    """
    try:
        items = await db.fetch_due_digest_items(cadence, limit)
    except Exception as e:
        logger.error("flush_digests: fetch failed (%s): %s", cadence, e)
        return 0
    if not items:
        return 0

    groups: dict[tuple, list[dict]] = defaultdict(list)
    for it in items:
        groups[(it["account_id"], it["recipient_type"],
                it["recipient_id"], it["channel"])].append(it)

    sent = 0
    for (account_id, rtype, rid, chan_key), group in groups.items():
        ids = [g["id"] for g in group]
        channel = get_channel(chan_key)
        if channel is None:
            logger.warning("flush_digests: unknown channel %r — items kept", chan_key)
            continue

        # Re-check consent at SEND time, not enqueue time: a daily item can
        # sit here for 24h, and a recipient who switched the channel off (or
        # un-verified it) in the meantime must not get the backlog.  Their
        # buffered items are dropped, not kept — consent is gone, so there
        # is no later run that may legitimately send them.
        conn = await db.get_notification_channel(account_id, rtype, rid, chan_key)
        if not conn or not conn.get("enabled_master") or not conn.get("verified"):
            logger.info("flush_digests: %s no longer enabled for %s/%s — %d dropped",
                        chan_key, rtype, rid, len(ids))
            await db.clear_digest_items(ids, account_id=account_id)
            continue

        rcpt = Recipient(
            account_id=account_id, type=rtype, id=str(rid),
            # CURRENT address — a correction made after enqueue wins over
            # the copy captured on the buffered rows.
            address=conn.get("address")
            or next((g["address"] for g in group if g["address"]), ""),
        )
        try:
            content = build_digest_content(group, cadence)
            res = await channel.send(rcpt, channel.render(rcpt, content))
        except Exception as e:
            logger.error("flush_digests: send failed (%s): %s", chan_key, e)
            continue
        if not res.ok:
            logger.warning("flush_digests: %s not delivered (%s) — items kept",
                           chan_key, res.error)
            continue
        await db.clear_digest_items(ids, account_id=account_id)
        sent += 1
    return sent
