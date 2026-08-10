"""The watchdog watches the WORK: stale tiers and silent jobs become
operator sentences; fresh machinery stays quiet."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from capabilities.platform.watchdog import collect_problems


class _Cur:
    def __init__(self, val):
        self._val = val

    async def fetchone(self):
        return (self._val,)


class _DB:
    def __init__(self, newest_by_table):
        self._m = newest_by_table

    class _Inner:
        def __init__(self, outer):
            self._o = outer

        async def execute(self, sql):
            for key, val in self._o._m.items():
                if key in sql:
                    return _Cur(val)
            return _Cur(None)

    @property
    def _db(self):
        return self._Inner(self)


@pytest.mark.asyncio
async def test_fresh_tiers_report_nothing(monkeypatch):
    import infra.cache as cache
    monkeypatch.setattr(cache, "_pool", None)
    now = datetime.now(timezone.utc)
    db = _DB({
        "vehicle_state_day": (now - timedelta(hours=10)).isoformat(),
        "vehicle_state_hour": (now - timedelta(hours=1)).isoformat(),
        "vehicle_state_week": (now - timedelta(days=4)).isoformat(),
    })
    assert await collect_problems(db) == []


@pytest.mark.asyncio
async def test_stale_day_tier_becomes_a_sentence(monkeypatch):
    """The Aug 6-9 outage shape: day tier 4 days stale while hours
    stay current — exactly one problem, named, with its age."""
    import infra.cache as cache
    monkeypatch.setattr(cache, "_pool", None)
    now = datetime.now(timezone.utc)
    db = _DB({
        "vehicle_state_day": (now - timedelta(days=4)).isoformat(),
        "vehicle_state_hour": (now - timedelta(hours=1)).isoformat(),
        "vehicle_state_week": (now - timedelta(days=4)).isoformat(),
    })
    problems = await collect_problems(db)
    assert len(problems) == 1
    assert "day tier" in problems[0] and "96h" in problems[0]
