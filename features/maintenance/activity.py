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
    restore_permissions=('can_maintenance_all',),
    restore_table="maintenance_tasks",
    company_scoped=True,
))
register_entity(EntityDescriptor(
    "maintenance_template", "Maintenance template", "maintenance",
    view_permissions=('can_maintenance_all',),
    restore_permissions=('can_maintenance_all',),
    restore_table="maintenance_templates",
))
register_entity(EntityDescriptor(
    "maintenance", "Maintenance task", "maintenance",
    view_permissions=('can_maintenance_all',),
    # The frozen audit_log's imported rows (migration 178) — their
    # entity_id is a real maintenance_tasks id, so naming the owning
    # table lets the company wall resolve for them too.  Restore stays
    # OFF: that needs restore_permissions, which these never get (a
    # legacy row carries no field values to replay).
    restore_table="maintenance_tasks",
    company_scoped=True,
))
# "maintenance" = the frozen-log import alias (pre-trail rows).
