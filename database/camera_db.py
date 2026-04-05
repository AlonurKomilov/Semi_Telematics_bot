"""Camera check history CRUD mixin."""

from __future__ import annotations


class CameraMixin:

    async def save_camera_check(
        self, account_id: int, vehicle_id: str, vehicle_name: str,
        camera_type: str, status: str, obstruction: str,
        alignment: str, quality: str, summary: str,
    ):
        """Insert a single camera check result."""
        await self._db.execute(
            "INSERT INTO camera_checks "
            "(account_id, vehicle_id, vehicle_name, camera_type, "
            "status, obstruction, alignment, quality, summary, checked_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (account_id, vehicle_id, vehicle_name, camera_type,
             status, obstruction, alignment, quality, summary, self._now()),
        )
        await self._db.commit()

    async def get_camera_check_history(
        self, account_id: int, limit: int = 30,
        vehicle_name: str | None = None,
    ) -> list[dict]:
        """Get recent camera check history, newest first.

        Optionally filter by vehicle_name.
        """
        if vehicle_name:
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
