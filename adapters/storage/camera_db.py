"""Camera check history CRUD mixin."""

from __future__ import annotations


class CameraMixin:

    async def save_camera_check(
        self, account_id: int, vehicle_id: str, vehicle_name: str,
        camera_type: str, status: str, obstruction: str,
        alignment: str, quality: str, summary: str,
        image_path: str = "",
    ):
        """Insert a single camera check result."""
        await self._db.execute(
            "INSERT INTO camera_checks "
            "(account_id, vehicle_id, vehicle_name, camera_type, "
            "status, obstruction, alignment, quality, summary, checked_at, image_path) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (account_id, vehicle_id, vehicle_name, camera_type,
             status, obstruction, alignment, quality, summary, self._now(),
             image_path),
        )
        await self._db.commit()

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
