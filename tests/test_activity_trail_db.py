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
    async def test_vehicle_registry_round_trip(self, seeded_db):
        db, acct, owner = seeded_db
        vid = await db.add_vehicle(
            acct.id, unit_number="777", vehicle_type="truck",
            company_code="TFC", make="Kenworth",
            actor_user_id=owner.id,
        )
        await db.update_vehicle(
            acct.id, vid, actor_user_id=owner.id, make="Peterbilt",
        )
        await db.deactivate_vehicle(acct.id, vid, actor_user_id=owner.id)
        events = await db.list_activity_events(
            acct.id, entity_type="vehicle", entity_id=str(vid),
        )
        assert [e["action"] for e in events] == ["deactivate", "update", "create"]
        assert events[1]["changes"]["make"] == {"from": "Kenworth", "to": "Peterbilt"}
        assert events[0]["changes"]["status"]["to"] == "inactive"
        # machine sync writes (no actor) stay silent
        await db.update_vehicle(acct.id, vid, notes="sync fill")
        assert len(await db.list_activity_events(
            acct.id, entity_type="vehicle", entity_id=str(vid))) == 3

    @pytest.mark.asyncio
    async def test_vendor_merge_records_both_sides_with_loser_body(self, seeded_db):
        db, acct, owner = seeded_db
        a = await db.create_vendor(
            acct.id, "Bob's Truck Repare", phone="555-1",
            actor_user_id=owner.id,
        )
        b = await db.create_vendor(
            acct.id, "Bobs Truck Repair", phone="555-2",
            actor_user_id=owner.id,
        )
        ok = await db.merge_vendors(
            acct.id, a["id"], b["id"], actor_user_id=owner.id,
        )
        assert ok
        loser_ev = await db.list_activity_events(
            acct.id, entity_type="vendor", entity_id=str(a["id"]),
            action="merge_away",
        )
        assert len(loser_ev) == 1
        # the loser's body is the recovery record
        assert loser_ev[0]["changes"]["name"]["from"] == "Bob's Truck Repare"
        assert loser_ev[0]["context"]["into"] == b["id"]
        winner_ev = await db.list_activity_events(
            acct.id, entity_type="vendor", entity_id=str(b["id"]),
            action="merge_in",
        )
        assert winner_ev and winner_ev[0]["group_id"] == loser_ev[0]["group_id"]

    @pytest.mark.asyncio
    async def test_part_create_then_edit_records_values(self, seeded_db):
        db, acct, owner = seeded_db
        part, created = await db.create_catalog_part(
            acct.id, "Water Pump", part_number="WP-1",
            actor_user_id=owner.id,
        )
        assert created
        await db.update_catalog_part(
            part["id"], acct.id, actor_user_id=owner.id,
            part_number="WP-2",
        )
        events = await db.list_activity_events(
            acct.id, entity_type="part", entity_id=str(part["id"]),
        )
        assert [e["action"] for e in events] == ["update", "create"]
        assert events[0]["changes"]["part_number"] == {"from": "WP-1", "to": "WP-2"}
        # idempotent re-create of the same name records nothing new
        _, again = await db.create_catalog_part(
            acct.id, "Water Pump", actor_user_id=owner.id,
        )
        assert not again
        assert len(await db.list_activity_events(
            acct.id, entity_type="part", entity_id=str(part["id"]))) == 2

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
        feed = await account_activity(
            db, acct.id, limit=50,
            viewer_can_see={"maintenance_task": True},
        )
        groups = [e for e in feed if e.get("is_group")]
        assert len(groups) == 1
        assert groups[0]["count"] == 2
        # per-entity lens still sees its own delete despite the collapse
        one = await db.list_activity_events(
            acct.id, entity_type="maintenance_task", entity_id=str(ids[0]),
        )
        assert any(e["action"] == "delete" for e in one)


class TestLegacyImport:
    """Migration 178 — the frozen log's human rows join the trail."""

    @pytest.mark.asyncio
    async def test_human_rows_import_with_actor_mapping(self, seeded_db):
        from adapters.storage.migrations import (
            migrate_activity_trail_legacy_import,
        )
        db, acct, owner = seeded_db
        # One mapped human row, one orphaned human row, one machine row.
        await db.add_audit_log(
            acct.id, owner.telegram_id, "role_change",
            target_type="user", target_id="42", details="Changed role to hr",
        )
        await db.add_audit_log(
            acct.id, 999999999, "company_add",
            target_type="company", target_id="7", details="Code: ZZZ",
        )
        await db.add_audit_log(
            acct.id, None, "alert_auto_resolved",
            target_type="alert", target_id="1", details="machine noise",
        )
        await migrate_activity_trail_legacy_import(db._db)

        imported = await db.list_activity_events(acct.id, action="role_change")
        assert len(imported) == 1
        ev = imported[0]
        assert ev["actor_user_id"] == owner.id          # telegram → platform
        assert ev["context"]["source"] == "audit_log"
        assert ev["note"] == "Changed role to hr"

        orphan = await db.list_activity_events(acct.id, action="company_add")
        assert orphan and orphan[0]["actor_user_id"] is None
        assert "system" in orphan[0]["context"]         # people-only contract

        # machine churn stays behind
        assert await db.list_activity_events(
            acct.id, action="alert_auto_resolved") == []


class TestRestoreFromTrail:
    """Owner requirement, pinned: restore APPENDS — it never erases.

    The record's history after a restore must contain the whole chain
    (create → delete → restore) on the same id; the delete event and
    everything before it stay untouched forever.
    """

    @pytest.mark.asyncio
    async def test_delete_then_restore_keeps_the_whole_chain(self, seeded_db):
        from capabilities.activity_trail.registry import (
            ensure_declarations_loaded,
        )
        from capabilities.activity_trail.restore import (
            RestoreConflict, restore_from_event,
        )
        ensure_declarations_loaded()
        db, acct, owner = seeded_db
        tid = await db.add_maintenance_task(
            acct.id, "TFC", "224", "oil", "Full PM",
            due_miles=236772, priority="medium", actor_user_id=owner.id,
        )
        await db.delete_maintenance_task(
            tid, account_id=acct.id, actor_user_id=owner.id,
        )
        assert await db.get_maintenance_task(tid, account_id=acct.id) is None

        events = await db.list_activity_events(
            acct.id, entity_type="maintenance_task", entity_id=str(tid),
        )
        delete_ev = next(e for e in events if e["action"] == "delete")

        restored_id = await restore_from_event(
            db, acct.id, delete_ev, actor_user_id=owner.id,
        )
        assert restored_id == tid                       # same id — one story

        back = await db.get_maintenance_task(tid, account_id=acct.id)
        assert back is not None
        assert float(back["due_miles"]) == 236772.0
        assert back["vehicle_name"] == "224"

        # THE requirement: append-only. All three chapters present.
        chain = await db.list_activity_events(
            acct.id, entity_type="maintenance_task", entity_id=str(tid),
        )
        actions = [e["action"] for e in chain]
        assert actions == ["restore", "delete", "create"]   # newest first
        restore_ev = chain[0]
        assert restore_ev["context"]["restored_from_event"] == delete_ev["id"]
        assert restore_ev["actor_user_id"] == owner.id
        # and the delete event still carries its full recovery body
        assert chain[1]["changes"]["due_miles"]["from"] is not None

        # restoring twice cannot silently overwrite
        with pytest.raises(RestoreConflict):
            await restore_from_event(
                db, acct.id, delete_ev, actor_user_id=owner.id,
            )

    @pytest.mark.asyncio
    async def test_undeclared_entities_fail_closed(self, seeded_db):
        from capabilities.activity_trail.registry import (
            ensure_declarations_loaded,
        )
        from capabilities.activity_trail.restore import restore_from_event
        ensure_declarations_loaded()
        db, acct, owner = seeded_db
        fake = {"id": 1, "entity_type": "setting", "entity_id": "9",
                "action": "delete", "changes": {"x": {"from": 1, "to": None}},
                "context": {}}
        with pytest.raises(ValueError):
            await restore_from_event(db, acct.id, fake, actor_user_id=owner.id)


class TestHistoryCompanyWall:
    """Security review 2026-08-02: the generic history lens served
    company-scoped entities without the company wall its siblings
    enforce, so a company-restricted manager could read another
    company's work-order and task history by walking ids."""

    @pytest.mark.asyncio
    async def test_history_scope_resolves_company_from_the_live_row(self, seeded_db):
        from capabilities.activity_trail import router as trail_router
        from capabilities.activity_trail.registry import (
            ensure_declarations_loaded, entity_descriptor,
        )
        ensure_declarations_loaded()
        db, acct, owner = seeded_db
        d = entity_descriptor("maintenance_task")
        assert d.company_scoped, "maintenance must declare the wall"

        # A task belonging to company COMPB.
        tid = await db.add_maintenance_task(
            acct.id, "COMPB", "224", "oil", "Full PM",
            due_miles=1000, actor_user_id=owner.id,
        )
        events = await db.list_activity_events(
            acct.id, entity_type="maintenance_task", entity_id=str(tid),
        )
        # The create event carries NO company_code — this is exactly why
        # the read path must consult the row, not the event body.
        assert "company_code" not in (events[0]["changes"] or {})

        async def as_user(assigned):
            async def _codes(_u):
                return assigned
            orig = trail_router.get_user_company_codes
            trail_router.get_user_company_codes = _codes
            try:
                return await trail_router.history_in_company_scope(
                    d, str(tid), events, {"role": "fleet"}, db, acct.id,
                )
            finally:
                trail_router.get_user_company_codes = orig

        assert await as_user(["COMPB"]) is True     # own company
        assert await as_user(["COMPA"]) is False    # another company → 404
        assert await as_user([]) is True            # unrestricted / owner

    @pytest.mark.asyncio
    async def test_history_scope_of_a_deleted_record_uses_its_delete_body(self, seeded_db):
        from capabilities.activity_trail import router as trail_router
        from capabilities.activity_trail.registry import (
            ensure_declarations_loaded, entity_descriptor,
        )
        ensure_declarations_loaded()
        db, acct, owner = seeded_db
        d = entity_descriptor("maintenance_task")
        tid = await db.add_maintenance_task(
            acct.id, "COMPB", "225", "oil", "Full PM",
            due_miles=1000, actor_user_id=owner.id,
        )
        await db.delete_maintenance_task(
            tid, account_id=acct.id, actor_user_id=owner.id,
        )
        events = await db.list_activity_events(
            acct.id, entity_type="maintenance_task", entity_id=str(tid),
        )

        async def as_user(assigned):
            async def _codes(_u):
                return assigned
            orig = trail_router.get_user_company_codes
            trail_router.get_user_company_codes = _codes
            try:
                return await trail_router.history_in_company_scope(
                    d, str(tid), events, {"role": "fleet"}, db, acct.id,
                )
            finally:
                trail_router.get_user_company_codes = orig

        # The row is gone; the wall still holds via the delete body.
        assert await as_user(["COMPA"]) is False
        assert await as_user(["COMPB"]) is True
