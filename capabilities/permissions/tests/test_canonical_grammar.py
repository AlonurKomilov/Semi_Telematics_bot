"""The alias bridge: every canonical verb name works TODAY.

Stage C of the verb/scope migration.  Legacy fields stay physical
(production speaks them); the canonical grammar is installed as
properties over them, and both live on the wire.  These tests walk the
TAXONOMY — the contract — not the built artifacts, so a contract row
whose bridge is missing fails loudly instead of vanishing from a loop.
"""

from __future__ import annotations

import os

os.environ.setdefault("ENCRYPTION_KEY", "")

from dataclasses import replace

from capabilities.permissions.roles import (
    CANONICAL_TO_LEGACY,
    FeatureSet,
    MANAGER_GRANTS,
    TIER_GRANTS,
    ROLE_PERMISSIONS,
    normalize_stored_perm_keys,
    wire_perms,
)
from capabilities.permissions.taxonomy import TAXONOMY, Fate


def test_every_contract_target_is_readable_on_a_featureset():
    fs = FeatureSet()
    for flag, v in TAXONOMY.items():
        if v.target:
            assert isinstance(getattr(fs, v.target), bool), v.target


def test_alias_equals_primary_for_every_rename():
    """The PERSONA recipe's core claim, walked from the contract:
    flip the legacy field and the canonical property flips with it."""
    for flag, v in TAXONOMY.items():
        if v.fate.value.startswith("verb_") and v.target != flag \
                and v.target in CANONICAL_TO_LEGACY:
            base = FeatureSet()
            flipped = replace(base, **{flag: not getattr(base, flag)})
            assert getattr(flipped, v.target) == getattr(flipped, flag), flag


def test_pair_view_verbs_are_an_or_of_their_pair():
    """can_view_maintenance asks "may open the feature at all" —
    width is Team Management's business now, not this layer's."""
    driver = FeatureSet(can_maintenance_vehicle=True)
    assert driver.can_view_maintenance and not driver.can_manage_maintenance
    fleet = FeatureSet(can_maintenance_all=True)
    assert fleet.can_view_maintenance and fleet.can_manage_maintenance
    nobody = FeatureSet()
    assert not nobody.can_view_maintenance
    # And a pure-view pair, both directions:
    assert FeatureSet(can_events_vehicle=True).can_view_events
    assert FeatureSet(can_events_all=True).can_view_events
    assert not FeatureSet().can_view_events


def test_stored_canonical_keys_land_on_legacy_fields():
    """The merge site drops unknown keys, so a grant stored under a
    canonical name MUST be mapped before that filter — otherwise a
    custom edit silently reverts to the role default."""
    out = normalize_stored_perm_keys({"can_view_faults": True})
    assert out == {"can_faults": True}


def test_legacy_wins_a_collision():
    """The matrix edits legacy today; a stale canonical duplicate must
    not shadow the fresher legacy write."""
    out = normalize_stored_perm_keys(
        {"can_view_faults": True, "can_faults": False})
    assert out == {"can_faults": False}


def test_wire_carries_both_grammars_equal_by_construction():
    fs = FeatureSet(can_maintenance_all=True, can_faults=True)
    d = wire_perms(fs)
    for canonical, legacy in CANONICAL_TO_LEGACY.items():
        assert d[canonical] == d[legacy], canonical
    for flag, v in TAXONOMY.items():
        if v.fate is Fate.SCOPE_SPLIT:
            assert v.target in d, v.target


def test_tier_and_manager_grants_are_physical_fields():
    """senior_default_featureset applies grants with dataclasses.replace,
    which rejects non-fields — so a grant string migrated to a
    property-only name would crash tier resolution at runtime.  Pin it
    here instead."""
    fields = set(FeatureSet.__dataclass_fields__)
    for tier in TIER_GRANTS.values():
        assert set(tier.grants) <= fields, set(tier.grants) - fields
    for grants in MANAGER_GRANTS.values():
        assert set(grants) <= fields, set(grants) - fields


def test_seeds_still_speak_legacy_only():
    """Stage C invariant: role seeds stay in legacy names (the
    physical fields).  The seed flip is stage E's move, after no
    reader of a legacy name is left — a canonical kwarg here today
    would crash FeatureSet(**seed) construction at import."""
    for role, fs in ROLE_PERMISSIONS.items():
        assert isinstance(fs, FeatureSet), role


def test_the_merge_site_normalizes_before_its_unknown_key_filter():
    """The stored-grant resolver drops unknown keys by design; the
    normalize call must run BEFORE that filter or canonical-stored
    grants silently revert to role defaults.  Source-pinned (the full
    path needs a live platform DB); the semantics above are the real
    guard — this pins the call site, and the trailing "(" pins the
    CALL, not a mention in a comment (the wall-guard lesson)."""
    import inspect
    from capabilities.permissions import roles
    src = inspect.getsource(roles._resolve_role_key_permissions) \
        if hasattr(roles, "_resolve_role_key_permissions") else ""
    if not src:  # resolver name differs — find it by its cache
        for name in dir(roles):
            fn = getattr(roles, name)
            if callable(fn) and "known_fields" in (inspect.getsource(fn)
                    if inspect.isfunction(fn) else ""):
                src = inspect.getsource(fn)
                break
    assert "normalize_stored_perm_keys(" in src
    assert src.index("normalize_stored_perm_keys(") < src.index("filtered")
