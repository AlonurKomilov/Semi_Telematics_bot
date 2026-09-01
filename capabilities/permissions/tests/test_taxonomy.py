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


def test_bijection_both_ways():
    """Every flag has exactly one verdict; every verdict names a flag.

    A flag added to FeatureSet without a verdict is a migration hole;
    a verdict for a deleted flag is the table lying about the world.
    """
    assert TAXONOMY.keys() == FLAGS, (
        f"missing verdicts: {sorted(FLAGS - TAXONOMY.keys())}; "
        f"stale verdicts: {sorted(TAXONOMY.keys() - FLAGS)}")


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


def test_derived_rows_are_exactly_the_derived_flags():
    """Pinned by name: these four are computed by derive_service_perms
    and must never be granted or migrated as stored keys."""
    derived = {f for f, v in TAXONOMY.items() if v.fate is Fate.DERIVED}
    assert derived == {"can_ai_chat", "can_digest",
                      "can_alerts_all", "can_alerts_vehicle"}


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


def test_dark_features_keep_their_darkness_across_the_rename():
    for flag in DARK_FEATURE_FIELDS:
        v = TAXONOMY[flag]
        assert "DARK" in v.note, (
            f"{flag} is a dark feature; its verdict must carry the DARK "
            "note so the rename stage knows to move the darkness too")
