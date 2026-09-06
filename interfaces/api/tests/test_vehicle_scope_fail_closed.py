"""A driver with no truck assigned is restricted to nothing.

``get_user_vehicle_scope`` used to answer ``None`` — unrestricted — for
that person, so a driver whose truck had been archived or reassigned saw
the whole account: every vehicle, its position, its fuel, its faults,
and on the delivery side camera alerts, which are dashcam images.
Three more modules restated the rule as deliberate.  The repo had
already decided the other way everywhere the question was answered
later: the AI scope resolver, the bot's unit-width filter and this
router's own live-position endpoint all deny such a driver.

These tests pin the chokepoint and the one shape that must NOT change —
a non-driver stays unrestricted here, because WHO is restricted is a
separate question from WHAT they may see.
"""
import pytest

from capabilities.permissions.vehicle_scope import VehicleScope
from interfaces.api import deps


@pytest.mark.asyncio
async def test_a_driver_with_no_assignment_gets_an_empty_scope(monkeypatch):
    async def _none(_user):
        return []
    monkeypatch.setattr(deps, "get_user_vehicle_nums", _none)
    scope = await deps.get_user_vehicle_scope({"role": "driver", "account_id": 1})
    assert scope is not None, "None means UNRESTRICTED — the bug this closes"
    assert scope.empty


@pytest.mark.asyncio
async def test_an_empty_scope_admits_no_row(monkeypatch):
    async def _none(_user):
        return []
    monkeypatch.setattr(deps, "get_user_vehicle_nums", _none)
    rows = [{"name": "142", "id": "p1"}, {"name": "220", "id": "p2"}]
    out = await deps.filter_by_assigned_trucks(rows, {"role": "driver", "account_id": 1})
    assert out == []


@pytest.mark.asyncio
async def test_a_non_driver_stays_unrestricted_here():
    """WHO is restricted is not what this change touches.

    Narrowing a non-driver member is the WIDTH layer's job
    (``member_unit_scope``), and converging the two is its own task —
    doing it here would silently narrow every dispatcher and manager.
    """
    for role in ("owner", "fleet", "dispatcher", "safety", "hr", "accounting"):
        assert await deps.get_user_vehicle_scope({"role": role, "account_id": 1}) is None


@pytest.mark.asyncio
async def test_an_assigned_driver_still_gets_a_real_scope(monkeypatch):
    """The change must not cost an assigned driver their own truck."""
    async def _some(_user):
        return ["142"]

    class _Router:
        async def get_tenant(self, _a):
            return object()

    async def _build(_tenant, _acct, names):
        return VehicleScope.from_names(names)

    monkeypatch.setattr(deps, "get_user_vehicle_nums", _some)
    monkeypatch.setattr(deps, "_get_router", lambda: _Router())
    monkeypatch.setattr(
        "capabilities.permissions.vehicle_scope.build_vehicle_scope", _build)
    scope = await deps.get_user_vehicle_scope({"role": "driver", "account_id": 1})
    assert not scope.empty
    assert scope.allows(name="142")
    assert not scope.allows(name="1420")
