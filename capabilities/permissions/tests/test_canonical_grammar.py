"""The alias layer after the flip: legacy names over canonical fields.

Stage E part 2 of the verb/scope migration.  Canonical fields are
PHYSICAL; every legacy name is a deprecated property over its target
until the legacy-pair ratchet reaches zero.  These walk the TAXONOMY —
the contract — not the built maps.
"""

from __future__ import annotations

import os

os.environ.setdefault("ENCRYPTION_KEY", "")

from dataclasses import fields, replace

from capabilities.permissions.roles import (
    LEGACY_TO_CANONICAL, PAIRED_UNIT_FEATURES, UNIT_FEATURES,
    FeatureSet, MANAGER_GRANTS, TIER_GRANTS, ROLE_PERMISSIONS,
    normalize_stored_perm_keys, wire_perms,
)
from capabilities.permissions.taxonomy import TAXONOMY, Fate

FIELDS = {f.name for f in fields(FeatureSet)}


def test_every_legacy_name_is_an_alias_and_not_a_field():
    for legacy, canonical in LEGACY_TO_CANONICAL.items():
        assert legacy not in FIELDS, legacy
        assert canonical in FIELDS, canonical
        assert isinstance(getattr(FeatureSet, legacy), property), legacy


def test_alias_mirrors_its_field_for_every_rename():
    for flag, v in TAXONOMY.items():
        if v.fate in (Fate.VERB_VIEW, Fate.VERB_MANAGE) and v.target != flag and flag in LEGACY_TO_CANONICAL:
            target = LEGACY_TO_CANONICAL[flag]
            base = FeatureSet()
            default = getattr(base, target)          # some fields default True
            assert getattr(base, flag) is default, flag
            flipped = replace(base, **{target: not default})
            assert getattr(flipped, flag) is (not default), flag


def test_pair_aliases_read_the_folded_fields():
    for noun, (wide, narrow) in PAIRED_UNIT_FEATURES.items():
        view, manage = UNIT_FEATURES[noun]
        fs = replace(FeatureSet(), **{view: True})
        assert getattr(fs, narrow) is True            # narrow half → view
        if manage:
            assert getattr(fs, wide) is False         # wide half → manage
            assert getattr(replace(fs, **{manage: True}), wide) is True
        else:
            assert getattr(fs, wide) is True          # pure view pair: both → view


def test_stored_legacy_keys_fold_onto_canonical():
    out = normalize_stored_perm_keys({"can_faults": True})
    assert out == {"can_view_faults": True}
    # pure-view pair: either half opens the feature
    assert normalize_stored_perm_keys({"can_events_all": False, "can_events_vehicle": True}) == {"can_view_events": True}
    # manage-pair: wide → manage (and implies opening), narrow → view
    assert normalize_stored_perm_keys({"can_maintenance_all": True, "can_maintenance_vehicle": False}) == {
        "can_manage_maintenance": True, "can_view_maintenance": True}
    assert normalize_stored_perm_keys({"can_maintenance_all": False, "can_maintenance_vehicle": True}) == {
        "can_manage_maintenance": False, "can_view_maintenance": True}


def test_a_collision_grants_if_either_side_grants():
    """A row carrying both a legacy key and its canonical target has no
    timestamp per key, so neither may silently revoke the other: the
    values combine by OR.  The sweep that rewrites stored rows to
    canonical keys makes this case impossible afterwards."""
    assert normalize_stored_perm_keys({"can_faults": True, "can_view_faults": False})["can_view_faults"] is True
    assert normalize_stored_perm_keys({"can_faults": False, "can_view_faults": True})["can_view_faults"] is True
    assert normalize_stored_perm_keys({"can_faults": False, "can_view_faults": False})["can_view_faults"] is False


def test_wire_carries_both_grammars_equal_by_construction():
    d = wire_perms(replace(FeatureSet(), can_manage_maintenance=True, can_view_faults=True))
    for legacy, canonical in LEGACY_TO_CANONICAL.items():
        assert d[legacy] == d[canonical], legacy


def test_tier_and_manager_grants_are_physical_fields():
    for tier in TIER_GRANTS.values():
        assert set(tier.grants) <= FIELDS, set(tier.grants) - FIELDS
    for grants in MANAGER_GRANTS.values():
        assert set(grants) <= FIELDS, set(grants) - FIELDS


def test_seeds_speak_only_canonical():
    # Constructing a FeatureSet with a legacy kwarg is a TypeError — so
    # the seeds importing at all proves it; make the claim explicit.
    for role, fs in ROLE_PERMISSIONS.items():
        assert isinstance(fs, FeatureSet), role


class TestManageImpliesViewOnStoredRows:
    """The seeds grant View wherever they grant Manage; a stored row
    must resolve the same way, or an owner's pre-fold Manage grant
    leaves a role with Manage and no View — gates open, page hidden."""

    def test_a_stored_manage_grant_opens_the_view_verb(self):
        from capabilities.permissions.roles import normalize_stored_perm_keys
        out = normalize_stored_perm_keys({"can_manage_coaching": True})
        assert out["can_view_coaching"] is True
        out = normalize_stored_perm_keys({"can_manage_maintenance": True, "can_view_maintenance": False})
        assert out["can_view_maintenance"] is True      # manage wins over a stale False

    def test_a_stored_manage_revocation_adds_nothing(self):
        from capabilities.permissions.roles import normalize_stored_perm_keys
        out = normalize_stored_perm_keys({"can_manage_coaching": False})
        assert "can_view_coaching" not in out            # the seed decides View
        assert out["can_manage_coaching"] is False

    def test_every_manage_verb_of_both_families_is_covered(self):
        from capabilities.permissions.roles import (
            PERSON_FEATURES, UNIT_FEATURES, normalize_stored_perm_keys,
        )
        pairs = [p for p in (*UNIT_FEATURES.values(), *PERSON_FEATURES.values()) if p[1]]
        assert pairs, "no manage pairs?"
        for view, manage in pairs:
            assert normalize_stored_perm_keys({manage: True})[view] is True, (view, manage)
