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

    # Alerts
    can_alerts_all: bool = False     # new fault alerts (all trucks)
    can_alerts_vehicle: bool = False     # alerts for assigned vehicle only
    # Parking — own feature (NOT part of Alerts).  Defaults mirror the old
    # alerts/vehicle gate: everyone sees all, drivers see their assigned vehicle.
    can_parking_all: bool = True     # unsafe-parking events (all trucks)
    can_parking_vehicle: bool = False    # unsafe-parking events (assigned vehicle)
    # Cameras — own feature (NOT part of Faults).  Defaults mirror can_faults.
    can_cameras: bool = False        # dashcam footage viewer
    # AI Chat — own feature (NOT part of Faults).  Defaults on for everyone
    # (the old gate was faults-or-vehicle, which every role passed).
    can_ai_chat: bool = True         # AI assistant chat + summary

    # Management
    can_invite: bool = False         # /invite
    can_manage_users: bool = False   # /users, /setrole, /remove
    can_manage_companies: bool = False    # /addcompany, /removecompany
    can_manage_account: bool = False # /account settings

    # Dispatcher extras
    can_rolling_stopped: bool = False   # rolling/stopped notifications

    # ── New features ──────────────────────────────────────────────
    can_geofence_all: bool = False      # geofence alerts (all trucks)
    can_geofence_vehicle: bool = False      # geofence alerts (assigned vehicle)
    can_digest: bool = False            # auto reports subscription
    can_maintenance_all: bool = False   # maintenance scheduler (all trucks)
    can_maintenance_vehicle: bool = False   # maintenance scheduler (assigned vehicle)
    can_work_orders_all: bool = False   # shop-invoice work orders (all trucks)
    can_work_orders_vehicle: bool = False   # shop-invoice work orders (assigned vehicle)
    can_cost_reports: bool = False      # /cost-reports executive rollups (split off can_maintenance_all)
    can_scorecard_all: bool = False     # scorecards for all subjects (driver or vehicle)
    can_scorecard_vehicle: bool = False     # scorecards for the assigned vehicle(s) only
    can_location_map: bool = False      # live location map (all trucks)
    can_location_vehicle: bool = False      # live location map (assigned vehicle)
    can_fuel_cost: bool = False         # fuel cost tracker
    can_route_all: bool = False         # route replay (all trucks)
    can_route_vehicle: bool = False         # route replay (assigned vehicle)
    can_cost_per_mile: bool = False     # cost-per-mile dashboard
    can_events_all: bool = False        # safety events (all trucks)
    can_events_vehicle: bool = False        # safety events (assigned vehicle)
    can_manage_billing: bool = False    # billing & subscription management (owner + admin)
    can_manage_poi_layers: bool = False # create/edit/delete custom POI map layers (owner/admin/fleet)
    can_risk_report_all: bool = False   # generate Stakeholder Risk Summary for any subject
    can_risk_report_own: bool = False   # generate Stakeholder Risk Summary for own subject only
    can_payroll_admin: bool = False     # configure rules / trigger runs / view all paystubs
    can_payroll_view_own: bool = False  # view own paystub history (driver self-service)
    can_coaching_admin: bool = False    # manage coaching rules + assign manually + view all
    can_coaching_view_own: bool = False # see + acknowledge own coaching assignments
    # Driver Module — profile + document management.
    # Admin permission grants full CRUD on any driver in the account
    # (used by Workforce → Drivers admin page).  "Own" permission
    # lets a driver view their own profile + documents from the
    # miniapp (read-only in MVP; re-upload requests go to admin).
    can_manage_driver_docs: bool = False   # create / update / upload / delete for any driver
    can_driver_docs_own: bool = False      # read own profile + documents
    # PTI (Pre-Trip Inspection) module — weekly photo-evidence
    # walkaround.  ``can_inspections_all`` lets fleet/safety review
    # submissions across the whole account; ``can_inspections_vehicle``
    # lets a driver complete + submit their own assigned vehicle.
    can_inspections_all: bool = False
    can_inspections_vehicle: bool = False


# ─── Role → Permission Map ───────────────────────────────────────

ROLE_PERMISSIONS: dict[Role, FeatureSet] = {
    Role.OWNER: FeatureSet(
        can_faults=True, can_fuel=True, can_cameras=True,
        can_efficiency=True, can_health=True,
        can_vehicle_all=True, can_vehicle_vehicle=True,
        can_alerts_all=True, can_alerts_vehicle=True,
        can_invite=True, can_manage_users=True,
        can_manage_companies=True, can_manage_account=True,
        can_rolling_stopped=True,
        can_geofence_all=True, can_geofence_vehicle=True,
        can_parking_all=True, can_parking_vehicle=True,
        can_digest=True,
        can_maintenance_all=True, can_maintenance_vehicle=True,
        can_work_orders_all=True, can_work_orders_vehicle=True,
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
        can_payroll_admin=True, can_payroll_view_own=True,
        can_coaching_admin=True, can_coaching_view_own=True,
        can_manage_driver_docs=True, can_driver_docs_own=True,
        can_inspections_all=True, can_inspections_vehicle=True,
    ),
    Role.ADMIN: FeatureSet(
        can_faults=True, can_fuel=True, can_cameras=True,
        can_efficiency=True, can_health=True,
        can_vehicle_all=True, can_vehicle_vehicle=True,
        can_alerts_all=True, can_alerts_vehicle=True,
        can_invite=True, can_manage_users=True,
        can_manage_companies=False, can_manage_account=False,
        can_rolling_stopped=True,
        can_geofence_all=True, can_geofence_vehicle=True,
        can_digest=True,
        can_maintenance_all=True, can_maintenance_vehicle=True,
        can_work_orders_all=True, can_work_orders_vehicle=True,
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
        can_payroll_admin=True, can_payroll_view_own=True,
        can_coaching_admin=True, can_coaching_view_own=True,
        can_manage_driver_docs=True, can_driver_docs_own=True,
        can_inspections_all=True, can_inspections_vehicle=True,
    ),
    Role.FLEET: FeatureSet(
        can_faults=True, can_fuel=True, can_cameras=True,
        can_efficiency=True, can_health=True,
        can_vehicle_all=True, can_vehicle_vehicle=True,
        can_alerts_all=True, can_alerts_vehicle=True,
        can_invite=False, can_manage_users=False,
        can_manage_companies=False, can_manage_account=False,
        can_rolling_stopped=False,
        can_geofence_all=True, can_geofence_vehicle=True,
        can_digest=True,
        can_maintenance_all=True, can_maintenance_vehicle=True,
        can_work_orders_all=True, can_work_orders_vehicle=True,
        can_cost_reports=True,
        can_scorecard_all=True, can_scorecard_vehicle=True,
        can_location_map=True, can_location_vehicle=True,
        can_fuel_cost=True,
        can_route_all=True, can_route_vehicle=True,
        can_cost_per_mile=True,
        can_events_all=True, can_events_vehicle=True,
        can_manage_poi_layers=True,
        can_risk_report_all=False, can_risk_report_own=True,
        can_payroll_admin=False, can_payroll_view_own=False,
        can_coaching_admin=True, can_coaching_view_own=False,
        # Fleet managers handle driver records day-to-day (assignments,
        # CDL renewals) so they get the admin permission too.
        can_manage_driver_docs=True, can_driver_docs_own=False,
        can_inspections_all=True, can_inspections_vehicle=False,
    ),
    Role.SAFETY: FeatureSet(
        can_faults=True, can_fuel=False, can_cameras=True,
        can_efficiency=False, can_health=True,
        can_vehicle_all=True, can_vehicle_vehicle=True,
        can_alerts_all=True, can_alerts_vehicle=True,
        can_invite=False, can_manage_users=False,
        can_manage_companies=False, can_manage_account=False,
        can_rolling_stopped=False,
        can_geofence_all=True, can_geofence_vehicle=True,
        can_digest=True,
        can_maintenance_all=True, can_maintenance_vehicle=True,
        can_work_orders_all=True, can_work_orders_vehicle=True,
        can_scorecard_all=True, can_scorecard_vehicle=True,
        can_location_map=True, can_location_vehicle=True,
        can_fuel_cost=False,
        can_route_all=True, can_route_vehicle=True,
        can_cost_per_mile=False,
        can_events_all=True, can_events_vehicle=True,
        can_risk_report_all=True, can_risk_report_own=True,
        can_payroll_admin=False, can_payroll_view_own=False,
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
        # Dispatchers need to see geofence/parking alerts and safety events
        # to react to deviations from the route plan. Without these they
        # were effectively blind to anything happening to a truck mid-shift.
        can_alerts_all=True, can_alerts_vehicle=True,
        can_invite=False, can_manage_users=False,
        can_manage_companies=False, can_manage_account=False,
        can_rolling_stopped=True,
        can_geofence_all=True, can_geofence_vehicle=True,
        can_digest=True,
        can_maintenance_all=False, can_maintenance_vehicle=False,
        can_scorecard_all=True, can_scorecard_vehicle=True,
        can_location_map=True, can_location_vehicle=True,
        can_fuel_cost=False,
        can_route_all=True, can_route_vehicle=True,
        can_cost_per_mile=False,
        can_events_all=True, can_events_vehicle=True,
        can_risk_report_all=False, can_risk_report_own=False,
        can_payroll_admin=False, can_payroll_view_own=False,
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
        can_inspections_all=True,              # PTI review for compliance audit
        # Read-only context — HR needs to see WHO is doing WHAT,
        # not edit fleet ops:
        can_vehicle_all=True,                  # Which vehicle a driver is on
        can_location_map=True,                 # Where drivers are right now
        can_alerts_all=True,                   # Driver-related alerts
        can_events_all=True,                   # Safety events drive coaching
        can_scorecard_all=True,                # Driver behaviour insight
        can_risk_report_all=True,              # Personnel risk reporting
        can_geofence_all=True,                 # See geofence context for incidents
        # Subscriptions / digest:
        can_digest=True,
    ),
    Role.ACCOUNTING: FeatureSet(
        # Accounting persona — money management.  Focus: billing,
        # cost analytics, payroll, financial reports.  No driver
        # admin or vehicle-ops controls.
        can_manage_billing=True,               # Billing & subscriptions
        can_fuel=True,                         # Fuel report
        can_fuel_cost=True,                    # Fuel cost tracker
        can_cost_per_mile=True,                # CPM dashboard
        can_payroll_admin=True,                # Payroll runs + history
        can_efficiency=True,                   # Efficiency report for cost analysis
        # Cost rollups by truck — used to be granted via the overloaded
        # ``can_maintenance_all`` flag; split into its own gate in
        # 2026-06 so toggling Maintenance for accounting no longer
        # silently also affects Cost Reports access.
        can_cost_reports=True,
        # Read-only context — accounting needs to see WHICH assets
        # generate WHICH costs:
        can_vehicle_all=True,                  # Vehicle list for asset accounting
        # Subscriptions / digest:
        can_digest=True,
    ),
    Role.DRIVER: FeatureSet(
        can_faults=False, can_fuel=False,
        can_efficiency=False, can_health=False,
        can_vehicle_all=False, can_vehicle_vehicle=True,
        can_alerts_all=False, can_alerts_vehicle=True,
        can_invite=False, can_manage_users=False,
        can_manage_companies=False, can_manage_account=False,
        can_rolling_stopped=False,
        can_geofence_all=False, can_geofence_vehicle=True,
        can_digest=True,
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
        can_payroll_admin=False, can_payroll_view_own=True,
        can_coaching_admin=False, can_coaching_view_own=True,
        # Drivers see their own profile + documents (read-only); they
        # never see other drivers' records.
        can_manage_driver_docs=False, can_driver_docs_own=True,
        can_inspections_all=False, can_inspections_vehicle=True,
    ),
}


# ─── Owner lockout protection ─────────────────────────────────────
# The account owner is the ultimate authority and must NEVER be able to
# revoke — from themselves — the account-control permissions that are the
# only way back from a misconfiguration.  Even if a stored DB override
# (or a malformed API call) sets these False for the owner role, the
# resolver below forces them back on.  This is what lets the Role
# Permissions matrix safely let an owner hide *operational* features from
# their own view without ever risking a self-lockout.
OWNER_PROTECTED_PERMS: frozenset[str] = frozenset({
    "can_manage_account",    # gates Role Permissions, Modules, Settings, Storage…
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


def get_permissions(role: Role) -> FeatureSet:
    """Get the default permission set for a role (sync, no DB).

    For account-specific permissions, use get_account_permissions() instead.
    """
    return ROLE_PERMISSIONS.get(role, FeatureSet())


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
        from capabilities.iam.modules import mask_disabled_modules
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
    import time as _time
    cache_key = (account_id, role.value if hasattr(role, "value") else role, company_id)
    cached = _permissions_cache.get(cache_key)
    now = _time.monotonic()
    if cached is not None:
        expires_at, fs = cached
        if expires_at > now:
            return fs
        # Stale — drop and re-resolve.  No "stale-while-revalidate"
        # because permission staleness is a security concern: if the
        # Owner just removed can_manage_billing from Admin, we don't
        # want any worker serving the old True for even one request
        # past the TTL window.

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
            fs = _protect_owner(role, FeatureSet(**merged))
            fs = await _apply_module_mask(fs, account_id)
            _permissions_cache[cache_key] = (now + _PERMS_CACHE_TTL_S, fs)
            return fs
    except Exception as e:
        logger.debug("Could not load permissions from DB (using defaults): %s", e)

    fs = _protect_owner(role, ROLE_PERMISSIONS.get(role, FeatureSet()))
    fs = await _apply_module_mask(fs, account_id)
    _permissions_cache[cache_key] = (now + _PERMS_CACHE_TTL_S, fs)
    return fs


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
    "can_manage_account": "account settings",
    "can_rolling_stopped": "rolling/stopped status",
    "can_geofence_all": "geofence alerts (all)",
    "can_geofence_vehicle": "geofence alerts (assigned vehicle)",
    "can_digest": "auto reports",
    "can_maintenance_all": "maintenance (all trucks)",
    "can_maintenance_vehicle": "maintenance (assigned vehicle)",
    "can_work_orders_all": "work orders (all trucks)",
    "can_work_orders_vehicle": "work orders (assigned vehicle)",
    "can_parking_all": "parking events (all trucks)",
    "can_parking_vehicle": "parking events (assigned vehicle)",
    "can_cameras": "dashcam cameras",
    "can_ai_chat": "AI assistant chat",
    "can_cost_reports": "cost reports (executive rollups)",
    "can_inspections_all": "inspections (review all)",
    "can_inspections_vehicle": "inspections (assigned vehicle)",
    "can_scorecard_all": "driver scorecards (all)",
    "can_scorecard_vehicle": "driver scorecards (assigned vehicle)",
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
    "can_payroll_admin": "payroll: manage rules, trigger runs, view all paystubs",
    "can_payroll_view_own": "payroll: view own paystub history",
    "can_coaching_admin": "coaching: manage rules, assign coaching, view all",
    "can_coaching_view_own": "coaching: see + acknowledge own assignments",
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
    "get_rolling_stopped":      ["can_rolling_stopped"],                     # owner/admin/dispatcher
    "get_vehicle_odometer":     ["can_vehicle_all", "can_vehicle_vehicle"],          # all roles
    "get_drivers_list":         ["can_vehicle_all"],                           # all except driver
    "search_vehicles":          ["can_vehicle_all"],                           # all except driver
    "search_knowledge_base":    None,                                        # all roles
    "get_idle_vehicles":        ["can_vehicle_all"],                           # owner/admin/dispatcher/fleet/safety — not driver (fleet-wide)
    "get_driver_hos_status":    ["can_vehicle_all"],                           # owner/admin/dispatcher/fleet/safety — HR concern, not driver-facing
    "get_alert_history":        ["can_alerts_all", "can_alerts_vehicle"],         # owner/admin/fleet/safety/driver(own)
    "get_recent_work_orders":   ["can_maintenance_all", "can_maintenance_vehicle"],  # owner/admin/fleet/safety/driver(own)
    "get_recent_inspections":   ["can_maintenance_all", "can_maintenance_vehicle"],  # owner/admin/fleet/safety/driver(own)
    "get_vehicle_history":      ["can_vehicle_all", "can_vehicle_vehicle"],       # all roles — vehicle-specific tool, isolation enforced below
}

# Tools that are account-wide — driver must NOT call these even if permitted
# via can_*_own flags (they return data for ALL vehicles).
ACCOUNT_WIDE_TOOLS: frozenset[str] = frozenset({
    "get_low_fuel_vehicles", "get_vehicle_health",
    "get_weather", "get_efficiency_summary", "get_account_stats",
    "get_events_summary", "get_maintenance_summary",
    "get_fuel_cost_summary", "get_rolling_stopped",
    "get_drivers_list", "search_vehicles",
    "get_idle_vehicles", "get_driver_hos_status",
    "get_alert_history",
})

# Tools that accept a vehicle_name param and must enforce driver vehicle isolation.
VEHICLE_SPECIFIC_TOOLS: frozenset[str] = frozenset({
    "get_vehicle_faults", "get_vehicle_detail", "get_vehicle_location",
    "get_vehicle_events", "get_vehicle_maintenance", "get_vehicle_fuel_costs",
    "check_vehicle_camera", "get_vehicle_odometer",
    "get_recent_work_orders", "get_recent_inspections", "get_vehicle_history",
})

# Legacy alias — keeps any external code that imports TRUCK_SPECIFIC_TOOLS working.
TRUCK_SPECIFIC_TOOLS: frozenset[str] = VEHICLE_SPECIFIC_TOOLS
