"""Tests for /safety/scorecards routes + truck-filter correctness.

Covers the audit findings:
- Auth/permission gating on /safety/scorecards
- ``days`` query-param clamp (1..90)
- "own"-perm truck filter is structural (vehicle_summaries[].vehicle.name),
  not a fragile substring match against the driver display name
- Composite endpoint registers and is permission-gated
- Rules endpoint requires ``can_manage_account``
"""

from __future__ import annotations

import os
import sys

os.environ.setdefault("ENCRYPTION_KEY", "")

import pytest
import pytest_asyncio

from httpx import ASGITransport, AsyncClient

from adapters.storage import Database, Role
from interfaces.api.auth import create_jwt


@pytest_asyncio.fixture
async def safety_app(pg_db, monkeypatch):
    """Spin up the API with a seeded DB and stub the upstream Samsara call.

    Returns dict with ``app``, ``token_owner``, ``token_driver`` plus a
    ``calls`` list capturing each invocation of ``get_driver_efficiency``
    so tests can assert on the kwargs the route forwards.
    """
    database = pg_db

    acct = await database.create_account("Safety Co")
    await database.add_company(acct.id, "SC", "key_sc", "Safety Co")
    owner = await database.create_user(7001, acct.id, role=Role.OWNER)
    driver = await database.create_user(7002, acct.id, role=Role.DRIVER)

    token_owner = create_jwt(owner.telegram_id, acct.id, "owner")
    token_driver = create_jwt(driver.telegram_id, acct.id, "driver")

    import infra.platform as _cp
    _old_db = _cp._db
    _cp._db = database

    # Stub the service-layer fetch so tests don't hit Samsara.  Capture every
    # call so we can assert on the vehicle_nums filter the route forwards.
    calls: list[dict] = []

    async def _fake_efficiency(account_id, days=7, company=None, vehicle_nums=None):
        calls.append({
            "account_id": account_id,
            "days": days,
            "company": company,
            "vehicle_nums": vehicle_nums,
        })
        return [
            {
                "driver_id": "drv-1", "driver_name": "Alice", "_org": "SC",
                "_miles": 100, "_mpg": 7, "_drive_h": 5, "_idle_h": 1,
                "_drive_pct": 80, "_idle_pct": 20, "_green_pct": 90,
                "_overspeed_min": 0, "_coast_min": 0, "_cruise_min": 0,
                "_antic_pct": 0,
            },
        ]

    monkeypatch.setattr(
        "features.scorecards.router._svc_driver_efficiency",
        _fake_efficiency,
    )

    from interfaces.api.app import create_api
    app = create_api()

    yield {
        "app": app,
        "db": database,
        "acct": acct,
        "token_owner": token_owner,
        "token_driver": token_driver,
        "calls": calls,
    }

    _cp._db = _old_db
    # database.close() handled by the pg_db fixture's own teardown


def _h(token: str) -> dict:
    return {"Authorization": f"Bearer {token}"}


# ── /safety/scorecards ──────────────────────────────────────────


class TestScorecardsRoute:
    async def test_no_token_returns_401(self, safety_app):
        async with AsyncClient(
            transport=ASGITransport(app=safety_app["app"]), base_url="http://t"
        ) as c:
            r = await c.get("/api/safety/scorecards")
            # See tests/test_api_routes.py — Authorization became
            # optional with cross-subdomain SSO; missing both header
            # and cookie now yields 401, not 422.
            assert r.status_code == 401

    async def test_bad_token_returns_401(self, safety_app):
        async with AsyncClient(
            transport=ASGITransport(app=safety_app["app"]), base_url="http://t"
        ) as c:
            r = await c.get(
                "/api/safety/scorecards",
                headers={"Authorization": "Bearer junk"},
            )
            assert r.status_code == 401

    async def test_owner_gets_200(self, safety_app):
        async with AsyncClient(
            transport=ASGITransport(app=safety_app["app"]), base_url="http://t"
        ) as c:
            r = await c.get(
                "/api/safety/scorecards",
                headers=_h(safety_app["token_owner"]),
            )
            assert r.status_code == 200
            data = r.json()
            assert "scorecards" in data
            assert data["days"] == 7
            assert data["count"] == 1

    async def test_days_clamp_rejects_zero(self, safety_app):
        async with AsyncClient(
            transport=ASGITransport(app=safety_app["app"]), base_url="http://t"
        ) as c:
            r = await c.get(
                "/api/safety/scorecards?days=0",
                headers=_h(safety_app["token_owner"]),
            )
            assert r.status_code == 422

    async def test_days_clamp_rejects_over_90(self, safety_app):
        async with AsyncClient(
            transport=ASGITransport(app=safety_app["app"]), base_url="http://t"
        ) as c:
            r = await c.get(
                "/api/safety/scorecards?days=91",
                headers=_h(safety_app["token_owner"]),
            )
            assert r.status_code == 422

    async def test_owner_does_not_get_truck_filter(self, safety_app):
        """`can_scorecard_all` callers should pass vehicle_nums=None."""
        async with AsyncClient(
            transport=ASGITransport(app=safety_app["app"]), base_url="http://t"
        ) as c:
            await c.get(
                "/api/safety/scorecards",
                headers=_h(safety_app["token_owner"]),
            )
        assert safety_app["calls"][-1]["vehicle_nums"] is None

    async def test_driver_gets_truck_filter_from_assignment(
        self, safety_app, monkeypatch,
    ):
        """`can_scorecard_vehicle` callers must have their assigned trucks
        forwarded to the service, not None."""
        async def _fake_truck_nums(_user):
            return ["T-101", "T-202"]

        monkeypatch.setattr(
            "features.scorecards.router.get_user_vehicle_nums",
            _fake_truck_nums,
        )
        async with AsyncClient(
            transport=ASGITransport(app=safety_app["app"]), base_url="http://t"
        ) as c:
            r = await c.get(
                "/api/safety/scorecards",
                headers=_h(safety_app["token_driver"]),
            )
            assert r.status_code == 200
        assert safety_app["calls"][-1]["vehicle_nums"] == ["T-101", "T-202"]


# ── /safety/scorecards/rules — admin-only ───────────────────────


class TestScorecardRulesRoute:
    async def test_owner_can_list_rules(self, safety_app):
        async with AsyncClient(
            transport=ASGITransport(app=safety_app["app"]), base_url="http://t"
        ) as c:
            r = await c.get(
                "/api/safety/scorecards/rules",
                headers=_h(safety_app["token_owner"]),
            )
            assert r.status_code == 200
            data = r.json()
            assert "rules" in data
            assert data["count"] >= 1
            # Every rule has the documented contract
            sample = data["rules"][0]
            for key in ("id", "label", "category", "kind", "points",
                        "default_points", "enabled", "overridden"):
                assert key in sample

    async def test_driver_cannot_list_rules(self, safety_app):
        async with AsyncClient(
            transport=ASGITransport(app=safety_app["app"]), base_url="http://t"
        ) as c:
            r = await c.get(
                "/api/safety/scorecards/rules",
                headers=_h(safety_app["token_driver"]),
            )
            assert r.status_code == 403

    async def test_unknown_rule_id_returns_404(self, safety_app):
        async with AsyncClient(
            transport=ASGITransport(app=safety_app["app"]), base_url="http://t"
        ) as c:
            r = await c.put(
                "/api/safety/scorecards/rules/does.not.exist",
                json={"points": -5, "enabled": True},
                headers=_h(safety_app["token_owner"]),
            )
            assert r.status_code == 404


# ── Truck filter: exact match against vehicle_summaries ──


class TestTruckFilter:
    """Direct unit tests on the service-layer filter (audit finding H3).

    The previous implementation matched by substring against the driver's
    display name ("T1" matched "T10", "T100", "Tony").  The fix matches
    each driver's ``_vehicle_summaries[].vehicle.name`` exactly (case
    insensitive), eliminating the leak.
    """

    @pytest.fixture
    def patched(self, monkeypatch):
        """Stub the Samsara client + prepare_companies so we can drive the
        filter purely from in-memory rows."""
        rows = [
            {
                "driver_id": "1", "driver_name": "Alice T1",
                "_vehicle_summaries": [{"vehicle": {"name": "T1"}}],
            },
            {
                "driver_id": "2", "driver_name": "Bob T10",
                "_vehicle_summaries": [{"vehicle": {"name": "T10"}}],
            },
            {
                "driver_id": "3", "driver_name": "T100 Carol",
                "_vehicle_summaries": [{"vehicle": {"name": "T100"}}],
            },
            {
                "driver_id": "4", "driver_name": "Tony",
                "_vehicle_summaries": [{"vehicle": {"name": "T-X"}}],
            },
        ]

        class _StubClient:
            async def get_driver_efficiency(self, days=7, company=None):
                return list(rows)

        async def _stub_get_client(_acct):
            return _StubClient()

        async def _stub_prepare(_acct):
            return None

        async def _stub_window(
            account_id, *, days=7, driver_id=None, samsara_fallback=None,
        ):
            # No warehouse in this unit test — the no-filter path goes through
            # the warehouse reader, so fall back to the live stub rows.
            return await samsara_fallback() if samsara_fallback else list(rows)

        monkeypatch.setattr(
            "features.vehicles.warehouse.service.get_client", _stub_get_client,
        )
        monkeypatch.setattr(
            "features.vehicles.warehouse.service.prepare_companies", _stub_prepare,
        )
        monkeypatch.setattr(
            "features.vehicles.warehouse.readers.get_driver_efficiency_window",
            _stub_window,
        )
        return rows

    async def test_substring_no_longer_leaks(self, patched):
        """Filtering on truck "T1" must NOT also return T10, T100 or "Tony"."""
        from features.vehicles.warehouse.service import get_driver_efficiency

        out = await get_driver_efficiency(
            account_id=1, days=7, vehicle_nums=["T1"],
        )
        names = {d["driver_name"] for d in out}
        assert names == {"Alice T1"}

    async def test_case_insensitive_exact_match(self, patched):
        from features.vehicles.warehouse.service import get_driver_efficiency

        out = await get_driver_efficiency(
            account_id=1, days=7, vehicle_nums=["t10"],
        )
        names = {d["driver_name"] for d in out}
        assert names == {"Bob T10"}

    async def test_empty_truck_list_returns_empty(self, patched):
        from features.vehicles.warehouse.service import get_driver_efficiency

        out = await get_driver_efficiency(
            account_id=1, days=7, vehicle_nums=[],
        )
        assert out == []

    async def test_none_means_no_filter(self, patched):
        from features.vehicles.warehouse.service import get_driver_efficiency

        out = await get_driver_efficiency(
            account_id=1, days=7, vehicle_nums=None,
        )
        assert len(out) == 4


# ── pillar-based composite shape + curve overrides ──


class TestPillarShapeContract:
    """Dual-shape API contract: legacy fields kept for one release,
    new ``pillars`` / ``exposure`` / ``insufficient_data`` fields added.
    Frontend & bot both depend on this shape — regress = silent UI break."""

    async def test_response_carries_both_legacy_and_pillar_blocks(
        self, safety_app, monkeypatch,
    ):
        # The composite endpoint goes through capabilities.scorecards.service.
        # Stub it directly so the test focuses on response *shape*, not on
        # the (separately tested) scoring engine.
        async def _fake_evaluate(account_id, **_kw):
            return [{
                "driver_id": "drv-1",
                "driver_name": "Alice",
                "subject_id": "drv-1",
                "subject_name": "Alice",
                "subject_type": "driver",
                "company": "SC",
                # Legacy aliases.
                "score": 87,
                "base": 0,
                "bonus_total": 7,
                "penalty_total": -3,
                "bonuses": [],
                "penalties": [],
                # New Option-C fields.
                "total": 87,
                "pillars": {
                    "safety": {
                        "name": "safety", "cap": 50, "subtotal": 47,
                        "bonus_total": 0, "penalty_total": -3,
                        "has_data": True, "bonuses": [], "penalties": [],
                    },
                    "efficiency": {
                        "name": "efficiency", "cap": 25, "subtotal": 22,
                        "bonus_total": 0, "penalty_total": 0,
                        "has_data": True, "bonuses": [], "penalties": [],
                    },
                    "compliance": {
                        "name": "compliance", "cap": 25, "subtotal": 18,
                        "bonus_total": 0, "penalty_total": 0,
                        "has_data": True, "bonuses": [], "penalties": [],
                    },
                },
                "exposure": {"miles": 250.0, "drive_hours": 8.5},
                "insufficient_data": False,
            }]
        monkeypatch.setattr(
            "features.scorecards.router._svc_evaluate_subjects",
            _fake_evaluate,
        )
        async with AsyncClient(
            transport=ASGITransport(app=safety_app["app"]), base_url="http://t"
        ) as c:
            r = await c.get(
                "/api/safety/scorecards/composite",
                headers=_h(safety_app["token_owner"]),
            )
            assert r.status_code == 200
            cards = r.json()["scorecards"]
            assert cards, "stub fixture should yield at least one driver"
            card = cards[0]
            # Legacy aliases (pre-Option C consumers).
            for key in ("score", "base", "bonus_total", "penalty_total",
                        "bonuses", "penalties"):
                assert key in card, f"missing legacy key {key!r}"
            # New Option-C blocks.
            assert "total" in card
            assert "pillars" in card and isinstance(card["pillars"], dict)
            for pk in ("safety", "efficiency", "compliance"):
                assert pk in card["pillars"]
                p = card["pillars"][pk]
                for f in ("name", "cap", "subtotal", "has_data"):
                    assert f in p
            assert "exposure" in card and "miles" in card["exposure"]
            assert "insufficient_data" in card
            # total == score (single source of truth, just two names).
            assert card["total"] == card["score"]


class TestRuleCurveRoundTrip:
    """PUT a curve override on a curve-backed rule and assert the values
    surface back through GET — proves DB column wiring is intact."""

    async def test_put_then_get_round_trip(self, safety_app):
        rule_id = "safety.harsh_brake"
        async with AsyncClient(
            transport=ASGITransport(app=safety_app["app"]), base_url="http://t"
        ) as c:
            put = await c.put(
                f"/api/safety/scorecards/rules/{rule_id}",
                json={
                    "points": -20,
                    "enabled": True,
                    "curve_x_zero": 0.0,
                    "curve_x_max": 12.0,
                    "curve_y_max": -25,
                },
                headers=_h(safety_app["token_owner"]),
            )
            assert put.status_code == 200, put.text

            got = await c.get(
                "/api/safety/scorecards/rules",
                headers=_h(safety_app["token_owner"]),
            )
            assert got.status_code == 200
            row = next(
                r for r in got.json()["rules"] if r["id"] == rule_id
            )
            assert row["overridden"] is True
            assert row["pillar"] == "safety"
            assert row["curve_kind"] == "linear_per_kmi"
            assert row["curve_x_zero"] == 0.0
            assert row["curve_x_max"] == 12.0
            assert row["curve_y_max"] == -25
