"""The verb/scope migration contract — one verdict per FeatureSet flag.

Permissions answer exactly one question — MAY this role do this VERB on
this FEATURE — and Team Management answers the other one — WHICH UNITS
does this person see.  Today eleven ``*_vehicle`` flags answer both
questions with one word, which is where every "why can a driver see
this page but not that button" confusion has come from.  This table is
the owner-reviewed contract for pulling the two questions apart.

Every flag in ``FeatureSet`` appears here exactly once (a test pins the
bijection both ways).  Fates:

``VERB_VIEW`` / ``VERB_MANAGE``
    The flag survives as a pure verb.  ``target`` names its canonical
    form; a target equal to the flag itself means the name is already
    grammatical and nothing renames.  A renamed flag keeps its old name
    as a deprecated same-object alias until the cleanup stage — the
    wire-key recipe in docs/architecture/PERSONA.md.

``SCOPE_SPLIT``
    A ``*_vehicle`` flag: it DIES.  Its verb half becomes ``target``
    (a view verb) and its scope half becomes Team Management's
    per-member vehicle scope (``assigned`` — the ``driver_trucks``
    link).  The pair's ``*_all`` sibling carries the wide grant and
    maps to its own verb row.

``SERVICE``
    A cross-cutting service, not a feature: always on for every role,
    nothing to grant (the matrix UI's "SERVICES" band).  It works over
    whatever FEATURES the role is allowed — AI answers only from data
    the role can already see — so a service's access is simply its
    features' access.  END-OF-MIGRATION NOTE (owner, 2026-09-01):
    services should eventually leave ``FeatureSet`` entirely, so the
    matrix and the stored grants never mention them — deferred to the
    cleanup stage, recorded here so it cannot be forgotten.

``DERIVED``
    Computed by ``derive_service_perms`` from OTHER grants — never
    stored, never granted, but not always-on either (the alerts inbox
    scope follows vehicle visibility).  Untouched; the derivation
    re-reads the new names when they land.

``CONFIG``
    The config family (role/account scope pair).  Shipped 2026-07 as a
    deliberate cross-feature pair (SSOT:
    capabilities/config/docs/ARCHITECTURE.md) and OUT of this
    migration; a per-feature config split is its own future arc.

``PERSON_SPLIT``
    Person-scope ("my paystub", "my coaching", "my loads") — the same
    disease as ``*_vehicle`` but the subject is a PERSON, not a truck.
    The own flag dies into the feature's view verb; its width is the
    ROLE's (driver → self, everyone else → all), a pure function with
    no storage and no Team Management control —
    ``capabilities/permissions/scope.person_width``.  The own risk
    summary is not person width at all: its wall was "vehicle subject
    in assigned trucks", i.e. unit width, so it is a SCOPE_SPLIT.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum


class Fate(str, Enum):
    VERB_VIEW = "verb_view"
    VERB_MANAGE = "verb_manage"
    SCOPE_SPLIT = "scope_split"
    SERVICE = "service"
    DERIVED = "derived"
    CONFIG = "config"
    PERSON_SPLIT = "person_split"


@dataclass(frozen=True)
class Verdict:
    fate: Fate
    #: canonical post-migration name; None for fates that keep the flag
    #: out of the verb grammar (DERIVED / CONFIG / SERVICE).
    target: str | None = None
    note: str = ""


V, M, S, P = Fate.VERB_VIEW, Fate.VERB_MANAGE, Fate.SCOPE_SPLIT, Fate.PERSON_SPLIT

TAXONOMY: dict[str, Verdict] = {
    # ── read-only report/tool pages: bare nouns become view verbs ──
    "can_faults":        Verdict(V, "can_view_faults"),
    "can_fuel":          Verdict(V, "can_view_fuel"),
    "can_efficiency":    Verdict(V, "can_view_efficiency"),
    "can_health":        Verdict(V, "can_view_health"),
    "can_cameras":       Verdict(V, "can_view_cameras"),
    "can_kpi":           Verdict(V, "can_view_kpi"),
    "can_cost_reports":  Verdict(V, "can_view_cost_reports"),
    "can_fuel_cost":     Verdict(V, "can_view_fuel_cost"),
    "can_cost_per_mile": Verdict(V, "can_view_cost_per_mile"),
    "can_truck_anatomy": Verdict(
        V, "can_view_truck_anatomy",
        "DARK feature — stays in DARK_FEATURE_FIELDS under the new name."),
    "can_carrier_directory": Verdict(V, "can_view_carrier_directory"),
    "can_vehicle_docs":  Verdict(V, "can_view_vehicle_docs"),

    # ── wide/assigned pairs: _all → verb, _vehicle → dies into
    #    (view verb + Team Management scope=assigned) ────────────────
    # A pair whose _all member today bundles WRITES maps to the manage
    # verb (seeds also grant the view verb, so nobody loses reads).
    "can_vehicle_all":       Verdict(V, "can_view_vehicles"),
    "can_vehicle_vehicle":   Verdict(S, "can_view_vehicles"),
    "can_parking_all":       Verdict(V, "can_view_parking"),
    "can_parking_vehicle":   Verdict(S, "can_view_parking"),
    "can_geofence_all":      Verdict(
        M, "can_manage_geofence",
        "third of this shape (after maintenance and inspections): the "
        "zone CRUD — create and delete — rides the wide flag, so it is "
        "a manage verb, not a wide read"),
    "can_geofence_vehicle":  Verdict(S, "can_view_geofence"),
    "can_location_map":      Verdict(
        V, "can_view_location",
        "the pair's _all half despite the odd historic name"),
    "can_location_vehicle":  Verdict(S, "can_view_location"),
    "can_route_all":         Verdict(V, "can_view_routes"),
    "can_route_vehicle":     Verdict(S, "can_view_routes"),
    "can_events_all":        Verdict(V, "can_view_events"),
    "can_events_vehicle":    Verdict(S, "can_view_events"),
    "can_scorecard_all":     Verdict(V, "can_view_scorecards"),
    "can_scorecard_vehicle": Verdict(S, "can_view_scorecards"),
    "can_maintenance_all":   Verdict(
        M, "can_manage_maintenance",
        "today's flag bundles writes; seeds grant can_view_maintenance too"),
    "can_maintenance_vehicle": Verdict(S, "can_view_maintenance"),
    "can_work_orders_all":   Verdict(
        M, "can_manage_work_orders",
        "same shape as maintenance: writes bundled; view seeded alongside"),
    "can_work_orders_vehicle": Verdict(S, "can_view_work_orders"),
    "can_inspections_all":   Verdict(
        M, "can_manage_inspections",
        "same shape as maintenance, found when enforcement reached it: "
        "the flag bundles the account-wide WRITES — template CRUD, "
        "review, remind — behind eleven solo gates, not just the wide "
        "read.  Stage A read the docstring and judged it view-only"),
    "can_inspections_vehicle": Verdict(
        S, "can_view_inspections",
        "submitting an own-truck PTI stays feature-owned: any holder of "
        "the view verb may submit for a truck inside their scope, which "
        "is exactly today's behaviour under a cleaner name"),

    # ── single-flag features whose page includes edits ─────────────
    "can_parts":         Verdict(
        M, "can_manage_parts",
        "reads for WO-editor pickers stay open by design (feature-owned)"),
    "can_service_tasks": Verdict(
        M, "can_manage_service_tasks",
        "reads stay open to task/WO creators so pickers never break"),
    "can_loads_all":     Verdict(V, "can_view_loads"),

    # ── already-grammatical manage flags: target == self, no rename ─
    "can_manage_vehicle_docs":      Verdict(M, "can_manage_vehicle_docs"),
    "can_manage_users":             Verdict(M, "can_manage_users"),
    "can_manage_companies":         Verdict(M, "can_manage_companies"),
    "can_manage_vehicles":          Verdict(M, "can_manage_vehicles"),
    "can_manage_loads":             Verdict(M, "can_manage_loads"),
    "can_manage_account":           Verdict(M, "can_manage_account"),
    "can_manage_permissions":       Verdict(M, "can_manage_permissions"),
    "can_manage_integrations":      Verdict(M, "can_manage_integrations"),
    "can_manage_storage":           Verdict(M, "can_manage_storage"),
    "can_manage_work_hours":        Verdict(M, "can_manage_work_hours"),
    "can_manage_billing":           Verdict(M, "can_manage_billing"),
    "can_manage_poi_layers":        Verdict(M, "can_manage_poi_layers"),
    "can_manage_driver_docs":       Verdict(M, "can_manage_driver_docs"),
    "can_manage_drivers":           Verdict(M, "can_manage_drivers"),
    "can_manage_applications":      Verdict(M, "can_manage_applications"),
    "can_manage_carrier_directory": Verdict(M, "can_manage_carrier_directory"),
    "can_manage_role_bot":          Verdict(
        M, "can_manage_role_bot",
        "manager-tier hard lock unchanged; never a base-role seed"),

    # ── admin flags renamed into the manage grammar ────────────────
    "can_driver_pay_admin": Verdict(M, "can_manage_driver_pay"),
    "can_coaching_admin":   Verdict(M, "can_manage_coaching"),
    "can_risk_report_all":  Verdict(
        V, "can_view_risk_reports",
        "generation is the feature's only interaction — one verb"),

    # ── action grants whose verb IS the feature: name kept ─────────
    # Renaming these buys no clarity and costs churn; the grammar
    # exception is deliberate and this note is its record.
    "can_invite":          Verdict(M, "can_invite",
                                   "deliberately narrower than can_manage_users"),
    "can_onboard_drivers": Verdict(M, "can_onboard_drivers",
                                   "deliberately narrower than both neighbours"),

    # ── services: always on, nothing to grant ──────────────────────
    "can_ai_chat":        Verdict(Fate.SERVICE),
    "can_digest":         Verdict(Fate.SERVICE),

    # ── computed from other grants, never stored ───────────────────
    "can_alerts_all":     Verdict(Fate.DERIVED),
    "can_alerts_vehicle": Verdict(
        Fate.DERIVED,
        note="derivation input moves from can_vehicle_all to the TM "
             "scope when the enforcement stage reaches alerts.  Done in "
             "stage E: alerts follow VEHICLE visibility, so every gate "
             "asks can_view_vehicles and every width read asks the width "
             "core for the vehicles feature; the derived pair now only "
             "feeds the wire and the bot's menus"),

    # ── out of this migration, on the record ───────────────────────
    "can_manage_config_role": Verdict(Fate.CONFIG),
    "can_manage_config_all":  Verdict(Fate.CONFIG),
    # ── person pairs: the own half dies into the view verb, width is
    #    the role's (scope.person_width) ─────────────────────────────
    "can_loads_own":           Verdict(P, "can_view_loads"),
    "can_driver_pay_view_own": Verdict(P, "can_view_driver_pay"),
    "can_coaching_view_own":   Verdict(P, "can_view_coaching"),
    "can_driver_docs_own":     Verdict(P, "can_view_driver_docs"),
    # The own risk summary walled "a vehicle subject in the caller's
    # assigned trucks" — unit width in disguise, so it joins the unit
    # family: (can_risk_report_all, can_risk_report_own) → risk_reports.
    "can_risk_report_own":     Verdict(S, "can_view_risk_reports"),
}
