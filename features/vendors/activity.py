"""Vendors — activity-trail entity declaration (see the registry).

Declares the entity types this feature writes and the permission that
gates viewing their per-record history.  One file, data only — the
Retention-hub declaration pattern.
"""

from capabilities.activity_trail.registry import (
    EntityDescriptor,
    register_entity,
)

register_entity(EntityDescriptor(
    "vendor", "Vendor", "vendors",
    view_permissions=('can_manage_work_orders',),
    restore_permissions=('can_manage_work_orders',),
    restore_table="vendors",
))
register_entity(EntityDescriptor(
    "vendor_directory_entry", "Shared vendor entry", "vendors",
    view_permissions=('can_manage_work_orders',),
))
register_entity(EntityDescriptor(
    "sharing_settings", "Sharing settings", "vendors",
    view_permissions=('can_manage_account',),
))
