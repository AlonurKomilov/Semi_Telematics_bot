"""Tests for /api/safety/scorecards/me — driver miniapp scorecard route.

Covers:
- 404 when caller has no truck assignment.
- Returns the caller's own card + per-pillar rank + total rank.
- Rank counts only drivers with ``has_data`` for that pillar.
- ``week_delta`` populated from prior-week snapshot when available.
- ``can_scorecard_own`` (driver role) callers are admitted.
"""

from __future__ import annotations

import os
import sys

os.environ.setdefault("ENCRYPTION_KEY", "")

import pytest_asyncio

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

from httpx import ASGITransport, AsyncClient

from adapters.storage import Database, Role
from interfaces.api.auth import create_jwt


@pytest_asyncio.fixture
async def me_app(pg_db, monkeypatch):
    database = pg_db

    acct = await database.create_account("ME Co")
    await database.add_company(acct.id, "MC", "key_mc", "ME Co")
    driver = await database.create_user(8001, acct.id, role=Role.DRIVER)
    driver_no_truck = await database.create_user(8002, acct.id, role=Role.DRIVER)

    # Assign a primary truck to the first driver so get_user_vehicle_nums returns a value.
    await database.assign_vehicle(
        user_id=driver.id, account_id=acct.id, truck_num="T-100",
        assigned_by=0, is_primary=True,
    )

    token_driver = create_jwt(driver.telegram_id, acct.id, "driver")
    token_no_truck = create_jwt(driver_no_truck.telegram_id, acct.id, "driver")

    import infra.platform as _cp
    _old_db = _cp._db
    _cp._db = database

    # Stub the scoring service:
    # - Full-leaderboard call (vehicle_nums=None): 3 drivers with mixed pillar data.
    # - My-card call (vehicle_nums=['T-100']): the caller's single driver row.
    def _make_card(driver_id: str, name: str, total: int, pillars: dict) -> dict:
        return {
            "subject_id": driver_id, "subject_name": name, "subject_type": "driver",
            "driver_id": driver_id, "driver_name": name, "company": "MC",
            "score": total, "base": 0,
            "bonus_total": 0, "penalty_total": 0,
            "bonuses": [], "penalties": [],
            "total": total, "pillars": pillars,
            "exposure": {"miles": 100.0, "drive_hours": 5.0, "idle_hours": 1.0},
            "insufficient_data": False,
            "inputs": {},
        }

    def _pillars(s: int, e: int, c: int, *, e_has: bool = True) -> dict:
        return {
            "safety": {"name": "safety", "cap": 50, "subtotal": s,
                       "bonus_total": 0, "penalty_total": 0,
                       "has_data": True, "events": []},
            "efficiency": {"name": "efficiency", "cap": 25, "subtotal": e,
                           "bonus_total": 0, "penalty_total": 0,
                           "has_data": e_has, "events": []},
            "compliance": {"name": "compliance", "cap": 25, "subtotal": c,
                           "bonus_total": 0, "penalty_total": 0,
                           "has_data": True, "events": []},
        }

    # Top dog: 95.  Caller: 80.  Bottom: 60.  Caller is rank 2/3 overall.
    # Caller's safety subtotal 40 ranks 2/3 (best 47, worst 30).
    # Caller's efficiency 20 ranks 1/2 because the top dog has has_data=False
    # (so the efficiency leaderboard only counts caller + bottom).
    full = [
        _make_card("drv-top",  "Top",    95, _pillars(47, 23, 25, e_has=False)),
        _make_card("drv-me",   "Me",     80, _pillars(40, 20, 20)),
        _make_card("drv-bot",  "Bottom", 60, _pillars(30, 15, 15)),
    ]
    my_only = [full[1]]

    async def _fake_evaluate(account_id, *, subject="driver", days=7,
                             company=None, vehicle_nums=None):
        if vehicle_nums:
            return list(my_only)
        # Sorted DESC by total in real impl — return same order.
        return list(full)

    monkeypatch.setattr(
        "interfaces.api.routes.safety._svc_evaluate_subjects",
        _fake_evaluate,
    )

    # Seed 14 daily snapshots for each driver so none are flagged
    # probationary (grace period = <14 daily snapshots).
    # The week_delta logic still relies on the caller's index-6
    # snapshot carrying a baseline_breakdown.
    import datetime as _dt
    today = _dt.date.today()
    # Earliest snapshot (~7 days back) on drv-me carries the
    # "previous week" baseline used to compute week_delta:
    #   total=72, safety=35, efficiency=18, compliance=19.
    baseline_breakdown = {
        "pillars": {
            "safety":     {"subtotal": 35, "has_data": True},
            "efficiency": {"subtotal": 18, "has_data": True},
            "compliance": {"subtotal": 19, "has_data": True},
        }
    }
    drivers_to_seed = (
        ("drv-top", "Top",    95),
        ("drv-me",  "Me",     80),
        ("drv-bot", "Bottom", 60),
    )
    for i in range(1, 15):  # 14 daily snapshots, today-1 .. today-14
        d = (today - _dt.timedelta(days=i)).isoformat()
        for sid, sname, base_total in drivers_to_seed:
            total = base_total
            bd: dict = {"pillars": {}}
            # Only drv-me gets the baseline at i=7 so week_delta works.
            if sid == "drv-me":
                total = 72 if i == 7 else 78
                if i == 7:
                    bd = baseline_breakdown
            await database.save_scorecard_snapshot(
                acct.id,
                snapshot_date=d, subject_type="driver",
                subject_id=sid, subject_name=sname,
                total_score=total, window_days=7, breakdown=bd, source="test",
            )

    from interfaces.api.app import create_api
    app = create_api()

    yield {
        "app": app, "db": database, "acct": acct,
        "token_driver": token_driver,
        "token_no_truck": token_no_truck,
    }

    _cp._db = _old_db
    # database.close() handled by pg_db fixture teardown


def _h(token: str) -> dict:
    return {"Authorization": f"Bearer {token}"}


class TestScorecardMeRoute:
    async def test_404_when_no_truck_assignment(self, me_app):
        async with AsyncClient(
            transport=ASGITransport(app=me_app["app"]), base_url="http://t"
        ) as c:
            r = await c.get(
                "/api/safety/scorecards/me",
                headers=_h(me_app["token_no_truck"]),
            )
            assert r.status_code == 404
            assert "truck" in r.json()["detail"].lower()

    async def test_returns_my_card_with_rank_and_delta(self, me_app):
        async with AsyncClient(
            transport=ASGITransport(app=me_app["app"]), base_url="http://t"
        ) as c:
            r = await c.get(
                "/api/safety/scorecards/me?days=7",
                headers=_h(me_app["token_driver"]),
            )
            assert r.status_code == 200, r.text
            data = r.json()
            # Caller's own card present.
            assert data["scorecard"]["subject_id"] == "drv-me"
            assert data["scorecard"]["total"] == 80
            # Driver-role hardening: callers with only ``can_scorecard_own``
            # MUST NOT see account-wide aggregates.  ``account_size`` and rank
            # fields would let a driver enumerate the rest of the fleet.
            assert data["account_size"] is None
            assert data["rank_total"] is None
            assert data["rank_in_pillar"] is None
            # Week delta: total 80 vs baseline 72 → +8.  Pillar deltas
            # match: 40-35, 20-18, 20-19.  These are own-only and remain
            # in the response.
            assert data["week_delta"]["total"] == 8
            assert data["week_delta"]["safety"] == 5
            assert data["week_delta"]["efficiency"] == 2
            assert data["week_delta"]["compliance"] == 1

    async def test_week_delta_null_when_no_snapshots(self, pg_db, monkeypatch):
        """A fresh tenant with no snapshot history should still return 200,
        but ``week_delta`` values are all ``None``."""
        database = pg_db
        acct = await database.create_account("Fresh")
        await database.add_company(acct.id, "FR", "kf", "Fresh")
        driver = await database.create_user(9001, acct.id, role=Role.DRIVER)
        await database.assign_vehicle(
            user_id=driver.id, account_id=acct.id, truck_num="T-1",
            assigned_by=0, is_primary=True,
        )
        token = create_jwt(driver.telegram_id, acct.id, "driver")

        import infra.platform as _cp
        _old_db = _cp._db
        _cp._db = database

        async def _fake(account_id, *, subject="driver", days=7,
                        company=None, vehicle_nums=None):
            card = {
                "subject_id": "drv-x", "subject_name": "X",
                "subject_type": "driver",
                "driver_id": "drv-x", "driver_name": "X", "company": "FR",
                "score": 50, "base": 0,
                "bonus_total": 0, "penalty_total": 0,
                "bonuses": [], "penalties": [],
                "total": 50,
                "pillars": {
                    "safety":     {"name": "safety",     "cap": 50,
                                   "subtotal": 25, "bonus_total": 0,
                                   "penalty_total": 0,
                                   "has_data": True, "events": []},
                    "efficiency": {"name": "efficiency", "cap": 25,
                                   "subtotal": 12, "bonus_total": 0,
                                   "penalty_total": 0,
                                   "has_data": True, "events": []},
                    "compliance": {"name": "compliance", "cap": 25,
                                   "subtotal": 13, "bonus_total": 0,
                                   "penalty_total": 0,
                                   "has_data": True, "events": []},
                },
                "exposure": {"miles": 0, "drive_hours": 0, "idle_hours": 0},
                "insufficient_data": False, "inputs": {},
            }
            return [card]

        monkeypatch.setattr(
            "interfaces.api.routes.safety._svc_evaluate_subjects", _fake,
        )

        from interfaces.api.app import create_api
        app = create_api()
        try:
            async with AsyncClient(
                transport=ASGITransport(app=app), base_url="http://t"
            ) as c:
                r = await c.get(
                    "/api/safety/scorecards/me",
                    headers={"Authorization": f"Bearer {token}"},
                )
                assert r.status_code == 200, r.text
                data = r.json()
                assert data["scorecard"]["subject_id"] == "drv-x"
                # No prior snapshots → all delta values are null.
                assert data["week_delta"] == {
                    "total": None, "safety": None,
                    "efficiency": None, "compliance": None,
                }
                # Driver-role hardening: rank fields are suppressed for
                # ``can_scorecard_own`` callers regardless of fleet size.
                assert data["rank_total"] is None
                assert data["rank_in_pillar"] is None
        finally:
            _cp._db = _old_db
            # database.close() handled by pg_db fixture teardown

    async def test_404_when_my_truck_has_no_card(self, me_app, monkeypatch):
        """If the truck-filtered evaluate returns empty, the route 404s
        (caller's truck has no telemetry/safety data in the window)."""
        async def _empty_when_filtered(account_id, *, subject="driver",
                                       days=7, company=None, vehicle_nums=None):
            if vehicle_nums:
                return []
            return [{
                "subject_id": "other", "subject_name": "Other",
                "subject_type": "driver",
                "driver_id": "other", "driver_name": "Other", "company": "MC",
                "score": 50, "base": 0,
                "bonus_total": 0, "penalty_total": 0,
                "bonuses": [], "penalties": [],
                "total": 50,
                "pillars": {
                    "safety":     {"subtotal": 25, "has_data": True, "cap": 50,
                                   "name": "safety", "bonus_total": 0,
                                   "penalty_total": 0, "events": []},
                    "efficiency": {"subtotal": 12, "has_data": True, "cap": 25,
                                   "name": "efficiency", "bonus_total": 0,
                                   "penalty_total": 0, "events": []},
                    "compliance": {"subtotal": 13, "has_data": True, "cap": 25,
                                   "name": "compliance", "bonus_total": 0,
                                   "penalty_total": 0, "events": []},
                },
                "exposure": {"miles": 0, "drive_hours": 0, "idle_hours": 0},
                "insufficient_data": False, "inputs": {},
            }]

        monkeypatch.setattr(
            "interfaces.api.routes.safety._svc_evaluate_subjects",
            _empty_when_filtered,
        )

        async with AsyncClient(
            transport=ASGITransport(app=me_app["app"]), base_url="http://t"
        ) as c:
            r = await c.get(
                "/api/safety/scorecards/me",
                headers=_h(me_app["token_driver"]),
            )
            assert r.status_code == 404
            assert "truck" in r.json()["detail"].lower()


class TestScorecardMeAdminRankVisibility:
    """Admin-role callers (``can_scorecard_all``) must still see fleet-wide
    aggregates on ``/scorecards/me``.  This is the counterpart to the
    driver-only hardening assertions above: regression-test that the
    privacy fix didn't accidentally null out ranks for legitimate
    leaderboard viewers.
    """

    async def test_admin_token_sees_ranks_and_account_size(self, pg_db, monkeypatch):
        from adapters.storage import Database, Role
        from interfaces.api.auth import create_jwt
        import infra.platform as _cp

        database = pg_db
        acct = await database.create_account("Admin Co")
        await database.add_company(acct.id, "AC", "key_ac", "Admin Co")
        # Admin-with-truck pattern: a dispatcher who occasionally drives
        # and therefore has a primary truck on file.  Without a truck the
        # /me route 404s before any rank logic runs.
        admin = await database.create_user(7001, acct.id, role=Role.OWNER)
        await database.assign_vehicle(
            user_id=admin.id, account_id=acct.id, truck_num="A-1",
            assigned_by=0, is_primary=True,
        )
        token_admin = create_jwt(admin.telegram_id, acct.id, "owner")

        _old_db = _cp._db
        _cp._db = database

        def _make(driver_id: str, name: str, total: int, s: int, e: int, c: int) -> dict:
            return {
                "subject_id": driver_id, "subject_name": name,
                "subject_type": "driver",
                "driver_id": driver_id, "driver_name": name, "company": "AC",
                "score": total, "base": 0,
                "bonus_total": 0, "penalty_total": 0,
                "bonuses": [], "penalties": [],
                "total": total,
                "pillars": {
                    "safety":     {"name": "safety",     "cap": 50,
                                   "subtotal": s, "bonus_total": 0,
                                   "penalty_total": 0,
                                   "has_data": True, "events": []},
                    "efficiency": {"name": "efficiency", "cap": 25,
                                   "subtotal": e, "bonus_total": 0,
                                   "penalty_total": 0,
                                   "has_data": True, "events": []},
                    "compliance": {"name": "compliance", "cap": 25,
                                   "subtotal": c, "bonus_total": 0,
                                   "penalty_total": 0,
                                   "has_data": True, "events": []},
                },
                "exposure": {"miles": 100, "drive_hours": 5, "idle_hours": 1},
                "insufficient_data": False, "inputs": {},
            }

        full = [
            _make("drv-top", "Top",     95, 47, 23, 25),
            _make("drv-A1",  "A-1",     80, 40, 20, 20),
            _make("drv-bot", "Bottom",  60, 30, 15, 15),
        ]
        my_only = [full[1]]

        async def _fake_evaluate(account_id, *, subject="driver", days=7,
                                 company=None, vehicle_nums=None):
            return list(my_only) if vehicle_nums else list(full)

        monkeypatch.setattr(
            "interfaces.api.routes.safety._svc_evaluate_subjects",
            _fake_evaluate,
        )

        # stub the trends batch so every driver has enough
        # snapshots to be a "veteran" (>= PROBATIONARY_MIN_SNAPSHOTS).
        # Without this, all three drivers would be flagged probationary
        # and the rank pool would collapse to just the caller.
        async def _fake_trends(account_id, *, subject_type="driver", days=14):
            return {
                "drv-top": list(range(95, 95 + 14)),
                "drv-A1":  list(range(80, 80 + 14)),
                "drv-bot": list(range(60, 60 + 14)),
            }
        monkeypatch.setattr(
            database, "get_scorecard_trends_batch", _fake_trends,
        )

        from interfaces.api.app import create_api
        app = create_api()
        try:
            async with AsyncClient(
                transport=ASGITransport(app=app), base_url="http://t"
            ) as c:
                r = await c.get(
                    "/api/safety/scorecards/me?days=7",
                    headers={"Authorization": f"Bearer {token_admin}"},
                )
                assert r.status_code == 200, r.text
                data = r.json()
                assert data["scorecard"]["subject_id"] == "drv-A1"
                # Admin/owner with ``can_scorecard_all`` MUST see fleet
                # aggregates — this is the privacy-fix regression check.
                assert data["account_size"] == 3
                assert data["rank_total"] == {"pos": 2, "total": 3}
                assert data["rank_in_pillar"] is not None
                assert data["rank_in_pillar"]["safety"]["pos"] == 2
                assert data["rank_in_pillar"]["safety"]["total"] == 3
                assert data["rank_in_pillar"]["efficiency"]["pos"] == 2
                assert data["rank_in_pillar"]["compliance"]["pos"] == 2
                # caller has 14 snapshots → not probationary.
                assert data["scorecard"].get("probationary") is False
        finally:
            _cp._db = _old_db
            # database.close() handled by pg_db fixture teardown


class TestScorecardMeProbationary:
    """probationary drivers (<14 daily snapshots) are visible
    on their own card but excluded from rank pools so brand-new hires
    don't outrank veterans on data they don't yet have."""

    async def test_new_driver_excluded_from_rank_pool(self, pg_db, monkeypatch):
        database = pg_db
        acct = await database.create_account("Prob Co")
        await database.add_company(acct.id, "PC", "kp", "Prob Co")
        admin = await database.create_user(7001, acct.id, role=Role.OWNER)
        await database.assign_vehicle(
            user_id=admin.id, account_id=acct.id, truck_num="T-NEW",
            assigned_by=0, is_primary=True,
        )
        token = create_jwt(admin.telegram_id, acct.id, "owner")

        import infra.platform as _cp
        _old_db = _cp._db
        _cp._db = database

        def _card(sid: str, name: str, total: int, s: int) -> dict:
            return {
                "subject_id": sid, "subject_name": name, "subject_type": "driver",
                "driver_id": sid, "driver_name": name, "company": "PC",
                "score": total, "base": 0,
                "bonus_total": 0, "penalty_total": 0,
                "bonuses": [], "penalties": [],
                "total": total,
                "pillars": {
                    "safety":     {"name": "safety",     "cap": 50, "subtotal": s,
                                   "bonus_total": 0, "penalty_total": 0,
                                   "has_data": True, "events": []},
                    "efficiency": {"name": "efficiency", "cap": 25, "subtotal": 20,
                                   "bonus_total": 0, "penalty_total": 0,
                                   "has_data": True, "events": []},
                    "compliance": {"name": "compliance", "cap": 25, "subtotal": 20,
                                   "bonus_total": 0, "penalty_total": 0,
                                   "has_data": True, "events": []},
                },
                "exposure": {"miles": 100, "drive_hours": 5, "idle_hours": 1},
                "insufficient_data": False, "inputs": {},
            }

        # New caller (drv-new) is in their first week — 5 snapshots only.
        # Two veterans (drv-vet-a, drv-vet-b) have full 14-day history.
        full = [
            _card("drv-vet-a", "Vet A",   90, 45),
            _card("drv-vet-b", "Vet B",   70, 35),
            _card("drv-new",   "Newbie", 100, 50),  # would top the chart!
        ]
        my_only = [full[2]]

        async def _fake_evaluate(account_id, *, subject="driver", days=7,
                                 company=None, vehicle_nums=None):
            return list(my_only) if vehicle_nums else list(full)
        monkeypatch.setattr(
            "interfaces.api.routes.safety._svc_evaluate_subjects",
            _fake_evaluate,
        )

        async def _fake_trends(account_id, *, subject_type="driver", days=14):
            return {
                "drv-vet-a": list(range(80, 94)),   # 14 entries → veteran
                "drv-vet-b": list(range(60, 74)),   # 14 entries → veteran
                "drv-new":   list(range(95, 100)),  #  5 entries → probationary
            }
        monkeypatch.setattr(
            database, "get_scorecard_trends_batch", _fake_trends,
        )

        from interfaces.api.app import create_api
        app = create_api()
        try:
            async with AsyncClient(
                transport=ASGITransport(app=app), base_url="http://t"
            ) as c:
                r = await c.get(
                    "/api/safety/scorecards/me?days=7",
                    headers={"Authorization": f"Bearer {token}"},
                )
                assert r.status_code == 200, r.text
                data = r.json()
                # Caller card carries the probationary flag for UI.
                assert data["scorecard"]["subject_id"] == "drv-new"
                assert data["scorecard"]["probationary"] is True
                # Rank pool excludes the new driver from the OTHER drivers'
                # rank, so account_size = 2 veterans (not 3).
                assert data["account_size"] == 2
                # A probationary caller doesn't get a rank chip — pairing
                # one with the "your score is still building" banner would
                # contradict itself.  Both rank fields come back null.
                assert data["rank_total"] is None
                assert data["rank_in_pillar"] is None
        finally:
            _cp._db = _old_db
            # database.close() handled by pg_db fixture teardown


class TestScoreExplanation:
    """Score-explanation endpoint diffs the latest snapshot vs one
    ~N days ago and surfaces what changed: penalties added / cleared,
    bonuses earned / lost.  No new schema — reuses the existing
    ``daily_scorecard_snapshots`` table.
    """

    async def test_diff_surfaces_added_and_cleared_events(self, pg_db, monkeypatch):
        import datetime as _dt
        database = pg_db
        acct = await database.create_account("Expl Co")
        await database.add_company(acct.id, "EC", "ke", "Expl Co")
        admin = await database.create_user(6001, acct.id, role=Role.OWNER)
        await database.assign_vehicle(
            user_id=admin.id, account_id=acct.id, truck_num="T-EXP",
            assigned_by=0, is_primary=True,
        )
        token = create_jwt(admin.telegram_id, acct.id, "owner")

        import infra.platform as _cp
        _old_db = _cp._db
        _cp._db = database

        today = _dt.date.today()
        prev_breakdown = {
            "bonuses":   [{"rule_id": "safety.zero_events_week", "label": "Zero safety", "points": 10, "occurrences": 1}],
            "penalties": [{"rule_id": "compliance.camera_obstructed", "label": "Camera obstructed", "points": -3, "occurrences": 1}],
            "pillars": {},
        }
        curr_breakdown = {
            "bonuses":   [{"rule_id": "compliance.vehicle_health_clean", "label": "Paired truck fault-free", "points": 4, "occurrences": 1}],
            "penalties": [
                {"rule_id": "safety.overspeed_rate", "label": "Speeding", "points": -5, "occurrences": 2},
            ],
            "pillars": {},
        }
        await database.save_scorecard_snapshot(
            acct.id,
            snapshot_date=(today - _dt.timedelta(days=7)).isoformat(),
            subject_type="driver",
            subject_id="drv-exp", subject_name="Driver E",
            total_score=80, window_days=7, breakdown=prev_breakdown, source="test",
        )
        await database.save_scorecard_snapshot(
            acct.id,
            snapshot_date=today.isoformat(),
            subject_type="driver",
            subject_id="drv-exp", subject_name="Driver E",
            total_score=72, window_days=7, breakdown=curr_breakdown, source="test",
        )

        from interfaces.api.app import create_api
        app = create_api()
        try:
            async with AsyncClient(
                transport=ASGITransport(app=app), base_url="http://t"
            ) as c:
                r = await c.get(
                    "/api/safety/scorecards/drv-exp/explanation?subject_type=driver&days=7",
                    headers={"Authorization": f"Bearer {token}"},
                )
                assert r.status_code == 200, r.text
                data = r.json()
                assert data["available"] is True
                assert data["from_score"] == 80
                assert data["to_score"] == 72
                assert data["score_delta"] == -8
                added_ids = [e["rule_id"] for e in data["penalties_added"]]
                assert "safety.overspeed_rate" in added_ids
                cleared_ids = [e["rule_id"] for e in data["penalties_cleared"]]
                assert "compliance.camera_obstructed" in cleared_ids
                earned_ids = [e["rule_id"] for e in data["bonuses_earned"]]
                lost_ids   = [e["rule_id"] for e in data["bonuses_lost"]]
                assert "compliance.vehicle_health_clean" in earned_ids
                assert "safety.zero_events_week" in lost_ids
        finally:
            _cp._db = _old_db
            # database.close() handled by pg_db fixture teardown

    async def test_returns_unavailable_when_only_one_snapshot(self, pg_db, monkeypatch):
        """With fewer than two snapshots there's nothing to diff —
        the endpoint should return ``available=False``."""
        import datetime as _dt
        database = pg_db
        acct = await database.create_account("OneShot")
        await database.add_company(acct.id, "OS", "ko", "OneShot")
        admin = await database.create_user(6101, acct.id, role=Role.OWNER)
        await database.assign_vehicle(
            user_id=admin.id, account_id=acct.id, truck_num="T-OS",
            assigned_by=0, is_primary=True,
        )
        token = create_jwt(admin.telegram_id, acct.id, "owner")

        import infra.platform as _cp
        _old_db = _cp._db
        _cp._db = database

        await database.save_scorecard_snapshot(
            acct.id,
            snapshot_date=_dt.date.today().isoformat(),
            subject_type="driver",
            subject_id="drv-os", subject_name="Driver O",
            total_score=85, window_days=7,
            breakdown={"bonuses": [], "penalties": [], "pillars": {}},
            source="test",
        )

        from interfaces.api.app import create_api
        app = create_api()
        try:
            async with AsyncClient(
                transport=ASGITransport(app=app), base_url="http://t"
            ) as c:
                r = await c.get(
                    "/api/safety/scorecards/drv-os/explanation",
                    headers={"Authorization": f"Bearer {token}"},
                )
                assert r.status_code == 200, r.text
                data = r.json()
                assert data["available"] is False
                assert data["reason"] == "not_enough_snapshots"
        finally:
            _cp._db = _old_db
            # database.close() handled by pg_db fixture teardown


class TestMyScorecardExplanation:
    """``/scorecards/me/explanation`` — driver-facing version.  Auto-
    resolves the caller's subject from their truck assignment so the
    miniapp doesn't need to know its own subject_id."""

    async def test_returns_diff_for_calling_driver(self, pg_db, monkeypatch):
        import datetime as _dt
        database = pg_db
        acct = await database.create_account("ME Expl Co")
        await database.add_company(acct.id, "MX", "kx", "ME Expl Co")
        driver = await database.create_user(7501, acct.id, role=Role.DRIVER)
        await database.assign_vehicle(
            user_id=driver.id, account_id=acct.id, truck_num="T-ME",
            assigned_by=0, is_primary=True,
        )
        token = create_jwt(driver.telegram_id, acct.id, "driver")

        import infra.platform as _cp
        _old_db = _cp._db
        _cp._db = database

        my_card = {
            "subject_id": "drv-me-expl", "subject_name": "Me", "subject_type": "driver",
            "driver_id": "drv-me-expl", "driver_name": "Me", "company": "MX",
            "score": 75, "base": 0, "bonus_total": 0, "penalty_total": 0,
            "bonuses": [], "penalties": [],
            "total": 75, "pillars": {},
            "exposure": {}, "insufficient_data": False, "inputs": {},
        }

        async def _fake_evaluate(account_id, *, subject="driver", days=7,
                                 company=None, vehicle_nums=None):
            return [my_card]
        monkeypatch.setattr(
            "interfaces.api.routes.safety._svc_evaluate_subjects",
            _fake_evaluate,
        )

        today = _dt.date.today()
        prev_bd = {
            "bonuses": [],
            "penalties": [{"rule_id": "compliance.camera_obstructed", "label": "Camera obstructed", "points": -3, "occurrences": 1}],
            "pillars": {},
        }
        curr_bd = {
            "bonuses": [{"rule_id": "compliance.vehicle_health_clean", "label": "Paired truck fault-free", "points": 4, "occurrences": 1}],
            "penalties": [],
            "pillars": {},
        }
        await database.save_scorecard_snapshot(
            acct.id,
            snapshot_date=(today - _dt.timedelta(days=7)).isoformat(),
            subject_type="driver",
            subject_id="drv-me-expl", subject_name="Me",
            total_score=72, window_days=7, breakdown=prev_bd, source="test",
        )
        await database.save_scorecard_snapshot(
            acct.id,
            snapshot_date=today.isoformat(),
            subject_type="driver",
            subject_id="drv-me-expl", subject_name="Me",
            total_score=75, window_days=7, breakdown=curr_bd, source="test",
        )

        from interfaces.api.app import create_api
        app = create_api()
        try:
            async with AsyncClient(
                transport=ASGITransport(app=app), base_url="http://t"
            ) as c:
                r = await c.get(
                    "/api/safety/scorecards/me/explanation?days=7",
                    headers={"Authorization": f"Bearer {token}"},
                )
                assert r.status_code == 200, r.text
                data = r.json()
                assert data["available"] is True
                assert data["subject_id"] == "drv-me-expl"
                assert data["score_delta"] == 3   # 72 → 75
                cleared_ids = [e["rule_id"] for e in data["penalties_cleared"]]
                earned_ids = [e["rule_id"] for e in data["bonuses_earned"]]
                assert "compliance.camera_obstructed" in cleared_ids
                assert "compliance.vehicle_health_clean" in earned_ids
        finally:
            _cp._db = _old_db
            # database.close() handled by pg_db fixture teardown

    async def test_404_when_no_truck_assignment(self, pg_db, monkeypatch):
        """A user with no truck assignment can't have a personal
        explanation — same 404 contract as ``/scorecards/me``."""
        database = pg_db
        acct = await database.create_account("Empty")
        driver = await database.create_user(7601, acct.id, role=Role.DRIVER)
        # deliberately no assign_vehicle — caller has no truck.
        token = create_jwt(driver.telegram_id, acct.id, "driver")

        import infra.platform as _cp
        _old_db = _cp._db
        _cp._db = database

        from interfaces.api.app import create_api
        app = create_api()
        try:
            async with AsyncClient(
                transport=ASGITransport(app=app), base_url="http://t"
            ) as c:
                r = await c.get(
                    "/api/safety/scorecards/me/explanation",
                    headers={"Authorization": f"Bearer {token}"},
                )
                assert r.status_code == 404
                assert "truck" in r.json()["detail"].lower()
        finally:
            _cp._db = _old_db
            # database.close() handled by pg_db fixture teardown
