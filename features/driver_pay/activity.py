"""Driver Pay — activity-trail entity declaration (see the registry).

Declares the entity types this feature writes and the permission that
gates viewing their per-record history.  One file, data only — the
Retention-hub declaration pattern.
"""

from capabilities.activity_trail.registry import (
    EntityDescriptor,
    register_entity,
)

register_entity(EntityDescriptor(
    "driver_pay_run", "Driver pay run", "driver_pay",
    view_permissions=('can_driver_pay_admin',),
))
