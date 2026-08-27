"""Storage-layer tests for PTI inspections + templates.

Covers the two mixins introduced in Step 2 of the PTI module:

  * ``DriverInspectionsMixin``  — inspection CRUD, item updates, media,
    status workflow, cron queries.
  * ``PTITemplateMixin``        — fleet-editable templates: read,
    upsert/delete items, reorder, reset-to-default, new-account seed.

The migration 060 backfill is exercised indirectly via the ``pg_db``
fixture — by the time a fresh DB is handed to a test, the Standard DOT
templates already exist for every active account.
"""

from __future__ import annotations

import os
import sys

os.environ.setdefault("ENCRYPTION_KEY", "")

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

from adapters.storage import Role
from features.inspections.templates import (
    STANDARD_DOT_TRAILER_ITEMS,
    STANDARD_DOT_TRUCK_ITEMS,
)


@pytest.fixture
async def db(pg_db):
    yield pg_db


# ── PTI templates ──────────────────────────────────────────────────


class TestPTITemplates:
    async def test_create_account_seeds_default_templates(self, db):
        """Migration 060 + the ``create_account`` hook should both leave
        every active account with a truck + trailer template seeded."""
        acct = await db.create_account("Seeded Fleet")
        truck = await db.get_active_template_with_items(acct.id, "truck")
        trailer = await db.get_active_template_with_items(acct.id, "trailer")
        assert truck is not None
        assert trailer is not None
        assert len(truck["items"]) == len(STANDARD_DOT_TRUCK_ITEMS)
        assert len(trailer["items"]) == len(STANDARD_DOT_TRAILER_ITEMS)
        # Sort order is preserved exactly.
        assert [i["item_key"] for i in truck["items"]] == [
            it["item_key"] for it in STANDARD_DOT_TRUCK_ITEMS
        ]

    async def test_upsert_template_item_adds_new_item(self, db):
        acct = await db.create_account("Custom Fleet")
        tpl = await db.get_active_template(acct.id, "truck")
        assert tpl is not None
        new_id = await db.upsert_template_item(
            int(tpl["id"]),
            item_key="custom_belt_pulley",
            label="Belt pulley inspection",
            category="engine",
            requires_media=True,
            required=True,
            sort_order=100,
        )
        assert new_id > 0
        items = await db.get_template_items(int(tpl["id"]))
        keys = {i["item_key"] for i in items}
        assert "custom_belt_pulley" in keys

    async def test_upsert_template_item_updates_existing(self, db):
        acct = await db.create_account("X")
        tpl = await db.get_active_template(acct.id, "truck")
        await db.upsert_template_item(
            int(tpl["id"]),
            item_key="brakes_service",
            label="Service brake response (revised)",
            category="brakes",
            requires_media=True,  # was False in default; flipping
            required=True,
            sort_order=1,
        )
        items = await db.get_template_items(int(tpl["id"]))
        brake = next(i for i in items if i["item_key"] == "brakes_service")
        assert brake["label"] == "Service brake response (revised)"
        assert brake["requires_media"] == 1

    async def test_delete_template_item(self, db):
        acct = await db.create_account("X")
        tpl = await db.get_active_template(acct.id, "truck")
        ok = await db.delete_template_item(int(tpl["id"]), "horn")
        assert ok is True
        items = await db.get_template_items(int(tpl["id"]))
        assert "horn" not in {i["item_key"] for i in items}

    async def test_reorder_template_items(self, db):
        acct = await db.create_account("X")
        tpl = await db.get_active_template(acct.id, "truck")
        # Reverse the first three items
        new_order = ["tires_walkaround", "brakes_parking", "brakes_service"]
        n = await db.reorder_template_items(int(tpl["id"]), new_order)
        assert n == 3
        items = await db.get_template_items(int(tpl["id"]))
        ordered = sorted(items, key=lambda i: int(i["sort_order"]))
        assert ordered[0]["item_key"] == "tires_walkaround"
        assert ordered[1]["item_key"] == "brakes_parking"
        assert ordered[2]["item_key"] == "brakes_service"

    async def test_reset_template_bumps_version(self, db):
        acct = await db.create_account("X")
        old = await db.get_active_template(acct.id, "truck")
        assert int(old["version"]) == 1
        # Add a custom item, then reset
        await db.upsert_template_item(
            int(old["id"]), item_key="custom_x", label="x", category="cab",
            requires_media=False, required=True, sort_order=99,
        )
        new_id = await db.reset_template_to_default(
            acct.id, "truck", "weekly", STANDARD_DOT_TRUCK_ITEMS,
        )
        new_tpl = await db.get_active_template(acct.id, "truck")
        assert int(new_tpl["id"]) == new_id
        assert int(new_tpl["version"]) == 2
        # Custom item gone; only defaults remain
        items = await db.get_template_items(new_id)
        assert "custom_x" not in {i["item_key"] for i in items}
        assert len(items) == len(STANDARD_DOT_TRUCK_ITEMS)


# ── Inspections lifecycle ──────────────────────────────────────────


class TestInspectionsLifecycle:
    async def _setup_acct(self, db):
        acct = await db.create_account("Fleet")
        driver = await db.create_user(11001, acct.id, role=Role.DRIVER)
        return acct, driver

    async def _spawn_with_items(self, db, acct, driver):
        tpl = await db.get_active_template_with_items(acct.id, "truck")
        ins_id = await db.create_inspection(
            account_id=acct.id, user_id=driver.id,
            vehicle_name="107", inspection_type="weekly",
            template_id=int(tpl["id"]),
            template_version=int(tpl["version"]),
            scheduled_for="2026-06-01T06:00:00",
            due_by="2026-06-08T06:00:00",
        )
        await db.add_inspection_items(ins_id, tpl["items"])
        return ins_id

    async def test_create_inspection_starts_scheduled(self, db):
        acct, driver = await self._setup_acct(db)
        ins_id = await self._spawn_with_items(db, acct, driver)
        ins = await db.get_inspection(ins_id, acct.id)
        assert ins["status"] == "scheduled"
        assert ins["defects_count"] == 0
        assert ins["has_oos_defect"] == 0

    async def test_items_copied_from_template(self, db):
        acct, driver = await self._setup_acct(db)
        ins_id = await self._spawn_with_items(db, acct, driver)
        items = await db.get_inspection_items(ins_id)
        assert len(items) == len(STANDARD_DOT_TRUCK_ITEMS)
        assert all(i["status"] == "pending" for i in items)
        # Photo-required items propagate through
        tw = next(i for i in items if i["item_key"] == "tires_walkaround")
        assert tw["requires_media"] == 1

    async def test_update_item_status(self, db):
        acct, driver = await self._setup_acct(db)
        ins_id = await self._spawn_with_items(db, acct, driver)
        item = await db.get_inspection_item_by_key(ins_id, "brakes_service")
        ok = await db.update_inspection_item(
            int(item["id"]), status="ok", notes="all good",
        )
        assert ok is True
        refreshed = await db.get_inspection_item(int(item["id"]))
        assert refreshed["status"] == "ok"
        assert refreshed["notes"] == "all good"
        assert refreshed["completed_at"] is not None

    async def test_mark_in_progress_idempotent(self, db):
        acct, driver = await self._setup_acct(db)
        ins_id = await self._spawn_with_items(db, acct, driver)
        first = await db.mark_inspection_in_progress(ins_id)
        second = await db.mark_inspection_in_progress(ins_id)
        assert first is True
        # Second call returns False (no rows changed) — idempotent.
        assert second is False
        ins = await db.get_inspection(ins_id, acct.id)
        assert ins["status"] == "in_progress"

    async def test_submit_flips_status(self, db):
        acct, driver = await self._setup_acct(db)
        ins_id = await self._spawn_with_items(db, acct, driver)
        await db.mark_inspection_in_progress(ins_id)
        ok = await db.submit_inspection(
            ins_id, defects_count=2, has_oos_defect=False,
        )
        assert ok is True
        ins = await db.get_inspection(ins_id, acct.id)
        assert ins["status"] == "submitted"
        assert ins["defects_count"] == 2
        assert ins["submitted_at"] is not None

    async def test_review_approved(self, db):
        acct, driver = await self._setup_acct(db)
        owner = await db.create_user(11002, acct.id, role=Role.OWNER)
        ins_id = await self._spawn_with_items(db, acct, driver)
        await db.mark_inspection_in_progress(ins_id)
        await db.submit_inspection(ins_id, defects_count=0, has_oos_defect=False)
        ok = await db.review_inspection(
            ins_id,
            reviewer_id=owner.telegram_id,
            review_status="approved",
            review_notes="LGTM",
        )
        assert ok is True
        ins = await db.get_inspection(ins_id, acct.id)
        assert ins["review_status"] == "approved"
        assert ins["status"] == "reviewed"
        assert ins["reviewed_by"] == owner.telegram_id
        assert ins["review_notes"] == "LGTM"

    async def test_review_revision_required_reopens(self, db):
        acct, driver = await self._setup_acct(db)
        owner = await db.create_user(22002, acct.id, role=Role.OWNER)
        ins_id = await self._spawn_with_items(db, acct, driver)
        await db.mark_inspection_in_progress(ins_id)
        await db.submit_inspection(ins_id, defects_count=0, has_oos_defect=False)
        await db.review_inspection(
            ins_id, reviewer_id=owner.telegram_id,
            review_status="revision_required",
            review_notes="photo blurry, retake",
        )
        ins = await db.get_inspection(ins_id, acct.id)
        # Workflow status reopens; driver can act on it again.
        assert ins["status"] == "revision_required"
        # Open-for-user query picks it back up.
        open_ins = await db.get_open_inspection_for_user(driver.id)
        assert open_ins is not None
        assert int(open_ins["id"]) == ins_id

    async def test_review_rejects_bad_status(self, db):
        acct, driver = await self._setup_acct(db)
        owner = await db.create_user(33003, acct.id, role=Role.OWNER)
        ins_id = await self._spawn_with_items(db, acct, driver)
        await db.mark_inspection_in_progress(ins_id)
        await db.submit_inspection(ins_id, defects_count=0, has_oos_defect=False)
        with pytest.raises(ValueError, match="unknown review_status"):
            await db.review_inspection(
                ins_id, reviewer_id=owner.telegram_id,
                review_status="bogus", review_notes="",
            )


# ── Media attach + delete ──────────────────────────────────────────


class TestInspectionMedia:
    async def test_attach_and_list_media(self, db):
        acct = await db.create_account("Fleet")
        driver = await db.create_user(11005, acct.id, role=Role.DRIVER)
        tpl = await db.get_active_template_with_items(acct.id, "truck")
        ins_id = await db.create_inspection(
            account_id=acct.id, user_id=driver.id,
            vehicle_name="107", template_id=int(tpl["id"]),
            template_version=int(tpl["version"]),
        )
        await db.add_inspection_items(ins_id, tpl["items"])
        item = await db.get_inspection_item_by_key(ins_id, "tires_walkaround")

        media_id = await db.attach_inspection_media(
            ins_id,
            item_id=int(item["id"]),
            media_type="photo",
            file_path="acct_1/inspection_1/tire_front.jpg",
            file_name="tire_front.jpg",
            file_size=120_000,
            content_type="image/jpeg",
            uploaded_by=driver.telegram_id,
        )
        assert media_id > 0
        media = await db.list_inspection_media(ins_id)
        assert len(media) == 1
        assert media[0]["media_type"] == "photo"
        assert media[0]["item_id"] == int(item["id"])

    async def test_delete_media(self, db):
        acct = await db.create_account("Fleet")
        driver = await db.create_user(11006, acct.id, role=Role.DRIVER)
        tpl = await db.get_active_template_with_items(acct.id, "truck")
        ins_id = await db.create_inspection(
            account_id=acct.id, user_id=driver.id,
            vehicle_name="107", template_id=int(tpl["id"]),
            template_version=int(tpl["version"]),
        )
        await db.add_inspection_items(ins_id, tpl["items"])
        mid = await db.attach_inspection_media(
            ins_id, media_type="photo",
            file_path="x", file_name="x.jpg",
            uploaded_by=driver.telegram_id,
        )
        ok = await db.delete_inspection_media(mid)
        assert ok is True
        assert (await db.list_inspection_media(ins_id)) == []

    async def test_mark_media_annotated_stamps_metadata(self, db):
        # ``mark_media_annotated`` records WHEN the driver baked in
        # the overlay and refreshes file_size + content_type since the
        # post-annotation PNG may differ from the original capture.
        acct = await db.create_account("Fleet")
        driver = await db.create_user(11007, acct.id, role=Role.DRIVER)
        tpl = await db.get_active_template_with_items(acct.id, "truck")
        ins_id = await db.create_inspection(
            account_id=acct.id, user_id=driver.id,
            vehicle_name="107", template_id=int(tpl["id"]),
            template_version=int(tpl["version"]),
        )
        await db.add_inspection_items(ins_id, tpl["items"])
        mid = await db.attach_inspection_media(
            ins_id, media_type="photo",
            file_path="x", file_name="x.jpg",
            file_size=100_000, content_type="image/jpeg",
            uploaded_by=driver.telegram_id,
        )
        ok = await db.mark_media_annotated(
            mid, file_size=150_000, content_type="image/png",
        )
        assert ok is True
        row = await db.get_inspection_media(mid)
        assert row["annotated_at"]
        assert int(row["file_size"]) == 150_000
        assert row["content_type"] == "image/png"

    async def test_save_media_ai_review_persists_verdict(self, db):
        # The per-photo AI verdict is stored as status + JSON result so
        # the wizard + dashboard can deserialize it back.
        acct = await db.create_account("Fleet")
        driver = await db.create_user(11008, acct.id, role=Role.DRIVER)
        tpl = await db.get_active_template_with_items(acct.id, "truck")
        ins_id = await db.create_inspection(
            account_id=acct.id, user_id=driver.id,
            vehicle_name="107", template_id=int(tpl["id"]),
            template_version=int(tpl["version"]),
        )
        await db.add_inspection_items(ins_id, tpl["items"])
        mid = await db.attach_inspection_media(
            ins_id, media_type="photo",
            file_path="x", file_name="x.jpg",
            uploaded_by=driver.telegram_id,
        )
        ok = await db.save_media_ai_review(
            mid,
            status="completed",
            result_json='{"verdict": "possible_issue", "summary": "sidewall crack"}',
        )
        assert ok is True
        row = await db.get_inspection_media(mid)
        assert row["ai_review_status"] == "completed"
        assert "sidewall crack" in row["ai_review_result"]
        assert row["ai_reviewed_at"]


# ── Item types, reference photos, location ─────────────────────────


class TestItemTypesAndLocation:
    async def test_item_type_and_reference_snapshot_into_inspection(self, db):
        # A template item's item_type + reference_image_url must be
        # snapshotted onto the inspection item at spawn so an in-flight
        # PTI keeps its capture shape + example photo.
        acct = await db.create_account("Fleet")
        driver = await db.create_user(12010, acct.id, role=Role.DRIVER)
        tpl = await db.get_active_template_with_items(acct.id, "truck")
        ins_id = await db.create_inspection(
            account_id=acct.id, user_id=driver.id,
            vehicle_name="107", template_id=int(tpl["id"]),
            template_version=int(tpl["version"]),
        )
        await db.add_inspection_items(ins_id, [
            {"item_key": "front_wall", "label": "Front wall", "category": "front",
             "item_type": "photo", "reference_image_url": "ref_truck_front_wall.jpg",
             "required": True, "sort_order": 1},
            {"item_key": "annual_report", "label": "Annual report", "category": "docs",
             "item_type": "document", "required": True, "sort_order": 2},
        ])
        items = await db.get_inspection_items(ins_id)
        by_key = {i["item_key"]: i for i in items}
        assert by_key["front_wall"]["item_type"] == "photo"
        assert by_key["front_wall"]["reference_image_url"] == "ref_truck_front_wall.jpg"
        assert by_key["annual_report"]["item_type"] == "document"

    async def test_upsert_template_item_type_and_reference(self, db):
        acct = await db.create_account("Fleet")
        tpl = await db.get_active_template(acct.id, "truck")
        # New document item via upsert
        await db.upsert_template_item(
            int(tpl["id"]), item_key="registration", label="Registration",
            category="docs", item_type="document", sort_order=99,
        )
        # Metadata-only re-upsert must NOT wipe a later-set reference.
        await db.set_template_item_reference_image(
            int(tpl["id"]), "registration", "ref_truck_registration.jpg",
        )
        await db.upsert_template_item(
            int(tpl["id"]), item_key="registration", label="Registration (v2)",
            category="docs", item_type="document", sort_order=99,
        )
        items = await db.get_template_items(int(tpl["id"]))
        reg = next(i for i in items if i["item_key"] == "registration")
        assert reg["item_type"] == "document"
        assert reg["label"] == "Registration (v2)"
        assert reg["reference_image_url"] == "ref_truck_registration.jpg"

    async def test_save_inspection_location(self, db):
        acct = await db.create_account("Fleet")
        driver = await db.create_user(12011, acct.id, role=Role.DRIVER)
        tpl = await db.get_active_template(acct.id, "truck")
        ins_id = await db.create_inspection(
            account_id=acct.id, user_id=driver.id,
            vehicle_name="107", template_id=int(tpl["id"]),
            template_version=int(tpl["version"]),
        )
        ok = await db.save_inspection_location(
            ins_id, lat=40.7128, lon=-74.0060, source="vehicle",
        )
        assert ok is True
        row = await db.get_inspection(ins_id, acct.id)
        assert abs(float(row["location_lat"]) - 40.7128) < 1e-6
        assert row["location_source"] == "vehicle"
        assert row["location_at"]


# ── Cron queries ───────────────────────────────────────────────────


class TestCronQueries:
    async def test_list_due_soon(self, db):
        from datetime import datetime, timedelta, timezone
        acct = await db.create_account("Fleet")
        driver = await db.create_user(11007, acct.id, role=Role.DRIVER)
        tpl = await db.get_active_template(acct.id, "truck")
        # Due in 12h — should be in the 24h window.
        soon = (datetime.now(timezone.utc) + timedelta(hours=12)).isoformat(timespec="seconds")
        far = (datetime.now(timezone.utc) + timedelta(hours=72)).isoformat(timespec="seconds")
        await db.create_inspection(
            account_id=acct.id, user_id=driver.id, vehicle_name="107",
            template_id=int(tpl["id"]),
            template_version=int(tpl["version"]),
            due_by=soon,
        )
        await db.create_inspection(
            account_id=acct.id, user_id=driver.id, vehicle_name="213",
            template_id=int(tpl["id"]),
            template_version=int(tpl["version"]),
            due_by=far,
        )
        due_soon = await db.list_due_soon(acct.id, hours_ahead=24)
        names = {r["vehicle_name"] for r in due_soon}
        assert "107" in names
        assert "213" not in names

    async def test_list_overdue(self, db):
        from datetime import datetime, timedelta, timezone
        acct = await db.create_account("Fleet")
        driver = await db.create_user(11008, acct.id, role=Role.DRIVER)
        tpl = await db.get_active_template(acct.id, "truck")
        past = (datetime.now(timezone.utc) - timedelta(days=1)).isoformat(timespec="seconds")
        future = (datetime.now(timezone.utc) + timedelta(days=1)).isoformat(timespec="seconds")
        await db.create_inspection(
            account_id=acct.id, user_id=driver.id, vehicle_name="107",
            template_id=int(tpl["id"]),
            template_version=int(tpl["version"]),
            due_by=past,
        )
        await db.create_inspection(
            account_id=acct.id, user_id=driver.id, vehicle_name="213",
            template_id=int(tpl["id"]),
            template_version=int(tpl["version"]),
            due_by=future,
        )
        overdue = await db.list_overdue(acct.id)
        names = {r["vehicle_name"] for r in overdue}
        assert "107" in names
        assert "213" not in names

    async def test_last_submitted_for_user(self, db):
        acct = await db.create_account("Fleet")
        driver = await db.create_user(11009, acct.id, role=Role.DRIVER)
        tpl = await db.get_active_template_with_items(acct.id, "truck")
        # Inspection 1 — submitted earlier
        ins1 = await db.create_inspection(
            account_id=acct.id, user_id=driver.id, vehicle_name="107",
            template_id=int(tpl["id"]), template_version=int(tpl["version"]),
        )
        await db.add_inspection_items(ins1, tpl["items"])
        await db.mark_inspection_in_progress(ins1)
        await db.submit_inspection(ins1, defects_count=0, has_oos_defect=False)
        # Inspection 2 — newer
        ins2 = await db.create_inspection(
            account_id=acct.id, user_id=driver.id, vehicle_name="107",
            template_id=int(tpl["id"]), template_version=int(tpl["version"]),
        )
        await db.add_inspection_items(ins2, tpl["items"])
        await db.mark_inspection_in_progress(ins2)
        await db.submit_inspection(ins2, defects_count=1, has_oos_defect=False)

        latest = await db.get_last_submitted_inspection_for_user(driver.id)
        assert latest is not None
        assert int(latest["id"]) == ins2


# ── Dashboard list pagination ──────────────────────────────────────


class TestAccountListPagination:
    async def test_filter_by_review_status(self, db):
        acct = await db.create_account("Fleet")
        driver = await db.create_user(11010, acct.id, role=Role.DRIVER)
        owner = await db.create_user(11011, acct.id, role=Role.OWNER)
        tpl = await db.get_active_template_with_items(acct.id, "truck")
        # Two submitted, one approved
        for i in range(2):
            ins_id = await db.create_inspection(
                account_id=acct.id, user_id=driver.id, vehicle_name=f"10{i}",
                template_id=int(tpl["id"]),
                template_version=int(tpl["version"]),
            )
            await db.add_inspection_items(ins_id, tpl["items"])
            await db.mark_inspection_in_progress(ins_id)
            await db.submit_inspection(ins_id, defects_count=0, has_oos_defect=False)
            if i == 0:
                await db.review_inspection(
                    ins_id, reviewer_id=owner.telegram_id,
                    review_status="approved", review_notes="",
                )

        approved = await db.list_inspections_for_account(
            acct.id, review_status="approved",
        )
        assert approved["total"] == 1

        submitted = await db.list_inspections_for_account(
            acct.id, status="submitted",
        )
        assert submitted["total"] == 1
