"""Person width — the second family's width is the role's, not a grant's.

The five `*_own` flags folded into view verbs; width for a person-subject
feature is a pure function of the role (driver → 'self', else 'all')
with no storage and no Team Management control.
"""

from __future__ import annotations

import os

os.environ.setdefault("ENCRYPTION_KEY", "")

import pytest

from capabilities.permissions.roles import Role
from capabilities.permissions.roles import (
    FeatureSet, PAIRED_PERSON_FEATURES, PAIRED_UNIT_FEATURES, PERSON_FEATURES,
)
from capabilities.permissions.scope import person_width


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

    def test_the_person_family_is_the_four_nouns_and_risk_is_a_unit_pair(self):
        # risk_report_own was vehicle width in disguise: it folded into
        # the unit family, so it has no person noun.
        assert set(PAIRED_PERSON_FEATURES) == {"loads", "driver_pay", "coaching", "driver_docs"}
        assert set(PERSON_FEATURES) == set(PAIRED_PERSON_FEATURES)
        assert "risk_reports" in PAIRED_UNIT_FEATURES
        fields = set(FeatureSet.__dataclass_fields__)
        for noun, (view, manage) in PERSON_FEATURES.items():
            assert view == f"can_view_{noun}" and view in fields, noun
            assert manage is None or manage in fields, noun
        for own in PAIRED_PERSON_FEATURES.values():
            assert own not in fields, own          # an alias now, not a field
