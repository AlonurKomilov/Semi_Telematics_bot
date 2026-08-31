"""Notifications — the cross-cutting multi-channel delivery layer.

Design SSOT: capabilities/notifications/docs/ARCHITECTURE.md.  Event sources (alerts
today; more later) resolve recipients + preferences and hand each
delivery to a registered ``Channel``.  This package imports only
``infra`` + transport libs — never an event source — so any event
source can call into it without a cycle.

Importing this package registers the built-in channels.
"""

from capabilities.notifications.channels import (  # noqa: F401
    Channel,
    DeliveryResult,
    NotificationContent,
    Payload,
    Recipient,
    get_channel,
    list_channels,
    personal_channels,
    register_channel,
    shared_channels,
)

from capabilities.notifications.service import (  # noqa: F401,E402
    DIGEST_CADENCES,
    build_digest_content,
    dispatch,
    flush_digests,
    notify_user,
    update_delivery,
)
from capabilities.notifications.plan import (  # noqa: F401,E402
    DeliveryPlan,
    PersonalSend,
    PlanResult,
    Target,
    deliver,
)
from capabilities.notifications.categories import (  # noqa: F401,E402
    BROADCAST,
    TARGETED,
    NotificationCategory,
    get_category,
    list_categories,
    register_category,
)

# Register the built-in transports (their module-load @register_channel).
from capabilities.notifications import telegram as _telegram  # noqa: F401,E402
from capabilities.notifications.email import EmailChannel  # noqa: E402
from capabilities.notifications.webpush import WebPushChannel  # noqa: E402
from capabilities.notifications.inapp import InAppChannel  # noqa: E402

register_channel(EmailChannel())
register_channel(WebPushChannel())
register_channel(InAppChannel())

# Boot-time visibility: email links are signed with NOTIFICATION_SIGNING_
# SECRET if set, else JWT_SECRET.  This only warns when BOTH are absent
# (a misconfig — JWT_SECRET is required at boot), in which case the email
# channel's connect/verify/unsubscribe fail closed until a key exists.
from capabilities.notifications.tokens import signing_secret_ok  # noqa: E402

if not signing_secret_ok():
    import logging as _logging

    _logging.getLogger("bot.notifications").warning(
        "No signing secret for notification links (JWT_SECRET / "
        "NOTIFICATION_SIGNING_SECRET) — email verify/unsubscribe links are "
        "disabled until one is set.")
