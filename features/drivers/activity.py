"""Drivers — activity-trail entity declaration (see the registry).

Declares the entity types this feature writes and the permission that
gates viewing their per-record history.  One file, data only — the
Retention-hub declaration pattern.
"""

from capabilities.activity_trail.registry import (
    EntityDescriptor,
    register_entity,
)

register_entity(EntityDescriptor(
    "driver", "Driver", "drivers",
    view_permissions=('can_manage_drivers',),
    sensitive_fields=frozenset(['cdl_class', 'cdl_expires', 'cdl_number', 'cdl_state', 'dob', 'driver_notes', 'home_address', 'med_card_expires', 'phone']),
))
