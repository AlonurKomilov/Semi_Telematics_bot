"""Work Orders — activity-trail entity declaration (see the registry).

Declares the entity types this feature writes and the permission that
gates viewing their per-record history.  One file, data only — the
Retention-hub declaration pattern.
"""

from capabilities.activity_trail.registry import (
    EntityDescriptor,
    register_entity,
)

register_entity(EntityDescriptor(
    "work_order", "Work order", "work_orders",
    view_permissions=('can_manage_work_orders',),
    restore_permissions=('can_manage_work_orders',),
    restore_table="work_orders",
    company_scoped=True,
))
