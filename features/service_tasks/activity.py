"""Service Tasks — activity-trail entity declaration (see the registry).

Declares the entity types this feature writes and the permission that
gates viewing their per-record history.  One file, data only — the
Retention-hub declaration pattern.
"""

from capabilities.activity_trail.registry import (
    EntityDescriptor,
    register_entity,
)

register_entity(EntityDescriptor(
    "service_task", "Service task", "service_tasks",
    view_permissions=('can_service_tasks',),
    restore_permissions=('can_service_tasks',),
    restore_table="service_tasks",
))
