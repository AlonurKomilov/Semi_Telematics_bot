"""Settings & Team — activity-trail entity declaration (see the registry).

Declares the entity types this feature writes and the permission that
gates viewing their per-record history.  One file, data only — the
Retention-hub declaration pattern.
"""

from capabilities.activity_trail.registry import (
    EntityDescriptor,
    register_entity,
)

register_entity(EntityDescriptor(
    "user", "Team member", "settings",
    view_permissions=('can_manage_users',),
    sensitive_fields=frozenset(['dob', 'email', 'home_address', 'password_hash', 'phone']),
))
register_entity(EntityDescriptor(
    "invite", "Invite", "settings",
    view_permissions=('can_manage_users',),
))
register_entity(EntityDescriptor(
    "role", "Role permissions", "settings",
    view_permissions=('can_manage_users',),
))
register_entity(EntityDescriptor(
    "company", "Company", "settings",
    view_permissions=('can_manage_companies',),
))
register_entity(EntityDescriptor(
    "schedule", "Working hours", "settings",
    view_permissions=('can_manage_work_hours',),
    restore_permissions=('can_manage_work_hours',),
    restore_table="work_hours",
))
register_entity(EntityDescriptor(
    "account", "Account", "settings",
    view_permissions=('can_manage_account',),
))
register_entity(EntityDescriptor(
    "setting", "Account setting", "settings",
    view_permissions=('can_manage_account',),
))
