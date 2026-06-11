"""Service-layer tests for PTI workflow.

Covers ``capabilities/pti/service.py`` — the business-logic layer
between routes and storage:

  * ``spawn_weekly_inspections``  — cadence check, skip-when-open,
    template item copy
  * ``submit_inspection``         — required-items check, required-
    media check, status transition, defects computation
  * ``review_inspection``         — fleet review path, status guards
  * ``update_item_status``        — first-touch flip to in_progress
  * Error semantics               — every PTIError subclass raised in
    the documented condition
"""

from __future__ import annotations

import os
import sys
from datetime import datetime, timedelta, timezone

os.environ.setdefault("ENCRYPTION_KEY", "")

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

from adapters.storage import Role
from features.pti import service as pti_service
from features.pti.service import _items_missing_media, _summarize_items
from features.pti.templates import STANDARD_DOT_TRUCK_ITEMS


# ── Item-type gate helpers (pure, no DB) ───────────────────────────


class TestItemTypeGates:
    def test_check_item_pending_blocks(self):
        items = [{"id": 1, "item_key": "brakes", "item_type": "check",
                  "required": True, "status": "pending"}]
        _, _, pending = _summarize_items(items)
        assert pending == ["brakes"]

    def test_photo_and_document_items_skip_status_gate(self):
        # photo/document items have no OK/Minor status — they must NOT
        # land in the pending-status list even while 'pending'.
        items = [
            {"id": 1, "item_key": "front", "item_type": "photo",
             "required": True, "status": "pending"},
            {"id": 2, "item_key": "annual", "item_type": "document",
             "required": True, "status": "pending"},
        ]
        defects, has_oos, pending = _summarize_items(items)
        assert pending == []
        assert defects == 0 and has_oos is False

    def test_photo_item_requires_a_photo(self):
        items = [{"id": 1, "item_key": "front", "item_type": "photo",
                  "required": True, "status": "pending"}]
        # No media → flagged
        assert _items_missing_media(items, []) == ["front"]
        # A photo on the item → satisfied
        media = [{"item_id": 1, "media_type": "photo"}]
        assert _items_missing_media(items, media) == []

    def test_document_item_needs_a_document_not_a_photo(self):
        items = [{"id": 1, "item_key": "annual", "item_type": "document",
                  "required": True, "status": "pending"}]
        # A stray photo does NOT satisfy a document slot.
        assert _items_missing_media(items, [{"item_id": 1, "media_type": "photo"}]) == ["annual"]
        # A document does.
        assert _items_missing_media(items, [{"item_id": 1, "media_type": "document"}]) == []

    def test_check_item_requires_media_flag_still_enforced(self):
        items = [{"id": 1, "item_key": "tires", "item_type": "check",
                  "required": True, "status": "ok", "requires_media": True}]
        assert _items_missing_media(items, []) == ["tires"]
        assert _items_missing_media(items, [{"item_id": 1, "media_type": "photo"}]) == []

    def test_coords_validation(self):
        assert pti_service._coords_valid(40.7, -74.0) is True
        assert pti_service._coords_valid(0, 0) is False        # null island
        assert pti_service._coords_valid(91, 0) is False       # out of range
        assert pti_service._coords_valid("x", "y") is False    # non-numeric


@pytest.fixture
async def db(pg_db):
    yield pg_db


async def _make_account_with_driver(db, *, telegram_id=11001, truck_num="107"):
    acct = await db.create_account("Fleet")
    driver = await db.create_user(telegram_id, acct.id, role=Role.DRIVER)
    if truck_num:
        await db.assign_vehicle(
            user_id=driver.id, account_id=acct.id,
            truck_num=truck_num, assigned_by=0, is_primary=True,
        )
    return acct, driver


async def _spawn_one(db, acct, driver):
    """Helper: spawn one inspection via the service (the cadence path)."""
    spawned = await pti_service.spawn_weekly_inspections(acct.id, db)
    assert len(spawned) == 1
    return spawned[0]


# Minimal valid PNG data URL (1x1 white).  Long enough to clear the
# 200-char minimum the signature validator enforces — we pad with
# spaces to push the base64 part past the length floor without changing
# the rendered image.  Tests that exercise the submit path must attach
# a signature first, otherwise ``MissingSignature`` fires.
_TEST_SIGNATURE = "data:image/png;base64,iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mNgAAIAAAUAAen63NgAAAAASUVORK5CYII=" + ("A" * 200)


async def _sign(db, ins_id, *, role="driver"):
    """Attach the canned test signature so a subsequent submit passes."""
    await db.set_inspection_signature(
        ins_id, role=role, signature_data=_TEST_SIGNATURE,
    )


# ── Spawning ──────────────────────────────────────────────────────


class TestSpawn:
    async def test_spawns_for_assigned_driver(self, db):
        acct, driver = await _make_account_with_driver(db)
        spawned = await pti_service.spawn_weekly_inspections(acct.id, db)
        assert len(spawned) == 1
        # Items copied from template
        items = await db.get_inspection_items(spawned[0])
        assert len(items) == len(STANDARD_DOT_TRUCK_ITEMS)
        # Vehicle name pulled from the driver's assignment
        ins = await db.get_inspection(spawned[0], acct.id)
        assert ins["vehicle_name"] == "107"
        assert ins["due_by"] is not None

    async def test_skips_driver_without_truck(self, db):
        acct, _ = await _make_account_with_driver(db, truck_num="")
        spawned = await pti_service.spawn_weekly_inspections(acct.id, db)
        assert spawned == []

    async def test_skips_when_open_inspection_exists(self, db):
        acct, driver = await _make_account_with_driver(db)
        await _spawn_one(db, acct, driver)
        # Second tick — no new spawn since one is already scheduled.
        spawned = await pti_service.spawn_weekly_inspections(acct.id, db)
        assert spawned == []

    async def test_skips_when_last_submission_within_cadence(self, db):
        acct, driver = await _make_account_with_driver(db)
        ins_id = await _spawn_one(db, acct, driver)
        # Submit + mark a recent submission timestamp.  Photo-required
        # items get ``na`` so the submit gate passes without attaching
        # real media — this test cares about cadence, not media.
        items = await db.get_inspection_items(ins_id)
        for it in items:
            if it["requires_media"]:
                await db.update_inspection_item(int(it["id"]), status="na")
            else:
                await db.update_inspection_item(int(it["id"]), status="ok")
        await db.mark_inspection_in_progress(ins_id)
        await _sign(db, ins_id)
        await pti_service.submit_inspection(ins_id, driver.id, db)

        # Tick again immediately — last_submitted was just now, well
        # inside the 7-day cadence — no new spawn.
        spawned = await pti_service.spawn_weekly_inspections(acct.id, db)
        assert spawned == []

    async def test_spawns_when_last_submission_older_than_cadence(self, db):
        acct, driver = await _make_account_with_driver(db)
        ins_id = await _spawn_one(db, acct, driver)
        # Walk through + submit (photo-required → ``na`` so the media
        # gate passes without uploads).
        for it in await db.get_inspection_items(ins_id):
            if it["requires_media"]:
                await db.update_inspection_item(int(it["id"]), status="na")
            else:
                await db.update_inspection_item(int(it["id"]), status="ok")
        await db.mark_inspection_in_progress(ins_id)
        await _sign(db, ins_id)
        await pti_service.submit_inspection(ins_id, driver.id, db)

        # Backdate submitted_at to 10 days ago — outside the cadence
        # window — next tick should spawn a fresh one.
        old = (
            datetime.now(timezone.utc) - timedelta(days=10)
        ).isoformat(timespec="seconds")
        await db._db.execute(
            "UPDATE driver_inspections SET submitted_at = ? WHERE id = ?",
            (old, ins_id),
        )
        await db._db.commit()

        spawned = await pti_service.spawn_weekly_inspections(acct.id, db)
        assert len(spawned) == 1
        assert spawned[0] != ins_id


# ── Submit ────────────────────────────────────────────────────────


class TestSubmit:
    async def test_submit_rejects_pending_required_items(self, db):
        acct, driver = await _make_account_with_driver(db)
        ins_id = await _spawn_one(db, acct, driver)
        await db.mark_inspection_in_progress(ins_id)
        # Only mark half the items — half stay pending.
        items = await db.get_inspection_items(ins_id)
        for it in items[:9]:
            await db.update_inspection_item(int(it["id"]), status="ok")
        with pytest.raises(pti_service.IncompleteInspection):
            await pti_service.submit_inspection(ins_id, driver.id, db)

    async def test_submit_rejects_missing_media(self, db):
        acct, driver = await _make_account_with_driver(db)
        ins_id = await _spawn_one(db, acct, driver)
        await db.mark_inspection_in_progress(ins_id)
        # Mark all items OK but leave the photo-required ones without
        # any media row attached.
        items = await db.get_inspection_items(ins_id)
        for it in items:
            await db.update_inspection_item(int(it["id"]), status="ok")
        with pytest.raises(pti_service.MissingMedia) as exc:
            await pti_service.submit_inspection(ins_id, driver.id, db)
        assert "tires_walkaround" in str(exc.value)

    async def test_submit_accepts_na_for_media_required(self, db):
        acct, driver = await _make_account_with_driver(db)
        ins_id = await _spawn_one(db, acct, driver)
        await db.mark_inspection_in_progress(ins_id)
        items = await db.get_inspection_items(ins_id)
        for it in items:
            if it["requires_media"]:
                # "na" exempts the photo — item doesn't apply.
                await db.update_inspection_item(int(it["id"]), status="na")
            else:
                await db.update_inspection_item(int(it["id"]), status="ok")
        await _sign(db, ins_id)
        result = await pti_service.submit_inspection(ins_id, driver.id, db)
        assert result["status"] == "submitted"
        assert result["defects_count"] == 0

    async def test_submit_computes_defect_count(self, db):
        acct, driver = await _make_account_with_driver(db)
        ins_id = await _spawn_one(db, acct, driver)
        await db.mark_inspection_in_progress(ins_id)
        items = await db.get_inspection_items(ins_id)
        # Mark two as minor defects + one OOS, mark the photo-required
        # ones na so the media gate passes.
        defect_keys = []
        oos_key = None
        for it in items:
            if it["requires_media"]:
                await db.update_inspection_item(int(it["id"]), status="na")
                continue
            if len(defect_keys) < 2:
                await db.update_inspection_item(int(it["id"]), status="minor")
                defect_keys.append(it["item_key"])
            elif oos_key is None:
                await db.update_inspection_item(int(it["id"]), status="oos")
                oos_key = it["item_key"]
            else:
                await db.update_inspection_item(int(it["id"]), status="ok")
        await _sign(db, ins_id)
        result = await pti_service.submit_inspection(ins_id, driver.id, db)
        assert result["defects_count"] == 3   # 2 minor + 1 oos
        assert result["has_oos_defect"] is True

    async def test_submit_rejects_wrong_user(self, db):
        acct, driver = await _make_account_with_driver(db)
        other = await db.create_user(99999, acct.id, role=Role.DRIVER)
        ins_id = await _spawn_one(db, acct, driver)
        with pytest.raises(pti_service.NotFound):
            # Other driver can't see / submit someone else's inspection.
            await pti_service.submit_inspection(ins_id, other.id, db)

    async def test_submit_rejects_wrong_status(self, db):
        acct, driver = await _make_account_with_driver(db)
        ins_id = await _spawn_one(db, acct, driver)
        # Submit a freshly-scheduled inspection without going through
        # in_progress → service rejects.
        with pytest.raises(pti_service.WrongStatus):
            await pti_service.submit_inspection(ins_id, driver.id, db)

    async def test_submit_rejects_missing_signature(self, db):
        # Items + media all clear, but no signature attached → submit
        # must refuse with the MissingSignature error code so the Mini
        # App can bounce the driver back to the signature pad.
        acct, driver = await _make_account_with_driver(db)
        ins_id = await _spawn_one(db, acct, driver)
        await db.mark_inspection_in_progress(ins_id)
        for it in await db.get_inspection_items(ins_id):
            if it["requires_media"]:
                await db.update_inspection_item(int(it["id"]), status="na")
            else:
                await db.update_inspection_item(int(it["id"]), status="ok")
        with pytest.raises(pti_service.MissingSignature):
            await pti_service.submit_inspection(ins_id, driver.id, db)


# ── Signatures ────────────────────────────────────────────────────


class TestSignatures:
    async def test_driver_can_sign_in_progress(self, db):
        acct, driver = await _make_account_with_driver(db)
        ins_id = await _spawn_one(db, acct, driver)
        await db.mark_inspection_in_progress(ins_id)
        refreshed = await pti_service.set_signature(
            ins_id,
            role="driver",
            signature_data=_TEST_SIGNATURE,
            user_id=driver.id,
            account_id=acct.id,
            tenant_db=db,
        )
        assert refreshed["driver_signature"] == _TEST_SIGNATURE
        assert refreshed["driver_signed_at"]

    async def test_driver_signature_blocks_cross_user(self, db):
        # A different driver in the same account can't ghost-sign for
        # the inspection's assigned driver.
        acct, driver = await _make_account_with_driver(db)
        other = await db.create_user(91234, acct.id, role=Role.DRIVER)
        ins_id = await _spawn_one(db, acct, driver)
        await db.mark_inspection_in_progress(ins_id)
        with pytest.raises(pti_service.NotFound):
            await pti_service.set_signature(
                ins_id,
                role="driver",
                signature_data=_TEST_SIGNATURE,
                user_id=other.id,
                account_id=acct.id,
                tenant_db=db,
            )

    async def test_reviewer_can_only_sign_submitted(self, db):
        # Reviewer co-sign requires the inspection to be in the
        # 'submitted' state — signing earlier is meaningless.
        acct, driver = await _make_account_with_driver(db)
        owner = await db.create_user(75001, acct.id, role=Role.OWNER)
        ins_id = await _spawn_one(db, acct, driver)
        with pytest.raises(pti_service.WrongStatus):
            await pti_service.set_signature(
                ins_id,
                role="reviewer",
                signature_data=_TEST_SIGNATURE,
                user_id=owner.id,
                account_id=acct.id,
                tenant_db=db,
            )

    async def test_signature_rejects_blank_payload(self, db):
        acct, driver = await _make_account_with_driver(db)
        ins_id = await _spawn_one(db, acct, driver)
        await db.mark_inspection_in_progress(ins_id)
        with pytest.raises(pti_service.PTIError):
            await pti_service.set_signature(
                ins_id,
                role="driver",
                signature_data="",
                user_id=driver.id,
                account_id=acct.id,
                tenant_db=db,
            )


# ── Annotated photo payload validation ────────────────────────────


# Minimal valid 1x1 PNG.  Used to verify the decoder accepts a
# real-shaped payload, even if the image is tiny.
_TINY_PNG_DATA_URL = (
    "data:image/png;base64,"
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mNgAAIAAAUAAen63NgAAAAASUVORK5CYII="
)


class TestAnnotatePayload:
    def test_decoder_accepts_valid_png(self):
        raw = pti_service.decode_annotated_png(_TINY_PNG_DATA_URL)
        assert raw.startswith(b"\x89PNG\r\n\x1a\n")

    def test_decoder_rejects_non_png_prefix(self):
        with pytest.raises(pti_service.PTIError):
            pti_service.decode_annotated_png(
                "data:image/jpeg;base64,/9j/4AAQSkZJRgABAQAAAQABAAD/",
            )

    def test_decoder_rejects_empty(self):
        with pytest.raises(pti_service.PTIError):
            pti_service.decode_annotated_png("")

    def test_decoder_rejects_jpeg_bytes_under_png_prefix(self):
        # Same prefix, bytes are NOT a real PNG — magic-number check
        # is the last line of defense.
        with pytest.raises(pti_service.PTIError):
            pti_service.decode_annotated_png(
                "data:image/png;base64,/9j/4AAQSkZJRgABAQAAAQABAAD/",
            )

    def test_decoder_rejects_oversize_payload(self):
        # 30 MB of decodable base64 — over the 25 MB cap.
        oversize_bytes = b"\x89PNG\r\n\x1a\n" + b"x" * (30 * 1024 * 1024)
        import base64
        payload = "data:image/png;base64," + base64.b64encode(oversize_bytes).decode()
        with pytest.raises(pti_service.PTIError):
            pti_service.decode_annotated_png(payload)


# ── Review ────────────────────────────────────────────────────────


class TestReview:
    async def _ready_for_review(self, db):
        acct, driver = await _make_account_with_driver(db)
        owner = await db.create_user(22001, acct.id, role=Role.OWNER)
        ins_id = await _spawn_one(db, acct, driver)
        await db.mark_inspection_in_progress(ins_id)
        for it in await db.get_inspection_items(ins_id):
            if it["requires_media"]:
                await db.update_inspection_item(int(it["id"]), status="na")
            else:
                await db.update_inspection_item(int(it["id"]), status="ok")
        await _sign(db, ins_id)
        await pti_service.submit_inspection(ins_id, driver.id, db)
        return acct, driver, owner, ins_id

    async def test_review_approved(self, db):
        acct, _, owner, ins_id = await self._ready_for_review(db)
        result = await pti_service.review_inspection(
            ins_id,
            account_id=acct.id,
            reviewer_id=owner.telegram_id,
            review_status="approved",
            review_notes="LGTM",
            tenant_db=db,
        )
        assert result["review_status"] == "approved"
        assert result["status"] == "reviewed"

    async def test_review_revision_required_reopens(self, db):
        acct, driver, owner, ins_id = await self._ready_for_review(db)
        await pti_service.review_inspection(
            ins_id,
            account_id=acct.id,
            reviewer_id=owner.telegram_id,
            review_status="revision_required",
            review_notes="photo unclear",
            tenant_db=db,
        )
        # Driver's open-inspection picks the same row back up.
        open_ins = await db.get_open_inspection_for_user(driver.id)
        assert open_ins is not None
        assert int(open_ins["id"]) == ins_id

    async def test_review_rejects_invalid_status(self, db):
        acct, _, owner, ins_id = await self._ready_for_review(db)
        with pytest.raises(pti_service.PTIError):
            await pti_service.review_inspection(
                ins_id,
                account_id=acct.id,
                reviewer_id=owner.telegram_id,
                review_status="bogus",
                review_notes="",
                tenant_db=db,
            )

    async def test_review_rejects_unsubmitted_row(self, db):
        acct, driver = await _make_account_with_driver(db)
        owner = await db.create_user(22002, acct.id, role=Role.OWNER)
        ins_id = await _spawn_one(db, acct, driver)
        # Inspection still in 'scheduled' — can't review.
        with pytest.raises(pti_service.WrongStatus):
            await pti_service.review_inspection(
                ins_id,
                account_id=acct.id,
                reviewer_id=owner.telegram_id,
                review_status="approved",
                review_notes="",
                tenant_db=db,
            )

    async def test_review_rejects_cross_account(self, db):
        # Reviewer in account B can't review an inspection in account A.
        _, _, _, ins_id = await self._ready_for_review(db)
        other_acct = await db.create_account("Other")
        other_owner = await db.create_user(
            33001, other_acct.id, role=Role.OWNER,
        )
        with pytest.raises(pti_service.NotFound):
            await pti_service.review_inspection(
                ins_id,
                account_id=other_acct.id,
                reviewer_id=other_owner.telegram_id,
                review_status="approved",
                review_notes="",
                tenant_db=db,
            )


# ── update_item_status ────────────────────────────────────────────


class TestUpdateItem:
    async def test_first_touch_flips_to_in_progress(self, db):
        acct, driver = await _make_account_with_driver(db)
        ins_id = await _spawn_one(db, acct, driver)
        await pti_service.update_item_status(
            ins_id, "brakes_service",
            user_id=driver.id, status="ok", tenant_db=db,
        )
        ins = await db.get_inspection(ins_id, acct.id)
        assert ins["status"] == "in_progress"

    async def test_update_rejects_wrong_user(self, db):
        acct, driver = await _make_account_with_driver(db)
        other = await db.create_user(88888, acct.id, role=Role.DRIVER)
        ins_id = await _spawn_one(db, acct, driver)
        with pytest.raises(pti_service.NotFound):
            await pti_service.update_item_status(
                ins_id, "brakes_service",
                user_id=other.id, status="ok", tenant_db=db,
            )

    async def test_update_rejects_unknown_status(self, db):
        acct, driver = await _make_account_with_driver(db)
        ins_id = await _spawn_one(db, acct, driver)
        with pytest.raises(pti_service.PTIError):
            await pti_service.update_item_status(
                ins_id, "brakes_service",
                user_id=driver.id, status="bogus", tenant_db=db,
            )

    async def test_update_rejects_read_only_inspection(self, db):
        # An inspection already 'reviewed' is locked.
        acct, driver = await _make_account_with_driver(db)
        owner = await db.create_user(44444, acct.id, role=Role.OWNER)
        ins_id = await _spawn_one(db, acct, driver)
        await db.mark_inspection_in_progress(ins_id)
        for it in await db.get_inspection_items(ins_id):
            if it["requires_media"]:
                await db.update_inspection_item(int(it["id"]), status="na")
            else:
                await db.update_inspection_item(int(it["id"]), status="ok")
        await _sign(db, ins_id)
        await pti_service.submit_inspection(ins_id, driver.id, db)
        await pti_service.review_inspection(
            ins_id, account_id=acct.id, reviewer_id=owner.telegram_id,
            review_status="approved", review_notes="", tenant_db=db,
        )
        with pytest.raises(pti_service.WrongStatus):
            await pti_service.update_item_status(
                ins_id, "brakes_service",
                user_id=driver.id, status="minor", tenant_db=db,
            )


# ── Reset template helper ─────────────────────────────────────────


class TestResetTemplate:
    async def test_reset_routes_to_storage_with_correct_items(self, db):
        acct, _ = await _make_account_with_driver(db)
        new_id = await pti_service.reset_template_to_default(
            acct.id, "truck", db,
        )
        new_tpl = await db.get_active_template(acct.id, "truck")
        assert int(new_tpl["id"]) == new_id
        items = await db.get_template_items(new_id)
        assert len(items) == len(STANDARD_DOT_TRUCK_ITEMS)
