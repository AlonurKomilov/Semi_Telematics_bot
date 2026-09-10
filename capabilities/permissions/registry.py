"""The feature and service registry — the backend's unit of "what".

Permissions answer VERBS on FEATURES; Team Management answers WIDTH;
the account's department switches and, next, its plan answer "is this
FEATURE here at all".  Those last two questions need a unit larger
than a flag and smaller than a department: the feature.  This module
is that unit, in code, for the backend — one entry per catalog id,
the same id the dashboard's ``featureCatalog.ts`` uses, so billing,
the module mask and the plan mask all speak in one vocabulary.

Each entry says:

* ``kind``    — a FEATURE (owns data, a surface, a lifecycle; granted
                per role) or a SERVICE (a channel — Alerts, AI,
                Reports — whose CONTENT flows from feature grants).
* ``tier``    — who it is for (docs/FEATURES.md): shared / role /
                administration.  A service has none.
* ``modules`` — the departments that own it.  ``core`` and ``account``
                are always on, so an entry carrying either is never
                masked by a department switch.
* ``opens``   — the flags that open its front door (the catalog's
                ``permission``).
* ``flags``   — EVERY flag that lives under it: the door plus its
                actions, sub-features and components from the matrix
                tree.  A plan that excludes the feature masks all of
                them.  A flag may narrow its modules (Onboarding is
                HR's although Drivers is HR + Fleet + Safety).

Three guards hold it to the truth: every ``FeatureSet`` field belongs
to exactly one entry or the cross-feature bucket; the dashboard's
catalog and matrix tree agree with it (tests/test_feature_registry_drift.py);
and the module mask it derives is pinned as a snapshot, so a
department gaining or losing a flag is a deliberate edit, never drift.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Mapping

#: The departments an account switches on and off — the catalog's five
#: module pseudo-entries.  Core and Account admin are always on and are
#: not listed.  Order is display order on the Permissions page.
TOGGLEABLE_MODULES: tuple[str, ...] = (
    "fleet", "dispatch", "safety", "hr", "accounting",
)

#: the flags no feature owns — do-verbs that span features (the config
#: family).  Never masked by a department, never sold on their own.
CROSS_FEATURE_FLAGS: frozenset[str] = frozenset({
    "can_manage_config_role", "can_manage_config_all",
})


@dataclass(frozen=True)
class Entry:
    id: str
    kind: str                       # "feature" | "service"
    tier: str | None                # shared | role | administration | None
    modules: frozenset[str]
    opens: tuple[str, ...]
    #: flag → the modules that own THAT flag (None = the entry's)
    flags: Mapping[str, frozenset[str] | None] = field(default_factory=dict)
    #: False = a hub tab or route-only surface with no catalog entry
    nav: bool = True
    #: a SUB-FEATURE nests under its parent in the MATRIX tree while
    #: keeping its own id, home and flags (docs/FEATURES.md).  This is
    #: the matrix axis; the catalog's ``parentId`` is the SIDEBAR axis,
    #: and the two may differ (an entry with no flags nests nowhere in
    #: the matrix however the sidebar indents it).
    parent: str | None = None
    note: str = ""

    @property
    def maskable(self) -> bool:
        """Masked by a department switch only when EVERY owning module
        is toggleable — an entry on ``core`` or ``account`` is never off."""
        return bool(self.modules) and self.modules <= set(TOGGLEABLE_MODULES)

    def modules_of(self, flag: str) -> frozenset[str]:
        own = self.flags.get(flag)
        return own if own is not None else self.modules


def _e(id, kind="feature", tier=None, modules=(), opens=(), flags=None, nav=True, parent=None, note=""):
    mods = frozenset(modules)
    if flags is None:
        flags = tuple(opens)
    fm: dict[str, frozenset[str] | None] = {}
    for f in flags:
        if isinstance(f, tuple):
            fm[f[0]] = frozenset(f[1])
        else:
            fm[f] = None
    return Entry(id=id, kind=kind, tier=tier, modules=mods, opens=tuple(opens), flags=fm, nav=nav, parent=parent, note=note)


ENTRIES: tuple[Entry, ...] = (
    # ── core — always on ─────────────────────────────────────────────
    _e("overview", tier="shared", modules=["core"], note="an aggregator page; gated by what it shows"),
    _e("ai_assistant", kind="service", modules=["core"], opens=["can_view_ai_assistant"]),
    _e("notifications", kind="service", modules=["core"], opens=["can_view_notifications"],
       note="the delivery channel: the bell, the centre, Telegram/email/push and their settings; "
            "withheld, nothing reaches the person except mandatory security/billing notices"),
    _e("mods", kind="service", modules=["core"], opens=["can_view_mods"],
       note="the personal look — colour, corners, sound, effects, wallpaper; per person, per device, nothing account-wide flows through it. Without it every setting holds its default and the panel, the page and the doors are closed"),
    _e("alerts", kind="service", modules=["core"], opens=["can_view_alerts"],
       note="the inbox channel; what it shows follows the role's features, its width is Team Management's"),
    _e("reports", kind="service", modules=["core"],
       opens=["can_view_reports"],
       note="the hub channel; its tabs are the report-type features below, its subscription the sub-feature"),
    _e("scheduled_reports", tier="shared", modules=["core"], opens=["can_view_reports"], flags=[],
       nav=False, parent="reports",
       note="the Reports service's sub-feature (capabilities/reporting/scheduled/); route /reports/scheduled-reports"),
    _e("dot_binder", tier="shared", modules=["fleet"], opens=["can_manage_maintenance"], flags=[],
       nav=False, parent="reports",
       note="the hub's DOT Binder tab (features/reports/DotBinder.tsx, route /reports/dot-binder); "
            "rides Maintenance's manage verb — a page without a registry id is one the plan mask cannot see"),
    _e("knowledge_base", tier="shared", modules=["core"], opens=["can_view_knowledge_base"],
       note="tips & guides — a Shared feature, granted per role (owner decision 2026-09-10)"),
    _e("tours", kind="service", modules=["core"], opens=["can_view_tours"],
       note="the interactive walkthroughs — a service like Mods: per person, no account data, withheld per role"),
    _e("live_map", tier="shared", modules=["core"], opens=["can_view_location"],
       flags=["can_view_location", "can_manage_poi_layers"]),
    _e("vehicles", tier="shared", modules=["core"], opens=["can_view_vehicles"],
       flags=["can_view_vehicles", "can_manage_vehicles",
              "can_view_health", "can_view_faults", "can_view_fuel", "can_view_efficiency"]),
    # ── shared across departments ────────────────────────────────────
    _e("kpi", tier="shared", modules=["account", "dispatch", "accounting"], opens=["can_view_kpi"]),
    _e("kpi_my_payouts", tier="shared", modules=["dispatch"], opens=["can_view_kpi"], flags=[],
       note="finalized payout rows; rides KPI's view verb (owner decision 2026-09-10) — a role without KPI has no payouts to see"),
    _e("inventory", tier="shared", modules=["fleet", "account"],
       opens=["can_view_inventory"],
       flags=["can_view_inventory", "can_manage_inventory"],
       note="features/inventory/ — left Vehicles entirely on 2026-09-08: "
            "its own package, its own flags, its own /inventory address "
            "and its own nav entry.  It REFERENCES the vehicle registry "
            "(a unit number resolves to a truck) the way Work Orders "
            "does; that is a reference, not a parent"),
    _e("vehicle_documents", tier="shared", modules=["fleet", "account"], opens=["can_view_vehicle_docs"],
       flags=["can_view_vehicle_docs", "can_manage_vehicle_docs"], parent="vehicles"),
    _e("geofences", tier="shared", modules=["fleet", "dispatch"], opens=["can_view_geofence"],
       flags=["can_view_geofence", "can_manage_geofence"]),
    _e("loads", tier="shared", modules=["dispatch"], opens=["can_view_loads"],
       flags=["can_view_loads", "can_manage_loads"]),
    _e("scorecards", tier="shared", modules=["safety", "hr"], opens=["can_view_scorecards"]),
    _e("scorecard_rules", tier="shared", modules=["safety", "hr"], opens=["can_manage_config_all"], flags=[],
       note="a config component; its flag is the cross-feature config family's"),
    _e("drivers", tier="shared", modules=["hr", "fleet", "safety"], opens=["can_view_driver_docs"],
       flags=["can_view_driver_docs", "can_manage_driver_docs",
              ("can_onboard_drivers", ["hr"]),
              ("can_manage_drivers", ["fleet", "hr"])]),
    _e("truck-anatomy", tier="shared", modules=["fleet"], opens=["can_view_truck_anatomy"]),
    # ── fleet ────────────────────────────────────────────────────────
    _e("maintenance", tier="role", modules=["fleet"], opens=["can_view_maintenance"],
       flags=["can_view_maintenance", "can_manage_maintenance"]),
    _e("work_orders", tier="role", modules=["fleet"], opens=["can_view_work_orders"],
       flags=["can_view_work_orders", "can_manage_work_orders"]),
    _e("vendors", tier="role", modules=["fleet"], opens=["can_manage_work_orders"], flags=[],
       note="rides Work Orders' manage verb"),
    _e("parts", tier="role", modules=["fleet"], opens=["can_manage_parts"]),
    _e("service-tasks", tier="role", modules=["fleet"], opens=["can_manage_service_tasks"]),
    _e("inspections", tier="role", modules=["fleet"], opens=["can_manage_inspections"],
       flags=["can_view_inspections", "can_manage_inspections"]),
    # ── dispatch ─────────────────────────────────────────────────────
    _e("routes", tier="role", modules=["dispatch"], opens=["can_view_routes"]),
    _e("parking", tier="role", modules=["dispatch", "fleet", "safety"], opens=["can_view_parking"]),
    # ── safety ───────────────────────────────────────────────────────
    _e("events", tier="role", modules=["safety", "hr"], opens=["can_view_events"]),
    _e("cameras", tier="role", modules=["safety", "fleet"], opens=["can_view_cameras"]),
    _e("risk_summary", tier="role", modules=["safety"], opens=["can_view_risk_reports"], nav=False,
       note="a Reports-hub tab (route /reports/risk-summary); Safety's data"),
    # ── hr ───────────────────────────────────────────────────────────
    _e("coaching", tier="role", modules=["hr", "safety"], opens=["can_manage_coaching"],
       flags=["can_view_coaching", "can_manage_coaching"]),
    _e("applications", tier="role", modules=["hr"], opens=["can_manage_applications"]),
    _e("carrier_directory", tier="role", modules=["hr"], opens=["can_view_carrier_directory"],
       flags=["can_view_carrier_directory", "can_manage_carrier_directory"]),
    # ── accounting ───────────────────────────────────────────────────
    _e("driver_pay", tier="role", modules=["accounting"], opens=["can_manage_driver_pay"],
       flags=["can_view_driver_pay", "can_manage_driver_pay"]),
    _e("fuel_costs", tier="role", modules=["accounting", "dispatch"], opens=["can_view_fuel_cost"]),
    _e("cost_per_mile", tier="role", modules=["accounting", "fleet"], opens=["can_view_cost_per_mile"]),
    _e("cost_reports", tier="role", modules=["accounting"], opens=["can_view_cost_reports"], nav=False,
       note="a Reports-hub tab (route /reports/cost-reports); Accounting's data"),
    _e("billing", tier="role", modules=["accounting", "account"], opens=["can_manage_billing"]),
    # ── administration ───────────────────────────────────────────────
    _e("invites", tier="administration", modules=["account"], opens=["can_invite"]),
    _e("team_management", tier="administration", modules=["account"], opens=["can_manage_users"]),
    _e("audit_log", tier="administration", modules=["account"], opens=["can_manage_users"], flags=[],
       note="rides Team Management"),
    _e("companies", tier="administration", modules=["account"], opens=["can_manage_companies"]),
    _e("integrations", tier="administration", modules=["account"], opens=["can_manage_integrations"]),
    _e("storage", tier="administration", modules=["account"], opens=["can_manage_storage"]),
    _e("settings", tier="administration", modules=["account"], opens=["can_manage_account", "can_manage_role_bot"],
       flags=["can_manage_account", "can_manage_role_bot", "can_manage_work_hours"]),
    _e("role_permissions", tier="administration", modules=["account"], opens=["can_manage_permissions"]),
)

REGISTRY: dict[str, Entry] = {e.id: e for e in ENTRIES}
assert len(REGISTRY) == len(ENTRIES), "duplicate registry id"
assert all(e.parent is None or e.parent in REGISTRY for e in ENTRIES), "orphan sub-feature"

FEATURES: dict[str, Entry] = {k: v for k, v in REGISTRY.items() if v.kind == "feature"}
SERVICES: dict[str, Entry] = {k: v for k, v in REGISTRY.items() if v.kind == "service"}


def owner_of(flag: str) -> Entry | None:
    """The one entry a flag lives under, or None for a cross-feature flag."""
    for e in ENTRIES:
        if flag in e.flags:
            return e
    return None


def derive_flag_modules() -> dict[str, frozenset[str]]:
    """flag → owning departments, for every flag a department switch
    may turn off — the registry's answer to ``modules.FLAG_MODULES``.

    Every flag of a department-owned feature is here.  The hand-written
    mask this replaced had drifted from the catalog on seven features
    (nine flags) — loads, carrier directory, parts, service tasks, truck
    anatomy, risk summary, cost reports — whose department switch hid
    their page and left their API open; the owner closed that on
    2026-09-06, so a switch is a switch everywhere.
    """
    out: dict[str, frozenset[str]] = {}
    for e in ENTRIES:
        if not e.maskable:
            continue
        for flag in e.flags:
            out[flag] = e.modules_of(flag)
    return out
