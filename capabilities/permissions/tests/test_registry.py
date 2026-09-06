"""The feature registry — every flag has one home, the mask is derived.

Billing, the department mask and the plan mask speak in FEATURE ids;
the registry is where those ids live on the backend.  These pin its
invariants; the cross-layer agreement with the dashboard's catalog and
matrix tree is tests/test_feature_registry_drift.py.
"""

from __future__ import annotations

import os

os.environ.setdefault("ENCRYPTION_KEY", "")

from capabilities.permissions.registry import (
    CROSS_FEATURE_FLAGS, ENTRIES, FEATURES, MASK_DRIFT, REGISTRY, SERVICES,
    TOGGLEABLE_MODULES, derive_flag_modules, owner_of,
)
from capabilities.permissions.roles import FeatureSet

FIELDS = set(FeatureSet.__dataclass_fields__)

#: the hand-written mask the registry replaced (2026-09-06) — the
#: derivation must reproduce it exactly, drift and all, so this step
#: changes no account's behaviour.  Close a drift by deleting the flag
#: from registry.MASK_DRIFT and adding it HERE with its departments.
MASK_BEFORE = {
    "can_view_maintenance": {"fleet"}, "can_manage_maintenance": {"fleet"},
    "can_view_work_orders": {"fleet"}, "can_manage_work_orders": {"fleet"},
    "can_view_inspections": {"fleet"}, "can_manage_inspections": {"fleet"},
    "can_view_routes": {"dispatch"},
    "can_view_geofence": {"dispatch", "fleet"}, "can_manage_geofence": {"dispatch", "fleet"},
    "can_view_cameras": {"fleet", "safety"},
    "can_view_parking": {"dispatch", "fleet", "safety"},
    "can_view_events": {"hr", "safety"}, "can_view_scorecards": {"hr", "safety"},
    "can_manage_coaching": {"hr", "safety"}, "can_view_coaching": {"hr", "safety"},
    "can_manage_driver_docs": {"fleet", "hr", "safety"}, "can_view_driver_docs": {"fleet", "hr", "safety"},
    "can_manage_drivers": {"fleet", "hr"},
    "can_manage_applications": {"hr"}, "can_onboard_drivers": {"hr"},
    "can_view_fuel_cost": {"accounting", "dispatch"}, "can_view_cost_per_mile": {"accounting", "fleet"},
    "can_manage_driver_pay": {"accounting"}, "can_view_driver_pay": {"accounting"},
}


def test_every_field_has_exactly_one_home():
    homeless = sorted(f for f in FIELDS if owner_of(f) is None and f not in CROSS_FEATURE_FLAGS)
    assert not homeless, f"fields no entry owns: {homeless}"
    doubled = sorted(f for f in FIELDS if sum(1 for e in ENTRIES if f in e.flags) > 1)
    assert not doubled, f"fields two entries claim: {doubled}"
    ghosts = sorted(f for e in ENTRIES for f in e.flags if f not in FIELDS)
    assert not ghosts, f"registry flags that are not fields: {ghosts}"
    assert not (CROSS_FEATURE_FLAGS - FIELDS)


def test_kinds_and_tiers():
    assert set(SERVICES) == {"ai_assistant", "alerts", "reports"}
    for e in SERVICES.values():
        assert e.tier is None, e.id
    for e in FEATURES.values():
        assert e.tier in ("shared", "role", "administration"), e.id
    assert set(FEATURES) | set(SERVICES) == set(REGISTRY)


def test_the_door_is_under_the_roof():
    # What opens an entry is one of its own flags — unless it has none
    # and rides another feature's (vendors, audit log, the alerts door).
    for e in ENTRIES:
        if e.flags:
            for f in e.opens:
                assert f in e.flags or owner_of(f) is not None and e.kind == "service", (e.id, f)


def test_core_and_account_are_never_masked():
    for e in ENTRIES:
        if e.modules & {"core", "account"} or not e.modules:
            assert not e.maskable, e.id
        else:
            assert e.maskable, e.id
            assert e.modules <= set(TOGGLEABLE_MODULES), e.id


def test_the_derived_mask_is_the_mask_the_accounts_had():
    assert derive_flag_modules() == {k: frozenset(v) for k, v in MASK_BEFORE.items()}


def test_mask_drift_is_real_and_may_only_shrink():
    # Every drifted flag belongs to a department-owned entry (else the
    # entry is not drift, it is core) and stays out of the derived mask.
    derived = derive_flag_modules()
    for f in MASK_DRIFT:
        e = owner_of(f)
        assert e is not None and e.maskable, f
        assert f not in derived, f
    assert MASK_DRIFT <= {
        "can_view_loads", "can_manage_loads",
        "can_view_carrier_directory", "can_manage_carrier_directory",
        "can_manage_parts", "can_manage_service_tasks", "can_view_truck_anatomy",
        "can_view_risk_reports", "can_view_cost_reports",
    }, "MASK_DRIFT grew — a new feature must honour its department switch from day one"


def test_a_narrowed_flag_keeps_its_own_departments():
    drivers = REGISTRY["drivers"]
    assert drivers.modules == {"hr", "fleet", "safety"}
    assert drivers.modules_of("can_onboard_drivers") == {"hr"}
    assert drivers.modules_of("can_manage_drivers") == {"fleet", "hr"}
    assert drivers.modules_of("can_view_driver_docs") == drivers.modules


def test_sub_features_nest_under_a_real_parent():
    # A sub-feature's parent is a feature (Documents under Vehicles) or
    # a service (Scheduled Reports under Reports — the owner's call:
    # one permission on the service covers its subscription).
    for e in ENTRIES:
        if e.parent:
            assert e.parent in REGISTRY, e.id
            assert REGISTRY[e.parent].parent is None, f"{e.id}: nesting is one level deep"
    assert REGISTRY["vehicle_documents"].parent == "vehicles"
    assert REGISTRY["scheduled_reports"].parent == "reports"


def test_a_narrowed_flag_names_only_its_features_departments():
    # A narrowing may only SUBTRACT: a department the feature does not
    # belong to, or one that is not toggleable, could never be switched
    # off — the flag would silently become unmaskable.
    for e in ENTRIES:
        for flag, own in e.flags.items():
            if own is None:
                continue
            assert own, (e.id, flag)
            assert own <= e.modules, (e.id, flag, sorted(own), sorted(e.modules))
            assert own <= set(TOGGLEABLE_MODULES), (e.id, flag)

