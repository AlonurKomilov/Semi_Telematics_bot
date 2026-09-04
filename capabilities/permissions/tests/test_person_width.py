"""Person width — the second family's width is the role's, not a grant's.

The five `*_own` flags fold into view verbs; width for a person-subject
feature is a pure function of the role (driver → 'self', else 'all')
with no storage and no Team Management control.  The pre-flight planner
names the stored rows the fold would silently widen or narrow.
"""

from __future__ import annotations

import os

os.environ.setdefault("ENCRYPTION_KEY", "")

import pytest

from capabilities.permissions.fold import OWN_TO_WIDE_VERB, plan_own_preflight
from capabilities.permissions.roles import Role
from capabilities.permissions.scope import PAIRED_PERSON_FEATURES, person_width


class TestPersonWidth:
    def test_driver_is_self_everyone_else_is_all(self):
        for feature in PAIRED_PERSON_FEATURES:
            assert person_width(Role.DRIVER, feature) == "self"
            assert person_width("driver", feature) == "self"
            for r in Role:
                if r is not Role.DRIVER:
                    assert person_width(r, feature) == "all", (r, feature)

    def test_a_manager_tier_driver_is_still_a_driver(self):
        # The tier adds verbs, never width.
        assert person_width(Role.DRIVER, "driver_pay", is_manager=True) == "self"

    def test_only_person_paired_features_are_answered(self):
        with pytest.raises(KeyError):
            person_width(Role.DRIVER, "vehicles")   # a UNIT feature: unit_width's
        with pytest.raises(KeyError):
            person_width(Role.DRIVER, "nonsense")

    def test_every_own_flag_has_a_wide_verb_and_a_noun(self):
        # risk_report_own is vehicle width in disguise: it folds into the
        # unit family, so it has a wide verb here but no person noun.
        assert set(PAIRED_PERSON_FEATURES.values()) < set(OWN_TO_WIDE_VERB)
        assert "can_risk_report_own" in OWN_TO_WIDE_VERB


class TestPreflightPlanner:
    def test_staff_holding_only_the_own_half_is_reported(self):
        row = {"can_loads_own": True, "can_view_loads": False}
        assert plan_own_preflight("fleet", row) == [("can_loads_own", "staff_own_only")]

    def test_staff_holding_the_wide_verb_too_is_not(self):
        row = {"can_loads_own": True, "can_view_loads": True,
               "can_driver_docs_own": True, "can_manage_driver_docs": True}
        assert plan_own_preflight("safety", row) == []

    def test_a_driver_holding_the_wide_verb_is_reported(self):
        row = {"can_loads_own": True, "can_view_loads": True}
        assert plan_own_preflight("driver", row) == [("can_loads_own", "driver_holds_wide")]

    def test_a_driver_with_only_own_is_the_expected_shape(self):
        row = {k: True for k in OWN_TO_WIDE_VERB}
        assert plan_own_preflight("driver", row) == []

    def test_tier_keys_resolve_to_their_base_role(self):
        row = {"can_coaching_view_own": True, "can_manage_coaching": False}
        assert plan_own_preflight("hr__manager", row) == [("can_coaching_view_own", "staff_own_only")]

    def test_owner_rows_are_never_reported(self):
        row = {"can_loads_own": True, "can_view_loads": False}
        assert plan_own_preflight("owner", row) == []
        assert plan_own_preflight("owner__co", row) == []

    def test_absent_keys_read_as_false(self):
        assert plan_own_preflight("fleet", {}) == []
        assert plan_own_preflight("fleet", {"can_risk_report_own": True}) == [
            ("can_risk_report_own", "staff_own_only")]


class TestStaleOwnCrumbs:
    """The residue the person fold must not enshrine.

    Shapes taken from the owner's pre-flight (2026-09-04): eleven
    recruiter rows carrying all four own flags the seed turned off in
    327bf160, and nine fleet rows carrying the risk-summary own flag
    the fleet seed still grants.
    """

    def test_the_recruiter_shape_is_residue(self):
        from capabilities.permissions.fold import stale_own_crumbs
        row = {"can_risk_report_own": True, "can_driver_pay_view_own": True,
               "can_coaching_view_own": True, "can_driver_docs_own": True}
        assert stale_own_crumbs("recruiter", row) == [
            "can_coaching_view_own", "can_driver_docs_own",
            "can_driver_pay_view_own", "can_risk_report_own",
        ]

    def test_the_tier_row_is_swept_with_its_base(self):
        from capabilities.permissions.fold import stale_own_crumbs
        assert stale_own_crumbs("recruiter__manager", {"can_coaching_view_own": True}) == [
            "can_coaching_view_own"]

    def test_a_seed_that_still_grants_the_own_half_is_a_default_not_residue(self):
        # fleet's risk summary: the CURRENT seed grants it, so the row
        # is a live default and the owner decides it in the matrix.
        from capabilities.permissions.fold import stale_own_crumbs
        assert stale_own_crumbs("fleet", {"can_risk_report_own": True}) == []

    def test_a_seed_that_grants_the_wide_half_is_not_residue_either(self):
        from capabilities.permissions.fold import stale_own_crumbs
        # safety seeds can_manage_coaching: the own key beside it is a
        # default's shadow, not residue.
        assert "can_coaching_view_own" not in stale_own_crumbs(
            "safety", {"can_coaching_view_own": True})

    def test_a_wide_grant_on_the_row_is_someones_choice(self):
        from capabilities.permissions.fold import stale_own_crumbs
        row = {"can_coaching_view_own": True, "can_manage_coaching": True}
        assert stale_own_crumbs("recruiter", row) == []

    def test_owner_rows_and_unknown_keys_are_left_alone(self):
        from capabilities.permissions.fold import stale_own_crumbs
        row = {"can_risk_report_own": True}
        assert stale_own_crumbs("owner", row) == []
        assert stale_own_crumbs("owner__co", row) == []
        assert stale_own_crumbs("nonsense", row) == []

    def test_a_false_crumb_is_not_swept(self):
        from capabilities.permissions.fold import stale_own_crumbs
        assert stale_own_crumbs("recruiter", {"can_risk_report_own": False}) == []

    def test_the_sweep_leaves_every_other_key_exactly_as_stored(self):
        # The script builds the cleaned row this way; pin the shape.
        from capabilities.permissions.fold import stale_own_crumbs
        row = {"can_risk_report_own": True, "can_view_faults": True,
               "can_invite": False, "can_manage_carrier_directory": True}
        crumbs = stale_own_crumbs("recruiter", row)
        cleaned = {k: v for k, v in row.items() if k not in crumbs}
        assert cleaned == {"can_view_faults": True, "can_invite": False,
                           "can_manage_carrier_directory": True}
