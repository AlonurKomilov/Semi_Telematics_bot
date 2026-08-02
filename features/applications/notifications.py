"""Recruiting notifications — the Applications category.

A new driver application now also reaches the ONE shared inbox
(``notification_inbox``), so it appears in the top-bar panel's
**Applications** tab for whoever can act on it.  The feature keeps its
own RECORDS (``driver_applications`` and its pipeline lifecycle) — the
same split Alerts uses: a feature table plus a shared feed.

ONE in-app store, after a brief period of two.  Both bells read it — the
top-bar panel's Applications tab and the Applications page's own bell
(``useInboxSource('applications')``) — so clearing either one clears the
notice everywhere, which is what two stores could not do.
``application_notifications`` is retired: not written, not read, its
endpoints gone; the table waits for its own drop migration.

One property the retired table had and the shared inbox does not: an
``ON DELETE CASCADE`` from ``driver_applications``.  Nothing deletes a
single application today (the account purge sweeps every table with an
``account_id``, and the inbox prunes at 60 days regardless), but a
per-application delete would need to sweep its notices explicitly.

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
