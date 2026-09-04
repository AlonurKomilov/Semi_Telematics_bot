"""Bot surfaces that used to ask ``role == driver`` ask Team Management.

Width — every unit vs the trucks assigned to me — is the three-layer
answer (own override ⊃ account role width ⊃ built-in), never the role
alone.  A driver an account widened reads like a dispatcher; a
dispatcher an account narrowed reads like a driver.  The AI resolver
has its own tests (capabilities/ai/tests/test_ai_vehicle_access_scope.py);
these cover the menu shortcut, the geofence fan-out and the AI prompt
snapshot filter.
"""

from __future__ import annotations

import os

os.environ.setdefault("ENCRYPTION_KEY", "")

import pytest

from adapters.storage import Role


def _buttons(kb) -> set[str]:
    return {
        b.callback_data
        for row in kb.inline_keyboard for b in row
        if getattr(b, "callback_data", None)
    }


class TestMyVehicleShortcut:
    def test_narrow_member_of_a_wide_role_gets_the_shortcut(self):
        from interfaces.bot.keyboards import main_menu_kb
        kb = main_menu_kb(Role.DISPATCHER, ["CO1"], wide=False)
        assert "cmd_myvehicle" in _buttons(kb)

    def test_widened_driver_does_not(self):
        from interfaces.bot.keyboards import main_menu_kb
        kb = main_menu_kb(Role.DRIVER, ["CO1"], wide=True)
        assert "cmd_myvehicle" not in _buttons(kb)

    def test_no_width_means_the_built_in_one(self):
        from interfaces.bot.keyboards import main_menu_kb
        assert "cmd_myvehicle" in _buttons(main_menu_kb(Role.DRIVER, ["CO1"]))
        assert "cmd_myvehicle" not in _buttons(main_menu_kb(Role.DISPATCHER, ["CO1"]))


class _Sub:
    def __init__(self, role, truck=None, width="all"):
        self.role = role
        self.truck_num = truck
        self.width = width


async def _fake_unit_width(account_id, role, db_user, feature):
    assert feature == "geofence"
    return db_user.width


@pytest.mark.asyncio
class TestGeofenceFanOut:
    async def test_width_walls_the_subscriber_not_the_role(self, monkeypatch):
        import capabilities.permissions.scope as scope_mod
        monkeypatch.setattr(scope_mod, "unit_width", _fake_unit_width)
        from interfaces.bot.geofences import _filter_subscribers_for_zone
        wide_driver = _Sub("driver", "T-1", width="all")
        narrow_dispatcher_other = _Sub("dispatcher", "T-2", width="assigned")
        narrow_dispatcher_own = _Sub("dispatcher", "T-9", width="assigned")
        narrow_no_truck = _Sub("driver", None, width="assigned")
        wide_admin = _Sub("admin", None, width="all")
        out = await _filter_subscribers_for_zone(
            [wide_driver, narrow_dispatcher_other, narrow_dispatcher_own,
             narrow_no_truck, wide_admin],
            {"driver", "dispatcher", "admin"}, "T-9", 1,
        )
        assert out == [wide_driver, narrow_dispatcher_own, wide_admin]

    async def test_role_outside_the_zone_is_dropped_first(self, monkeypatch):
        import capabilities.permissions.scope as scope_mod
        monkeypatch.setattr(scope_mod, "unit_width", _fake_unit_width)
        from interfaces.bot.geofences import _filter_subscribers_for_zone
        out = await _filter_subscribers_for_zone(
            [_Sub("hr", "T-9")], {"driver"}, "T-9", 1,
        )
        assert out == []


class _User:
    def __init__(self, role, truck=None):
        self.role = role
        self.truck_num = truck


class TestAiSnapshotFilter:
    def test_reads_the_resolved_scope(self):
        from interfaces.bot.ai import _scope_filter
        u = _User(Role.DRIVER, "T-1")
        assert _scope_filter(u, {"scoped_vehicle_nums": None}) == (False, None)
        assert _scope_filter(u, {"scoped_vehicle_nums": ["A", "B"]}) == (False, ["A", "B"])
        # an assigned-width member with no truck is BLOCKED, never unfiltered
        assert _scope_filter(u, {"scoped_vehicle_nums": []}) == (True, [])

    def test_unresolved_scope_falls_to_the_built_in_width(self):
        from interfaces.bot.ai import _scope_filter
        assert _scope_filter(_User(Role.DRIVER, "T-1"), {}) == (False, ["T-1"])
        assert _scope_filter(_User(Role.DRIVER, None), {}) == (True, [])
        assert _scope_filter(_User(Role.DISPATCHER, "T-1"), {}) == (False, None)
