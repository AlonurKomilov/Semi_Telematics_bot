"""Notifications retention — the delivery layer owns its digest buffer.

``notifications.digest_queue`` -> physical table
``notification_digest_queue`` (platform-scoped).  A successful flush
clears its own items, so this sweep exists for the residue a flush can
never reach: items whose channel was unregistered, or whose cadence no
scheduler job drains.  Without it those rows accumulate silently and,
because a wedged group blocks nothing else, stay invisible.

14 days is deliberately longer than the longest cadence (daily) —
long enough that a multi-day transport outage still re-sends rather than
losing alerts, short enough that a stuck buffer can't grow unbounded.
"""

from capabilities.data_lifecycle.retention.registry import (
    RetentionNeed,
    RetentionTarget,
    register_need,
    register_target,
)

register_target(RetentionTarget(
    "notifications.digest_queue", "Notification digest buffer", "platform",
    lambda db, _acct, days: db.prune_notification_digest_queue(days=days),
))
register_need(RetentionNeed(
    "notifications", "notifications.digest_queue", 14,
    "undeliverable/undrained digest residue; outlives the daily cadence "
    "so an outage re-sends instead of dropping",
))
