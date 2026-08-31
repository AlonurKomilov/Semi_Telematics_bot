"""The config family's ACCOUNT scope (``can_manage_config_all``).

What must hold (SSOT: capabilities/config/docs/ARCHITECTURE.md):
  * KPI thresholds accept BOTH gates — ``can_manage_account`` keeps its
    historical reach (owner/Full-Admin), ``can_manage_config_all`` is the
    family's delegatable flag.
  * Scorecard rules ride ``can_manage_config_all`` alone (the folded
    ``can_manage_scorecard_rules``): owner via seed, safety MANAGER via
    tier grant — and that tier grant now also reaches KPI thresholds,
    the fold's documented widening, pinned here on purpose.
  * A plain employee has neither until the owner ticks the matrix row;
    ticking it is sufficient (delegation works without redeploy).
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
    acct = await db.create_account("Config Family Co")
    owner = await db.create_user(9501, acct.id, role=Role.OWNER)
    safety_mgr = await db.create_user(9502, acct.id, role=Role.SAFETY)
    await db.update_user(safety_mgr.id, is_manager=True)
    fleet_emp = await db.create_user(9503, acct.id, role=Role.FLEET)

    import infra.platform as _cp
    _old = _cp._db
    _cp._db = db
    from interfaces.api.app import create_api
    app = create_api()

    yield {
        "app": app, "db": db, "acct": acct,
        "owner": create_jwt(owner.telegram_id, acct.id, "owner"),
        "safety_mgr": create_jwt(safety_mgr.telegram_id, acct.id, "safety",
                                 is_manager=True),
        "emp": create_jwt(fleet_emp.telegram_id, acct.id, "fleet"),
    }
    _cp._db = _old


def _h(token: str) -> dict:
    return {"Authorization": f"Bearer {token}"}


async def _client(app):
    return AsyncClient(transport=ASGITransport(app=app), base_url="http://t")


BODY = {"thresholds": {"rpm_good": 2.5}}


class TestKpiThresholdGate:
    async def test_owner_keeps_reach_on_the_config_flag_alone(self, seeded):
        """can_manage_account is no longer in this gate.

        It was — via require_permission_any — to preserve the pre-family
        reach of owner/Full-Admin.  That was the View/Manage/Config mixing
        in miniature: General settings' MANAGE action carrying KPI's
        grading rules.  Both flags are owner-seeded, so removing it costs
        the owner nothing; what it stops is an account that delegated
        General settings picking up grading rules it never granted.
        """
        async with await _client(seeded["app"]) as c:
            r = await c.put("/api/kpi/thresholds", json=BODY,
                            headers=_h(seeded["owner"]))
            assert r.status_code == 200
            assert r.json()["thresholds"]["rpm_good"] == 2.5

    async def test_the_config_READ_is_gated_too_not_just_the_write(self, seeded):
        """GET /kpi/thresholds rode can_kpi — the VIEW action — on the
        reasoning that reading a threshold is harmless.

        Its only caller is ThresholdsDialog, the editor; the KPI page
        never asks, because grades arrive already computed.  A read that
        exists solely to populate an editor is part of Config, and leaving
        it on View made the write gate decorative: any can_kpi holder
        could see every value and simply not save.
        """
        from capabilities.permissions.roles import invalidate_permissions_cache
        db, acct = seeded["db"], seeded["acct"]
        try:
            async with await _client(seeded["app"]) as c:
                # can_kpi alone: the grades, yes; the thresholds, no.
                await db.set_role_permissions(
                    acct.id, "fleet", {"can_kpi": True}, 9501)
                invalidate_permissions_cache(acct.id)
                r = await c.get("/api/kpi/config", headers=_h(seeded["emp"]))
                assert r.status_code == 403, (
                    "the config READ is back on the VIEW action"
                )

                await db.set_role_permissions(
                    acct.id, "fleet",
                    {"can_kpi": True, "can_manage_config_all": True}, 9501)
                invalidate_permissions_cache(acct.id)
                r = await c.get("/api/kpi/config", headers=_h(seeded["emp"]))
                assert r.status_code == 200

                # The DEPRECATED alias must still answer identically —
                # that is the whole point of keeping it mounted.
                r = await c.get("/api/kpi/thresholds", headers=_h(seeded["emp"]))
                assert r.status_code == 200
        finally:
            # These grants are account-wide and the fixture is shared, so
            # leaving them set makes a LATER test pass or fail depending on
            # collection order.  It did: TestScorecardRulesGate's "plain
            # employee refused" started seeing a config-granted fleet role.
            await db.set_role_permissions(acct.id, "fleet", {}, 9501)
            invalidate_permissions_cache(acct.id)

    async def test_plain_employee_refused_until_owner_delegates(self, seeded):
        from capabilities.permissions.roles import invalidate_permissions_cache
        db, acct = seeded["db"], seeded["acct"]
        async with await _client(seeded["app"]) as c:
            r = await c.put("/api/kpi/thresholds", json=BODY,
                            headers=_h(seeded["emp"]))
            assert r.status_code == 403
            # The matrix row is the whole delegation story — one tick.
            await db.set_role_permissions(
                acct.id, "fleet", {"can_manage_config_all": True}, 9501)
            invalidate_permissions_cache(acct.id)
            r = await c.put("/api/kpi/thresholds", json=BODY,
                            headers=_h(seeded["emp"]))
            assert r.status_code == 200


class TestScorecardRulesGate:
    async def test_safety_manager_kept_scoring_config_through_the_fold(self, seeded):
        """The safety lead owned scoring config via the folded flag; the
        tier grant carried into can_manage_config_all."""
        async with await _client(seeded["app"]) as c:
            r = await c.get("/api/admin/scorecard-rules",
                            headers=_h(seeded["safety_mgr"]))
            assert r.status_code == 200

    async def test_the_folds_widening_is_deliberate(self, seeded):
        """Documented consequence: the safety manager's config grant now
        reaches KPI thresholds too — one family flag, one reach."""
        async with await _client(seeded["app"]) as c:
            r = await c.put("/api/kpi/thresholds", json=BODY,
                            headers=_h(seeded["safety_mgr"]))
            assert r.status_code == 200

    async def test_plain_employee_refused(self, seeded):
        async with await _client(seeded["app"]) as c:
            r = await c.get("/api/admin/scorecard-rules",
                            headers=_h(seeded["emp"]))
            assert r.status_code == 403
