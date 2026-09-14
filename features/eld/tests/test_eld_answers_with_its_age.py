"""An hours-of-service answer carries how old it is, or it is a claim
nobody can check.

A duty clock changes by the second and the ingest runs every five
minutes, so a reading is always somewhat behind.  The honest surface
is not one that hides the lag but one that states it — and the one
thing it must never do is let an empty table read as "everybody has
hours left".
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from features.eld.ai_tool import get_driver_hos_status
from features.eld.service import STALE_AFTER_MINUTES, get_hours, project


def _iso(minutes_ago: float) -> str:
    return (datetime.now(timezone.utc)
            - timedelta(minutes=minutes_ago)).isoformat(timespec="seconds")


def _row(**kw):
    base = {
        "provider_id": "samsara",
        "provider_driver_id": "p1",
        "user_id": 7,
        "duty_status": "driving",
        "drive_seconds_today": 3600,
        "on_duty_seconds_today": 7200,
        "cycle_seconds_remaining": 180000,
        "shift_seconds_remaining": 25200,
        "last_status_change": "2026-09-14T08:00:00+00:00",
        "driver_name": "Provider Jane",
        "source_ts": _iso(2),
        "updated_at": _iso(0),
        "display_name": "Jane Ruiz",
        "truck_num": "231",
        "linked": True,
    }
    base.update(kw)
    return base


class _DB:
    def __init__(self, rows, ever=None):
        self._rows = list(rows)
        self._ever = len(self._rows) if ever is None else ever

    async def get_driver_hos_live(self, account_id, user_id=None):
        if user_id is None:
            return list(self._rows)
        return [r for r in self._rows if r.get("user_id") == user_id]

    async def count_driver_hos_live(self, account_id):
        return self._ever


# ── The distinction the feature exists for ────────────────────────

@pytest.mark.asyncio
async def test_never_ingested_is_not_everyone_has_hours_left():
    out = await get_hours(_DB([], ever=0), 1)
    assert out["connected"] is False
    assert out["drivers"] == []


@pytest.mark.asyncio
async def test_the_tool_refuses_rather_than_answering_from_nothing():
    res = await get_driver_hos_status({}, None, account_id=1, db=_DB([], ever=0))
    assert res["hos_unavailable"] is True
    assert "NOT a statement that every driver has hours remaining" in res["note"]
    assert res["drivers"] == []


@pytest.mark.asyncio
async def test_connected_but_no_match_is_a_real_none():
    """Zero because the filter matched nothing is a different answer
    from zero because no ELD exists, and the tool says which."""
    res = await get_driver_hos_status(
        {"driver_name": "nobody"}, None, account_id=1, db=_DB([_row()]))
    assert "hos_unavailable" not in res
    assert "real 'none match'" in res["note"]


@pytest.mark.asyncio
async def test_connected_is_not_derived_from_the_visible_rows():
    """Rows the caller cannot see must not make the feed look absent."""
    out = await get_hours(_DB([_row()], ever=1), 1, vehicle_scope=[])
    assert out["connected"] is True
    assert out["drivers"] == []


# ── Age ───────────────────────────────────────────────────────────

def test_age_comes_from_the_provider_time_not_our_write_time():
    """A row we stored thirty seconds ago can describe a driver who has
    been driving for the last twenty minutes."""
    p = project(_row(source_ts=_iso(20), updated_at=_iso(0)))
    assert p["age_minutes"] >= 19
    assert p["stale"] is True


def test_a_fresh_reading_is_not_stale():
    p = project(_row(source_ts=_iso(1)))
    assert p["stale"] is False
    assert p["age_minutes"] < STALE_AFTER_MINUTES


def test_an_unreadable_timestamp_is_unknown_age_not_fresh():
    """Unknown age is not the same as current, and a surface must be
    able to say so rather than implying the reading is good."""
    p = project(_row(source_ts=""))
    assert p["age_minutes"] is None
    assert p["stale"] is True


@pytest.mark.asyncio
async def test_the_tool_hands_the_model_the_age_and_the_limit():
    res = await get_driver_hos_status(
        {}, None, account_id=1, db=_DB([_row(source_ts=_iso(30))]))
    assert res["drivers"][0]["reading_age_minutes"] >= 29
    assert res["drivers"][0]["stale"] is True
    assert res["stale_count"] == 1
    assert res["stale_after_minutes"] == STALE_AFTER_MINUTES


# ── Clocks ────────────────────────────────────────────────────────

def test_an_unreported_clock_is_not_rendered_as_empty():
    p = project(_row(cycle_seconds_remaining=None, shift_seconds_remaining=0))
    assert p["cycle_remaining"] is None
    assert p["shift_remaining"] == {"seconds": 0, "hours": 0.0}


@pytest.mark.asyncio
async def test_unknown_and_out_of_hours_never_read_the_same():
    """``unknown`` vs ``0h``: the first is a gap in the feed, the
    second is a driver who must stop."""
    res = await get_driver_hos_status({}, None, account_id=1, db=_DB([
        _row(cycle_seconds_remaining=None, shift_seconds_remaining=0),
    ]))
    d = res["drivers"][0]
    assert d["cycle_remaining"] == "unknown"
    assert d["shift_remaining"] == "0m"


# ── Scope and identity ────────────────────────────────────────────

@pytest.mark.asyncio
async def test_the_caller_sees_only_their_own_trucks_drivers():
    db = _DB([_row(truck_num="231"), _row(provider_driver_id="p2",
                                          user_id=8, truck_num="104",
                                          display_name="Sam Lee")])
    res = await get_driver_hos_status(
        {"_scope_vehicles": ["231"]}, None, account_id=1, db=db)
    assert [d["name"] for d in res["drivers"]] == ["Jane Ruiz"]


@pytest.mark.asyncio
async def test_an_unlinked_driver_is_still_reported():
    """The provider knows this driver; we have not matched them to our
    roster yet.  Hiding them would make the account's answer quietly
    incomplete."""
    out = await get_hours(
        _DB([_row(user_id=None, linked=False, display_name="Provider Jane")]), 1)
    assert out["count"] == 1
    assert out["drivers"][0]["linked"] is False
    assert out["drivers"][0]["driver"] == "Provider Jane"


# ── What the model is told it may not do ──────────────────────────

@pytest.mark.asyncio
async def test_every_answer_says_who_the_record_actually_is():
    res = await get_driver_hos_status({}, None, account_id=1, db=_DB([_row()]))
    assert "system of record" in res["system_of_record"]
    assert "never compute or assert a violation" in res["system_of_record"]


def test_the_service_says_it_once_so_no_surface_has_to_remember():
    out = project(_row())
    assert "as_of" in out
    assert "source" in out
