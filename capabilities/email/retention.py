"""Email retention — the Email capability owns its delivery-event log.

``email.delivery_events`` -> physical table ``email_webhook_events`` (global,
platform-scoped bounce/complaint webhook log).  Declared at the legacy
14-day window so the cutover is behavior-preserving.
"""

from capabilities.lifecycle.retention.registry import (
    RetentionNeed,
    RetentionTarget,
    register_need,
    register_target,
)

register_target(RetentionTarget(
    "email.delivery_events", "Email delivery events", "platform",
    lambda db, _acct, days: db.prune_email_webhook_events(days=days),
))
register_need(RetentionNeed(
    "email", "email.delivery_events", 14, "bounce/complaint debugging",
))
