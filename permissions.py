"""
Role-Based Access Control (RBAC) — permission definitions & decorators.

Future-proof:
  • Permissions are data-driven (dict), not scattered across handlers.
  • Easy to add new features or roles without touching bot.py logic.
  • When migrating to Option B (FastAPI), these definitions move
    directly into the API middleware.

Two-tier authority model:
  SYSTEM OWNER — env-driven, NOT in the database. Controls the whole
                 bot platform. Sees all accounts, analytics, etc.
                 Identified solely by SYSTEM_OWNER_IDS in .env.

  CUSTOMER ROLES (in the database):
    owner        — full control of their account, manage companies/users
    admin        — manage users, all fleet features
    fleet_manager — all fleet features, no user management
    dispatcher   — fuel, truck location, rolling/stopped alerts
    driver       — own truck only, own fuel, own alerts
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Optional

from database import Role


# ─── System Owner (env-driven) ───────────────────────────────────

def _parse_system_owners() -> set[int]:
    """Parse SYSTEM_OWNER_IDS from environment."""
    raw = os.getenv("SYSTEM_OWNER_IDS", "")
    ids: set[int] = set()
    for part in raw.split(","):
        part = part.strip()
        if part:
            try:
                ids.add(int(part))
            except ValueError:
                pass
    return ids


SYSTEM_OWNER_IDS: set[int] = _parse_system_owners()


def is_system_owner(telegram_id: int) -> bool:
    """Check if a Telegram user ID is a system owner (platform admin)."""
    return telegram_id in SYSTEM_OWNER_IDS


# ─── Feature Flags ────────────────────────────────────────────────

@dataclass(frozen=True)
class FeatureSet:
    """What a role can see/do."""
    # Fleet reports
    can_faults: bool = False         # /faults  PDF
    can_critical: bool = False       # /critical PDF
    can_fuel: bool = False           # /fuel
    can_efficiency: bool = False     # /efficiency
    can_health: bool = False         # /health
    can_truck_all: bool = False      # /truck <any>
    can_truck_own: bool = False      # /truck <own> (driver)

    # Alerts
    can_alerts_all: bool = False     # new fault alerts (all trucks)
    can_alerts_own: bool = False     # alerts for own truck only

    # Management
    can_invite: bool = False         # /invite
    can_manage_users: bool = False   # /users, /setrole, /remove
    can_manage_companies: bool = False    # /addcompany, /removecompany
    can_manage_account: bool = False # /account settings

    # Dispatcher extras
    can_rolling_stopped: bool = False   # rolling/stopped notifications

    # ── New features ──────────────────────────────────────────────
    can_geofence_all: bool = False      # geofence alerts (all trucks)
    can_geofence_own: bool = False      # geofence alerts (own truck)
    can_digest: bool = False            # auto reports subscription
    can_maintenance_all: bool = False   # maintenance scheduler (all trucks)
    can_maintenance_own: bool = False   # maintenance scheduler (own truck)
    can_scorecard_all: bool = False     # driver scorecards (all drivers)
    can_scorecard_own: bool = False     # driver scorecards (own only)
    can_location_map: bool = False      # live location map (all trucks)
    can_location_own: bool = False      # live location map (own truck)
    can_fuel_cost: bool = False         # fuel cost tracker
    can_route_all: bool = False         # route replay (all trucks)
    can_route_own: bool = False         # route replay (own truck)
    can_cost_per_mile: bool = False     # cost-per-mile dashboard
    can_events_all: bool = False        # safety events (all trucks)
    can_events_own: bool = False        # safety events (own truck)


# ─── Role → Permission Map ───────────────────────────────────────

ROLE_PERMISSIONS: dict[Role, FeatureSet] = {
    Role.OWNER: FeatureSet(
        can_faults=True, can_critical=True, can_fuel=True,
        can_efficiency=True, can_health=True,
        can_truck_all=True, can_truck_own=True,
        can_alerts_all=True, can_alerts_own=True,
        can_invite=True, can_manage_users=True,
        can_manage_companies=True, can_manage_account=True,
        can_rolling_stopped=True,
        can_geofence_all=True, can_geofence_own=True,
        can_digest=True,
        can_maintenance_all=True, can_maintenance_own=True,
        can_scorecard_all=True, can_scorecard_own=True,
        can_location_map=True, can_location_own=True,
        can_fuel_cost=True,
        can_route_all=True, can_route_own=True,
        can_cost_per_mile=True,
        can_events_all=True, can_events_own=True,
    ),
    Role.ADMIN: FeatureSet(
        can_faults=True, can_critical=True, can_fuel=True,
        can_efficiency=True, can_health=True,
        can_truck_all=True, can_truck_own=True,
        can_alerts_all=True, can_alerts_own=True,
        can_invite=True, can_manage_users=True,
        can_manage_companies=False, can_manage_account=False,
        can_rolling_stopped=True,
        can_geofence_all=True, can_geofence_own=True,
        can_digest=True,
        can_maintenance_all=True, can_maintenance_own=True,
        can_scorecard_all=True, can_scorecard_own=True,
        can_location_map=True, can_location_own=True,
        can_fuel_cost=True,
        can_route_all=True, can_route_own=True,
        can_cost_per_mile=True,
        can_events_all=True, can_events_own=True,
    ),
    Role.FLEET_MGR: FeatureSet(
        can_faults=True, can_critical=True, can_fuel=True,
        can_efficiency=True, can_health=True,
        can_truck_all=True, can_truck_own=True,
        can_alerts_all=True, can_alerts_own=True,
        can_invite=False, can_manage_users=False,
        can_manage_companies=False, can_manage_account=False,
        can_rolling_stopped=False,
        can_geofence_all=True, can_geofence_own=True,
        can_digest=True,
        can_maintenance_all=True, can_maintenance_own=True,
        can_scorecard_all=True, can_scorecard_own=True,
        can_location_map=True, can_location_own=True,
        can_fuel_cost=True,
        can_route_all=True, can_route_own=True,
        can_cost_per_mile=True,
        can_events_all=True, can_events_own=True,
    ),
    Role.DISPATCHER: FeatureSet(
        can_faults=False, can_critical=False, can_fuel=True,
        can_efficiency=False, can_health=False,
        can_truck_all=True, can_truck_own=True,
        can_alerts_all=False, can_alerts_own=False,
        can_invite=False, can_manage_users=False,
        can_manage_companies=False, can_manage_account=False,
        can_rolling_stopped=True,
        can_geofence_all=True, can_geofence_own=True,
        can_digest=True,
        can_maintenance_all=False, can_maintenance_own=False,
        can_scorecard_all=True, can_scorecard_own=True,
        can_location_map=True, can_location_own=True,
        can_fuel_cost=False,
        can_route_all=True, can_route_own=True,
        can_cost_per_mile=False,
        can_events_all=False, can_events_own=False,
    ),
    Role.DRIVER: FeatureSet(
        can_faults=False, can_critical=False, can_fuel=False,
        can_efficiency=False, can_health=False,
        can_truck_all=False, can_truck_own=True,
        can_alerts_all=False, can_alerts_own=True,
        can_invite=False, can_manage_users=False,
        can_manage_companies=False, can_manage_account=False,
        can_rolling_stopped=False,
        can_geofence_all=False, can_geofence_own=True,
        can_digest=True,
        can_maintenance_all=False, can_maintenance_own=True,
        can_scorecard_all=False, can_scorecard_own=True,
        can_location_map=False, can_location_own=True,
        can_fuel_cost=False,
        can_route_all=False, can_route_own=True,
        can_cost_per_mile=False,
        can_events_all=False, can_events_own=True,
    ),
}


def get_permissions(role: Role) -> FeatureSet:
    """Get the permission set for a role."""
    return ROLE_PERMISSIONS.get(role, FeatureSet())


def can(role: Role, feature: str) -> bool:
    """Check if a role has a specific feature permission.

    Usage:  can(user.role, "can_faults")
    """
    perms = get_permissions(role)
    return getattr(perms, feature, False)


# ─── Role Display Helpers ─────────────────────────────────────────

ROLE_DISPLAY: dict[Role, str] = {
    Role.OWNER:       "👑 Owner",
    Role.ADMIN:       "🔑 Admin",
    Role.FLEET_MGR:   "🔧 Fleet",
    Role.DISPATCHER:  "📡 Dispatcher",
    Role.DRIVER:      "🚛 Driver",
}

ROLE_EMOJI: dict[Role, str] = {
    Role.OWNER:       "👑",
    Role.ADMIN:       "🔑",
    Role.FLEET_MGR:   "🔧",
    Role.DISPATCHER:  "📡",
    Role.DRIVER:      "🚛",
}


def role_display(role: Role) -> str:
    return ROLE_DISPLAY.get(role, str(role.value))


def role_emoji(role: Role) -> str:
    return ROLE_EMOJI.get(role, "👤")


# ─── Menu visibility — which buttons to show per role ─────────────

def visible_main_buttons(role: Role) -> list[str]:
    """Return list of callback_data strings the role can see in main menu."""
    perms = get_permissions(role)
    buttons = []
    if perms.can_faults:
        buttons.append("cmd_faults")
    if perms.can_fuel:
        buttons.append("cmd_fuel")
    if perms.can_alerts_all or perms.can_alerts_own:
        buttons.append("cmd_alerts")
    return buttons


def can_access_company_submenu(role: Role) -> bool:
    """Whether this role can filter by individual company."""
    perms = get_permissions(role)
    return perms.can_faults or perms.can_fuel
