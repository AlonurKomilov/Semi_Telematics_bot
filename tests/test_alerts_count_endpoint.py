"""Tests for ``GET /api/alerts/pending/count`` (Phase A).

Driver isolation must match the existing ``/api/alerts/pending`` filter
behaviour exactly so we can swap the front-end poll without privacy regression.
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
async def seeded(tmp_path):
    db_path = str(tmp_path / "alerts_count.db")
    database = Database(db_path)
    await database.initialize()

    acct = await database.create_account("Count Co")
    await database.add_company(acct.id, "CO", "key_co", "Count Co")
    owner = await database.create_user(7001, acct.id, role=Role.OWNER)
    driver = await database.create_user(7002, acct.id, role=Role.DRIVER)
    stranger = await database.create_user(7003, acct.id, role=Role.DRIVER)

    # Driver gets only T-DRIVER; stranger gets T-OTHER.
    await database.assign_vehicle(
        user_id=driver.id, account_id=acct.id, truck_num="T-DRIVER",
        assigned_by=0, is_primary=True,
    )
    await database.assign_vehicle(
        user_id=stranger.id, account_id=acct.id, truck_num="T-OTHER",
        assigned_by=0, is_primary=True,
    )

    # Two distinct active alerts: one on driver's truck, one on stranger's.
    await database.create_alert_ack(
        account_id=acct.id, alert_type="fault",
        vehicle_id="V1", vehicle_name="T-DRIVER",
        alert_key="CO:V1:driver", message_id=1, chat_id=7002, sent_to=7002,
    )
    await database.create_alert_ack(
        account_id=acct.id, alert_type="fault",
        vehicle_id="V2", vehicle_name="T-OTHER",
        alert_key="CO:V2:other", message_id=2, chat_id=7003, sent_to=7003,
    )

    import infra.platform as _cp
    from adapters.storage.tenant_router import LegacyRouter
    _old_router, _old_db = _cp._router, _cp._db
    _cp._router = LegacyRouter(database)
    _cp._db = database

    from interfaces.api.app import create_api
    app = create_api()

    yield {
        "app": app, "db": database, "acct": acct,
        "token_owner": create_jwt(owner.telegram_id, acct.id, "owner"),
        "token_driver": create_jwt(driver.telegram_id, acct.id, "driver"),
    }

    _cp._router, _cp._db = _old_router, _old_db
    await database.close()


def _h(token: str) -> dict:
    return {"Authorization": f"Bearer {token}"}


class TestPendingCountEndpoint:
    async def test_owner_sees_full_count(self, seeded):
        async with AsyncClient(
            transport=ASGITransport(app=seeded["app"]), base_url="http://t"
        ) as c:
            r = await c.get(
                "/api/alerts/pending/count",
                headers=_h(seeded["token_owner"]),
            )
            assert r.status_code == 200
            assert r.json() == {"count": 2}

    async def test_driver_only_sees_own_truck(self, seeded):
        async with AsyncClient(
            transport=ASGITransport(app=seeded["app"]), base_url="http://t"
        ) as c:
            r = await c.get(
                "/api/alerts/pending/count",
                headers=_h(seeded["token_driver"]),
            )
            assert r.status_code == 200
            # Driver isolation: only the alert on T-DRIVER is visible.
            assert r.json() == {"count": 1}

    async def test_no_token_unauthorized(self, seeded):
        async with AsyncClient(
            transport=ASGITransport(app=seeded["app"]), base_url="http://t"
        ) as c:
            r = await c.get("/api/alerts/pending/count")
            # FastAPI returns 422 when the auth dependency can't extract a
            # token; auth-rejected tokens return 401/403.  All count as "no
            # access" — make sure the endpoint isn't open.
            assert r.status_code in (401, 403, 422)

    async def test_count_zero_when_all_acked(self, seeded):
        # Acknowledge both pending alerts at the DB level.
        db = seeded["db"]
        pending = await db.get_pending_alerts(seeded["acct"].id)
        for row in pending:
            await db.acknowledge_alert(row["id"], 7001, account_id=seeded["acct"].id)

        async with AsyncClient(
            transport=ASGITransport(app=seeded["app"]), base_url="http://t"
        ) as c:
            r = await c.get(
                "/api/alerts/pending/count",
                headers=_h(seeded["token_owner"]),
            )
            assert r.status_code == 200
            assert r.json() == {"count": 0}
