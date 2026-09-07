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
    """Post-flip invariant: every verb TARGET is a physical field, the
    config pair survives under its own name, and no legacy name is a
    field any more."""
    targets = {v.target for v in TAXONOMY.values() if v.target}
    survivors = {f for f, v in TAXONOMY.items()
                 if v.fate is Fate.CONFIG}
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
            # the ten *_vehicle halves, and the one own half whose wall was
            # unit width all along (can_risk_report_own → risk_reports)
            assert flag.endswith(("_vehicle", "_own")), flag
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


def test_the_services_are_view_verbs_now():
    """Alerts, AI and Reports were "always on, nothing to grant" — four
    flags computed by derive_service_perms.  Since 2026-09-06 a service
    is granted per role like a feature: each legacy flag has a view-verb
    target, the alerts pair is a unit split (its width Team
    Management's), and no fate is left that computes anything."""
    assert not hasattr(Fate, "SERVICE") and not hasattr(Fate, "DERIVED")
    assert TAXONOMY["can_ai_chat"].target == "can_view_ai_assistant"
    assert TAXONOMY["can_digest"].target == "can_view_reports"
    assert TAXONOMY["can_alerts_all"].target == "can_view_alerts"
    assert TAXONOMY["can_alerts_vehicle"].fate is Fate.SCOPE_SPLIT
    assert TAXONOMY["can_alerts_vehicle"].target == "can_view_alerts"
    for f in ("can_view_alerts", "can_view_ai_assistant", "can_view_reports"):
        assert f in FLAGS, f
    for f in ("can_ai_chat", "can_digest", "can_alerts_all", "can_alerts_vehicle"):
        assert f not in FLAGS, f


def test_config_rows_are_exactly_the_config_pair():
    config = {f for f, v in TAXONOMY.items() if v.fate is Fate.CONFIG}
    assert config == {"can_manage_config_role", "can_manage_config_all"}


def test_own_family_died_into_view_verbs():
    """Every flag whose name says "own" has a split fate — PERSON for
    the four person-subject features, SCOPE for the risk summary whose
    wall was really unit width — and none survives as a field."""
    named_own = {f for f in TAXONOMY if f.endswith("_own")}
    person = {f for f, v in TAXONOMY.items() if v.fate is Fate.PERSON_SPLIT}
    assert person == {"can_loads_own", "can_driver_pay_view_own",
                      "can_coaching_view_own", "can_driver_docs_own"}
    assert TAXONOMY["can_risk_report_own"].fate is Fate.SCOPE_SPLIT
    assert named_own == person | {"can_risk_report_own"}
    for f in named_own:
        assert f not in FLAGS, f"{f} is still a FeatureSet field"
        assert TAXONOMY[f].target in FLAGS, (f, TAXONOMY[f].target)
        assert TAXONOMY[f].target.startswith("can_view_"), f


def test_non_verb_fates_carry_no_target():
    """A CONFIG row has no canonical rename —
    a target on one is almost always a positional-argument slip (a
    note landing in the target slot).  It happened: can_alerts_vehicle
    shipped with its NOTE as its target, and the bridge stage tried to
    install a property named by a full English sentence."""
    for flag, v in TAXONOMY.items():
        if v.fate is Fate.CONFIG:
            assert v.target is None, f"{flag}: {v.target!r}"


def test_dark_features_keep_their_darkness_across_the_rename():
    """DARK_FEATURE_FIELDS names canonical fields now; the contract row
    that produced each must carry the DARK note."""
    by_target = {v.target: (f, v) for f, v in TAXONOMY.items() if v.target}
    for field in DARK_FEATURE_FIELDS:
        assert field in by_target, field
        _flag, v = by_target[field]
        assert "DARK" in v.note, field
