"""The pair-death fold decides with SYNTHETIC grants only.

Real accounts are the owner's to inspect (scripts/fold_pair_width.py,
dry-run first); these pin the rule on FeatureSets built here, so the
rule is proven before it ever sees a customer row.
"""

from __future__ import annotations

import os

os.environ.setdefault("ENCRYPTION_KEY", "")

from dataclasses import replace

from capabilities.permissions.fold import (
    NARROW, NONE, WIDE, FoldDecision, builtin_width, classify_pairs,
    fold, merge_keys,
)
from capabilities.permissions.roles import (
    PAIRED_UNIT_FEATURES, ROLE_PERMISSIONS, Role,
)


def _narrow_all(fs):
    return replace(fs, **{w: False for w, _ in PAIRED_UNIT_FEATURES.values()},
                       **{n: True for _, n in PAIRED_UNIT_FEATURES.values()})


class TestSeedsFoldToNothing:
    """The deploy-safety claim: every seeded role folds to NO row,
    because seeds already equal the built-in defaults."""

    def test_every_seed_writes_no_row(self):
        for role, fs in ROLE_PERMISSIONS.items():
            d = fold(role.value, classify_pairs(fs))
            assert d.write is None, (role, d)
            assert d.shape in ("default", "no-access"), (role, d)

    def test_driver_seed_is_narrow_everywhere_it_can_see(self):
        classes = classify_pairs(ROLE_PERMISSIONS[Role.DRIVER])
        assert WIDE not in classes.values()
        assert NARROW in classes.values()


class TestTheRule:
    def test_consistent_narrow_non_driver_writes_assigned_losslessly(self):
        fs = _narrow_all(ROLE_PERMISSIONS[Role.FLEET])
        d = fold("fleet", classify_pairs(fs))
        assert d == FoldDecision("fleet", "assigned", "assigned", "consistent",
                                 (), (), tuple(sorted(PAIRED_UNIT_FEATURES)))

    def test_mixed_folds_narrow_and_names_what_is_lost(self):
        fs = ROLE_PERMISSIONS[Role.FLEET]           # wide everywhere
        w, n = PAIRED_UNIT_FEATURES["events"]
        fs = replace(fs, **{w: False, n: True})     # narrow on events only
        d = fold("fleet", classify_pairs(fs))
        assert d.write == "assigned" and d.shape == "mixed"
        assert d.narrow == ("events",)          # the evidence column
        assert "events" not in d.lost
        assert set(d.lost) == {f for f in PAIRED_UNIT_FEATURES if f != "events"}

    def test_a_widened_driver_is_mixed_but_writes_no_row(self):
        # An owner opened one feature account-wide for drivers.  The
        # built-in is the narrow side, so no row — yet the widening
        # folds away and must be reported.
        fs = ROLE_PERMISSIONS[Role.DRIVER]
        w, _ = PAIRED_UNIT_FEATURES["location"]
        fs = replace(fs, **{w: True})
        d = fold("driver", classify_pairs(fs))
        assert d.write is None and d.shape == "mixed" and d.lost == ("location",)

    def test_no_paired_access_at_all_does_not_vote(self):
        fs = replace(ROLE_PERMISSIONS[Role.FLEET],
                     **{w: False for w, _ in PAIRED_UNIT_FEATURES.values()},
                     **{n: False for _, n in PAIRED_UNIT_FEATURES.values()})
        d = fold("fleet", classify_pairs(fs))
        assert d == FoldDecision("fleet", None, None, "no-access")

    def test_builtin_defaults(self):
        assert builtin_width("driver") == "assigned"
        for r in Role:
            if r is not Role.DRIVER:
                assert builtin_width(r.value) == "all"


class TestMergingStorageKeys:
    """role_vehicle_scope has no tier and no company dimension; a role
    stored under several keys collapses to one row, narrowest wins,
    and the disagreement is named."""

    def test_base_wide_tier_narrow_loses_the_base_widths(self):
        base = fold("fleet", classify_pairs(ROLE_PERMISSIONS[Role.FLEET]))
        tier = fold("fleet", classify_pairs(_narrow_all(ROLE_PERMISSIONS[Role.FLEET])))
        m = merge_keys([base, tier])
        assert m.write == "assigned" and m.shape == "mixed"
        assert set(m.lost) == set(PAIRED_UNIT_FEATURES)

    def test_agreeing_keys_stay_consistent(self):
        a = fold("fleet", classify_pairs(_narrow_all(ROLE_PERMISSIONS[Role.FLEET])))
        m = merge_keys([a, a])
        assert m.write == "assigned" and m.shape == "consistent" and m.lost == ()

    def test_all_keys_default_writes_nothing(self):
        a = fold("fleet", classify_pairs(ROLE_PERMISSIONS[Role.FLEET]))
        m = merge_keys([a, a])
        assert m.write is None and m.shape == "default"


class TestStaleCrumbs:
    """The residue shape, on synthetic rows — the recruiter case as
    found in the owner's dry-run: narrow on every pair the seed opens
    none of."""

    def _old_recruiter_row(self):
        return {n: True for _, n in PAIRED_UNIT_FEATURES.values()}

    def test_recruiter_residue_is_all_ten_narrow_keys(self):
        from capabilities.permissions.fold import seed_for_key, stale_narrow_crumbs
        seed = seed_for_key("recruiter")
        got = stale_narrow_crumbs(seed, self._old_recruiter_row())
        assert set(got) == {n for _, n in PAIRED_UNIT_FEATURES.values()}

    def test_a_partially_cleaned_row_yields_only_what_is_left(self):
        from capabilities.permissions.fold import seed_for_key, stale_narrow_crumbs
        row = self._old_recruiter_row()
        for f in ("inspections", "maintenance", "work_orders"):
            row.pop(PAIRED_UNIT_FEATURES[f][1])
        got = stale_narrow_crumbs(seed_for_key("recruiter"), row)
        assert len(got) == 7 and PAIRED_UNIT_FEATURES["maintenance"][1] not in got

    def test_a_deliberate_wide_grant_is_never_residue(self):
        from capabilities.permissions.fold import seed_for_key, stale_narrow_crumbs
        w, n = PAIRED_UNIT_FEATURES["maintenance"]
        row = {w: True, n: True}
        assert stale_narrow_crumbs(seed_for_key("recruiter"), row) == []

    def test_a_pair_the_seed_grants_is_never_residue(self):
        # Fleet's seed opens maintenance; a narrow key there is a real
        # (if odd) grant, not a crumb.
        from capabilities.permissions.fold import seed_for_key, stale_narrow_crumbs
        w, n = PAIRED_UNIT_FEATURES["maintenance"]
        assert stale_narrow_crumbs(seed_for_key("fleet"), {n: True}) == []

    def test_canonical_keys_are_recognised_too(self):
        from capabilities.permissions.fold import seed_for_key, stale_narrow_crumbs
        # nothing canonical maps onto a narrow half (pair view verbs are
        # ambiguous writes and stay unmapped) — so a canonical-only row
        # is simply not residue
        assert stale_narrow_crumbs(seed_for_key("recruiter"),
                                   {"can_view_maintenance": True}) == []

    def test_seed_for_key_shapes(self):
        from capabilities.permissions.fold import seed_for_key
        assert seed_for_key("owner") is None and seed_for_key("owner__co") is None
        assert seed_for_key("nonsense") is None
        assert seed_for_key("fleet") is ROLE_PERMISSIONS[Role.FLEET]
        assert seed_for_key("recruiter__manager") is not None
