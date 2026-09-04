"""The stored-row sweep, decided on synthetic rows only."""

from __future__ import annotations

import os

os.environ.setdefault("ENCRYPTION_KEY", "")

from capabilities.permissions.fold import plan_row_sweep
from capabilities.permissions.roles import LEGACY_TO_CANONICAL, FeatureSet


def test_canonical_row_plans_nothing():
    row = {"can_view_faults": True, "can_manage_users": False}
    assert plan_row_sweep(row) == (row, [], [])


def test_legacy_row_becomes_canonical_and_names_what_left():
    row = {"can_faults": True, "can_events_all": False, "can_events_vehicle": True}
    out, removed, collisions = plan_row_sweep(row)
    assert out == {"can_view_faults": True, "can_view_events": True}
    assert removed == ["can_events_all", "can_events_vehicle", "can_faults"]
    assert collisions == []


def test_manage_pair_folds_both_halves():
    out, _, _ = plan_row_sweep({"can_maintenance_all": True, "can_maintenance_vehicle": False})
    assert out == {"can_manage_maintenance": True, "can_view_maintenance": True}


def test_a_collision_is_reported_and_resolved_by_or():
    out, removed, collisions = plan_row_sweep({"can_faults": True, "can_view_faults": False})
    assert out["can_view_faults"] is True
    assert collisions == ["can_view_faults"] and removed == ["can_faults"]


def test_agreeing_duplicates_are_not_collisions():
    out, _, collisions = plan_row_sweep({"can_faults": False, "can_view_faults": False})
    assert out["can_view_faults"] is False and collisions == []


def test_every_planned_key_is_a_physical_field():
    fields = {f.name for f in FeatureSet.__dataclass_fields__.values()}
    row = {k: True for k in LEGACY_TO_CANONICAL}
    out, removed, _ = plan_row_sweep(row)
    assert set(out) <= fields and set(removed) == set(LEGACY_TO_CANONICAL)
