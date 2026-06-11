"""Role-based alert relevance — which alert types each role can subscribe to.

Personal-DM alert preferences (the per-user toggles on the bot's
``/alerts`` command and the dashboard's "My Notifications" page) are
**filtered by role** so a user only sees toggles for alert types that
match their job.  A Safety user shouldn't see a "Fuel" toggle; a
Dispatcher shouldn't see "Health" or "Maintenance".

The mapping is **derived from the existing FeatureSet permissions**
(``capabilities/iam/permissions.py:ROLE_PERMISSIONS``) so it never
goes out of sync with what the role is otherwise allowed to do.

This module exposes two things:

* ``ALERT_TYPE_REQUIRED_PERM`` — the {alert_type → required permission}
  mapping, where the value is either a single permission name (str)
  or a list of names (ANY-OF satisfies).
* ``alert_types_for_role(role)`` — returns the list of alert types
  the given role can subscribe to.

Two callers consume this:

1. ``adapters/storage/users.py:get_typed_alert_subscribers`` —
   subscriber-fetch gate.  Even if a user has ``alert_fuel=1`` cached
   from before role-filtering, if their role doesn't have ``can_fuel``
   they're filtered out.  Prevents UI from lying when toggles are
   hidden for irrelevant types (option B from the design discussion).

2. The bot's ``/alerts`` keyboard builder and the dashboard's "My
   Notifications" page — both render only the toggles for the user's
   role-relevant types.  Calls ``alert_types_for_role(user.role)``.
"""

from __future__ import annotations

from typing import Union

from adapters.storage.models import Role
from capabilities.iam.permissions import ROLE_PERMISSIONS, FeatureSet


# Mapping from canonical alert type → required permission(s).
#
# When the value is a list, the role needs ANY ONE of the listed
# permissions (so a driver with only ``can_geofence_vehicle`` still gets
# parking + geofence toggles for their own truck).
#
# Adding a new alert type: add an entry here AND make sure the
# corresponding ``users.alert_<type>`` column exists.  See
# ``adapters/storage/platform_schema.py`` for the column list.
ALERT_TYPE_REQUIRED_PERM: dict[str, Union[str, list[str]]] = {
    # Engine fault codes (SPN/FMI).  Camera-issue alerts piggy-back on
    # the same permission since they're vehicle-health adjacent.
    "faults":   "can_faults",
    "camera":   "can_faults",
    # Mechanical telemetry — oil / coolant / battery / DEF readings.
    "health":   "can_health",
    # Fuel level + DEF + fuel efficiency events.
    "fuel":     "can_fuel",
    # Safety / harsh-event alerts (braking, cornering, speeding, etc.).
    "events":   ["can_events_all", "can_events_vehicle"],
    # Geofence entry/exit alerts on platform-defined zones.
    "geofence": ["can_geofence_all", "can_geofence_vehicle"],
    # Unauthorised-stop + long-idle parking alerts.  Uses geofence
    # permission because parking detection sits in the geofencing
    # capability today.
    "parking":  ["can_geofence_all", "can_geofence_vehicle"],
    # Maintenance overdue + due-soon alerts.
    "maintenance": ["can_maintenance_all", "can_maintenance_vehicle"],
}


def _role_has_perm(perms: FeatureSet, req: Union[str, list[str]]) -> bool:
    """ANY-OF check against a FeatureSet."""
    if isinstance(req, str):
        return bool(getattr(perms, req, False))
    return any(bool(getattr(perms, p, False)) for p in req)


def alert_types_for_role(role: Union[Role, str]) -> list[str]:
    """Return the alert types a user with this role can subscribe to.

    Returns an empty list for unknown roles (defensive — better to
    silently drop someone from notifications than to leak alerts to
    a misconfigured account).

    The returned list preserves the order defined in
    ``ALERT_TYPE_REQUIRED_PERM`` so the UI renders toggles in a
    consistent order across the bot and the dashboard.
    """
    if isinstance(role, str):
        try:
            role = Role(role)
        except ValueError:
            return []
    perms = ROLE_PERMISSIONS.get(role)
    if perms is None:
        return []
    return [
        atype for atype, req in ALERT_TYPE_REQUIRED_PERM.items()
        if _role_has_perm(perms, req)
    ]


def role_can_receive_alert(role: Union[Role, str], alert_type: str) -> bool:
    """One-shot check: is this alert type relevant to this role?

    Equivalent to ``alert_type in alert_types_for_role(role)`` but
    avoids the list allocation when the caller only needs a yes/no.
    """
    if alert_type not in ALERT_TYPE_REQUIRED_PERM:
        return False
    if isinstance(role, str):
        try:
            role = Role(role)
        except ValueError:
            return False
    perms = ROLE_PERMISSIONS.get(role)
    if perms is None:
        return False
    return _role_has_perm(perms, ALERT_TYPE_REQUIRED_PERM[alert_type])
