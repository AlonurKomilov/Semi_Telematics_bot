"""The category personal triggers deliver under.

TARGETED, not broadcast, and that is the whole difference between a
trigger and every other alert type.  ``alert.faults`` goes to whoever
holds the right role — the notification service resolves an audience.  A
trigger has no audience to resolve: it belongs to the one person who
wrote it, and the evaluator already knows who that is.

One category for every metric rather than ``alert.trigger.fuel_pct`` and
friends.  The person did not subscribe to "fuel triggers"; they wrote a
sentence and can delete it.  A per-metric category would put a second
mute switch beside the delete button, and two ways to stop the same
notice is how "I turned it off and it kept coming" happens.

The ``alert.`` prefix is deliberate: it puts these notices in the same
inbox source as the rest of alerting, so the bell's Alerts bucket holds
everything alerting sends rather than growing a bucket of one.
"""

from __future__ import annotations

from capabilities.notifications.categories import (
    TARGETED, NotificationCategory, register_category,
)

TRIGGER_FIRED = "alert.trigger"

register_category(NotificationCategory(
    key=TRIGGER_FIRED,
    label="An alert trigger I set fires",
    kind=TARGETED,
))
