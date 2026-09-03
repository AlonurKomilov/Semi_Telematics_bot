"""The taxonomy is a CONTRACT, so drift in either direction is red.

The migration will span many commits by three writers; the one failure
mode that would poison all of it is the table and the FeatureSet
disagreeing about what exists.  Every rule here was proven red before
it was trusted.
"""

from __future__ import annotations

import dataclasses
import re

from capabilities.permissions.roles import (
    DARK_FEATURE_FIELDS,
    FeatureSet,
)
from capabilities.permissions.taxonomy import TAXONOMY, Fate


FLAGS = {f.name for f in dataclasses.fields(FeatureSet)
         if f.name.startswith("can_")}


def test_the_contract_matches_the_flipped_featureset():
    """Post-flip invariant: every verb TARGET is a physical field, every
    non-verb flag (derived / service / config / parked) survives under
    its own name, and no legacy pair name is a field any more."""
    targets = {v.target for v in TAXONOMY.values() if v.target}
    survivors = {f for f, v in TAXONOMY.items()
                 if v.fate in (Fate.DERIVED, Fate.SERVICE, Fate.CONFIG, Fate.OWN_LATER)}
    assert targets | survivors == FLAGS, (
        f"fields without a contract: {sorted(FLAGS - targets - survivors)}; "
        f"contract names that are not fields: {sorted((targets | survivors) - FLAGS)}")
    dead = {f for f, v in TAXONOMY.items() if v.fate is Fate.SCOPE_SPLIT}
    assert not (dead & FLAGS), f"dying names still physical: {sorted(dead & FLAGS)}"


def test_verb_targets_speak_the_grammar():
    """A verb row's target is can_view_* / can_manage_* — with the two
    deliberate action-grant exceptions spelled out HERE, so adding a
    third requires editing this set and owning the decision."""
    exceptions = frozenset({"can_invite", "can_onboard_drivers"})
    for flag, v in TAXONOMY.items():
        if v.fate in (Fate.VERB_VIEW, Fate.VERB_MANAGE):
            if flag in exceptions:
                assert v.target == flag
                continue
            want = "can_view_" if v.fate is Fate.VERB_VIEW else "can_manage_"
            assert v.target and v.target.startswith(want), (
                f"{flag}: {v.fate.value} target {v.target!r} "
                f"does not start {want!r}")


def test_scope_splits_die_into_a_view_verb():
    """Every split row is a *_vehicle flag whose verb half is a VIEW —
    the write half of any pair lives on the _all sibling, never on the
    flag that is dying."""
    for flag, v in TAXONOMY.items():
        if v.fate is Fate.SCOPE_SPLIT:
            assert flag.endswith("_vehicle"), flag
            assert v.target and v.target.startswith("can_view_"), (
                f"{flag} splits into {v.target!r} — a split's verb half "
                "must be a view verb")


def test_every_split_has_a_surviving_sibling():
    """The wide half of each pair must exist as a verb row over the
    same feature noun — otherwise the split orphans its feature."""
    verb_targets = {v.target for v in TAXONOMY.values()
                    if v.fate in (Fate.VERB_VIEW, Fate.VERB_MANAGE)}
    for flag, v in TAXONOMY.items():
        if v.fate is Fate.SCOPE_SPLIT:
            noun = v.target.removeprefix("can_view_")
            assert (f"can_view_{noun}" in verb_targets
                    or f"can_manage_{noun}" in verb_targets), (
                f"{flag}: no surviving verb row for feature {noun!r}")


def test_no_two_verb_rows_share_a_target():
    """A split row may converge on a verb row's target (that is the
    point); two VERB rows converging would silently merge two grants."""
    seen: dict[str, str] = {}
    for flag, v in TAXONOMY.items():
        if v.fate in (Fate.VERB_VIEW, Fate.VERB_MANAGE):
            assert v.target not in seen, (
                f"{flag} and {seen[v.target]} both target {v.target!r}")
            seen[v.target] = flag


def test_services_and_derived_are_distinct_and_pinned():
    """The owner's split, pinned by name.  SERVICE = always on for
    every role, nothing to grant (the matrix UI's SERVICES band) — it
    rides whatever features the role is allowed.  DERIVED = computed
    from OTHER grants (the alerts inbox follows vehicle visibility) —
    also never stored, but not always-on.  Confusing the two buckets
    would either grant a service or freeze a derivation."""
    service = {f for f, v in TAXONOMY.items() if v.fate is Fate.SERVICE}
    derived = {f for f, v in TAXONOMY.items() if v.fate is Fate.DERIVED}
    assert service == {"can_ai_chat", "can_digest"}
    assert derived == {"can_alerts_all", "can_alerts_vehicle"}


def test_config_rows_are_exactly_the_config_pair():
    config = {f for f, v in TAXONOMY.items() if v.fate is Fate.CONFIG}
    assert config == {"can_manage_config_role", "can_manage_config_all"}


def test_own_family_is_marked_and_nothing_own_is_missed():
    """Person-scope flags all sit in OWN_LATER — and every flag whose
    name says "own" is in that set, so none is silently migrated with
    vehicle-scope semantics."""
    own = {f for f, v in TAXONOMY.items() if v.fate is Fate.OWN_LATER}
    named_own = {f for f in FLAGS if f.endswith("_own")}
    assert own == named_own, (
        f"own-named flags not marked OWN_LATER: {sorted(named_own - own)}; "
        f"OWN_LATER rows not own-named: {sorted(own - named_own)}")


def test_non_verb_fates_carry_no_target():
    """A DERIVED/CONFIG/SERVICE/OWN_LATER row has no canonical rename —
    a target on one is almost always a positional-argument slip (a
    note landing in the target slot).  It happened: can_alerts_vehicle
    shipped with its NOTE as its target, and the bridge stage tried to
    install a property named by a full English sentence."""
    for flag, v in TAXONOMY.items():
        if v.fate in (Fate.DERIVED, Fate.CONFIG, Fate.SERVICE,
                      Fate.OWN_LATER):
            assert v.target is None, f"{flag}: {v.target!r}"


def test_dark_features_keep_their_darkness_across_the_rename():
    """DARK_FEATURE_FIELDS names canonical fields now; the contract row
    that produced each must carry the DARK note."""
    by_target = {v.target: (f, v) for f, v in TAXONOMY.items() if v.target}
    for field in DARK_FEATURE_FIELDS:
        assert field in by_target, field
        _flag, v = by_target[field]
        assert "DARK" in v.note, field
