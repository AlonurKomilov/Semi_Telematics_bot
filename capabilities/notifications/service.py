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
from typing import Any, Iterable

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
) -> list[DeliveryResult]:
    """Deliver one semantic event to every subscriber on every channel.

    Each channel RENDERS ``content`` its own way (escape once, at render
    time) — Telegram HTML, an email subject+body, later a short SMS — then
    sends the result.  ``content`` stays RAW: it must NOT be pre-escaped,
    since ``escape_html`` is not idempotent for ``&`` and rendering is
    where escaping belongs.  ``channels`` restricts the fan-out (default:
    every registered channel).  ``content.alert_type`` selects subscribers.
    """
    keys = list(channels) if channels is not None else [c.key for c in list_channels()]
    line = (content.title or content.body).split("\n", 1)[0].strip()
    results: list[DeliveryResult] = []

    for key in keys:
        channel = get_channel(key)
        if channel is None:
            logger.warning("dispatch: unknown channel %r", key)
            continue
        try:
            subs = await db.get_notification_subscribers(
                account_id, content.alert_type, key)
        except Exception as e:                      # a broken channel query
            logger.error("dispatch: subscriber lookup failed (%s): %s", key, e)
            continue

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
                        key, cadence, content.alert_type, line,
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
                results.append(await channel.send(rcpt, payload))
            except Exception as e:                  # one bad address ≠ dead fan-out
                logger.error("dispatch: send failed (%s): %s", key, e)
                results.append(DeliveryResult(ok=False, error="exception"))
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
    by_type: dict[str, int] = defaultdict(int)
    sev = "info"
    for it in items:
        by_type[it["alert_type"]] += 1
        if _SEV_RANK.get(it.get("severity", "info"), 0) > _SEV_RANK.get(sev, 0):
            sev = it.get("severity", "info")
    counts = ", ".join(
        f"{c} {t.replace('_', ' ')}" for t, c in sorted(by_type.items())
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
        alert_type="digest", severity=sev,
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
