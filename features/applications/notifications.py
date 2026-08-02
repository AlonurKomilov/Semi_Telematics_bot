"""Recruiting notifications — the Applications category.

A new driver application now also reaches the ONE shared inbox
(``notification_inbox``), so it appears in the top-bar panel's
**Applications** tab for whoever can act on it.  The feature keeps its
own RECORDS (``driver_applications`` and its pipeline lifecycle) — the
same split Alerts uses: a feature table plus a shared feed.

BOTH in-app stores are written, by decision: ``application_notifications``
still feeds the in-page bell a reviewer works next to, while the shared
inbox is how someone NOT on that page finds out.  The cost is honest and
accepted — read-state is per-store, so clearing one bell leaves the other
bold.  Collapsing to one store is a later, separate call.

SCOPE: the IN-APP notice only.  Email and Telegram keep the feature's own
templated senders — they are good and live, and moving them is a separate,
deliberate step (they would otherwise re-render through generic channel
templates).

Visibility is a PERMISSION, never a role: the tab renders for holders of
``can_manage_applications`` (recruiter and HR today, plus anyone an owner
grants it), which is also who the sender loop resolves as recipients.
"""

from __future__ import annotations

import logging

from capabilities.notifications import (
    TARGETED, NotificationCategory, register_category,
)

logger = logging.getLogger(__name__)

APPLICATION_RECEIVED = "applications.received"

# TARGETED, not BROADCAST: the recipient list is computed from the
# PERMISSION (``can_manage_applications``, per-account overrides honoured)
# rather than from a role predicate, so the caller resolves who and emits
# one notice each.  Non-mandatory — a recruiter who lives in the
# Applications page can mute it.
register_category(NotificationCategory(
    key=APPLICATION_RECEIVED,
    label="A new driver application arrives",
    kind=TARGETED,
))
