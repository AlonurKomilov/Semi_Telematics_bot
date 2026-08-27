"""Loads writes: accountability, not walls.

The dispatcher own-scope write wall came down 2026-07-29 (owner
decision): any ``can_manage_loads`` holder edits ANY load, and the
per-load trail (``load_events`` — actor + field-level old→new on every
human write) is what answers "who changed what".  These tests pin the
trail's guarantees at the storage layer:

  * every mutating method leaves an event IN THE SAME CALL — there is
    no code path that edits a load without a row;
  * edit events carry the exact old→new pairs (a disputed rate change
    is settleable);
  * soft-deleting a load keeps its trail (the trail IS the point);
  * the retired ``can_loads_manage_all`` flag no longer exists.
"""

from __future__ import annotations

import os
import sys

os.environ.setdefault("ENCRYPTION_KEY", "")

import pytest

from dataclasses import fields

from adapters.storage import Role
from capabilities.permissions.roles import FeatureSet


class TestTheWallIsGone:
    def test_the_flag_no_longer_exists(self):
        assert "can_loads_manage_all" not in {f.name for f in fields(FeatureSet)}

    def test_router_has_no_scope_helpers(self):
        import features.loads.router as r
        assert not hasattr(r, "_can_write_load")
        assert not hasattr(r, "_is_manage_all")


@pytest.mark.asyncio
class TestTheTrail:
    async def _mk_load(self, db, account_id, actor=None, **f):
        return await db.add_load(
            account_id, actor_user_id=actor,
            load_number="L-1", customer="Acme", total_rate=2100.0,
            dispatcher_user_id=7, **f,
        )

    async def test_create_edit_delete_each_leave_a_row(self, seeded_db):
        db, account = seeded_db["db"], seeded_db["account"]
        load_id = await self._mk_load(db, account.id, actor=7)

        await db.update_load(account.id, load_id, actor_user_id=12,
                             total_rate=1800.0, dispatcher_user_id=12)
        await db.deactivate_load(account.id, load_id, actor_user_id=12)

        events = await db.list_load_events(account.id, load_id)
        assert [e["event_type"] for e in events] == \
            ["deleted", "edited", "created"]          # newest first

    async def test_edit_event_carries_old_to_new(self, seeded_db):
        """The dispute-settling guarantee: 'rate 2100→1800, dispatcher
        7→12', never just 'edited by X'."""
        db, account = seeded_db["db"], seeded_db["account"]
        load_id = await self._mk_load(db, account.id)

        await db.update_load(account.id, load_id, actor_user_id=12,
                             total_rate=1800.0, dispatcher_user_id=12)

        edited = (await db.list_load_events(account.id, load_id))[0]
        assert edited["event_type"] == "edited"
        assert edited["actor_user_id"] == 12
        assert edited["changes"]["total_rate"] == [2100.0, 1800.0]
        assert edited["changes"]["dispatcher_user_id"] == [7, 12]
        # Snapshot: the dispatcher BEFORE the change — who the load was
        # taken from, not who took it.
        assert edited["dispatcher_user_id"] == 7

    async def test_noop_edit_leaves_no_noise(self, seeded_db):
        db, account = seeded_db["db"], seeded_db["account"]
        load_id = await self._mk_load(db, account.id)
        await db.update_load(account.id, load_id, actor_user_id=12,
                             total_rate=2100.0)     # same value
        events = await db.list_load_events(account.id, load_id)
        assert [e["event_type"] for e in events] == ["created"]

    async def test_deleted_load_keeps_its_trail(self, seeded_db):
        db, account = seeded_db["db"], seeded_db["account"]
        load_id = await self._mk_load(db, account.id, actor=7)
        await db.deactivate_load(account.id, load_id, actor_user_id=12)
        events = await db.list_load_events(account.id, load_id)
        assert events and events[0]["event_type"] == "deleted"
        assert events[0]["actor_user_id"] == 12

    async def test_line_items_write_into_the_parent_loads_story(self, seeded_db):
        db, account = seeded_db["db"], seeded_db["account"]
        load_id = await self._mk_load(db, account.id)
        item_id = await db.add_load_line_item(
            account.id, kind="tonu", amount=250.0, load_id=load_id,
            created_by=900012, actor_user_id=12,
        )
        await db.delete_load_line_item(account.id, item_id, actor_user_id=12)
        types = [e["event_type"]
                 for e in await db.list_load_events(account.id, load_id)]
        assert types == ["line_item_removed", "line_item_added", "created"]


@pytest.mark.asyncio
class TestRouterSmoke:
    """Drive the write endpoints through FastAPI with a REAL JWT-shaped
    user — the storage-layer tests above can't catch a payload-key
    mistake in the router (the exact gap that once hid a broken
    ``user[\"id\"]`` lookup)."""

    async def test_create_edit_history_roundtrip(self, pg_db):
        db = pg_db
        acct = await db.create_account("Trail Co")
        dispatcher = await db.create_user(9601, acct.id, role=Role.DISPATCHER)

        import infra.platform as _cp
        _old = _cp._db
        _cp._db = db
        try:
            from httpx import ASGITransport, AsyncClient
            from interfaces.api.app import create_api
            from interfaces.api.auth import create_jwt
            app = create_api()
            tok = create_jwt(dispatcher.telegram_id, acct.id, "dispatcher",
                             user_id=dispatcher.id)
            h = {"Authorization": f"Bearer {tok}"}
            async with AsyncClient(transport=ASGITransport(app=app),
                                   base_url="http://t") as c:
                r = await c.post("/api/loads/", headers=h, json={
                    "customer": "Acme", "total_rate": 2100.0})
                assert r.status_code == 200, r.text
                load_id = r.json()["id"]
                # The creator became the default dispatcher (attribution).
                assert r.json()["dispatcher_user_id"] == dispatcher.id

                r = await c.put(f"/api/loads/{load_id}", headers=h,
                                json={"total_rate": 1800.0})
                assert r.status_code == 200, r.text

                r = await c.get(f"/api/loads/{load_id}/history", headers=h)
                assert r.status_code == 200, r.text
                events = r.json()["events"]
                assert [e["event_type"] for e in events] == ["edited", "created"]
                assert events[0]["changes"]["total_rate"] == [2100.0, 1800.0]
                assert events[0]["actor_user_id"] == dispatcher.id
                assert events[0]["actor_name"]      # resolved, never blank
        finally:
            _cp._db = _old
