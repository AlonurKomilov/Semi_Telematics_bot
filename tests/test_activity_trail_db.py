"""Activity Trail — the maintenance adopter, round-tripped on a real DB.

Proves the Phase-2 contract end to end: human mutations leave events
with VALUES, machine mutations leave nothing, bulk deletes record every
member with its full body (the 2026-07-30 incident's fix), and the
facade serves it all back in one shape.
"""

import pytest
import pytest_asyncio

from adapters.storage.models import Role


@pytest_asyncio.fixture
async def seeded_db(db):
    account = await db.create_account("Trail Fleet Co")
    await db.add_company(
        account_id=account.id, code="TFC",
        samsara_api_key="samsara_api_test_key_123",
        display_name="Trail Fleet",
    )
    owner = await db.create_user(
        telegram_id=222333, account_id=account.id, role=Role.OWNER,
    )
    return db, account, owner


class TestMaintenanceAdoption:

    @pytest.mark.asyncio
    async def test_create_update_records_values_not_just_names(self, seeded_db):
        db, acct, owner = seeded_db
        tid = await db.add_maintenance_task(
            acct.id, "TFC", "224", "oil", "Full PM",
            due_miles=236772, priority="medium",
            actor_user_id=owner.id,
        )
        await db.update_maintenance_task(
            tid, account_id=acct.id, actor_user_id=owner.id,
            due_miles=250000.0,
        )
        events = await db.list_activity_events(
            acct.id, entity_type="maintenance_task", entity_id=str(tid),
        )
        actions = [e["action"] for e in events]
        assert actions == ["update", "create"]          # newest first
        upd = events[0]
        # the incident lesson, pinned: VALUES, not field names
        assert upd["changes"]["due_miles"] == {"from": 236772.0, "to": 250000.0}
        assert upd["actor_user_id"] == owner.id
        create = events[1]
        assert create["changes"]["vehicle_name"]["to"] == "224"

    @pytest.mark.asyncio
    async def test_machine_writes_leave_no_events(self, seeded_db):
        db, acct, owner = seeded_db
        tid = await db.add_maintenance_task(
            acct.id, "TFC", "225", "oil", "Full PM", due_miles=100000,
        )                                               # no actor: scheduler-style
        await db.update_maintenance_status(tid, "overdue", account_id=acct.id)
        events = await db.list_activity_events(
            acct.id, entity_type="maintenance_task", entity_id=str(tid),
        )
        assert events == []                             # people only

    @pytest.mark.asyncio
    async def test_bulk_delete_records_every_member_with_full_body(self, seeded_db):
        from capabilities.activity_trail import new_group_id
        db, acct, owner = seeded_db
        ids = []
        for unit in ("101", "102", "103"):
            ids.append(await db.add_maintenance_task(
                acct.id, "TFC", unit, "oil", "Full PM",
                due_miles=300000 + int(unit), actor_user_id=owner.id,
            ))
        group = new_group_id()
        deleted = await db.delete_maintenance_tasks_bulk(
            acct.id, ids, actor_user_id=owner.id, trail_group_id=group,
        )
        assert deleted == 3
        members = await db.list_activity_events(acct.id, group_id=group)
        assert len(members) == 3                        # NEVER truncated
        for m in members:
            assert m["action"] == "delete"
            body = m["changes"]
            # the recovery record: restore values with to:null
            assert body["due_miles"]["to"] is None
            assert body["due_miles"]["from"] is not None
            assert body["vehicle_name"]["from"] in ("101", "102", "103")

    @pytest.mark.asyncio
    async def test_status_flip_and_completion_are_evented(self, seeded_db):
        db, acct, owner = seeded_db
        tid = await db.add_maintenance_task(
            acct.id, "TFC", "224", "oil", "Full PM", due_miles=1,
            actor_user_id=owner.id,
        )
        await db.update_maintenance_status(
            tid, "completed", account_id=acct.id, actor_user_id=owner.id,
        )
        events = await db.list_activity_events(
            acct.id, entity_type="maintenance_task", entity_id=str(tid),
        )
        assert events[0]["action"] == "complete"
        assert events[0]["changes"]["status"]["to"] == "completed"

    @pytest.mark.asyncio
    async def test_facade_merges_and_collapses(self, seeded_db):
        from capabilities.activity_trail import new_group_id
        from capabilities.activity_trail.facade import account_activity
        db, acct, owner = seeded_db
        ids = [await db.add_maintenance_task(
            acct.id, "TFC", str(n), "oil", "Full PM", due_miles=5,
            actor_user_id=owner.id, trail_group_id=None,
        ) for n in (1, 2)]
        group = new_group_id()
        await db.delete_maintenance_tasks_bulk(
            acct.id, ids, actor_user_id=owner.id, trail_group_id=group,
        )
        feed = await account_activity(db, acct.id, limit=50)
        groups = [e for e in feed if e.get("is_group")]
        assert len(groups) == 1
        assert groups[0]["count"] == 2
        # per-entity lens still sees its own delete despite the collapse
        one = await db.list_activity_events(
            acct.id, entity_type="maintenance_task", entity_id=str(ids[0]),
        )
        assert any(e["action"] == "delete" for e in one)
