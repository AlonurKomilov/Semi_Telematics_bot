"""The incentive config surface: /kpi/config/incentives.

This configuration decides what dispatchers are PAID, so the tests care
about three things the thresholds surface never had to:

  * a bad config is refused at WRITE time with the engine's message —
    never stored to become a silent 0% at run time;
  * per-company targets cannot reference another account's companies,
    and one company cannot carry two targets (the customer's Excel
    mistake, made impossible);
  * everything rides can_manage_config_all — money rules are config,
    and a plain employee is refused until the owner delegates.
"""

from __future__ import annotations

import os
import sys

os.environ.setdefault("ENCRYPTION_KEY", "")

import pytest_asyncio

from httpx import ASGITransport, AsyncClient

from adapters.storage import Role
from interfaces.api.auth import create_jwt


@pytest_asyncio.fixture
async def seeded(pg_db):
    db = pg_db
    acct = await db.create_account("Incentive Co")
    owner = await db.create_user(9601, acct.id, role=Role.OWNER)
    emp = await db.create_user(9602, acct.id, role=Role.DISPATCHER)
    osy = await db.add_company(acct.id, "OSY", display_name="OSY LLC")
    cft = await db.add_company(acct.id, "CFT", display_name="CFT Inc")

    import infra.platform as _cp
    _old = _cp._db
    _cp._db = db
    from interfaces.api.app import create_api
    app = create_api()

    yield {
        "app": app, "db": db, "acct": acct,
        "osy": osy, "cft": cft,
        "owner": create_jwt(owner.telegram_id, acct.id, "owner"),
        "emp": create_jwt(emp.telegram_id, acct.id, "dispatcher"),
    }
    _cp._db = _old


def _h(token: str) -> dict:
    return {"Authorization": f"Bearer {token}"}


async def _client(app):
    return AsyncClient(transport=ASGITransport(app=app), base_url="http://t")


# The golden sheet's ladder, as the editor would submit it.
LADDER = {
    "model": "ladder",
    "calc_cadence": "weekly",
    "exception_cap_pct": 2.0,
    "floor_weekly_gross": 7000.0,
    "floor_rpm": 1.9,
    "tiers": [
        {"min_rpm": 2.0, "pct": 1.0},
        {"requires_target": True, "pct": 1.0},
        {"requires_target": True, "min_rpm": 2.0, "pct": 1.5},
        {"requires_target": True, "min_rpm": 2.5, "pct": 2.0},
    ],
}


class TestRoundTrip:
    async def test_unconfigured_reads_as_null_not_defaults(self, seeded):
        """Money config has no invented defaults: until an owner sets it,
        the UI must show the setup state, not a fake ladder."""
        async with await _client(seeded["app"]) as c:
            r = await c.get("/api/kpi/config/incentives",
                            headers=_h(seeded["owner"]))
            assert r.status_code == 200
            body = r.json()
            assert body["config"] is None
            assert body["targets"] == []
            assert {x["code"] for x in body["companies"]} == {"OSY", "CFT"}

    async def test_put_then_get_returns_what_was_stored(self, seeded):
        async with await _client(seeded["app"]) as c:
            r = await c.put("/api/kpi/config/incentives", json=LADDER,
                            headers=_h(seeded["owner"]))
            assert r.status_code == 200, r.text
            cfg = r.json()["config"]
            assert cfg["model"] == "ladder"
            assert cfg["exception_cap_pct"] == 2.0
            assert len(cfg["tiers"]) == 4
            assert cfg["tiers"][2] == {
                "requires_target": True, "min_rpm": 2.0, "pct": 1.5}

    async def test_targets_round_trip_with_company_join(self, seeded):
        async with await _client(seeded["app"]) as c:
            r = await c.put(
                "/api/kpi/config/incentives/targets",
                json={"targets": {
                    str(seeded["osy"].id): 8000.0,
                    str(seeded["cft"].id): 7000.0,
                }},
                headers=_h(seeded["owner"]),
            )
            assert r.status_code == 200, r.text
            targets = {t["company_code"]: t["weekly_gross_target"]
                       for t in r.json()["targets"]}
            assert targets == {"OSY": 8000.0, "CFT": 7000.0}


class TestValidation:
    """The engine's message reaches the admin as a 422, before storage."""

    async def test_unknown_model_is_refused(self, seeded):
        async with await _client(seeded["app"]) as c:
            r = await c.put(
                "/api/kpi/config/incentives",
                json={**LADDER, "model": "vibes"},
                headers=_h(seeded["owner"]),
            )
            assert r.status_code == 422
            assert "model" in r.json()["detail"]

    async def test_tier_with_wrong_keys_for_model_is_refused(self, seeded):
        async with await _client(seeded["app"]) as c:
            r = await c.put(
                "/api/kpi/config/incentives",
                json={**LADDER,
                      "tiers": [{"gross_min": 0, "pct": 1.0}]},
                headers=_h(seeded["owner"]),
            )
            assert r.status_code == 422
            assert "ladder model does not use" in r.json()["detail"]

    async def test_percent_typo_is_refused(self, seeded):
        # 150 where someone meant 1.5 — a two-order-of-magnitude payout.
        async with await _client(seeded["app"]) as c:
            r = await c.put(
                "/api/kpi/config/incentives",
                json={**LADDER, "tiers": [{"pct": 150}]},
                headers=_h(seeded["owner"]),
            )
            assert r.status_code == 422
            assert "typo" in r.json()["detail"]

    async def test_foreign_company_target_is_refused(self, seeded):
        async with await _client(seeded["app"]) as c:
            r = await c.put(
                "/api/kpi/config/incentives/targets",
                json={"targets": {"999999": 8000.0}},
                headers=_h(seeded["owner"]),
            )
            assert r.status_code == 422
            assert "unknown company" in r.json()["detail"]

    async def test_inverted_grading_thresholds_are_refused(self, seeded):
        """good/bad pairs must point the right way — an inverted pair
        silently grades every dispatcher wrong.  The check runs on the
        RESULT (overrides merged over defaults), so a partial write that
        inverts against a default is caught too."""
        async with await _client(seeded["app"]) as c:
            r = await c.put(
                "/api/kpi/config",
                json={"thresholds": {"rpm_good": 1.0, "rpm_bad": 2.0}},
                headers=_h(seeded["owner"]),
            )
            assert r.status_code == 422
            assert "must be higher" in r.json()["detail"]

            # A partial override inverting against the DEFAULT bad (2.0).
            r = await c.put(
                "/api/kpi/config",
                json={"thresholds": {"rpm_good": 1.5}},
                headers=_h(seeded["owner"]),
            )
            assert r.status_code == 422

            # A coherent set still saves.
            r = await c.put(
                "/api/kpi/config",
                json={"thresholds": {"rpm_good": 2.5, "rpm_bad": 2.1}},
                headers=_h(seeded["owner"]),
            )
            assert r.status_code == 200, r.text
            assert r.json()["thresholds"]["rpm_good"] == 2.5

    async def test_nothing_was_stored_by_the_refusals(self, seeded):
        async with await _client(seeded["app"]) as c:
            await c.put("/api/kpi/config/incentives",
                        json={**LADDER, "model": "vibes"},
                        headers=_h(seeded["owner"]))
            r = await c.get("/api/kpi/config/incentives",
                            headers=_h(seeded["owner"]))
            assert r.json()["config"] is None


class TestGate:
    async def test_a_dispatcher_cannot_read_or_write_the_rules(self, seeded):
        """The people the rules PAY must not set the rules.  Dispatchers
        get their outcomes (and later /me); the configuration is
        can_manage_config_all only."""
        async with await _client(seeded["app"]) as c:
            r = await c.get("/api/kpi/config/incentives",
                            headers=_h(seeded["emp"]))
            assert r.status_code == 403
            r = await c.put("/api/kpi/config/incentives", json=LADDER,
                            headers=_h(seeded["emp"]))
            assert r.status_code == 403
