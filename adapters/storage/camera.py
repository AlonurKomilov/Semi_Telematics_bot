"""Camera check history CRUD mixin."""

from __future__ import annotations

import logging
import os

logger = logging.getLogger(__name__)

# Statuses that mean "nothing wrong".  Everything else is a finding worth
# keeping a photo of for longer — see features/cameras/retention.py.
OK_STATUSES = ("OK",)


class CameraMixin:

    async def save_camera_check(
        self, account_id: int, vehicle_id: str, vehicle_name: str,
        camera_type: str, status: str, obstruction: str,
        alignment: str, quality: str, summary: str,
        image_path: str = "",
    ) -> int:
        """Insert a single camera check result.  Returns the new row id.

        The id is returned because the check's photo is keyed BY it —
        ``{company}_{truck}_{camera}_{check_id}.jpg``.  The old key had no
        check id, so every check for a truck+camera overwrote the previous
        photo: 17,689 checks resolved to 340 files, and history showed the
        newest image against every past row.  Callers therefore insert
        first, save the image second, and set the path third.
        """
        cur = await self._db.execute(
            "INSERT INTO camera_checks "
            "(account_id, vehicle_id, vehicle_name, camera_type, "
            "status, obstruction, alignment, quality, summary, checked_at, image_path) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (account_id, vehicle_id, vehicle_name, camera_type,
             status, obstruction, alignment, quality, summary, self._now(),
             image_path),
        )
        await self._db.commit()
        return int(cur.lastrowid)

    async def set_camera_check_image(
        self, account_id: int, check_id: int, image_path: str,
    ) -> bool:
        """Attach the stored photo path to a check written moments ago."""
        cur = await self._db.execute(
            "UPDATE camera_checks SET image_path = ? "
            "WHERE id = ? AND account_id = ?",
            (image_path, check_id, account_id),
        )
        await self._db.commit()
        return cur.rowcount > 0

    async def prune_camera_images(
        self, account_id: int, keep_days: int, *, ok_only: bool,
    ) -> int:
        """Delete camera PHOTOS past the window; keep the check ROWS.

        Rows are tiny and are the actual history (when, which truck, what
        status).  Photos are the bulk — 158 checks/day at ~77 KB, which
        is ~12.7 GB at the 1095-day default nobody chose deliberately.
        So this prunes the file and blanks ``image_path``, leaving the
        row's verdict intact forever.

        SERVER-LOCAL ONLY.  Resolves the stored path through
        ``resolve_disk_path`` and ``os.remove``s it — it never goes
        through ``ObjectStorage.delete``, because on the hybrid backend
        that method deletes from the customer's Google Drive too.  Their
        cloud is theirs; we only reclaim our own disk.

        ``ok_only`` splits the two windows: a passing check's photo is
        worth days (proof the camera worked this week), a WARNING /
        ERROR / PROBLEM photo is worth months (the evidence you show a
        driver, plus trend).
        """
        from adapters.storage.object_storage import resolve_disk_path
        from datetime import datetime, timedelta, timezone

        cutoff = (
            datetime.now(timezone.utc) - timedelta(days=keep_days)
        ).isoformat()
        op = "IN" if ok_only else "NOT IN"
        placeholders = ", ".join("?" for _ in OK_STATUSES)
        cur = await self._db.execute(
            f"SELECT id, image_path FROM camera_checks "
            f"WHERE account_id = ? AND image_path <> '' AND checked_at < ? "
            f"AND status {op} ({placeholders})",
            (account_id, cutoff, *OK_STATUSES),
        )
        rows = [dict(r) for r in await cur.fetchall()]
        if not rows:
            return 0

        # BLANK FIRST, DELETE SECOND — and only delete a file no surviving
        # row still points at.
        #
        # Rows written before the per-check key share one file: 17,689
        # checks resolved to 340 images, up to 367 rows deep.  Deleting
        # per-row would have taken 287 files that other rows still needed
        # and blanked 13,073 images — nearly the whole history — because
        # an OK check past 30 days shares its file with PROBLEM checks
        # well inside their 180-day window.  The sync worker already
        # refuses to delete a shared file; this path must too, and for the
        # same reason: the row-to-file mapping is many-to-one until the
        # legacy rows age out.
        for r in rows:
            await self._db.execute(
                "UPDATE camera_checks SET image_path = '' WHERE id = ?",
                (r["id"],),
            )
        await self._db.commit()

        removed = 0
        for path in {r.get("image_path") or "" for r in rows}:
            if not path:
                continue
            cur = await self._db.execute(
                "SELECT 1 FROM camera_checks WHERE account_id = ? "
                "AND image_path = ? LIMIT 1",
                (account_id, path),
            )
            if await cur.fetchone() is not None:
                # A surviving row still reads this file — keeping it costs
                # disk, deleting it costs an image nobody can get back.
                continue
            full = resolve_disk_path(path)
            if not full:
                continue
            try:
                os.remove(full)
                removed += 1
            except OSError:
                # Already gone, or a permissions/mount problem.  The rows
                # are blanked either way — a path pointing at nothing is
                # worse than no path.
                logger.debug("camera prune: could not remove %s", full)
        return removed

    async def get_camera_check_history(
        self, account_id: int, limit: int = 30,
        vehicle_name: str | None = None,
        latest_only: bool = False,
    ) -> list[dict]:
        """Get recent camera check history, newest first.

        Optionally filter by vehicle_name.
        If latest_only=True, return only the most recent check per vehicle.
        """
        if latest_only:
            base = (
                "SELECT c.* FROM camera_checks c "
                "INNER JOIN ("
                "  SELECT vehicle_name, camera_type, MAX(checked_at) AS max_ts "
                "  FROM camera_checks WHERE account_id = ? "
                "  GROUP BY vehicle_name, camera_type"
                ") latest ON c.vehicle_name = latest.vehicle_name "
                "  AND c.camera_type = latest.camera_type "
                "  AND c.checked_at = latest.max_ts "
                "WHERE c.account_id = ? "
            )
            params: list = [account_id, account_id]
            if vehicle_name:
                base += "AND c.vehicle_name = ? "
                params.append(vehicle_name)
            base += "ORDER BY c.checked_at DESC LIMIT ?"
            params.append(limit)
            cur = await self._db.execute(base, params)
        elif vehicle_name:
            cur = await self._db.execute(
                "SELECT * FROM camera_checks "
                "WHERE account_id = ? AND vehicle_name = ? "
                "ORDER BY checked_at DESC LIMIT ?",
                (account_id, vehicle_name, limit),
            )
        else:
            cur = await self._db.execute(
                "SELECT * FROM camera_checks "
                "WHERE account_id = ? "
                "ORDER BY checked_at DESC LIMIT ?",
                (account_id, limit),
            )
        rows = await cur.fetchall()
        return [dict(r) for r in rows]
