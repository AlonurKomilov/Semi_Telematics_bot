"""CRUD mixin for driver_trucks junction table (Python identifiers use 'vehicle')."""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    class _MixinBase:
        """Typing stub — attributes provided by the concrete DB class at runtime."""
        _db: Any

        def acquire(self) -> Any: ...
        def transaction(self) -> Any: ...
        async def read_all(self, sql: str, params: tuple = ()) -> list: ...
        async def read_one(self, sql: str, params: tuple = ()) -> Any: ...
        @staticmethod
        def _now() -> str: ...
else:
    _MixinBase = object

logger = logging.getLogger(__name__)


@dataclass
class DriverVehicle:
    id: int
    user_id: int
    account_id: int
    vehicle_num: str
    is_primary: bool
    assigned_by: int
    assigned_at: str


class DriverVehiclesMixin(_MixinBase):
    """Manage multi-vehicle assignments for drivers."""

    async def get_user_vehicles(self, user_id: int) -> list[DriverVehicle]:
        """Get all vehicle assignments for a user, primary first."""
        async with self.acquire() as conn:
            cur = await conn.execute(
                "SELECT * FROM driver_trucks "
                "WHERE user_id = ? ORDER BY is_primary DESC, truck_num",
                (user_id,),
            )
            rows = await cur.fetchall()
        return [self._row_to_driver_vehicle(r) for r in rows]

    async def get_user_vehicle_nums(self, user_id: int) -> list[str]:
        """Get just the vehicle_num strings for a user."""
        async with self.acquire() as conn:
            cur = await conn.execute(
                "SELECT truck_num FROM driver_trucks "
                "WHERE user_id = ? ORDER BY is_primary DESC, truck_num",
                (user_id,),
            )
            rows = await cur.fetchall()
        return [r[0] for r in rows]

    async def get_account_vehicle_nums_map(
        self, account_id: int,
    ) -> dict[int, list[str]]:
        """``user_id → [truck_nums]`` for a whole account (batch, list
        views).  One query instead of a per-user N+1 — per-user lookups
        fired concurrently acquire a pool connection each and can exhaust
        Postgres client slots."""
        async with self.acquire() as conn:
            cur = await conn.execute(
                "SELECT user_id, truck_num FROM driver_trucks "
                "WHERE account_id = ? "
                "ORDER BY user_id, is_primary DESC, truck_num",
                (account_id,),
            )
            rows = await cur.fetchall()
        out: dict[int, list[str]] = {}
        for r in rows:
            out.setdefault(r[0], []).append(r[1])
        return out

    async def assign_vehicle(
        self,
        user_id: int,
        account_id: int,
        truck_num: str,
        assigned_by: int = 0,
        is_primary: bool = False,
    ) -> DriverVehicle:
        """Assign a vehicle to a user. If is_primary, demote other primaries."""
        now = self._now()
        truck_num = truck_num.strip()
        async with self.transaction():
            if is_primary:
                await self._db.execute(
                    "UPDATE driver_trucks SET is_primary = 0 WHERE user_id = ?",
                    (user_id,),
                )
            cur = await self._db.execute(
                "INSERT INTO driver_trucks "
                "(user_id, account_id, truck_num, is_primary, assigned_by, assigned_at) "
                "VALUES (?, ?, ?, ?, ?, ?) "
                "ON CONFLICT(user_id, truck_num) "
                "DO UPDATE SET is_primary = ?, assigned_by = ?, assigned_at = ?",
                (user_id, account_id, truck_num,
                 int(is_primary), assigned_by, now,
                 int(is_primary), assigned_by, now),
            )
        # Also keep users.truck_num in sync (primary truck)
        if is_primary:
            await self._db.execute(
                "UPDATE users SET truck_num = ? WHERE id = ?",
                (truck_num, user_id),
            )
            await self._db.commit()

        logger.info(
            "Vehicle assigned: user=%d vehicle=%s primary=%s",
            user_id, truck_num, is_primary,
        )
        return DriverVehicle(
            id=cur.lastrowid, user_id=user_id, account_id=account_id,
            vehicle_num=truck_num, is_primary=is_primary,
            assigned_by=assigned_by, assigned_at=now,
        )

    async def unassign_vehicle(self, user_id: int, truck_num: str) -> bool:
        """Remove a vehicle assignment. Returns True if a row was deleted."""
        async with self.transaction():
            cur = await self._db.execute(
                "DELETE FROM driver_trucks WHERE user_id = ? AND truck_num = ?",
                (user_id, truck_num),
            )
        if cur.rowcount > 0:
            # If we removed the primary, promote the next one
            remaining = await self.get_user_vehicles(user_id)
            if remaining and not any(t.is_primary for t in remaining):
                await self._db.execute(
                    "UPDATE driver_trucks SET is_primary = 1 WHERE id = ?",
                    (remaining[0].id,),
                )
                await self._db.commit()
                # Sync users.truck_num
                await self._db.execute(
                    "UPDATE users SET truck_num = ? WHERE id = ?",
                    (remaining[0].vehicle_num, user_id),
                )
                await self._db.commit()
            elif not remaining:
                # No vehicles left — clear users.truck_num
                await self._db.execute(
                    "UPDATE users SET truck_num = NULL WHERE id = ?",
                    (user_id,),
                )
                await self._db.commit()
            return True
        return False

    async def set_user_vehicles(
        self,
        user_id: int,
        account_id: int,
        vehicle_nums: list[str],
        assigned_by: int = 0,
    ) -> list[DriverVehicle]:
        """Replace all vehicle assignments for a user. First in list is primary."""
        now = self._now()
        async with self.transaction():
            await self._db.execute(
                "DELETE FROM driver_trucks WHERE user_id = ?",
                (user_id,),
            )
            for i, tn in enumerate(vehicle_nums):
                tn = tn.strip()
                if not tn:
                    continue
                await self._db.execute(
                    "INSERT INTO driver_trucks "
                    "(user_id, account_id, truck_num, is_primary, assigned_by, assigned_at) "
                    "VALUES (?, ?, ?, ?, ?, ?)",
                    (user_id, account_id, tn, int(i == 0), assigned_by, now),
                )

        # Sync users.truck_num with primary
        primary = vehicle_nums[0].strip() if vehicle_nums else None
        await self._db.execute(
            "UPDATE users SET truck_num = ? WHERE id = ?",
            (primary, user_id),
        )
        await self._db.commit()

        return await self.get_user_vehicles(user_id)

    async def get_account_driver_vehicle_map(
        self, account_id: int,
    ) -> dict[str, str]:
        """Return ``{vehicle_num.lower(): driver_display_name}`` for every
        driver-role user in the account that has at least one vehicle
        assigned.

        Used by the scorecards route to inline a manual driver name
        alongside the vehicle row when Samsara has no paired driver
        of its own (``paired_driver_name`` is ``None``).

        One row per (user, vehicle): if a single user is assigned to
        multiple vehicles the map points each vehicle at that user's
        display name.  When two users claim the same vehicle (data
        anomaly), the one returned is implementation-defined \u2014 the
        admin Users page enforces uniqueness in practice but the map
        treats this defensively rather than raising.
        """
        async with self.acquire() as conn:
            cur = await conn.execute(
                "SELECT dt.truck_num, u.display_name "
                "FROM driver_trucks dt "
                "JOIN users u ON u.id = dt.user_id "
                "WHERE dt.account_id = ? "
                "  AND u.role = 'driver' "
                "  AND COALESCE(u.is_active, 1) = 1 "
                "ORDER BY dt.is_primary DESC, dt.assigned_at DESC",
                (account_id,),
            )
            rows = await cur.fetchall()
        out: dict[str, str] = {}
        for r in rows:
            tn = (r[0] or "").strip().lower()
            name = (r[1] or "").strip()
            if not tn or not name:
                continue
            out.setdefault(tn, name)
        return out

    @staticmethod
    def _row_to_driver_vehicle(row) -> DriverVehicle:
        """Convert an asyncpg Record row to DriverVehicle dataclass."""
        return DriverVehicle(
            id=row[0],
            user_id=row[1],
            account_id=row[2],
            vehicle_num=row[3],
            is_primary=bool(row[4]),
            assigned_by=row[5],
            assigned_at=row[6],
        )
