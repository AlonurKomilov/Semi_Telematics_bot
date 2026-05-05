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
    fleet        — all fleet features, no user management
    safety       — safety-focused: scorecards, events, alerts, no costs
    dispatcher   — fuel, truck location, rolling/stopped alerts
    driver       — own truck only, own fuel, own alerts
"""

from __future__ import annotations

import logging
import os
from dataclasses import dataclass
from typing import Optional

from adapters.storage import Role

logger = logging.getLogger(__name__)


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
    can_vehicle_all: bool = False      # /vehicle <any>
    can_vehicle_own: bool = False      # /vehicle <own> (driver)

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
    can_scorecard_all: bool = False     # scorecards for all subjects (driver or vehicle)
    can_scorecard_own: bool = False     # scorecards for own assigned truck(s) only
    can_location_map: bool = False      # live location map (all trucks)
    can_location_own: bool = False      # live location map (own truck)
    can_fuel_cost: bool = False         # fuel cost tracker
    can_route_all: bool = False         # route replay (all trucks)
    can_route_own: bool = False         # route replay (own truck)
    can_cost_per_mile: bool = False     # cost-per-mile dashboard
    can_events_all: bool = False        # safety events (all trucks)
    can_events_own: bool = False        # safety events (own truck)
    can_manage_billing: bool = False    # billing & subscription management (owner + admin)
    can_manage_poi_layers: bool = False # create/edit/delete custom POI map layers (owner/admin/fleet)


# ─── Role → Permission Map ───────────────────────────────────────

ROLE_PERMISSIONS: dict[Role, FeatureSet] = {
    Role.OWNER: FeatureSet(
        can_faults=True, can_critical=True, can_fuel=True,
        can_efficiency=True, can_health=True,
        can_vehicle_all=True, can_vehicle_own=True,
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
        can_manage_billing=True,
        can_manage_poi_layers=True,
    ),
    Role.ADMIN: FeatureSet(
        can_faults=True, can_critical=True, can_fuel=True,
        can_efficiency=True, can_health=True,
        can_vehicle_all=True, can_vehicle_own=True,
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
        can_manage_billing=True,
        can_manage_poi_layers=True,
    ),
    Role.FLEET: FeatureSet(
        can_faults=True, can_critical=True, can_fuel=True,
        can_efficiency=True, can_health=True,
        can_vehicle_all=True, can_vehicle_own=True,
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
        can_manage_poi_layers=True,
    ),
    Role.SAFETY: FeatureSet(
        can_faults=True, can_critical=True, can_fuel=False,
        can_efficiency=False, can_health=True,
        can_vehicle_all=True, can_vehicle_own=True,
        can_alerts_all=True, can_alerts_own=True,
        can_invite=False, can_manage_users=False,
        can_manage_companies=False, can_manage_account=False,
        can_rolling_stopped=False,
        can_geofence_all=True, can_geofence_own=True,
        can_digest=True,
        can_maintenance_all=True, can_maintenance_own=True,
        can_scorecard_all=True, can_scorecard_own=True,
        can_location_map=True, can_location_own=True,
        can_fuel_cost=False,
        can_route_all=True, can_route_own=True,
        can_cost_per_mile=False,
        can_events_all=True, can_events_own=True,
    ),
    Role.DISPATCHER: FeatureSet(
        can_faults=False, can_critical=False, can_fuel=True,
        can_efficiency=False, can_health=False,
        can_vehicle_all=True, can_vehicle_own=True,
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
        can_vehicle_all=False, can_vehicle_own=True,
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
    """Get the default permission set for a role (sync, no DB).

    For account-specific permissions, use get_account_permissions() instead.
    """
    return ROLE_PERMISSIONS.get(role, FeatureSet())


async def get_account_permissions(
    role: Role,
    account_id: int,
    company_id: Optional[int] = None,
) -> FeatureSet:
    """Get permission set for a role within a specific account.

    Resolution order:
    1. DB: company-specific override (if company_id given)
    2. DB: account-wide custom permissions
    3. Fallback: hardcoded ROLE_PERMISSIONS defaults

    Results are cached per (account_id, role, company_id).
    """
    cache_key = (account_id, role.value if hasattr(role, "value") else role, company_id)
    cached = _permissions_cache.get(cache_key)
    if cached is not None:
        return cached

    try:
        from infra.platform import get_platform_db
        pdb = get_platform_db()
        role_str = role.value if hasattr(role, "value") else role
        perm_dict = await pdb.get_role_permissions(account_id, role_str, company_id)
        if perm_dict is not None:
            # Start from role defaults so newly-added permission fields get
            # their correct default value even when the stored DB row predates
            # the field being added (avoids silently locking users out of new
            # features because the DB column didn't exist at the time).
            from dataclasses import asdict as _asdict
            known_fields = {f.name for f in FeatureSet.__dataclass_fields__.values()}
            role_defaults = _asdict(ROLE_PERMISSIONS.get(role, FeatureSet()))
            filtered = {k: v for k, v in perm_dict.items() if k in known_fields}
            merged = {**role_defaults, **filtered}
            fs = FeatureSet(**merged)
            _permissions_cache[cache_key] = fs
            return fs
    except Exception as e:
        logger.debug("Could not load permissions from DB (using defaults): %s", e)

    fs = ROLE_PERMISSIONS.get(role, FeatureSet())
    _permissions_cache[cache_key] = fs
    return fs


def invalidate_permissions_cache(
    account_id: Optional[int] = None,
) -> None:
    """Clear cached permissions. Call after Owner edits role permissions."""
    if account_id is None:
        _permissions_cache.clear()
    else:
        keys_to_drop = [k for k in _permissions_cache if k[0] == account_id]
        for k in keys_to_drop:
            del _permissions_cache[k]


# In-memory cache: (account_id, role_str, company_id) → FeatureSet
_permissions_cache: dict[tuple, FeatureSet] = {}


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
    Role.FLEET:      "🔧 Fleet",
    Role.SAFETY:      "🛡️ Safety",
    Role.DISPATCHER:  "📡 Dispatcher",
    Role.DRIVER:      "🚛 Driver",
}

ROLE_EMOJI: dict[Role, str] = {
    Role.OWNER:       "👑",
    Role.ADMIN:       "🔑",
    Role.FLEET:      "🔧",
    Role.SAFETY:      "🛡️",
    Role.DISPATCHER:  "📡",
    Role.DRIVER:      "🚛",
}


def role_display(role: Role) -> str:
    return ROLE_DISPLAY.get(role, str(role.value))


def role_emoji(role: Role) -> str:
    return ROLE_EMOJI.get(role, "👤")


# ─── AI Role Guidance (dynamic, auto-synced with permissions) ─────

# Human-readable labels for permission flags → AI-friendly descriptions
_FEATURE_LABELS: dict[str, str] = {
    "can_faults": "fault reports",
    "can_critical": "critical fault alerts",
    "can_fuel": "fuel levels",
    "can_efficiency": "driver efficiency",
    "can_health": "vehicle health",
    "can_vehicle_all": "all vehicles",
    "can_vehicle_own": "own vehicle only",
    "can_alerts_all": "alerts for all trucks",
    "can_alerts_own": "alerts for own truck",
    "can_invite": "invite users",
    "can_manage_users": "manage users",
    "can_manage_companies": "manage companies",
    "can_manage_account": "account settings",
    "can_rolling_stopped": "rolling/stopped status",
    "can_geofence_all": "geofence alerts (all)",
    "can_geofence_own": "geofence alerts (own)",
    "can_digest": "auto reports",
    "can_maintenance_all": "maintenance (all trucks)",
    "can_maintenance_own": "maintenance (own truck)",
    "can_scorecard_all": "driver scorecards (all)",
    "can_scorecard_own": "driver scorecards (own)",
    "can_location_map": "live location map (all)",
    "can_location_own": "live location (own truck)",
    "can_fuel_cost": "fuel cost tracking",
    "can_route_all": "route replay (all)",
    "can_route_own": "route replay (own)",
    "can_cost_per_mile": "cost per mile",
    "can_events_all": "safety events (all)",
    "can_events_own": "safety events (own)",
    "can_manage_billing": "billing & subscription management",
}


def build_role_guidance(role_str: str) -> str:
    """Build AI guidance text dynamically from the role's actual permissions.

    Returns a short paragraph the AI can use to understand what data
    this user can and cannot access. Always reflects current ROLE_PERMISSIONS.
    """
    try:
        role = Role(role_str)
    except (ValueError, KeyError):
        # Unknown role: use the most restrictive safe default — do not
        # assume fleet-manager scope for an unrecognised identity.
        return "Unknown role — answer only with publicly visible information and avoid disclosing any user, vehicle, or operational data."

    perms = get_permissions(role)
    allowed: list[str] = []
    denied: list[str] = []
    for field_name, label in _FEATURE_LABELS.items():
        if getattr(perms, field_name, False):
            allowed.append(label)
        else:
            denied.append(label)

    lines = [f"Role: {role.value.upper()}"]
    if allowed:
        lines.append(f"CAN access: {', '.join(allowed)}.")
    if denied:
        lines.append(f"CANNOT access: {', '.join(denied)}.")

    # Add role-specific behavioral hints
    if role == Role.DRIVER:
        lines.append(
            "Focus on their assigned truck. Use simple language. "
            "If they say 'my truck' use their assigned truck number."
        )
    elif role in (Role.OWNER, Role.ADMIN):
        lines.append(
            "Include cost analysis, fleet-wide metrics, management insights."
        )
    elif role == Role.DISPATCHER:
        lines.append(
            "Focus on routes, locations, ETAs, scheduling, fuel levels."
        )
    elif role == Role.SAFETY:
        lines.append(
            "Focus on safety events, scorecards, compliance, cameras."
        )
    elif role == Role.FLEET:
        lines.append(
            "Focus on vehicle health, maintenance, fault trends, safety events, "
            "scorecards, compliance, live locations, routes, geofences, and alerts. "
            "This role has full operational visibility but cannot manage users or account settings."
        )

    return "\n".join(lines)


async def build_role_guidance_for_account(db, account_id: int, role_str: str) -> str:
    """Like build_role_guidance() but checks for a per-account DB override first.

    If the account has set a custom guidance string for *role_str*, that string
    is returned instead of the auto-generated one.  Falls back to the default
    sync implementation when no override exists or when db is None.
    """
    if db is not None and account_id:
        try:
            override = await db.get_role_ai_guidance(account_id, role_str)
            if override:
                return override
        except Exception:
            pass  # any DB error → fall back to defaults
    return build_role_guidance(role_str)


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


# ─── Role Hierarchy ───────────────────────────────────────────────

ROLE_HIERARCHY: dict[str, int] = {
    "owner": 5, "admin": 4, "fleet": 3, "safety": 3,
    "dispatcher": 2, "driver": 1,
}


def role_rank(role: Role | str) -> int:
    """Numeric rank for a role (higher = more privileged)."""
    key = role.value if isinstance(role, Role) else role
    return ROLE_HIERARCHY.get(key, 0)


def validate_invite_role(
    actor_role: Role | str,
    invite_role: Role | str,
) -> tuple[bool, str]:
    """Check that *actor_role* may issue an invite for *invite_role*.

    Returns ``(True, "")`` on success, ``(False, reason_key)`` on failure
    where *reason_key* is a stable string callers can map to a message.
    """
    # Owner can never be created via an invite link
    key = invite_role.value if isinstance(invite_role, Role) else invite_role
    if key == "owner":
        return False, "owner_via_invite"
    # Cannot invite to a role that is >= your own rank (fixes the > bug)
    if role_rank(invite_role) >= role_rank(actor_role):
        return False, "cant_invite_higher"
    return True, ""


def validate_role_change(
    actor_role: Role | str,
    target_current_role: Role | str,
    new_role: Role | str,
) -> tuple[bool, str]:
    """Check that *actor_role* may change *target_current_role* → *new_role*.

    Returns ``(True, "")`` on success, ``(False, reason_key)`` on failure.
    """
    actor_rank = role_rank(actor_role)
    # Cannot touch someone whose rank is >= yours
    if role_rank(target_current_role) >= actor_rank:
        return False, "cant_modify_higher"
    # Cannot assign a role that is >= yours
    if role_rank(new_role) >= actor_rank:
        return False, "cant_promote_above_self"
    return True, ""


def is_management_role(role: Role | str) -> bool:
    """True for owner or admin — roles that manage the account."""
    key = role.value if isinstance(role, Role) else role
    return key in ("owner", "admin")


# ─── AI Tool Permission Mappings ──────────────────────────────────
# Centralized here (not in AI code) so RBAC policy stays in one file.

# Map each tool to the permission flag(s) required.
# If ANY listed permission is True for the user's role, the tool is allowed.
# None means always allowed.
TOOL_PERMISSIONS: dict[str, list[str] | None] = {
    "get_vehicle_faults":       ["can_faults", "can_critical"],              # owner/admin/fleet/safety
    "get_vehicle_detail":       ["can_vehicle_all", "can_vehicle_own"],          # all roles
    "get_driver_efficiency":    ["can_efficiency"],                          # owner/admin/fleet
    "get_low_fuel_vehicles":    ["can_fuel"],                                # owner/admin/fleet/dispatcher
    "get_vehicle_health":       ["can_health"],                              # owner/admin/fleet/safety
    "get_weather":              ["can_vehicle_all"],                           # all except driver
    "get_efficiency_summary":   ["can_efficiency"],                          # owner/admin/fleet
    "get_vehicle_location":     ["can_location_map", "can_location_own"],    # all roles
    "get_geofences":            ["can_geofence_all", "can_geofence_own"],    # all roles
    "get_account_stats":        ["can_vehicle_all"],                           # all except driver
    "get_vehicle_events":       ["can_events_all", "can_events_own"],        # owner/admin/fleet/safety/driver(own)
    "get_events_summary":       ["can_events_all"],                          # owner/admin/fleet/safety
    "get_vehicle_maintenance":  ["can_maintenance_all", "can_maintenance_own"],  # owner/admin/fleet/safety/driver(own)
    "get_maintenance_summary":  ["can_maintenance_all"],                     # owner/admin/fleet/safety
    "get_vehicle_fuel_costs":   ["can_fuel_cost"],                           # owner/admin/fleet
    "get_fuel_cost_summary":    ["can_fuel_cost"],                           # owner/admin/fleet
    "check_vehicle_camera":     ["can_vehicle_all"],                           # all except driver
    "get_driver_scorecard":     ["can_scorecard_all", "can_scorecard_own"],  # all except dispatcher
    "get_rolling_stopped":      ["can_rolling_stopped"],                     # owner/admin/dispatcher
    "get_vehicle_odometer":     ["can_vehicle_all", "can_vehicle_own"],          # all roles
    "get_drivers_list":         ["can_vehicle_all"],                           # all except driver
    "search_vehicles":          ["can_vehicle_all"],                           # all except driver
    "search_knowledge_base":    None,                                        # all roles
}

# Tools that are account-wide — driver must NOT call these even if permitted
# via can_*_own flags (they return data for ALL vehicles).
ACCOUNT_WIDE_TOOLS: frozenset[str] = frozenset({
    "get_low_fuel_vehicles", "get_vehicle_health",
    "get_weather", "get_efficiency_summary", "get_account_stats",
    "get_events_summary", "get_maintenance_summary",
    "get_fuel_cost_summary", "get_rolling_stopped",
    "get_drivers_list", "search_vehicles",
})

# Tools that accept a vehicle_name param and must enforce driver vehicle isolation.
VEHICLE_SPECIFIC_TOOLS: frozenset[str] = frozenset({
    "get_vehicle_faults", "get_vehicle_detail", "get_vehicle_location",
    "get_vehicle_events", "get_vehicle_maintenance", "get_vehicle_fuel_costs",
    "check_vehicle_camera", "get_vehicle_odometer",
})

# Legacy alias — keeps any external code that imports TRUCK_SPECIFIC_TOOLS working.
TRUCK_SPECIFIC_TOOLS: frozenset[str] = VEHICLE_SPECIFIC_TOOLS
