"""Alerting registers its notification categories with the notification
service — the first source to do so.

This is the one-way seam in action: alerting imports notifications
(never the reverse).  Each alert type becomes a BROADCAST
``alert.<type>`` category whose audience is the existing role-eligibility
rule, so ``dispatch()`` can drop a recipient whose current role isn't
eligible without the notification core ever knowing what an "alert" is.

Importing this module (done from ``capabilities.alerting`` at boot)
performs the registration.
"""

from __future__ import annotations

from functools import partial

from capabilities.alerting.relevance import (
    ALERT_TYPE_REQUIRED_PERM,
    role_can_receive_alert,
)
from capabilities.notifications.categories import (
    BROADCAST,
    NotificationCategory,
    register_category,
)

_LABELS = {
    "faults": "Engine faults",
    "health": "Vehicle health",
    "fuel": "Fuel & DEF",
    "geofence": "Geofence entry/exit",
    "events": "Safety events",
    "camera": "Camera issues",
    "parking": "Unsafe parking",
}


def register_alert_categories() -> None:
    for atype in ALERT_TYPE_REQUIRED_PERM:
        register_category(NotificationCategory(
            key=f"alert.{atype}",
            label=_LABELS.get(atype, atype.replace("_", " ").title()),
            kind=BROADCAST,
            # audience(role) -> role_can_receive_alert(role, alert_type=atype)
            audience=partial(role_can_receive_alert, alert_type=atype),
        ))


register_alert_categories()
