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
