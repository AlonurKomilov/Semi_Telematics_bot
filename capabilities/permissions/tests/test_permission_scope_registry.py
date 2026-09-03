"""UNIT_FEATURES registry completeness (roles.py).

Every ``can_view_<noun>`` field of a unit-scoped feature must be
registered in UNIT_FEATURES (the width helper validates features
against it), and every registered field must exist.  Replaced the
OWN_VEHICLE_SCOPE_FLAGS registry, whose *_vehicle flags died in the
verb/scope migration.
"""

from __future__ import annotations

import os

os.environ.setdefault("ENCRYPTION_KEY", "")

from dataclasses import fields

from capabilities.permissions.roles import FeatureSet, UNIT_FEATURES, PAIRED_UNIT_FEATURES


def test_every_registered_field_exists():
    names = {f.name for f in fields(FeatureSet)}
    for noun, (view, manage) in UNIT_FEATURES.items():
        assert view in names, (noun, view)
        if manage:
            assert manage in names, (noun, manage)


def test_legacy_pair_registry_names_no_field():
    names = {f.name for f in fields(FeatureSet)}
    for noun, (wide, narrow) in PAIRED_UNIT_FEATURES.items():
        assert wide not in names and narrow not in names, noun


def test_registry_nouns_agree():
    assert set(UNIT_FEATURES) == set(PAIRED_UNIT_FEATURES)
