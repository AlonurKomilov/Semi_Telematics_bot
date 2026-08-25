"""``/alerts/triggers`` end to end — and the walls around one person's list.

A trigger is self-service: no permission flag gates writing one, because
anyone who can receive an alert may decide the number at which they want
it.  What is NOT self-service is reaching another person's list, or
naming a warehouse column, or asking for a threshold that could never
fire.  These tests pin the walls, not the happy path alone.
"""

from __future__ import annotations

import os
import sys

os.environ.setdefault("ENCRYPTION_KEY", "test-key-32-chars-min-aaaaaaaaaaaaaaaaaaaa")
os.environ.setdefault("JWT_SECRET", "test-secret-32-chars-or-more-please-aaaaaaaaaaaa")
os.environ.setdefault("OBJECT_STORE_BACKEND", "disk")
os.environ.setdefault("OBJECT_STORE_ROOT", "/tmp/trigger_api_store")
sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

import pytest_asyncio
from httpx import AsyncClient, ASGITransport

from adapters.storage import Role

API = "/api/alerts/triggers"


@pytest_asyncio.fixture
async def api(pg_db):
    import infra.platform as cp
    cp._db = pg_db
    from interfaces.api.app import create_api
    return create_api(), pg_db


def _headers(db_user, acct, role: str = "fleet"):
    from interfaces.api.auth import create_jwt
    return {"Authorization": f"Bearer {create_jwt(db_user.telegram_id or 0, acct.id, role, user_id=db_user.id)}"}


async def _user(db, acct_id, email, role=Role.FLEET):
    from interfaces.api.auth import _hash_password
    return await db.create_user_with_email(
        email=email, password_hash=_hash_password("password12345"),
        account_id=acct_id, role=role, display_name="T",
    )


class TestTriggerCrud:
    async def test_create_list_edit_delete(self, api):
        app, db = api
        acct = await db.create_account("Trigger Co")
        me = await _user(db, acct.id, f"a.{acct.id}@x.com")
        h = _headers(me, acct)
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
            r = await c.post(API, headers=h, json={"metric": "def_pct", "threshold": 10})
            assert r.status_code == 200, r.text
            made = r.json()
            # The server hands back the sentence a person reads, so the
            # client never rebuilds the phrasing out of parts.
            assert made["describes"] == "DEF level below 10%"

            listed = (await c.get(API, headers=h)).json()["triggers"]
            assert [t["id"] for t in listed] == [made["id"]]

            r = await c.patch(f"{API}/{made['id']}", headers=h, json={"threshold": 15})
            assert r.status_code == 200 and r.json()["threshold"] == 15

            r = await c.patch(f"{API}/{made['id']}", headers=h, json={"enabled": False})
            assert r.status_code == 200 and r.json()["enabled"] is False

            assert (await c.delete(f"{API}/{made['id']}", headers=h)).status_code == 200
            assert (await c.get(API, headers=h)).json()["triggers"] == []

    async def test_the_metric_list_drives_the_editor(self, api):
        """The form is rendered from this, so it must carry every fact the
        editor needs — the range, the unit, the direction, and the sentence
        that stops someone setting a physically meaningless number."""
        app, db = api
        acct = await db.create_account("Menu Co")
        me = await _user(db, acct.id, f"m.{acct.id}@x.com")
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
            body = (await c.get(f"{API}/metrics", headers=_headers(me, acct))).json()
        by_key = {m["key"]: m for m in body["metrics"]}
        assert set(by_key) == {"fuel_pct", "def_pct", "battery_v", "coolant_c", "oil_psi"}
        battery = by_key["battery_v"]
        assert battery["direction"] == "below" and battery["unit"] == "V"
        assert battery["min"] < battery["max"]
        assert battery["requires_engine"] == "on"
        assert "parked" in battery["hint"].lower()


class TestWalls:
    async def test_another_persons_trigger_is_not_reachable(self, api):
        """Not 403 — 404.  Someone else's trigger id is not a thing that
        exists as far as this person's list is concerned."""
        app, db = api
        acct = await db.create_account("Two Co")
        mine = await _user(db, acct.id, f"one.{acct.id}@x.com")
        theirs = await _user(db, acct.id, f"two.{acct.id}@x.com")
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
            made = (await c.post(API, headers=_headers(mine, acct),
                                 json={"metric": "fuel_pct", "threshold": 26})).json()
            other = _headers(theirs, acct)
            assert (await c.get(API, headers=other)).json()["triggers"] == []
            assert (await c.patch(f"{API}/{made['id']}", headers=other,
                                  json={"threshold": 5})).status_code == 404
            assert (await c.delete(f"{API}/{made['id']}", headers=other)).status_code == 404
            # And it is still there for its owner.
            assert len((await c.get(API, headers=_headers(mine, acct))).json()["triggers"]) == 1

    async def test_a_column_name_is_not_a_metric(self, api):
        """The catalog is a whitelist.  If it were not, this form would be
        an arbitrary read of the warehouse."""
        app, db = api
        acct = await db.create_account("White Co")
        me = await _user(db, acct.id, f"w.{acct.id}@x.com")
        h = _headers(me, acct)
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
            for bad in ("odometer_mi", "speed_mph", "rpm", "password"):
                r = await c.post(API, headers=h, json={"metric": bad, "threshold": 10})
                assert r.status_code == 400, bad
                assert "not a metric" in r.json()["detail"]

    async def test_a_threshold_nobody_could_cross_is_refused(self, api):
        app, db = api
        acct = await db.create_account("Range Co")
        me = await _user(db, acct.id, f"r.{acct.id}@x.com")
        h = _headers(me, acct)
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
            r = await c.post(API, headers=h, json={"metric": "fuel_pct", "threshold": 1})
            assert r.status_code == 400 and "never fire" in r.json()["detail"]
            r = await c.post(API, headers=h, json={"metric": "fuel_pct", "threshold": 95})
            assert r.status_code == 400 and "almost every vehicle" in r.json()["detail"]

    async def test_an_edit_is_validated_like_a_create(self, api):
        """Otherwise the range check is a formality anyone can step past
        by creating a sane trigger and then editing it."""
        app, db = api
        acct = await db.create_account("Edit Co")
        me = await _user(db, acct.id, f"e.{acct.id}@x.com")
        h = _headers(me, acct)
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
            made = (await c.post(API, headers=h,
                                 json={"metric": "fuel_pct", "threshold": 26})).json()
            r = await c.patch(f"{API}/{made['id']}", headers=h, json={"threshold": 99})
            assert r.status_code == 400

    async def test_the_cap_refuses_with_something_actionable(self, api):
        app, db = api
        acct = await db.create_account("Cap Co")
        me = await _user(db, acct.id, f"c.{acct.id}@x.com")
        h = _headers(me, acct)
        from capabilities.alerting.triggers.models import MAX_TRIGGERS_PER_USER
        # Fill the quota through storage rather than the API: the POST
        # route is also rate-limited, and a test that spends the throttle
        # proves the throttle works, not the cap.
        for i in range(MAX_TRIGGERS_PER_USER):
            await db.create_alert_trigger(
                acct.id, me.id, metric="fuel_pct", threshold=20 + (i % 30))
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
            r = await c.post(API, headers=h, json={"metric": "def_pct", "threshold": 10})
        assert r.status_code == 409, r.text
        assert "delete one" in r.json()["detail"]

    async def test_a_trigger_never_lands_on_the_alerts_board(self, api):
        """A personal threshold is not the account's news.  If a trigger
        wrote an alert_history row, one person's 26% would join a board
        that already carries thousands nobody has acknowledged."""
        app, db = api
        acct = await db.create_account("Board Co")
        me = await _user(db, acct.id, f"b.{acct.id}@x.com")
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
            await c.post(API, headers=_headers(me, acct),
                         json={"metric": "fuel_pct", "threshold": 26})
        cur = await db._db.execute(
            "SELECT COUNT(*) FROM alert_history WHERE account_id = ?", (acct.id,))
        assert (await cur.fetchone())[0] == 0
