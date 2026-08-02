"""Vehicles — activity-trail entity declaration (see the registry).

Declares the entity types this feature writes and the permission that
gates viewing their per-record history.  One file, data only — the
Retention-hub declaration pattern.
"""

from capabilities.activity_trail.registry import (
    EntityDescriptor,
    register_entity,
)

register_entity(EntityDescriptor(
    "vehicle", "Vehicle", "vehicles",
    view_permissions=('can_manage_vehicles',),
))
register_entity(EntityDescriptor(
    "inventory_item", "Inventory item", "vehicles",
    view_permissions=('can_manage_vehicles',),
))
