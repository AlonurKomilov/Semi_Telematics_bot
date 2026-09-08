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
    CROSS_FEATURE_FLAGS, ENTRIES, FEATURES, REGISTRY, SERVICES,
    TOGGLEABLE_MODULES, derive_flag_modules, owner_of,
)
from capabilities.permissions.roles import FeatureSet, ROLE_PERMISSIONS

FIELDS = set(FeatureSet.__dataclass_fields__)

#: the department mask, pinned.  The hand-written list the registry
#: replaced (2026-09-06) had the first 24; the nine below it were the
#: drift — features that belong to a department but whose switch only
#: hid the page — closed the same day by the owner.  Adding a flag
#: here is a deliberate edit: a new feature honours its department
#: switch from its first day.
MASK = {
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
    # the seven features whose switch used to hide the page only
    "can_view_loads": {"dispatch"}, "can_manage_loads": {"dispatch"},
    "can_view_carrier_directory": {"hr"}, "can_manage_carrier_directory": {"hr"},
    "can_manage_parts": {"fleet"}, "can_manage_service_tasks": {"fleet"},
    "can_view_truck_anatomy": {"fleet"},
    "can_view_risk_reports": {"safety"},
    "can_view_cost_reports": {"accounting"},
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
    # Four since 2026-09-08: Mods joined as a service — one View row so a
    # role can be withheld the personal look like any channel.
    assert set(SERVICES) == {"ai_assistant", "alerts", "reports", "mods"}
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


def test_the_derived_mask_is_pinned():
    assert derive_flag_modules() == {k: frozenset(v) for k, v in MASK.items()}


def test_every_department_flag_honours_its_switch():
    # No exemptions: every flag of a department-owned feature is in the
    # mask — a switch is a switch for the API, not only for the nav.
    derived = derive_flag_modules()
    for e in ENTRIES:
        if e.maskable:
            for flag in e.flags:
                assert flag in derived, (e.id, flag)
        else:
            for flag in e.flags:
                assert flag not in derived, (e.id, flag)


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



def test_a_service_is_seeded_for_every_role_that_should_hold_it():
    """Mods is ON for everyone — the owner's call — and withheld by editing
    the matrix, never by shipping it off.

    Two halves, because they fail in different ways. The SEEDS decide what
    a fresh account's roles get. The FIELD DEFAULT decides what an account
    whose stored permission row predates the field gets: the resolver
    starts from the seed and lays the stored row over it, so a field that
    defaults False would take Mods away from every existing account on
    upgrade — silently, and only for accounts old enough to have a row.
    """
    assert FeatureSet().can_view_mods is True, (
        "can_view_mods defaults False — every account with a stored "
        "permission row loses Mods on upgrade"
    )
    # A role withheld in the SEEDS rather than in the matrix — that is a
    # decision for the owner to make per account, not one this repo ships.
    missing = sorted(r.value for r, fs in ROLE_PERMISSIONS.items() if not fs.can_view_mods)
    assert missing == [], f"roles seeded without Mods: {missing}"
