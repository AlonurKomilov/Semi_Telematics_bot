"""Maintenance — activity-trail entity declaration (see the registry).

Declares the entity types this feature writes and the permission that
gates viewing their per-record history.  One file, data only — the
Retention-hub declaration pattern.
"""

from capabilities.activity_trail.registry import (
    EntityDescriptor,
    register_entity,
)

register_entity(EntityDescriptor(
    "maintenance_task", "Maintenance task", "maintenance",
    view_permissions=('can_maintenance_all',),
))
register_entity(EntityDescriptor(
    "maintenance_template", "Maintenance template", "maintenance",
    view_permissions=('can_maintenance_all',),
))
register_entity(EntityDescriptor(
    "maintenance", "Maintenance task", "maintenance",
    view_permissions=('can_maintenance_all',),
))
# "maintenance" = the frozen-log import alias (pre-trail rows).
