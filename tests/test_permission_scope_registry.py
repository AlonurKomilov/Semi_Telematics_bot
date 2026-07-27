"""OWN_VEHICLE_SCOPE_FLAGS registry completeness (roles.py).

The registry is the developer-facing answer to "what is
``can_<x>_vehicle`` and who consumes it" — it is only trustworthy if it
can never drift from FeatureSet.  Both directions are enforced:

* every FeatureSet field named ``*_vehicle`` must be registered
  (a new own-vehicle flag without a registry entry fails here);
* every registry entry must be a real FeatureSet field
  (a renamed/removed flag can't leave a stale registry line).
"""

from dataclasses import fields

from capabilities.permissions.roles import (
    FeatureSet,
    OWN_VEHICLE_SCOPE_FLAGS,
    ROLE_PERMISSIONS,
    Role,
)


def _featureset_vehicle_fields() -> set[str]:
    return {
        f.name for f in fields(FeatureSet)
        if f.name.endswith("_vehicle")
    }


def test_every_vehicle_flag_is_registered():
    missing = _featureset_vehicle_fields() - set(OWN_VEHICLE_SCOPE_FLAGS)
    assert not missing, (
        f"FeatureSet gained own-vehicle flag(s) {sorted(missing)} — "
        "register them in OWN_VEHICLE_SCOPE_FLAGS with a consumer note."
    )


def test_every_registry_entry_exists():
    stale = set(OWN_VEHICLE_SCOPE_FLAGS) - _featureset_vehicle_fields()
    assert not stale, (
        f"OWN_VEHICLE_SCOPE_FLAGS lists {sorted(stale)} which FeatureSet "
        "no longer defines — remove or rename the registry entries."
    )


def test_no_duplicates_in_registry():
    assert len(OWN_VEHICLE_SCOPE_FLAGS) == len(set(OWN_VEHICLE_SCOPE_FLAGS))


def test_driver_holds_own_vehicle_scope():
    """The driver role is the reason the scope class exists — it must
    hold the core own-vehicle grants (and never the account-wide pair)."""
    driver = ROLE_PERMISSIONS[Role.DRIVER]
    for flag in ("can_vehicle_vehicle", "can_parking_vehicle",
                 "can_inspections_vehicle", "can_events_vehicle"):
        assert getattr(driver, flag) is True, f"driver lost {flag}"
    assert driver.can_vehicle_all is False
    assert driver.can_parking_all is False


def test_employment_roles_never_operate_in_own_vehicle_scope():
    """THE SPLIT, as CI law (owner decision 2026-07-27): employment
    roles operate account-wide or not at all — own-vehicle scope
    belongs to Driver.

    A seed may hold ``*_vehicle`` only redundantly next to its
    ``*_all`` (harmless under the ANY-OF gates); holding own-vehicle
    scope WITHOUT the account-wide flag is the driver template leaking
    into an employment role — exactly the recruiter bug this guards
    against.  Owners can still grant vehicle data to any role via the
    Permissions matrix, using the ``*_all`` flags.

    ``can_alerts_vehicle`` is derived at runtime, never seeded — it
    passes trivially.  ``can_location_vehicle`` pairs with
    ``can_location_map`` (naming predates the convention).
    """
    paired_all = {
        "can_location_vehicle": "can_location_map",
        "can_scorecard_vehicle": "can_scorecard_all",
    }
    for role, seed in ROLE_PERMISSIONS.items():
        if role == Role.DRIVER:
            continue
        for flag in OWN_VEHICLE_SCOPE_FLAGS:
            if not getattr(seed, flag, False):
                continue
            all_flag = paired_all.get(flag) or (
                flag[: -len("_vehicle")] + "_all"
            )
            assert getattr(seed, all_flag, False) is True, (
                f"{role}: employment role holds own-vehicle scope "
                f"{flag} without {all_flag} — driver-template leak; "
                "grant the account-wide flag or drop the vehicle one."
            )
