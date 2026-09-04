"""Role-based alert relevance — which alert types each role can subscribe to.

Personal-DM alert preferences (the per-user toggles on the bot's
``/alerts`` command and the dashboard's "My Notifications" page) are
**filtered by role** so a user only sees toggles for alert types that
match their job.  A Safety user shouldn't see a "Fuel" toggle; a
Dispatcher shouldn't see "Health" or "Maintenance".

The mapping is **derived from the existing FeatureSet permissions**
(``capabilities/permissions/permissions.py:ROLE_PERMISSIONS``) so it never
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
from capabilities.permissions.roles import ROLE_PERMISSIONS, FeatureSet


# Mapping from canonical alert type → required permission(s).
#
# When the value is a list, the role needs ANY ONE of the listed
# permissions (so a driver with assigned-width ``can_view_parking`` still gets
# the parking toggle for their own truck).
#
# Adding a new alert type: add an entry here AND make sure the
# corresponding ``users.alert_<type>`` column exists.  See
# ``adapters/storage/platform_schema.py`` for the column list.
ALERT_TYPE_REQUIRED_PERM: dict[str, Union[str, list[str]]] = {
    # Engine fault codes (SPN/FMI).
    "faults":   "can_faults",
    # Camera-issue alerts belong to the Cameras feature and gate on its
    # own permission.  (They rode ``can_faults`` until 2026-07-27 as a
    # "vehicle-health adjacent" shortcut — owner decision: each alert
    # type obeys its owning feature's permission.)
    "camera":   "can_cameras",
    # Mechanical telemetry — oil / coolant / battery / DEF readings.
    "health":   "can_health",
    # Fuel level + DEF + fuel efficiency events.
    "fuel":     "can_fuel",
    # Safety / harsh-event alerts (braking, cornering, speeding, etc.).
    "events":   ["can_view_events"],
    # Geofence entry/exit alerts on platform-defined zones.
    "geofence": ["can_view_geofence"],
    # Unauthorised-stop + long-idle parking alerts.  Parking is its own
    # feature with its own permissions — detection sitting in the
    # geofencing capability is an implementation detail that must not
    # decide who receives the alerts (rode ``can_geofence_*`` until
    # 2026-07-27).
    "parking":  ["can_view_parking"],
    # Maintenance overdue + due-soon alerts.
    "maintenance": ["can_view_maintenance"],
    # A truck's papers.  Deliberately NOT the "documents" type: that one
    # is Driver Documents, whose audience is HR and the owner with
    # dispatch explicitly excluded — and dispatch is precisely who needs
    # to know a tractor's insurance lapses on Friday.  Same idea,
    # different audience, so its own category.
    "vehicle_documents": "can_vehicle_docs",
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

    STATIC role defaults — the FALLBACK the effective gate below drops
    to when per-account resolution fails; delivery paths must call
    ``filter_users_by_alert_access`` / ``alert_types_for_user`` instead
    (§9d closed 2026-07-27).  Don't copy this pattern into an
    authorization context.
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


# ─── Effective (per-account) delivery gates — §9d closed ─────────
#
# The Board's visibility filter always used the user's EFFECTIVE
# FeatureSet (matrix overrides + manager tier + owner rows); the
# delivery gates used static role seeds, so a matrix-customized account
# could see one thing and receive another.  Owner decision 2026-07-27:
# permissions are the single truth for RECEIVING too — an owner
# revoking a feature stops its personal DMs, a grant starts them.
#
# Fail-open BY TIER, not per call: if the stored-perms resolution
# breaks (perms storage down mid-fan-out), that tier falls back to the
# static seed gate above with a warning — a perms hiccup must never
# silence alert delivery entirely.

async def _effective_fs(cache: dict, account_id: int, role,
                        is_manager: bool, is_primary_owner: bool):
    """Resolve one (account, role, tier) to a FeatureSet, memoized in
    ``cache``; None marks a failed resolution (caller falls back)."""
    key = (account_id, str(role), bool(is_manager), bool(is_primary_owner))
    if key in cache:
        return cache[key]
    try:
        from capabilities.permissions.roles import get_user_permissions
        fs = await get_user_permissions(
            role if isinstance(role, Role) else Role(str(role)),
            account_id,
            is_manager=bool(is_manager),
            is_primary_owner=bool(is_primary_owner),
        )
    except Exception as e:
        import logging
        logging.getLogger(__name__).warning(
            "effective-perms resolve failed (account=%s role=%s): %s — "
            "falling back to static role defaults for this tier",
            account_id, role, e)
        fs = None
    cache[key] = fs
    return fs


async def filter_users_by_alert_access(users: list, alert_type: str) -> list:
    """Keep only users whose EFFECTIVE per-account permissions allow
    ``alert_type`` — the delivery-side twin of the Board's
    ``perms_allow_alert_type`` filter, same permission SSOT.

    Accepts User rows from any account mix (the camera digest passes a
    cross-account list); resolution is memoized per (account, role,
    tier) so a fan-out resolves each distinct tier once, not per user.
    """
    cache: dict = {}
    out = []
    for u in users:
        fs = await _effective_fs(
            cache, u.account_id, u.role,
            getattr(u, "is_manager", False),
            getattr(u, "is_primary_owner", False),
        )
        if fs is None:
            if role_can_receive_alert(u.role, alert_type):
                out.append(u)
        elif perms_allow_alert_type(fs, alert_type):
            out.append(u)
    return out


async def alert_types_for_user(
    role, account_id: int, *,
    is_manager: bool = False, is_primary_owner: bool = False,
) -> list[str]:
    """Effective-permission twin of ``alert_types_for_role`` for toggle
    UIs (bot /alerts keyboard, dashboard My Notifications): the visible
    toggles match what the delivery gate would actually deliver for
    THIS user in THIS account.  Order follows ALERT_TYPE_REQUIRED_PERM,
    same as the static variant.  Falls back to the static list when
    resolution fails."""
    fs = await _effective_fs({}, account_id, role, is_manager, is_primary_owner)
    if fs is None:
        return alert_types_for_role(role)
    return [
        atype for atype, req in ALERT_TYPE_REQUIRED_PERM.items()
        if _role_has_perm(fs, req)
    ]


# Category / Board visibility for profile-upgraded types (profiles.py).
# NOT merged into ALERT_TYPE_REQUIRED_PERM: that map also drives the
# bot's legacy per-type DM toggles, which require a users.alert_<type>
# column — these types have none (their personal delivery is the
# notification matrix only).  Same ANY-OF semantics.
CATEGORY_EXTRA_PERMS: dict[str, Union[str, list[str]]] = {
    "documents": ["can_manage_driver_docs", "can_driver_docs_own"],
    "scorecard": ["can_view_scorecards"],
}


def perms_allow_alert_type(perms, alert_type: str) -> bool:
    """Permission-SSOT check for ANY alert type against an effective
    FeatureSet (per-account overrides included when the caller passes
    the resolved set).  Types in neither map (system, samsara_sync,
    legacy oddballs) stay visible — fail-open matches the Board's
    historical behavior for the core family."""
    req = ALERT_TYPE_REQUIRED_PERM.get(alert_type) \
        or CATEGORY_EXTRA_PERMS.get(alert_type)
    if req is None:
        return True
    return _role_has_perm(perms, req)
