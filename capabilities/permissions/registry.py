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
and the module mask it derives is the mask the account has today
(``MASK_DRIFT`` names the flags the hand-written mask never carried —
a set that may only shrink).
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
    _e("ai_assistant", kind="service", modules=["core"], opens=["can_ai_chat"]),
    _e("alerts", kind="service", modules=["core"], opens=["can_view_vehicles"],
       flags=["can_alerts_all", "can_alerts_vehicle"],
       note="the door rides Vehicles; the two flags are derived width, retired with the services step"),
    _e("reports", kind="service", modules=["core"],
       opens=["can_view_faults", "can_view_risk_reports", "can_view_cost_reports", "can_digest"],
       flags=[],
       note="the hub; its tabs are the report-type features below, its subscription the sub-feature"),
    _e("scheduled_reports", tier="shared", modules=["core"], opens=["can_digest"], flags=["can_digest"],
       nav=False, parent="reports",
       note="the Reports service's sub-feature (capabilities/reporting/scheduled/); route /reports/scheduled-reports"),
    _e("knowledge_base", tier="shared", modules=["core"]),
    _e("tours", tier="shared", modules=["core"]),
    _e("live_map", tier="shared", modules=["core"], opens=["can_view_location"],
       flags=["can_view_location", "can_manage_poi_layers"]),
    _e("vehicles", tier="shared", modules=["core"], opens=["can_view_vehicles"],
       flags=["can_view_vehicles", "can_manage_vehicles",
              "can_view_health", "can_view_faults", "can_view_fuel", "can_view_efficiency"]),
    # ── shared across departments ────────────────────────────────────
    _e("kpi", tier="shared", modules=["account", "dispatch", "accounting"], opens=["can_view_kpi"]),
    _e("kpi_my_payouts", tier="shared", modules=["dispatch"], note="finalized payout rows; no flag of its own"),
    _e("vehicle_inventory", tier="shared", modules=["fleet", "account"], opens=["can_view_vehicles"], flags=[],
       parent="vehicles", note="rides Vehicles"),
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
    _e("inspections", tier="role", modules=["fleet"], opens=["can_view_inspections"],
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
    _e("coaching", tier="role", modules=["hr", "safety"], opens=["can_view_coaching"],
       flags=["can_view_coaching", "can_manage_coaching"]),
    _e("applications", tier="role", modules=["hr"], opens=["can_manage_applications"]),
    _e("carrier_directory", tier="role", modules=["hr"], opens=["can_view_carrier_directory"],
       flags=["can_view_carrier_directory", "can_manage_carrier_directory"]),
    # ── accounting ───────────────────────────────────────────────────
    _e("driver_pay", tier="role", modules=["accounting"], opens=["can_view_driver_pay"],
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


#: Flags the hand-written module mask never carried although their
#: feature belongs to a toggleable department — i.e. a department
#: switch hides their page in the nav but leaves their API open.  Kept
#: here, named, so the registry's derived mask reproduces today's
#: behaviour exactly; closing one is a deliberate change (delete it
#: here and the flag starts honouring the switch).  May only shrink.
MASK_DRIFT: frozenset[str] = frozenset({
    "can_view_loads", "can_manage_loads",                       # loads · dispatch
    "can_view_carrier_directory", "can_manage_carrier_directory",  # carrier directory · hr
    "can_manage_parts",                                         # parts · fleet
    "can_manage_service_tasks",                                 # service tasks · fleet
    "can_view_truck_anatomy",                                   # truck anatomy · fleet
    "can_view_risk_reports",                                    # risk summary · safety
    "can_view_cost_reports",                                    # cost reports · accounting
})


def derive_flag_modules() -> dict[str, frozenset[str]]:
    """flag → owning departments, for every flag a department switch
    may turn off — the registry's answer to ``modules.FLAG_MODULES``."""
    out: dict[str, frozenset[str]] = {}
    for e in ENTRIES:
        if not e.maskable:
            continue
        for flag in e.flags:
            if flag in MASK_DRIFT:
                continue
            out[flag] = e.modules_of(flag)
    return out
