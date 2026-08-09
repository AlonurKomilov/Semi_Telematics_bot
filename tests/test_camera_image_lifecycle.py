"""Camera photos: one per check, and they age out.

TWO defects this pins, both measured on live data before the fix:

1. ONE PHOTO PER TRUCK, NOT PER CHECK.  The object-store key was
   ``{truck}_{camera}.jpg`` and ``put`` overwrites, so every check
   overwrote the previous photo — 17,689 checks resolved to 340 files,
   up to 367 rows sharing one image.  History showed the newest photo
   beside every past row: right date, right verdict, wrong picture.
   It also made the file unsafe to sync, because deleting it after
   upload would 404 the other 366 rows.

2. NO RETENTION AT ALL.  ``camera_checks`` was in no retention target,
   so its photos would grow unbounded — ~12.7 GB steady-state at the
   1095-day default, for images whose usefulness expires in weeks.
   Rows are kept forever (they are the history); only photos age out,
   and passing checks age out sooner than faults.
"""

from __future__ import annotations

import os
import sys

os.environ.setdefault("ENCRYPTION_KEY", "")

import pytest_asyncio

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

from adapters.storage import Database  # noqa: E402
from adapters.storage.object_storage import (  # noqa: E402
    DiskObjectStorage, resolve_disk_path,
)


def _store_image(account_id: int, name: str, data: bytes = b"jpeg") -> str:
    """Write a real file through the store and return its stored path.

    NOT tmp_path: stored references must resolve inside the data root, and
    ``_disk_path_candidates`` now refuses anything outside it.  A tmp file
    is unresolvable, so a prune "kept" it for the wrong reason — the
    containment guard, not the shared-file guard.  Using the real store
    means these tests exercise the path production actually takes.
    """
    store = DiskObjectStorage(account_id=account_id)
    return store.put("TESTCO/camera-images", name, data)


@pytest_asyncio.fixture
async def cam_acct(pg_db):
    db: Database = pg_db
    acct = await db.create_account("Cam Co")
    return {"db": db, "account_id": acct.id}


class TestPerCheckKey:
    async def test_save_returns_a_distinct_id_per_check(self, cam_acct):
        """The id is what makes the filename unique — it must exist."""
        db, acct = cam_acct["db"], cam_acct["account_id"]
        ids = []
        for _ in range(3):
            ids.append(await db.save_camera_check(
                account_id=acct, vehicle_id="v1", vehicle_name="103",
                camera_type="forward", status="OK", obstruction="none",
                alignment="centered", quality="good", summary="",
            ))
        assert all(ids), "save_camera_check must return the new row id"
        assert len(set(ids)) == 3, "ids must differ or the key still collides"

    async def test_image_path_is_attached_after_insert(self, cam_acct):
        db, acct = cam_acct["db"], cam_acct["account_id"]
        cid = await db.save_camera_check(
            account_id=acct, vehicle_id="v1", vehicle_name="103",
            camera_type="forward", status="OK", obstruction="none",
            alignment="centered", quality="good", summary="",
        )
        ok = await db.set_camera_check_image(
            acct, cid, f"data/userdata/account-{acct}/CO/camera-images/CO_103_forward_{cid}.jpg",
        )
        assert ok
        rows = await db.get_camera_check_history(acct, limit=10)
        row = next(r for r in rows if r["id"] == cid)
        assert str(cid) in row["image_path"], (
            "the stored path must carry the check id — without it, the next "
            "check for this truck overwrites this photo"
        )

    async def test_two_checks_same_truck_get_different_paths(self, cam_acct):
        """THE regression: same truck + same camera, different files."""
        db, acct = cam_acct["db"], cam_acct["account_id"]
        paths = []
        for _ in range(2):
            cid = await db.save_camera_check(
                account_id=acct, vehicle_id="v1", vehicle_name="103",
                camera_type="forward", status="OK", obstruction="none",
                alignment="centered", quality="good", summary="",
            )
            p = f"data/userdata/account-{acct}/CO/camera-images/CO_103_forward_{cid}.jpg"
            await db.set_camera_check_image(acct, cid, p)
            paths.append(p)
        assert paths[0] != paths[1], (
            "both checks resolved to one file — the second overwrote the first"
        )


class TestRetentionSplitsByStatus:
    """Photos age out on two clocks; rows never do."""

    async def _aged_check(self, db, acct, status: str, days_old: int, _unused=None):
        from datetime import datetime, timedelta, timezone
        cid = await db.save_camera_check(
            account_id=acct, vehicle_id="v1", vehicle_name="103",
            camera_type="forward", status=status, obstruction="none",
            alignment="centered", quality="good", summary="",
        )
        path = _store_image(acct, f"TESTCO_103_forward_{cid}.jpg")
        await db.set_camera_check_image(acct, cid, path)
        old = (datetime.now(timezone.utc) - timedelta(days=days_old)).isoformat()
        await db._db.execute(
            "UPDATE camera_checks SET checked_at = ? WHERE id = ?", (old, cid),
        )
        await db._db.commit()
        return cid, path

    async def test_old_ok_photo_is_pruned_but_row_survives(self, cam_acct, tmp_path):
        db, acct = cam_acct["db"], cam_acct["account_id"]
        cid, path = await self._aged_check(db, acct, "OK", 90, tmp_path)

        await db.prune_camera_images(acct, keep_days=30, ok_only=True)

        rows = await db.get_camera_check_history(acct, limit=50)
        row = next((r for r in rows if r["id"] == cid), None)
        assert row is not None, "the CHECK ROW must survive — it is the history"
        assert row["image_path"] == "", "image_path must be blanked, not left dangling"

    async def test_problem_photo_survives_the_ok_window(self, cam_acct, tmp_path):
        """A fault photo at 90 days outlives the 30-day OK window."""
        db, acct = cam_acct["db"], cam_acct["account_id"]
        cid, path = await self._aged_check(db, acct, "PROBLEM", 90, tmp_path)

        await db.prune_camera_images(acct, keep_days=30, ok_only=True)

        rows = await db.get_camera_check_history(acct, limit=50)
        row = next(r for r in rows if r["id"] == cid)
        assert row["image_path"] != "", (
            "the OK prune took a PROBLEM photo — the two windows are separate"
        )

    async def test_recent_photo_is_untouched(self, cam_acct, tmp_path):
        db, acct = cam_acct["db"], cam_acct["account_id"]
        cid, path = await self._aged_check(db, acct, "OK", 5, tmp_path)

        await db.prune_camera_images(acct, keep_days=30, ok_only=True)

        rows = await db.get_camera_check_history(acct, limit=50)
        row = next(r for r in rows if r["id"] == cid)
        assert row["image_path"] != "", "a 5-day-old photo is inside the window"
        assert resolve_disk_path(path) is not None

    async def test_problem_prune_leaves_ok_alone(self, cam_acct, tmp_path):
        """Symmetry: the problem window must not reap passing checks."""
        db, acct = cam_acct["db"], cam_acct["account_id"]
        ok_id, _ = await self._aged_check(db, acct, "OK", 300, tmp_path)
        bad_id, _ = await self._aged_check(db, acct, "ERROR", 300, tmp_path)

        await db.prune_camera_images(acct, keep_days=180, ok_only=False)

        rows = {r["id"]: r for r in await db.get_camera_check_history(acct, limit=50)}
        assert rows[bad_id]["image_path"] == "", "ERROR photo past 180d should go"
        assert rows[ok_id]["image_path"] != "", (
            "the problem prune reaped an OK photo — that is the OK window's job"
        )


class TestLegacySharedFilesSurvivePrune:
    """The prune must not delete a file another row still points at.

    Rows written before the per-check key share one image — 17,689 checks
    over 340 files, up to 367 deep.  An OK check past its 30-day window
    shares its file with PROBLEM checks well inside their 180-day one, so
    a naive per-row ``os.remove`` took 287 files that surviving rows
    needed and blanked 13,073 images: nearly the entire history, from a
    job that runs nightly.

    The sync worker already refused to delete a shared file.  This path
    did not, and the omission is the whole reason this test exists.
    """

    async def test_shared_file_is_kept_while_another_row_needs_it(self, cam_acct):
        from datetime import datetime, timedelta, timezone
        db, acct = cam_acct["db"], cam_acct["account_id"]

        # The LEGACY key: no check id, so both rows land on one file.
        shared = _store_image(acct, "103_forward.jpg", b"one file, two rows")

        ids = {}
        for status, age in (("OK", 90), ("PROBLEM", 90)):
            cid = await db.save_camera_check(
                account_id=acct, vehicle_id="v1", vehicle_name="103",
                camera_type="forward", status=status, obstruction="none",
                alignment="centered", quality="good", summary="",
            )
            await db.set_camera_check_image(acct, cid, shared)
            old = (datetime.now(timezone.utc) - timedelta(days=age)).isoformat()
            await db._db.execute(
                "UPDATE camera_checks SET checked_at = ? WHERE id = ?", (old, cid),
            )
            ids[status] = cid
        await db._db.commit()

        # Prune OK photos past 30 days.  The PROBLEM row is at 90 days —
        # inside its 180-day window — and points at the SAME file.
        await db.prune_camera_images(acct, keep_days=30, ok_only=True)

        assert resolve_disk_path(shared) is not None, (
            "the OK prune deleted a file the surviving PROBLEM row still "
            "references — that row's image is now permanently broken"
        )
        rows = {r["id"]: r for r in await db.get_camera_check_history(acct, limit=50)}
        assert rows[ids["OK"]]["image_path"] == "", "the OK row should be blanked"
        assert rows[ids["PROBLEM"]]["image_path"] != "", (
            "the PROBLEM row must keep its reference"
        )

    async def test_unshared_file_is_still_deleted(self, cam_acct):
        """The guard must not stop the prune doing its job."""
        from datetime import datetime, timedelta, timezone
        db, acct = cam_acct["db"], cam_acct["account_id"]

        lone = _store_image(acct, "solo_104_forward_1.jpg", b"only one row here")
        cid = await db.save_camera_check(
            account_id=acct, vehicle_id="v2", vehicle_name="104",
            camera_type="forward", status="OK", obstruction="none",
            alignment="centered", quality="good", summary="",
        )
        await db.set_camera_check_image(acct, cid, lone)
        old = (datetime.now(timezone.utc) - timedelta(days=90)).isoformat()
        await db._db.execute(
            "UPDATE camera_checks SET checked_at = ? WHERE id = ?", (old, cid),
        )
        await db._db.commit()

        removed = await db.prune_camera_images(acct, keep_days=30, ok_only=True)
        assert removed >= 1
        assert resolve_disk_path(lone) is None, "an exclusively-referenced file should go"


class TestRetentionIsRegistered:
    async def test_camera_targets_resolve(self, cam_acct):
        """The whole point: these were in NO target before."""
        from capabilities.data_lifecycle.retention import discover
        from capabilities.data_lifecycle.retention.registry import resolve
        discover()
        got = {r.target.key: r.keep_days for r in resolve()}
        assert got.get("cameras.images_ok") == 30
        assert got.get("cameras.images_problem") == 180


class TestPhotosLandUnderTheirCompany:
    """A dashcam photo belongs in its company's folder, not a placeholder.

    Measured on live data: 749 camera images sat in ``unnamed-company``
    while the registry knew exactly which company owned each unit.  Two
    independent defects put them there, and either one alone is enough:

    1. ``analyze_snapshot`` renamed the wire key ``_org`` to the display
       key ``company``, and the save path still read ``_org`` — so the
       company was lost between analysis and storage on EVERY image.
    2. Nothing asked the registry.  A snapshot that genuinely arrives
       without an org tag still names a unit we own, and the registry is
       the SSOT for which company owns a unit.
    """

    async def test_analysis_keeps_the_wire_key_beside_the_label(
        self, monkeypatch,
    ):
        """The label may say "?"; the wire key must never inherit it.

        Both branches, because an image whose AI analysis errored still
        belongs to its company — and the error branch is exactly the one
        a reader skims past.
        """
        import asyncio
        import capabilities.ai as ai
        from features.cameras.service import analyze_snapshot

        snap = {
            "vehicle_name": "250", "vehicle_id": "v-250", "_org": "CFT",
            "camera_type": "forward", "image_bytes": b"jpeg",
        }

        async def _ok(*a, **kw):
            return {"status": "OK", "obstruction": "none",
                    "alignment": "centered", "quality": "good",
                    "summary": ""}

        async def _boom(*a, **kw):
            raise RuntimeError("vision provider down")

        for stub in (_ok, _boom):
            monkeypatch.setattr(ai, "analyze_camera_image", stub)
            got = await analyze_snapshot(snap, 1, asyncio.Semaphore(1))
            assert got["_org"] == "CFT", (
                f"the storage key was lost when analysis returned via "
                f"{stub.__name__} — the photo would file under the "
                f"holding pen instead of CFT"
            )

        # And the two keys must not be confused for each other: the
        # label carries a "?" placeholder that must never reach a path.
        monkeypatch.setattr(ai, "analyze_camera_image", _ok)
        blank = await analyze_snapshot(
            {**snap, "_org": ""}, 1, asyncio.Semaphore(1),
        )
        assert blank["company"] == "?" and blank["_org"] == ""

    async def test_registry_answers_which_company_owns_a_unit(self, pg_db):
        db: Database = pg_db
        acct = await db.create_account("Reg Co")
        await db.add_vehicle(acct.id, unit_number="250", company_code="CFT")
        await db.add_vehicle(acct.id, unit_number="241", company_code="PTG")

        assert await db.company_code_for_unit(acct.id, "250") == "CFT"
        assert await db.company_code_for_unit(acct.id, "241") == "PTG"
        # Case-insensitive, whitespace-tolerant: unit numbers reach us
        # from a telematics payload, not from a form.
        assert await db.company_code_for_unit(acct.id, " 250 ") == "CFT"

    async def test_unknown_and_ambiguous_units_admit_it(self, pg_db):
        """Silence beats a guess.  Filing a truck under the WRONG company
        is worse than the holding pen, because it looks correct."""
        db: Database = pg_db
        acct = await db.create_account("Amb Co")
        await db.add_vehicle(acct.id, unit_number="7", company_code="AAA")
        await db.add_vehicle(acct.id, unit_number="7", company_code="BBB")

        assert await db.company_code_for_unit(acct.id, "7") == ""
        assert await db.company_code_for_unit(acct.id, "nosuch") == ""
        assert await db.company_code_for_unit(acct.id, "") == ""

    async def test_lookup_is_account_scoped(self, pg_db):
        """Another tenant's unit number must not resolve a company."""
        db: Database = pg_db
        mine = await db.create_account("Mine")
        theirs = await db.create_account("Theirs")
        await db.add_vehicle(theirs.id, unit_number="900", company_code="XCO")

        assert await db.company_code_for_unit(mine.id, "900") == ""

    async def test_saved_photo_lands_under_the_registry_company(self, pg_db):
        """End to end: no org tag on the way in, right folder on disk."""
        from features.cameras.service import save_camera_image
        db: Database = pg_db
        acct = await db.create_account("E2E Co")
        await db.add_company(account_id=acct.id, code="CFT",
                             display_name="CARGO FREIGHT TRUCKING INC")
        await db.add_vehicle(acct.id, unit_number="250", company_code="CFT")

        path = await save_camera_image(
            acct.id, "250", "forward", b"jpegbytes",
            tenant_db=db, company_code="", check_id=4242,
        )
        assert path, "the save must succeed"
        assert "CARGO FREIGHT TRUCKING INC/camera-images" in path, (
            f"photo landed at {path!r} — it belongs under its company"
        )
        assert "_generic" not in path

    async def test_the_holding_pen_is_named_as_one(self):
        """When nothing can resolve, the folder must not read like a
        company — a customer browsing their Drive treats every top-level
        folder as one of their businesses."""
        from features.work_orders.storage import (
            GENERIC_COMPANY_FOLDER, resolve_company_folder,
        )
        assert GENERIC_COMPANY_FOLDER.startswith("_")
        got = await resolve_company_folder(None, 1, "")
        assert got == GENERIC_COMPANY_FOLDER
