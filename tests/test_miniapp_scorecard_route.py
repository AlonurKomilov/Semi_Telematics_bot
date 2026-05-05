"""Tests for /api/safety/scorecards/me — driver miniapp scorecard route (Phase D).

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
async def me_app(tmp_path, monkeypatch):
    db_path = str(tmp_path / "me.db")
    database = Database(db_path)
    await database.initialize()

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
    from adapters.storage.tenant_router import LegacyRouter
    _old_router, _old_db = _cp._router, _cp._db
    _cp._router = LegacyRouter(database)
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

    # Seed a prior-week snapshot for the caller so week_delta is populated.
    # We need a snapshot ~7 days back.  Use a synthetic past date string —
    # the route uses index 6 of the history list, so we just need at least
    # 7 rows older than today.  Insert 7 dated entries (today-7 .. today-1).
    import datetime as _dt
    today = _dt.date.today()
    # Earliest snapshot (>=7 days back) carries the "previous week" baseline:
    # total=72, safety=35, efficiency=18, compliance=19.
    baseline_breakdown = {
        "pillars": {
            "safety":     {"subtotal": 35, "has_data": True},
            "efficiency": {"subtotal": 18, "has_data": True},
            "compliance": {"subtotal": 19, "has_data": True},
        }
    }
    for i in range(1, 8):
        d = (today - _dt.timedelta(days=i)).isoformat()
        # Vary totals slightly so the index-6 row is the oldest one.
        total = 72 if i == 7 else 78
        bd = baseline_breakdown if i == 7 else {"pillars": {}}
        await database.save_scorecard_snapshot(
            acct.id,
            snapshot_date=d, subject_type="driver",
            subject_id="drv-me", subject_name="Me",
            total_score=total, window_days=7, breakdown=bd, source="test",
        )

    from interfaces.api.app import create_api
    app = create_api()

    yield {
        "app": app, "db": database, "acct": acct,
        "token_driver": token_driver,
        "token_no_truck": token_no_truck,
    }

    _cp._router, _cp._db = _old_router, _old_db
    await database.close()


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

    async def test_week_delta_null_when_no_snapshots(self, tmp_path, monkeypatch):
        """A fresh tenant with no snapshot history should still return 200,
        but ``week_delta`` values are all ``None``."""
        db_path = str(tmp_path / "fresh.db")
        database = Database(db_path)
        await database.initialize()
        acct = await database.create_account("Fresh")
        await database.add_company(acct.id, "FR", "kf", "Fresh")
        driver = await database.create_user(9001, acct.id, role=Role.DRIVER)
        await database.assign_vehicle(
            user_id=driver.id, account_id=acct.id, truck_num="T-1",
            assigned_by=0, is_primary=True,
        )
        token = create_jwt(driver.telegram_id, acct.id, "driver")

        import infra.platform as _cp
        from adapters.storage.tenant_router import LegacyRouter
        _old_router, _old_db = _cp._router, _cp._db
        _cp._router = LegacyRouter(database)
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
            _cp._router, _cp._db = _old_router, _old_db
            await database.close()

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

    async def test_admin_token_sees_ranks_and_account_size(self, tmp_path, monkeypatch):
        from adapters.storage import Database, Role
        from interfaces.api.auth import create_jwt
        import infra.platform as _cp
        from adapters.storage.tenant_router import LegacyRouter

        db_path = str(tmp_path / "admin_ranks.db")
        database = Database(db_path)
        await database.initialize()
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

        _old_router, _old_db = _cp._router, _cp._db
        _cp._router = LegacyRouter(database)
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
        finally:
            _cp._router, _cp._db = _old_router, _old_db
            await database.close()
