"""Notifications delivery admin — activity-trail entity declaration (see the registry).

Declares the entity types this feature writes and the permission that
gates viewing their per-record history.  One file, data only — the
Retention-hub declaration pattern.
"""

from capabilities.activity_trail.registry import (
    EntityDescriptor,
    register_entity,
)

register_entity(EntityDescriptor(
    "alert_type", "Alert routing", "notifications",
    view_permissions=('can_manage_account',),
))
register_entity(EntityDescriptor(
    "alert_topic", "Alert topic", "notifications",
    view_permissions=('can_manage_account',),
))
