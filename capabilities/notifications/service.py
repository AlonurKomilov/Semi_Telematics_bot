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
    DeliveryResult,
    Payload,
    Recipient,
    get_channel,
    list_channels,
)

logger = logging.getLogger(__name__)

IMMEDIATE = "immediate"

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
    alert_type: str,
    payload: Payload,
    *,
    channels: Iterable[str] | None = None,
    digest_summary: str | None = None,
) -> list[DeliveryResult]:
    """Deliver one event to every subscriber on every channel.

    ``channels`` restricts the fan-out (default: every registered
    channel).  ``digest_summary`` is the one-line form buffered for
    batched recipients — falls back to the payload's first line.

    TEXT CONTRACT: ``payload.text`` and ``digest_summary`` must ALREADY be
    transport-safe — i.e. run through
    ``capabilities.formatting.helpers.escape_html`` by whoever rendered
    them, the same contract ``Payload`` states.  The digest cannot escape
    them for you: ``escape_html`` is not idempotent for ``&``, so
    re-escaping already-rendered text would turn every "Truck A & B" into
    "A &amp;amp; B".  Escape once, at render time, like the rest of the
    alerting code does.
    """
    keys = list(channels) if channels is not None else [c.key for c in list_channels()]
    line = (digest_summary or payload.text.split("\n", 1)[0]).strip()
    results: list[DeliveryResult] = []

    for key in keys:
        channel = get_channel(key)
        if channel is None:
            logger.warning("dispatch: unknown channel %r", key)
            continue
        try:
            subs = await db.get_notification_subscribers(account_id, alert_type, key)
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
                        key, cadence, alert_type, line, s.get("address", ""),
                    )
                except Exception as e:
                    logger.error("dispatch: enqueue failed (%s): %s", key, e)
                continue
            rcpt = Recipient(
                account_id=account_id, type=s["recipient_type"],
                id=str(s["recipient_id"]), address=s.get("address", ""),
            )
            try:
                results.append(await channel.send(rcpt, payload))
            except Exception as e:                  # one bad address ≠ dead fan-out
                logger.error("dispatch: send failed (%s): %s", key, e)
                results.append(DeliveryResult(ok=False, error="exception"))
    return results


def render_digest(items: list[dict], cadence: str) -> str:
    """One grouped summary from a recipient's buffered items."""
    label = {"hourly": "Hourly", "daily": "Daily"}.get(cadence, cadence.title())
    n = len(items)
    by_type: dict[str, int] = defaultdict(int)
    for it in items:
        by_type[it["alert_type"]] += 1
    counts = ", ".join(
        f"{c} {t.replace('_', ' ')}" for t, c in sorted(by_type.items())
    )
    lines = [f"<b>{label} summary — {n} notification{'s' if n != 1 else ''}</b>"]
    if counts:
        lines.append(counts)
    lines.append("")

    # Fill against BOTH caps.  Whichever runs out first stops the body;
    # the remainder is reported as a count so nothing vanishes silently.
    used = sum(len(x) + 1 for x in lines)
    shown = 0
    for it in items[:MAX_DIGEST_LINES]:
        line = f"• {it['summary']}"
        if used + len(line) + 1 > MAX_DIGEST_CHARS - 40:   # reserve the tail
            break
        lines.append(line)
        used += len(line) + 1
        shown += 1
    if n > shown:
        lines.append(f"…and {n - shown} more")
    return "\n".join(lines)


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
            res = await channel.send(rcpt, Payload(text=render_digest(group, cadence)))
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
