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
    driver       — assigned vehicle only, own fuel, own alerts
"""

from __future__ import annotations

import contextvars as _ctxvars
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
    can_fuel: bool = False           # /fuel
    can_efficiency: bool = False     # /efficiency
    can_health: bool = False         # /health
    can_vehicle_all: bool = False      # /vehicle <any>
    can_vehicle_vehicle: bool = False      # /vehicle <own> (driver)

    # Alerts inbox — DERIVED, never stored/toggled.  Every role HAS the inbox
    # (it's a system service); derive_service_perms() only sets the SCOPE from
    # the role's vehicle scope.  The field defaults below are placeholders the
    # resolver replaces.
    can_alerts_all: bool = False     # fleet-wide Alerts inbox  (derived)
    can_alerts_vehicle: bool = False     # own-vehicle Alerts inbox (derived)
    # Parking — own feature (NOT part of Alerts).  Defaults mirror the old
    # alerts/vehicle gate: everyone sees all, drivers see their assigned vehicle.
    can_parking_all: bool = True     # unsafe-parking events (all trucks)
    can_parking_vehicle: bool = False    # unsafe-parking events (assigned vehicle)
    # Cameras — own feature (NOT part of Faults).  Defaults mirror can_faults.
    can_cameras: bool = False        # dashcam footage viewer
    # AI assistant — DERIVED, never stored/toggled.  derive_service_perms()
    # forces this True (always-on service); per-tool gating lives in
    # TOOL_PERMISSIONS.  The default below is a placeholder.
    can_ai_chat: bool = True         # AI assistant chat + summary (derived)

    # Management
    can_invite: bool = False         # /invite
    can_manage_users: bool = False   # /users, /setrole, /remove
    can_manage_companies: bool = False    # /addcompany, /removecompany
    can_manage_vehicles: bool = False     # add/edit/remove vehicles in the registry
    # Loads (the load/shipment feature) — view-all vs own-scope vs manage,
    # the same split work orders / driver pay use.
    can_loads_all: bool = False       # view every load
    can_loads_own: bool = False       # view own loads (driver scope)
    can_manage_loads: bool = False    # add/edit/remove loads (see manage_all
                                      # for the scope: own vs any)
    # Write SCOPE for can_manage_loads.  Without this, a dispatcher manages
    # only loads they own (dispatcher_user_id == self) + the ones they
    # create; unassigned/other dispatchers' loads are off-limits.  WITH it
    # (owner/admin, or a delegated "dispatch manager") they manage ANY load.
    can_loads_manage_all: bool = False
    # KPI — the account-wide performance analytics surface (dispatcher
    # grades first; fleet/safety/driver sections later).  One shared page,
    # delegatable to any role via the matrix.
    can_kpi: bool = False
    can_manage_account: bool = False # /account settings (general config)
    # Settings components — granular delegation flags so account
    # administration can be split across roles (each Settings component
    # is independently grantable; see docs/FEATURES.md).
    can_manage_permissions: bool = False   # the Permissions matrix
    can_manage_integrations: bool = False  # telematics integrations
    can_manage_storage: bool = False       # storage backend + quota
    can_manage_work_hours: bool = False    # working-hours schedules

    # ── New features ──────────────────────────────────────────────
    can_geofence_all: bool = False      # geofence alerts (all trucks)
    can_geofence_vehicle: bool = False      # geofence alerts (assigned vehicle)
    can_digest: bool = True             # scheduled-report subscription (DERIVED — always on; see derive_service_perms)
    can_maintenance_all: bool = False   # maintenance scheduler (all trucks)
    can_maintenance_vehicle: bool = False   # maintenance scheduler (assigned vehicle)
    can_work_orders_all: bool = False   # shop-invoice work orders (all trucks)
    can_work_orders_vehicle: bool = False   # shop-invoice work orders (assigned vehicle)
    can_parts: bool = False             # parts catalog + per-part analytics (feature-owned gate; the WO editor's autocomplete read is shared — see features/parts)
    can_cost_reports: bool = False      # /cost-reports executive rollups (split off can_maintenance_all)
    can_scorecard_all: bool = False     # scorecards for all subjects (driver or vehicle)
    can_scorecard_vehicle: bool = False     # scorecards for the assigned vehicle(s) only
    can_manage_scorecard_rules: bool = False  # edit the scoring rules + pillar caps (Scorecards' admin component — was bundled in can_manage_account)
    can_location_map: bool = False      # live location map (all trucks)
    can_location_vehicle: bool = False      # live location map (assigned vehicle)
    can_fuel_cost: bool = False         # fuel cost tracker
    can_route_all: bool = False         # route replay (all trucks)
    can_route_vehicle: bool = False         # route replay (assigned vehicle)
    can_cost_per_mile: bool = False     # cost-per-mile dashboard
    can_events_all: bool = False        # safety events (all trucks)
    can_events_vehicle: bool = False        # safety events (assigned vehicle)
    can_manage_billing: bool = False    # the BILLING page (our charge to this account) — not Driver Pay
    can_manage_poi_layers: bool = False # create/edit/delete custom POI map layers (owner/admin/fleet)
    can_risk_report_all: bool = False   # generate Stakeholder Risk Summary for any subject
    can_risk_report_own: bool = False   # generate Stakeholder Risk Summary for own subject only
    can_driver_pay_admin: bool = False     # configure rules / trigger runs / view all paystubs
    can_driver_pay_view_own: bool = False  # view own paystub history (driver self-service)
    can_coaching_admin: bool = False    # manage coaching rules + assign manually + view all
    can_coaching_view_own: bool = False # see + acknowledge own coaching assignments
    # Driver Module — profile + document management.
    # Admin permission grants full CRUD on any driver in the account
    # (used by Workforce → Drivers admin page).  "Own" permission
    # lets a driver view their own profile + documents from the
    # miniapp (read-only in MVP; re-upload requests go to admin).
    can_manage_driver_docs: bool = False   # create / update / upload / delete for any driver
    can_driver_docs_own: bool = False      # read own profile + documents
    # Driver LIFECYCLE management — the Drivers feature's roster admin surface:
    # invite a driver, assign trucks, link Samsara/Datatruck/load identities,
    # provision-as-pending, activate/deactivate.  Distinct from
    # ``can_manage_users`` (STAFF administration): a fleet lead can run the
    # driver roster without holding office-user admin power.
    can_manage_drivers: bool = False
    # PTI (Pre-Trip Inspection) module — weekly photo-evidence
    # walkaround.  ``can_inspections_all`` lets fleet/safety review
    # submissions across the whole account; ``can_inspections_vehicle``
    # lets a driver complete + submit their own assigned vehicle.
    can_inspections_all: bool = False
    can_inspections_vehicle: bool = False
    # Recruiting — driver-application intake.  ``can_manage_applications``
    # gates the Applications dashboard + recruiting-link management;
    # ``can_convert_to_driver`` is the narrower right to turn an approved
    # applicant into a driver/invite WITHOUT the broad ``can_invite``
    # (so a recruiter can hire without being granted full user-invite
    # power).  The public applicant form needs neither — it's unauthed.
    can_manage_applications: bool = False
    can_convert_to_driver: bool = False
    # Carrier Knowledge Base — a recruiter-facing reference directory of the
    # external carriers the account recruits for (pre-qual criteria, sales
    # sheet, process notes).  ``can_carrier_directory`` reads it (recruiter +
    # manager); ``can_manage_carrier_directory`` is the edit right (manager
    # only).  Info-only — not wired to the apply flow or any other feature.
    can_carrier_directory: bool = False
    can_manage_carrier_directory: bool = False


# ─── Role → Permission Map ───────────────────────────────────────

ROLE_PERMISSIONS: dict[Role, FeatureSet] = {
    Role.OWNER: FeatureSet(
        can_faults=True, can_fuel=True, can_cameras=True,
        can_efficiency=True, can_health=True,
        can_vehicle_all=True, can_vehicle_vehicle=True,
        can_invite=True, can_manage_users=True,
        can_manage_companies=True, can_manage_vehicles=True, can_manage_account=True,
        can_loads_all=True, can_loads_own=True, can_manage_loads=True,
        can_loads_manage_all=True,   # owner/admin manage any load
        can_kpi=True,
        can_manage_permissions=True, can_manage_integrations=True,
        can_manage_storage=True, can_manage_work_hours=True,
        can_manage_scorecard_rules=True,   # Scorecards' admin component (owners delegate via the matrix)
        can_geofence_all=True, can_geofence_vehicle=True,
        can_parking_all=True, can_parking_vehicle=True,
        can_maintenance_all=True, can_maintenance_vehicle=True,
        can_work_orders_all=True, can_work_orders_vehicle=True,
        can_parts=True,
        can_cost_reports=True,
        can_scorecard_all=True, can_scorecard_vehicle=True,
        can_location_map=True, can_location_vehicle=True,
        can_fuel_cost=True,
        can_route_all=True, can_route_vehicle=True,
        can_cost_per_mile=True,
        can_events_all=True, can_events_vehicle=True,
        can_manage_billing=True,
        can_manage_poi_layers=True,
        can_risk_report_all=True, can_risk_report_own=True,
        can_driver_pay_admin=True, can_driver_pay_view_own=True,
        can_coaching_admin=True, can_coaching_view_own=True,
        can_manage_driver_docs=True, can_driver_docs_own=True,
        can_manage_drivers=True,
        can_inspections_all=True, can_inspections_vehicle=True,
        can_manage_applications=True, can_convert_to_driver=True,
        can_carrier_directory=True, can_manage_carrier_directory=True,
    ),
    Role.ADMIN: FeatureSet(
        can_faults=True, can_fuel=True, can_cameras=True,
        can_efficiency=True, can_health=True,
        can_vehicle_all=True, can_vehicle_vehicle=True,
        can_invite=True, can_manage_users=True,
        can_manage_companies=False, can_manage_vehicles=True, can_manage_account=False,
        can_loads_all=True, can_loads_own=True, can_manage_loads=True,
        can_loads_manage_all=True,   # owner/admin manage any load
        can_kpi=True,
        can_manage_permissions=False, can_manage_integrations=False,
        can_manage_storage=False, can_manage_work_hours=False,
        can_geofence_all=True, can_geofence_vehicle=True,
        can_maintenance_all=True, can_maintenance_vehicle=True,
        can_work_orders_all=True, can_work_orders_vehicle=True,
        can_parts=True,
        can_cost_reports=True,
        can_scorecard_all=True, can_scorecard_vehicle=True,
        can_location_map=True, can_location_vehicle=True,
        can_fuel_cost=True,
        can_route_all=True, can_route_vehicle=True,
        can_cost_per_mile=True,
        can_events_all=True, can_events_vehicle=True,
        can_manage_billing=True,
        can_manage_poi_layers=True,
        can_risk_report_all=True, can_risk_report_own=True,
        can_driver_pay_admin=True, can_driver_pay_view_own=True,
        can_coaching_admin=True, can_coaching_view_own=True,
        can_manage_driver_docs=True, can_driver_docs_own=True,
        can_manage_drivers=True,
        can_inspections_all=True, can_inspections_vehicle=True,
        can_manage_applications=True, can_convert_to_driver=True,
        can_carrier_directory=True, can_manage_carrier_directory=True,
    ),
    Role.FLEET: FeatureSet(
        can_faults=True, can_fuel=True, can_cameras=True,
        can_efficiency=True, can_health=True,
        can_vehicle_all=True, can_vehicle_vehicle=True,
        can_invite=False, can_manage_users=False,
        can_manage_companies=False, can_manage_vehicles=True, can_manage_account=False,
        # Loads is a Dispatch-owned feature (dispatcher CRUDs, KPI grades on
        # can_kpi, driver pay reads loads server-side) — Fleet has no loads
        # consumer, so it is NOT granted here.  Left at the FeatureSet default
        # (False) rather than seeded True.
        can_geofence_all=True, can_geofence_vehicle=True,
        can_maintenance_all=True, can_maintenance_vehicle=True,
        can_work_orders_all=True, can_work_orders_vehicle=True,
        can_parts=True,
        can_cost_reports=True,
        can_scorecard_all=True, can_scorecard_vehicle=True,
        can_location_map=True, can_location_vehicle=True,
        can_fuel_cost=True,
        can_route_all=True, can_route_vehicle=True,
        can_cost_per_mile=True,
        can_events_all=True, can_events_vehicle=True,
        can_manage_poi_layers=True,
        can_risk_report_all=False, can_risk_report_own=True,
        can_driver_pay_admin=False, can_driver_pay_view_own=False,
        can_coaching_admin=True, can_coaching_view_own=False,
        # Fleet managers handle driver records day-to-day (assignments,
        # CDL renewals) so they get the admin permission too.
        can_manage_driver_docs=True, can_driver_docs_own=False,
        can_manage_drivers=True,   # runs the driver roster (trucks, TMS links)
        can_inspections_all=True, can_inspections_vehicle=False,
    ),
    Role.SAFETY: FeatureSet(
        can_faults=True, can_fuel=False, can_cameras=True,
        can_efficiency=False, can_health=True,
        can_vehicle_all=True, can_vehicle_vehicle=True,
        can_invite=False, can_manage_users=False,
        can_manage_companies=False, can_manage_account=False,
        can_geofence_all=True, can_geofence_vehicle=True,
        can_maintenance_all=True, can_maintenance_vehicle=True,
        can_work_orders_all=True, can_work_orders_vehicle=True,
        can_scorecard_all=True, can_scorecard_vehicle=True,
        can_location_map=True, can_location_vehicle=True,
        can_fuel_cost=False,
        can_route_all=True, can_route_vehicle=True,
        can_cost_per_mile=False,
        can_events_all=True, can_events_vehicle=True,
        can_risk_report_all=True, can_risk_report_own=True,
        can_driver_pay_admin=False, can_driver_pay_view_own=False,
        can_coaching_admin=True, can_coaching_view_own=False,
        # Safety needs read-only access for compliance checks (CDL /
        # medical card expirations) — read-only via the admin route
        # is fine for MVP; a future ``can_view_driver_docs`` could
        # split read from write if needed.
        can_manage_driver_docs=True, can_driver_docs_own=False,
        can_inspections_all=True, can_inspections_vehicle=False,
    ),
    Role.DISPATCHER: FeatureSet(
        can_faults=False, can_fuel=True,
        can_efficiency=False, can_health=False,
        can_vehicle_all=True, can_vehicle_vehicle=True,
        can_loads_all=True, can_loads_own=True, can_manage_loads=True,  # can_loads_manage_all stays False → own-scope
        # Dispatchers need the geofence and safety-event features (granted
        # below) to react to deviations mid-shift.  Those alerts surface in the
        # always-on Alerts inbox every role has — the features decide WHICH
        # alerts show, not whether the inbox exists.
        can_invite=False, can_manage_users=False,
        can_manage_companies=False, can_manage_account=False,
        can_geofence_all=True, can_geofence_vehicle=True,
        can_maintenance_all=False, can_maintenance_vehicle=False,
        can_scorecard_all=True, can_scorecard_vehicle=True,
        can_location_map=True, can_location_vehicle=True,
        can_fuel_cost=False,
        can_route_all=True, can_route_vehicle=True,
        can_cost_per_mile=False,
        can_events_all=True, can_events_vehicle=True,
        can_risk_report_all=False, can_risk_report_own=False,
        can_driver_pay_admin=False, can_driver_pay_view_own=False,
        can_coaching_admin=False, can_coaching_view_own=False,
        can_inspections_all=True, can_inspections_vehicle=False,
    ),
    Role.HR: FeatureSet(
        # HR persona — people management.  Focus: driver compliance,
        # coaching, onboarding, audit trail.  No vehicle-ops or
        # financial perms.
        can_invite=True,                       # Onboarding new users
        can_manage_users=True,                 # User admin
        can_coaching_admin=True,               # Training / coaching workflows
        can_manage_driver_docs=True,           # CDL / medical / docs
        can_manage_drivers=True,               # driver roster (onboarding is HR's job)
        can_inspections_all=True,              # PTI review for compliance audit
        # Read-only context — HR needs to see WHO is doing WHAT,
        # not edit fleet ops:
        can_vehicle_all=True,                  # Which vehicle a driver is on
        can_location_map=True,                 # Where drivers are right now
        can_events_all=True,                   # Safety events drive coaching
        can_scorecard_all=True,                # Driver behaviour insight
        can_risk_report_all=True,              # Personnel risk reporting
        can_geofence_all=True,                 # See geofence context for incidents
    ),
    Role.ACCOUNTING: FeatureSet(
        # Accounting persona — money management.  Focus: billing,
        # cost analytics, driver pay, financial reports.  No driver
        # admin or vehicle-ops controls.
        can_manage_billing=True,               # Billing & subscriptions
        can_fuel=True,                         # Fuel report
        can_fuel_cost=True,                    # Fuel cost tracker
        can_cost_per_mile=True,                # CPM dashboard
        can_driver_pay_admin=True,                # Driver Pay runs + history
        can_efficiency=True,                   # Efficiency report for cost analysis
        # Cost rollups by truck — used to be granted via the overloaded
        # ``can_maintenance_all`` flag; split into its own gate in
        # 2026-06 so toggling Maintenance for accounting no longer
        # silently also affects Cost Reports access.
        can_cost_reports=True,
        # Read-only context — accounting needs to see WHICH assets
        # generate WHICH costs:
        can_vehicle_all=True,                  # Vehicle list for asset accounting
    ),
    Role.DRIVER: FeatureSet(
        can_faults=False, can_fuel=False,
        can_efficiency=False, can_health=False,
        can_vehicle_all=False, can_vehicle_vehicle=True,
        can_loads_own=True,
        can_invite=False, can_manage_users=False,
        can_manage_companies=False, can_manage_account=False,
        can_geofence_all=False, can_geofence_vehicle=True,
        can_maintenance_all=False, can_maintenance_vehicle=True,
        can_work_orders_all=False, can_work_orders_vehicle=True,
        can_parking_all=False, can_parking_vehicle=True,  # driver: assigned vehicle only
        can_scorecard_all=False, can_scorecard_vehicle=True,
        can_location_map=False, can_location_vehicle=True,
        can_fuel_cost=False,
        can_route_all=False, can_route_vehicle=True,
        can_cost_per_mile=False,
        can_events_all=False, can_events_vehicle=True,
        can_risk_report_all=False, can_risk_report_own=True,
        can_driver_pay_admin=False, can_driver_pay_view_own=True,
        can_coaching_admin=False, can_coaching_view_own=True,
        # Drivers see their own profile + documents (read-only); they
        # never see other drivers' records.
        can_manage_driver_docs=False, can_driver_docs_own=True,
        can_inspections_all=False, can_inspections_vehicle=True,
    ),
    # RECRUITER — driver acquisition / onboarding.  Operationally a
    # driver-equivalent baseline (no fleet ops / costs / admin) PLUS the
    # two recruiting rights granted by default so the role is USABLE out of
    # the box: can_manage_applications (the applications surface) and
    # can_convert_to_driver (hire → driver invite).  Owners can still
    # tighten or widen any flag per account from the Permissions matrix
    # (e.g. revoke can_convert_to_driver for a screening-only recruiter).
    # Hierarchy rank is 2 (not 1 like driver) so a matrix-granted
    # can_invite actually lets the recruiter invite drivers.
    Role.RECRUITER: FeatureSet(
        can_faults=False, can_fuel=False,
        can_efficiency=False, can_health=False,
        can_vehicle_all=False, can_vehicle_vehicle=True,
        can_invite=False, can_manage_users=False,
        can_manage_companies=False, can_manage_account=False,
        can_geofence_all=False, can_geofence_vehicle=True,
        can_maintenance_all=False, can_maintenance_vehicle=True,
        can_work_orders_all=False, can_work_orders_vehicle=True,
        can_parking_all=False, can_parking_vehicle=True,
        can_scorecard_all=False, can_scorecard_vehicle=True,
        can_location_map=False, can_location_vehicle=True,
        can_fuel_cost=False,
        can_route_all=False, can_route_vehicle=True,
        can_cost_per_mile=False,
        can_events_all=False, can_events_vehicle=True,
        can_risk_report_all=False, can_risk_report_own=True,
        can_driver_pay_admin=False, can_driver_pay_view_own=True,
        can_coaching_admin=False, can_coaching_view_own=True,
        can_manage_driver_docs=False, can_driver_docs_own=True,
        can_inspections_all=False, can_inspections_vehicle=True,
        # Recruiting IS the recruiter's defining function — granted by
        # default so the role is usable out of the box.  Owners can
        # narrow to screening-only by revoking can_convert_to_driver in
        # the Permissions matrix.
        can_manage_applications=True, can_convert_to_driver=True,
        can_carrier_directory=True,   # read the carrier directory (managers also edit)
    ),
}


# ─── Per-user role TIERS (seniority layered on the base role) ─────────
# A "tier" is NOT a separate role — it's a per-user status (``is_manager`` on
# the membership) layered on the base role.  The senior tier gets the base
# role's FeatureSet PLUS the tier's extra flags.  This keeps the Role enum
# from doubling and gives every feature a single ``is_manager`` signal.
#
# TIER_GRANTS is the ONLY definition of "what the senior tier of this role
# adds" + its display labels.  A role absent here has no tier (no toggle).
# The senior tier is DERIVED (base | grants), so it can never drift below base.
#
#   • recruiter → Manager / Employee: adds can_invite (build the recruiter
#     team; the invites feature restricts the target) + can_manage_carrier_directory.
#   • admin → Full admin / Standard admin: adds the account-administration
#     flags a standard admin lacks (integrations, storage, the permissions
#     matrix, general settings, billing) — the Owner↔Admin gap, grantable
#     per-user without minting an owner.
@dataclass(frozen=True)
class RoleTier:
    senior_label: str
    base_label: str
    grants: frozenset[str]


TIER_GRANTS: dict[Role, RoleTier] = {
    Role.RECRUITER: RoleTier(
        senior_label="Manager", base_label="Employee",
        grants=frozenset({"can_invite", "can_manage_carrier_directory"}),
    ),
    Role.ADMIN: RoleTier(
        senior_label="Full admin", base_label="Standard admin",
        # The account-administration flags a STANDARD admin lacks (a standard
        # admin already has billing + fleet ops + user management).  Full admin
        # closes most of the Owner↔Admin gap without minting an owner.
        grants=frozenset({
            "can_manage_integrations", "can_manage_storage",
            "can_manage_permissions", "can_manage_account",
            "can_manage_work_hours",
        }),
    ),
    # Department team leads — each adds the lead-only rights its base role
    # lacks.  These are SEED defaults: owners retune every tier per-account in
    # the Permissions matrix (each tier is its own stored row).  Managers with
    # can_invite may invite ONLY their own role (features/settings/invites:
    # MANAGER_INVITE_ONLY), and invited users arrive as plain employees.
    Role.FLEET: RoleTier(
        senior_label="Manager", base_label="Employee",
        grants=frozenset({
            "can_invite", "can_manage_work_hours", "can_risk_report_all",
        }),
    ),
    Role.SAFETY: RoleTier(
        senior_label="Manager", base_label="Employee",
        # The safety lead owns the scoring config (rules + pillar caps).
        grants=frozenset({"can_manage_scorecard_rules", "can_invite"}),
    ),
    Role.DISPATCHER: RoleTier(
        senior_label="Manager", base_label="Employee",
        # The dispatch lead builds shift schedules + curates map layers.
        grants=frozenset({
            "can_manage_work_hours", "can_manage_poi_layers", "can_invite",
        }),
    ),
    Role.HR: RoleTier(
        senior_label="Manager", base_label="Employee",
        # HR base already invites + manages users; the lead adds schedule
        # ownership + recruiting-pipeline oversight.
        grants=frozenset({
            "can_manage_work_hours", "can_manage_applications",
            "can_convert_to_driver",
        }),
    ),
    Role.ACCOUNTING: RoleTier(
        senior_label="Manager", base_label="Employee",
        # The accounting lead can review the shop invoices / maintenance
        # records behind the cost numbers (parts analytics included).
        grants=frozenset({"can_work_orders_all", "can_maintenance_all",
                          "can_parts"}),
    ),
}

# Back-compat alias: role → the senior tier's extra flags.  Some call sites +
# the /admin/permissions/roles endpoint reference MANAGER_GRANTS.
MANAGER_GRANTS: dict[Role, frozenset[str]] = {
    r: t.grants for r, t in TIER_GRANTS.items()
}


def role_tier(role: Role | str) -> Optional[RoleTier]:
    """The tier spec (labels + grants) for a role, or None if it has no tier."""
    try:
        r = role if isinstance(role, Role) else Role(role)
    except (ValueError, KeyError):
        return None
    return TIER_GRANTS.get(r)


def role_supports_manager(role: Role | str) -> bool:
    """True if this role has a senior tier (a Team-Management toggle is offered)."""
    return role_tier(role) is not None


def apply_manager_grants(fs: FeatureSet, role: Role | str, is_manager: bool) -> FeatureSet:
    """Overlay a role's senior-tier grants onto *fs* when *is_manager*.

    Pure + idempotent.  A no-op for the base tier, roles with no tier, or when
    *is_manager* is False.  Applied at the USER boundary (after the role-keyed
    ``get_account_permissions``), never inside the role cache — the cache stays
    the shared base-tier baseline; the per-user senior delta is layered per
    request.
    """
    if not is_manager:
        return fs
    tier = role_tier(role)
    if not tier or not tier.grants:
        return fs
    from dataclasses import replace
    return replace(fs, **{flag: True for flag in tier.grants})


async def get_user_permissions(
    role: Role,
    account_id: int,
    is_manager: bool = False,
    is_primary_owner: bool = False,
    company_id: Optional[int] = None,
) -> FeatureSet:
    """Account-aware permission set for a specific USER (role + tier).

    Each tier reads its OWN stored row, independently editable:
      * **Owner** splits by ``is_primary_owner`` — the PRIMARY owner is the
        full, owner-protected ``"owner"`` row; a CO-OWNER resolves the separate
        ``"owner__co"`` row (seeded from full owner, but NOT owner-protected so
        the primary can restrict it independently).
      * **Senior tier** (Full admin / Manager) reads ``{role}__manager`` (seed
        = base+grants).
      * Otherwise the plain base-role perms.

    Use this wherever a concrete user's effective permissions are needed
    (request auth, /me); use ``get_account_permissions`` for account-agnostic,
    role-level surfaces (which always yield the base/primary set).
    """
    r = role.value if hasattr(role, "value") else role
    if r == "owner" and not is_primary_owner:
        # Co-owner: own row, seeded from the full owner default, NOT
        # owner-protected (protect_role "_" is never "owner") so the primary
        # owner can restrict it independently of their own (primary) row.
        return await _resolve_perms(
            account_id, "owner__co", ROLE_PERMISSIONS.get(Role.OWNER, FeatureSet()),
            company_id, "_",
        )
    if is_manager and role_supports_manager(role):
        key = perm_tier_key(role, True)
        return await _resolve_perms(
            account_id, key, senior_default_featureset(role), company_id, role,
        )
    return await get_account_permissions(role, account_id, company_id)


# ─── Owner lockout protection ─────────────────────────────────────
# The account owner is the ultimate authority and must NEVER be able to
# revoke — from themselves — the account-control permissions that are the
# only way back from a misconfiguration.  Even if a stored DB override
# (or a malformed API call) sets these False for the owner role, the
# resolver below forces them back on.  This is what lets the Role
# Permissions matrix safely let an owner hide *operational* features from
# their own view without ever risking a self-lockout.
OWNER_PROTECTED_PERMS: frozenset[str] = frozenset({
    "can_manage_account",    # gates Settings general config + Modules
    "can_manage_permissions",  # the matrix itself — losing it = lockout
    "can_manage_users",      # gates Team Management + Audit Log
    "can_manage_billing",    # gates Billing
    "can_manage_companies",  # gates Companies
})


def _protect_owner(role: Role, fs: FeatureSet) -> FeatureSet:
    """Force the owner's escape-hatch permissions on, ignoring overrides."""
    role_str = role.value if hasattr(role, "value") else role
    if role_str != "owner":
        return fs
    from dataclasses import replace
    return replace(fs, **{k: True for k in OWNER_PROTECTED_PERMS})


# ─── Derived service surfaces (Alerts, AI assistant, Reports) ─────
# Alerts, the AI assistant, and the Reports hub are always-on infrastructure
# SERVICES, present for EVERY role — never owner-toggled features.  The owner
# can only narrow what FLOWS THROUGH a service by disabling features (disable
# Faults → no faults alerts in the inbox / no Faults report tab; the per-item
# gating lives in capabilities/alerting/relevance.py for alert *types*,
# TOOL_PERMISSIONS for AI *tools*, and the per-report-type flags for the
# Reports tabs), but the service surface itself — the inbox, the assistant,
# the Reports page + its scheduled-report subscription — is always there.  So
# these flags are DERIVED, never stored or shown as a matrix row; they appear
# read-only in a "System Services" panel instead.

# Service-surface flags that are DERIVED, never persisted or owner-toggled.
# The Permissions matrix must not offer these as editable rows (they live in a
# read-only "System Services" panel instead) and the save endpoint strips them
# from the stored override row.  Note: the report TYPES (can_risk_report_*,
# can_cost_reports) are NOT here — they're genuine per-role features that gate
# individual Reports tabs and live in the matrix under their owning department.
DERIVED_SERVICE_FIELDS: frozenset[str] = frozenset({
    "can_alerts_all", "can_alerts_vehicle", "can_ai_chat", "can_digest",
})


def derive_service_perms(fs: FeatureSet) -> FeatureSet:
    """Compute the always-on service permissions for any role.

    Returns a copy of *fs* with the derived service-surface flags overwritten:

      * ``can_ai_chat`` — always True; the AI assistant is available to every
        role (its per-tool gating lives in ``TOOL_PERMISSIONS``).
      * ``can_digest`` — always True; every role can manage its scheduled-
        report subscription.  The Reports hub is a system service; WHICH
        report tabs/digests a role actually sees follows its per-report-type
        feature flags (can_risk_report_*, can_cost_reports, etc.).
      * Alerts inbox — every role HAS the inbox; it is a system service, not a
        feature, so it is never withheld.  Only the *scope* is derived, from
        the role's vehicle scope: account-wide visibility (``can_vehicle_all``)
        → fleet-wide inbox (``can_alerts_all``); otherwise the own-vehicle
        inbox (``can_alerts_vehicle``).  The two scopes are mutually exclusive,
        and ``require_permission_any(can_alerts_all, can_alerts_vehicle)``
        matches ``_all`` first, so a fleet-wide role never needs the vehicle
        flag.  WHAT the inbox shows is gated per-feature downstream
        (relevance.py); a role with no alert-bearing features simply sees an
        empty inbox — the surface is still there.

    Applied as the LAST step of every resolver (after module masking).  Note
    the inbox scope tracks vehicle scope, so even disabling the Fleet/Vehicles
    module (which masks ``can_vehicle_all``) only NARROWS the inbox to
    own-vehicle scope — it never removes the inbox.

    The inbox is a *vehicle*-alerts surface, so it requires SOME vehicle
    visibility — every real role has either account-wide or own-vehicle scope,
    so every real role gets it.  Only a malformed/unknown role with no vehicle
    scope at all (the ``ROLE_PERMISSIONS.get(role, FeatureSet())`` fallback)
    gets no inbox, preserving the "unknown role grants nothing" contract.
    """
    from dataclasses import replace
    has_vehicle = bool(fs.can_vehicle_all or fs.can_vehicle_vehicle)
    return replace(
        fs,
        can_ai_chat=True, can_digest=True,
        can_alerts_all=bool(fs.can_vehicle_all),
        can_alerts_vehicle=has_vehicle and not bool(fs.can_vehicle_all),
    )


def get_permissions(role: Role) -> FeatureSet:
    """Get the hardcoded ROLE-DEFAULT permission set (sync, no DB).

    ⚠️  ROLE DEFAULTS ONLY.  This IGNORES per-account permission overrides
    (the Role Permissions matrix) AND module-disablement masking.  Using it
    in any path that decides what an authenticated user may DO or SEE is a
    silent authorization bypass — that is exactly the bug that let the AI
    tool gate serve data an account had revoked (see
    test_ai_tool_account_aware_perms.py).

    In request / tool / handler paths use the account-aware resolvers:
        await can_for_account(account_id, role, "can_...")        # one flag
        await get_account_permissions(role, account_id)           # full set

    This sync default is appropriate ONLY for account-agnostic surfaces
    (e.g. building a static menu skeleton) and as an explicit fallback when
    no account_id is available.

    The derived service surfaces (Alerts inbox, AI assistant) are computed
    from the feature defaults here too, so a caller reading
    ``get_permissions(role).can_alerts_all`` sees the same value the
    account-aware resolver would produce for a default-configured account.
    """
    return derive_service_perms(ROLE_PERMISSIONS.get(role, FeatureSet()))


async def _apply_module_mask(fs: FeatureSet, account_id: int) -> FeatureSet:
    """Force a disabled department's flags off (one hiding mechanism).

    Reads the account's ``disabled_modules`` and masks every flag whose
    owning module(s) are all turned off — so a disabled module hides its
    features *through* the permission system (nav + API), not via a
    separate filter.  Fail-open: on any error returns *fs* unmasked, so a
    transient DB hiccup never silently strips access.
    """
    try:
        from infra.platform import get_platform_db
        from capabilities.permissions.modules import mask_disabled_modules
        acct = await get_platform_db().get_account(account_id)
        disabled = getattr(acct, "disabled_modules", "") if acct else ""
        return mask_disabled_modules(fs, disabled)
    except Exception as e:
        logger.debug("Module mask skipped (using unmasked perms): %s", e)
        return fs


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

    Results are cached per (account_id, role, company_id) for
    ``_PERMS_CACHE_TTL_S`` seconds.  The TTL is the contract for
    multi-worker deployments: a worker that didn't receive the
    invalidation signal still ages out its stale entry within one
    TTL window.  Within a single worker, ``invalidate_permissions_cache``
    drops the entry immediately so the Owner-saving worker sees fresh
    state on the very next call.
    """
    role_str = role.value if hasattr(role, "value") else role
    return await _resolve_perms(
        account_id, role_str, ROLE_PERMISSIONS.get(role, FeatureSet()),
        company_id, role,
    )


async def _resolve_perms(
    account_id: int,
    role_key: str,
    default_fs: FeatureSet,
    company_id: Optional[int],
    protect_role: Role | str,
) -> FeatureSet:
    """Resolve + cache the stored permission set for one ROLE KEY.

    ``role_key`` is the storage key — a base role (``"admin"``) OR a tier key
    (``"admin__manager"``).  ``default_fs`` seeds any missing field (base-role
    defaults, or base+grants for a senior tier).  Cached per
    ``(account_id, role_key, company_id)`` so base + senior tiers cache apart.
    """
    import time as _time
    from dataclasses import asdict as _asdict
    cache_key = (account_id, role_key, company_id)
    cached = _permissions_cache.get(cache_key)
    now = _time.monotonic()
    if cached is not None:
        expires_at, fs = cached
        if expires_at > now:
            return fs
        # Stale — drop and re-resolve.  Permission staleness is a security
        # concern, so no stale-while-revalidate.

    try:
        from infra.platform import get_platform_db
        pdb = get_platform_db()
        perm_dict = await pdb.get_role_permissions(account_id, role_key, company_id)
        if perm_dict is not None:
            # Start from the seed defaults so newly-added permission fields get
            # their correct default even when the stored row predates the field.
            known_fields = {f.name for f in FeatureSet.__dataclass_fields__.values()}
            seed = _asdict(default_fs)
            filtered = {k: v for k, v in perm_dict.items() if k in known_fields}
            merged = {**seed, **filtered}
            fs = _protect_owner(protect_role, FeatureSet(**merged))
            fs = await _apply_module_mask(fs, account_id)
            # Derive always-on service surfaces LAST (after module mask).
            fs = derive_service_perms(fs)
            _permissions_cache[cache_key] = (now + _PERMS_CACHE_TTL_S, fs)
            return fs
    except Exception as e:
        logger.debug("Could not load permissions from DB (using defaults): %s", e)

    fs = _protect_owner(protect_role, default_fs)
    fs = await _apply_module_mask(fs, account_id)
    fs = derive_service_perms(fs)
    _permissions_cache[cache_key] = (now + _PERMS_CACHE_TTL_S, fs)
    return fs


# ─── Tier storage keys + seed defaults ────────────────────────────
def perm_tier_key(role: Role | str, is_manager: bool) -> str:
    """Storage key for a (role, tier): the base role, or ``{role}__manager``
    for the senior tier of a tiered role."""
    r = role.value if isinstance(role, Role) else role
    return f"{r}__manager" if (is_manager and role_supports_manager(r)) else r


def senior_default_featureset(role: Role | str) -> FeatureSet:
    """Seed defaults for a role's SENIOR tier: base role defaults + the tier's
    grants.  Owners then edit it independently (stored under its own key)."""
    r = role if isinstance(role, Role) else Role(role)
    base = ROLE_PERMISSIONS.get(r, FeatureSet())
    tier = role_tier(r)
    if not tier or not tier.grants:
        return base
    from dataclasses import replace
    return replace(base, **{flag: True for flag in tier.grants})


def invalidate_permissions_cache(
    account_id: Optional[int] = None,
) -> None:
    """Clear cached permissions. Call after Owner edits role permissions.

    Single-worker case: this drops the cache entry immediately so the
    next call re-resolves from DB.  Multi-worker case: only THIS
    process's cache is cleared — sibling workers still hold the old
    entry until its TTL expires (``_PERMS_CACHE_TTL_S``), which is
    the acceptable worst-case staleness for permission changes.
    """
    if account_id is None:
        _permissions_cache.clear()
    else:
        keys_to_drop = [k for k in _permissions_cache if k[0] == account_id]
        for k in keys_to_drop:
            del _permissions_cache[k]
    # The AI advertised-tool list is derived from these permissions, so drop
    # it too — otherwise the model keeps being shown a tool after its feature
    # was revoked, until that cache's own TTL expires.  Lazy import avoids a
    # circular dependency (registry → permissions).
    try:
        from capabilities.ai.tools.registry import invalidate_tool_cache
        invalidate_tool_cache(account_id)
    except Exception:
        pass


# TTL on cached permission entries.  Bounds the cross-worker
# staleness window for any deployment running multiple FastAPI
# workers — the worker that handled the Owner's PUT invalidates its
# own cache immediately, sibling workers age out within this many
# seconds.  60s is the operational sweet spot: short enough that an
# Owner change reaches the whole fleet within a minute, long enough
# that the DB isn't hit on every authed request (a busy account hits
# ``can_*`` checks hundreds of times per second).
_PERMS_CACHE_TTL_S: float = 60.0

# In-memory cache: (account_id, role_str, company_id) → (expires_at_monotonic, FeatureSet)
_permissions_cache: dict[tuple, tuple[float, FeatureSet]] = {}


# ─── Active-account contextvar (for per-account `can()` lookups) ──────
# When set (typically by the bot's _require_registered decorator at
# handler entry), the sync ``can()`` function below resolves through
# the per-account permissions cache instead of the hardcoded defaults.
# Pre-priming the cache via ``prime_account_permissions`` is what
# makes a sync check honour DB overrides without changing 101 call
# sites in the bot to ``await can_for_account(...)``.
#
# Setting the var to None means "no account context" — ``can()`` falls
# back to the Python defaults exactly as it did before this addition.
_active_account_id: _ctxvars.ContextVar[Optional[int]] = _ctxvars.ContextVar(
    "iam_active_account_id", default=None,
)


async def prime_account_permissions(account_id: int, role: Role) -> None:
    """Warm the per-account permission cache and bind it to this task tree.

    Call once at the top of an async handler (e.g. inside the bot's
    ``_require_registered`` decorator).  After this returns, every
    downstream sync ``can(role, "flag")`` call in the same task picks
    up the per-account permission overrides without an explicit await.

    Cheap when already cached — a hit returns in microseconds.  Safe to
    call multiple times.
    """
    await get_account_permissions(role, account_id, None)
    _active_account_id.set(account_id)


def can(role: Role, feature: str) -> bool:
    """Check if a role has a specific feature permission.

    Resolution order:
      1. If an active account context is set (see
         :func:`prime_account_permissions`) AND the per-account
         permission cache holds an entry for ``(account_id, role)``,
         use that.  This is how the bot honors per-account overrides
         set via the dashboard's Role Permissions admin page without
         every call-site needing ``await``.
      2. Otherwise, fall back to the hardcoded ``ROLE_PERMISSIONS``
         defaults — same behavior as before this change.

    For surfaces that already operate in an async context with a known
    account_id (FastAPI deps, ad-hoc scripts), prefer
    :func:`can_for_account` — it's explicit and doesn't rely on the
    contextvar being primed upstream.

    Usage:  can(user.role, "can_faults")
    """
    aid = _active_account_id.get()
    if aid is not None:
        import time as _time
        role_str = role.value if hasattr(role, "value") else role
        cached = _permissions_cache.get((aid, role_str, None))
        if cached is not None:
            expires_at, fs = cached
            if expires_at > _time.monotonic():
                return bool(getattr(fs, feature, False))
            # Stale cache entry — fall through to hardcoded defaults.
            # The contextvar-primed sync path can't await a fresh DB
            # read, so the next call to ``can_for_account`` (or any
            # async-context caller through ``get_account_permissions``)
            # will repopulate the cache from DB on its TTL refresh.
    perms = get_permissions(role)
    return getattr(perms, feature, False)


async def can_for_account(
    account_id: int,
    role: Role,
    feature: str,
    company_id: Optional[int] = None,
) -> bool:
    """Account-aware permission check honoring DB overrides.

    Async twin of :func:`can` — resolves through
    :func:`get_account_permissions` so per-account customizations
    (set via the dashboard's Role Permissions admin page) take effect.
    Falls back to the hardcoded ``ROLE_PERMISSIONS`` defaults when no
    override exists for this account.

    Cached per ``(account_id, role, company_id)`` in
    ``_permissions_cache``; cache is invalidated by
    :func:`invalidate_permissions_cache` whenever an admin saves a
    permission change, so cleared values propagate within the same
    process on the next call.

    Usage::
        if not await can_for_account(user.account_id, user.role, "can_faults"):
            ...

    Surfaces that should call this instead of :func:`can`:
      * Bot handlers (interfaces/bot/*) — was previously stuck on
        Python defaults; this closes the SSOT gap.
      * Long-running scheduler jobs that operate on behalf of one
        account at a time.
    """
    perms = await get_account_permissions(role, account_id, company_id)
    return bool(getattr(perms, feature, False))


# ─── Role Display Helpers ─────────────────────────────────────────

ROLE_DISPLAY: dict[Role, str] = {
    Role.OWNER:       "👑 Owner",
    Role.ADMIN:       "🔑 Admin",
    Role.FLEET:      "🔧 Fleet",
    Role.SAFETY:      "🛡️ Safety",
    Role.DISPATCHER:  "📡 Dispatcher",
    Role.HR:          "👥 HR",
    Role.ACCOUNTING:  "💰 Accounting",
    Role.RECRUITER:   "🤝 Recruiter",
    Role.DRIVER:      "🚛 Driver",
}

ROLE_EMOJI: dict[Role, str] = {
    Role.OWNER:       "👑",
    Role.ADMIN:       "🔑",
    Role.FLEET:      "🔧",
    Role.SAFETY:      "🛡️",
    Role.DISPATCHER:  "📡",
    Role.HR:          "👥",
    Role.ACCOUNTING:  "💰",
    Role.RECRUITER:   "🤝",
    Role.DRIVER:      "🚛",
}


def role_display(role: Role) -> str:
    return ROLE_DISPLAY.get(role, str(role.value))


def role_emoji(role: Role) -> str:
    return ROLE_EMOJI.get(role, "👤")


# ─── AI Briefing focus (dynamic, auto-synced with permissions) ────

# Feature → briefing-topic map, keyed by PERMISSION FLAG — deliberately not by
# role.  A role's briefing focus is derived from its effective (per-account,
# module-masked) permissions, so:
#   • every role — current or future — gets its own briefing for free;
#   • adding a new feature = adding ONE line here (alongside its
#     TOOL_PERMISSIONS / _FEATURE_LABELS entries) and every role holding the
#     flag automatically starts seeing it in briefings;
#   • an Owner disabling a feature for a role in the Permissions matrix also
#     removes it from that role's briefing — one source of truth.
# Order = priority order in the briefing prompt.  Both the *_all and
# *_vehicle scope flags map to the same topic; duplicates collapse.
BRIEFING_TOPICS: tuple[tuple[str, str], ...] = (
    ("can_location_map",       "current vehicle movement and locations"),
    ("can_location_vehicle",   "current vehicle movement and locations"),
    ("can_vehicle_all",        "which vehicles are rolling, idling, or parked"),
    ("can_route_all",          "routes and vehicle availability"),
    ("can_route_vehicle",      "routes and vehicle availability"),
    ("can_parking_all",        "unsafe parking events"),
    ("can_parking_vehicle",    "unsafe parking events"),
    ("can_faults",             "active fault codes"),
    ("can_health",             "vehicle health (battery, coolant, oil, DEF)"),
    ("can_fuel",               "fuel levels"),
    ("can_maintenance_all",    "pending and overdue maintenance"),
    ("can_maintenance_vehicle","pending and overdue maintenance"),
    ("can_work_orders_all",    "recent work orders and shop costs"),
    ("can_work_orders_vehicle","recent work orders and shop costs"),
    ("can_inspections_all",    "PTI inspections"),
    ("can_inspections_vehicle","PTI inspections"),
    ("can_events_all",         "safety events"),
    ("can_events_vehicle",     "safety events"),
    ("can_scorecard_all",      "driver scorecards"),
    ("can_scorecard_vehicle",  "driver scorecards"),
    ("can_coaching_admin",     "the driver-coaching backlog"),
    ("can_efficiency",         "driver efficiency (MPG, idle time)"),
    ("can_fuel_cost",          "fuel spend"),
    ("can_cost_per_mile",      "cost per mile"),
    ("can_alerts_all",         "open alerts"),
    ("can_alerts_vehicle",     "open alerts"),
    ("can_manage_applications","the driver-application (hiring) pipeline"),
)


async def briefing_focus_for_account(role_str: str, account_id: int) -> list[str]:
    """Resolve the briefing focus areas for *role_str* on *account_id*.

    Reads the role's EFFECTIVE permissions (per-account matrix overrides +
    department-module masking via :func:`get_account_permissions`) and maps
    them through :data:`BRIEFING_TOPICS`.  Returns an ordered, de-duplicated
    topic list; empty when the role is unknown (caller falls back to a
    generic briefing).
    """
    try:
        role = Role(role_str)
    except (ValueError, TypeError):
        return []
    perms = await get_account_permissions(role, account_id)
    seen: set[str] = set()
    topics: list[str] = []
    for flag, topic in BRIEFING_TOPICS:
        if topic not in seen and getattr(perms, flag, False):
            seen.add(topic)
            topics.append(topic)
    return topics


# ─── AI Role Guidance (dynamic, auto-synced with permissions) ─────

# Human-readable labels for permission flags → AI-friendly descriptions
_FEATURE_LABELS: dict[str, str] = {
    "can_faults": "fault reports",
    "can_fuel": "fuel levels",
    "can_efficiency": "driver efficiency",
    "can_health": "vehicle health",
    "can_vehicle_all": "all vehicles",
    "can_vehicle_vehicle": "assigned vehicle only",
    "can_alerts_all": "alerts for all trucks",
    "can_alerts_vehicle": "alerts for assigned vehicle",
    "can_invite": "invite users",
    "can_manage_users": "manage users",
    "can_manage_companies": "manage companies",
    "can_manage_vehicles": "manage vehicles",
    "can_loads_all": "loads (all)",
    "can_loads_own": "own loads",
    "can_manage_loads": "manage loads",
    "can_loads_manage_all": "manage all loads",
    "can_kpi": "KPI & performance",
    "can_manage_account": "account settings",
    "can_manage_permissions": "role permissions matrix",
    "can_manage_integrations": "telematics integrations",
    "can_manage_storage": "storage backend & quota",
    "can_manage_work_hours": "working-hours schedules",
    "can_geofence_all": "geofence alerts (all)",
    "can_geofence_vehicle": "geofence alerts (assigned vehicle)",
    "can_digest": "auto reports",
    "can_maintenance_all": "maintenance (all trucks)",
    "can_maintenance_vehicle": "maintenance (assigned vehicle)",
    "can_work_orders_all": "work orders (all trucks)",
    "can_work_orders_vehicle": "work orders (assigned vehicle)",
    "can_parts": "parts catalog & analytics",
    "can_parking_all": "parking events (all trucks)",
    "can_parking_vehicle": "parking events (assigned vehicle)",
    "can_cameras": "dashcam cameras",
    "can_ai_chat": "AI assistant chat",
    "can_cost_reports": "cost reports (executive rollups)",
    "can_inspections_all": "inspections (review all)",
    "can_inspections_vehicle": "inspections (assigned vehicle)",
    "can_scorecard_all": "scorecards (all)",
    "can_scorecard_vehicle": "scorecards (assigned vehicle)",
    "can_manage_scorecard_rules": "scorecard rules + pillar caps (scoring config)",
    "can_location_map": "live location map (all)",
    "can_location_vehicle": "live location (assigned vehicle)",
    "can_fuel_cost": "fuel cost tracking",
    "can_route_all": "route replay (all)",
    "can_route_vehicle": "route replay (assigned vehicle)",
    "can_cost_per_mile": "cost per mile",
    "can_events_all": "safety events (all)",
    "can_events_vehicle": "safety events (assigned vehicle)",
    "can_manage_billing": "billing & subscription management",
    "can_risk_report_all": "stakeholder risk summary report (all subjects)",
    "can_risk_report_own": "stakeholder risk summary report (own subject)",
    "can_driver_pay_admin": "driver pay: manage rules, trigger runs, view all paystubs",
    "can_driver_pay_view_own": "driver pay: view own paystub history",
    "can_coaching_admin": "coaching: manage rules, assign coaching, view all",
    "can_coaching_view_own": "coaching: see + acknowledge own assignments",
    "can_carrier_directory": "carrier directory (view)",
    "can_manage_carrier_directory": "carrier directory (manage)",
}


def build_role_guidance(role_str: str, is_manager: bool = False) -> str:
    """Build AI guidance text dynamically from the role's actual permissions.

    Returns a short paragraph the AI can use to understand what data
    this user can and cannot access. Always reflects current ROLE_PERMISSIONS
    plus any manager-tier grants (``is_manager``).
    """
    try:
        role = Role(role_str)
    except (ValueError, KeyError):
        # Unknown role: use the most restrictive safe default — do not
        # assume fleet-manager scope for an unrecognised identity.
        return "Unknown role — answer only with publicly visible information and avoid disclosing any user, vehicle, or operational data."

    perms = apply_manager_grants(get_permissions(role), role, is_manager)
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
    elif role == Role.HR:
        lines.append(
            "Focus on people: driver onboarding, qualification files, "
            "trainings, working hours, driver pay context. Avoid vehicle "
            "telematics detail unless it relates to a driver's record."
        )
    elif role == Role.ACCOUNTING:
        lines.append(
            "Focus on money: costs, fuel spend, cost-per-mile, billing, "
            "invoices, driver pay figures. Avoid live operational telematics "
            "unless it drives a cost number."
        )
    elif role == Role.RECRUITER:
        lines.append(
            "Focus on driver acquisition and onboarding: applicant "
            "pipeline, driver qualification files (CDL, medical card, "
            "DQF / 49 CFR Part 391), and inviting new drivers. Avoid "
            "operational telematics, costs, and safety scoring unless it "
            "relates to a candidate's qualification."
        )
        if is_manager:
            lines.append(
                "You lead the recruiting team: you can invite recruiters and "
                "manage the recruiting configuration (e.g. the carrier directory)."
            )

    return "\n".join(lines)


async def build_role_guidance_for_account(
    db, account_id: int, role_str: str, is_manager: bool = False,
) -> str:
    """Like build_role_guidance() but checks for a per-account DB override first.

    If the account has set a custom guidance string for *role_str*, that string
    is returned instead of the auto-generated one.  Falls back to the default
    sync implementation (manager-tier aware via *is_manager*) when no override
    exists or when db is None.
    """
    if db is not None and account_id:
        try:
            override = await db.get_role_ai_guidance(account_id, role_str)
            if override:
                return override
        except Exception:
            pass  # any DB error → fall back to defaults
    return build_role_guidance(role_str, is_manager)


# ─── Menu visibility — which buttons to show per role ─────────────

def visible_main_buttons(role: Role) -> list[str]:
    """Return list of callback_data strings the role can see in main menu."""
    perms = get_permissions(role)
    buttons = []
    if perms.can_faults:
        buttons.append("cmd_faults")
    if perms.can_fuel:
        buttons.append("cmd_fuel")
    if perms.can_alerts_all or perms.can_alerts_vehicle:
        buttons.append("cmd_alerts")
    return buttons


def can_access_company_submenu(role: Role) -> bool:
    """Whether this role can filter by individual company."""
    perms = get_permissions(role)
    return perms.can_faults or perms.can_fuel


# ─── Role Hierarchy ───────────────────────────────────────────────

ROLE_HIERARCHY: dict[str, int] = {
    "owner": 5, "admin": 4, "fleet": 3, "safety": 3,
    # hr + accounting are department roles, peers of dispatcher: owner/
    # admin can invite them; they can invite a driver (HR onboards
    # drivers) but not each other or anyone higher.  Without these
    # entries role_rank() fell back to 0, locking them out of inviting
    # ANYONE (even a driver).
    "dispatcher": 2, "hr": 2, "accounting": 2, "recruiter": 2, "driver": 1,
}


# ─── Role-string regex patterns for Pydantic ``Field(pattern=...)`` ──────
#
# Derived from the Role enum so a new role can NEVER desync a
# hand-maintained alternation — the exact bug where adding hr/accounting/
# recruiter left the change-role + invite endpoints rejecting them with a
# 422.  ALL = every role; ASSIGNABLE = every role except owner (ownership
# transfers via its own flow; owner is never invited or assigned here).
ALL_ROLES_PATTERN: str = r"^(" + "|".join(r.value for r in Role) + r")$"
ASSIGNABLE_ROLES_PATTERN: str = (
    r"^(" + "|".join(r.value for r in Role if r is not Role.OWNER) + r")$"
)


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


def is_kb_author_role(role: Role | str) -> bool:
    """True for roles allowed to create/edit knowledge base articles.

    Authors are operational staff who routinely produce SOPs, fault-code
    notes, safety reminders, etc. — owner, admin, fleet, safety.
    Drivers and dispatchers can read but not author.
    """
    key = role.value if isinstance(role, Role) else role
    return key in ("owner", "admin", "fleet", "safety")


def is_kb_approver_role(role: Role | str) -> bool:
    """True for roles allowed to approve/reject public KB submissions.

    Approval crosses the tenant boundary (public articles become visible
    to every account), so we keep this strictly to account leadership —
    owner + admin only.  Fleet/safety can submit; only owner/admin can
    bless cross-tenant publication.
    """
    return is_management_role(role)


# ─── AI Tool Permission Mappings ──────────────────────────────────
# Centralized here (not in AI code) so RBAC policy stays in one file.

# Map each tool to the permission flag(s) required.
# If ANY listed permission is True for the user's role, the tool is allowed.
# None means always allowed.
TOOL_PERMISSIONS: dict[str, list[str] | None] = {
    "get_vehicle_faults":       ["can_faults"],                                # owner/admin/fleet/safety
    "get_vehicle_detail":       ["can_vehicle_all", "can_vehicle_vehicle"],          # all roles
    "get_driver_efficiency":    ["can_efficiency"],                          # owner/admin/fleet
    "get_low_fuel_vehicles":    ["can_fuel"],                                # owner/admin/fleet/dispatcher
    "get_vehicle_health":       ["can_health"],                              # owner/admin/fleet/safety
    "get_weather":              ["can_vehicle_all"],                           # all except driver
    "get_efficiency_summary":   ["can_efficiency"],                          # owner/admin/fleet
    "get_vehicle_location":     ["can_location_map", "can_location_vehicle"],    # all roles
    "get_geofences":            ["can_geofence_all", "can_geofence_vehicle"],    # all roles
    "get_account_stats":        ["can_vehicle_all"],                           # all except driver
    "get_vehicle_events":       ["can_events_all", "can_events_vehicle"],        # owner/admin/fleet/safety/driver(own)
    "get_events_summary":       ["can_events_all"],                          # owner/admin/fleet/safety
    "get_vehicle_maintenance":  ["can_maintenance_all", "can_maintenance_vehicle"],  # owner/admin/fleet/safety/driver(own)
    "get_maintenance_summary":  ["can_maintenance_all"],                     # owner/admin/fleet/safety
    "get_vehicle_fuel_costs":   ["can_fuel_cost"],                           # owner/admin/fleet
    "get_fuel_cost_summary":    ["can_fuel_cost"],                           # owner/admin/fleet
    "check_vehicle_camera":     ["can_vehicle_all"],                           # all except driver
    "get_driver_scorecard":     ["can_scorecard_all", "can_scorecard_vehicle"],  # all except dispatcher
    "get_rolling_stopped":      ["can_vehicle_all"],                       # all except driver — account-wide fleet engine-state, follows Vehicles access like search_vehicles/get_parked_vehicles
    "get_vehicle_odometer":     ["can_vehicle_all", "can_vehicle_vehicle"],          # all roles
    "get_drivers_list":         ["can_vehicle_all"],                           # all except driver
    "search_vehicles":          ["can_vehicle_all"],                           # all except driver
    "search_knowledge_base":    None,                                        # all roles
    "get_parked_vehicles":        ["can_vehicle_all"],                           # owner/admin/dispatcher/fleet/safety — not driver (account-wide)
    "get_undriven_vehicles":      ["can_vehicle_all"],                           # owner/admin/dispatcher/fleet/safety — not driver (account-wide)
    "get_driver_hos_status":    ["can_vehicle_all"],                           # owner/admin/dispatcher/fleet/safety — HR concern, not driver-facing
    "get_alert_history":        ["can_alerts_all", "can_alerts_vehicle"],         # owner/admin/fleet/safety/driver(own)
    "get_recent_work_orders":   ["can_maintenance_all", "can_maintenance_vehicle"],  # owner/admin/fleet/safety/driver(own)
    "get_recent_inspections":   ["can_maintenance_all", "can_maintenance_vehicle"],  # owner/admin/fleet/safety/driver(own)
    "get_driver_applications":  ["can_manage_applications"],                          # owner/admin/hr/recruiter — applicant-pipeline triage, account-wide
    "get_vehicle_history":      ["can_vehicle_all", "can_vehicle_vehicle"],       # all roles — vehicle-specific tool, isolation enforced below
    # ── Write actions (copilot "hands") — propose during a chat turn;
    # the SAME flag is re-checked at the approve endpoint before the write.
    "create_maintenance_task":  ["can_maintenance_all"],                         # owner/admin/fleet/hr — mirrors POST /maintenance/tasks
    "acknowledge_alerts":       ["can_alerts_all", "can_alerts_vehicle"],        # owner/admin/fleet/safety/driver(own)
    "import_inventory_items":   ["can_manage_vehicles"],                         # owner/admin/fleet/hr — mirrors POST /vehicles/{v}/inventory; also gates attachment parsing
}

# Tools that are account-wide — driver must NOT call these even if permitted
# via can_*_own flags (they return data for ALL vehicles).
ACCOUNT_WIDE_TOOLS: frozenset[str] = frozenset({
    "get_low_fuel_vehicles", "get_vehicle_health",
    "get_weather", "get_efficiency_summary", "get_account_stats",
    "get_events_summary", "get_maintenance_summary",
    "get_fuel_cost_summary", "get_rolling_stopped",
    "get_drivers_list", "search_vehicles",
    "get_parked_vehicles", "get_undriven_vehicles", "get_driver_hos_status",
    "get_alert_history", "get_driver_applications",
    # Write action whose args are resource ids (not a vehicle_name): a scoped
    # caller must not clear alerts account-wide.  Also in SCOPE_AWARE_TOOLS
    # below, so the gate ALLOWS + injects _scope_vehicles and the tool filters
    # the ids to the caller's own vehicles.
    "acknowledge_alerts",
    # Bulk import across arbitrary vehicles (scope: account_unscoped) — no
    # single vehicle to gate on, so scoped callers are blocked outright.
    # Deliberately NOT in SCOPE_AWARE_TOOLS (the guard test enforces this).
    "import_inventory_items",
})

# Account-wide tools that have been taught to FILTER their results to a
# caller's effective vehicle scope (company/vehicle-restricted users).  For a
# scoped user the gate ALLOWS these (instead of blocking) and the orchestrator
# injects the allowed-vehicle set as ``tool_args["_scope_vehicles"]``; the tool
# returns only that subset.  Account-wide tools NOT in this set stay blocked
# for scoped users — so coverage grows safely, one tool at a time.
SCOPE_AWARE_TOOLS: frozenset[str] = frozenset({
    "get_parked_vehicles",
    "get_undriven_vehicles",
    "get_rolling_stopped",
    "search_vehicles",
    "get_alert_history",
    "get_maintenance_summary",
    "get_weather",
    "get_driver_hos_status",
    "get_low_fuel_vehicles",
    "get_fuel_cost_summary",
    "get_vehicle_health",
    "get_account_stats",
    "get_efficiency_summary",
    "get_events_summary",
    # Write action: validates its alert ids against the injected scope at
    # propose time; the executor + storage re-enforce it at approve time.
    "acknowledge_alerts",
})

# Tools that accept a vehicle_name param and must enforce driver vehicle isolation.
VEHICLE_SPECIFIC_TOOLS: frozenset[str] = frozenset({
    "get_vehicle_faults", "get_vehicle_detail", "get_vehicle_location",
    "get_vehicle_events", "get_vehicle_maintenance", "get_vehicle_fuel_costs",
    "check_vehicle_camera", "get_vehicle_odometer",
    "get_recent_work_orders", "get_recent_inspections", "get_vehicle_history",
    # Write action with a required vehicle_name — a scoped caller may only
    # create a task on a vehicle they can access (gate rejects otherwise).
    "create_maintenance_task",
})

# Legacy alias — keeps any external code that imports TRUCK_SPECIFIC_TOOLS working.
TRUCK_SPECIFIC_TOOLS: frozenset[str] = VEHICLE_SPECIFIC_TOOLS
