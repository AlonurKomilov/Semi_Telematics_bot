"""Notifications — the cross-cutting multi-channel delivery layer.

Design SSOT: docs/architecture/notifications.md.  Event sources (alerts
today; more later) resolve recipients + preferences and hand each
delivery to a registered ``Channel``.  This package imports only
``infra`` + transport libs — never an event source — so any event
source can call into it without a cycle.

Importing this package registers the built-in channels.
"""

from capabilities.notifications.channels import (  # noqa: F401
    Channel,
    DeliveryResult,
    Payload,
    Recipient,
    get_channel,
    list_channels,
    personal_channels,
    register_channel,
    shared_channels,
)

# Register the built-in transports (their module-load @register_channel).
from capabilities.notifications import telegram as _telegram  # noqa: F401,E402
