"""CRUD mixin for driver_trucks junction table."""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Optional

logger = logging.getLogger(__name__)


@dataclass
class DriverTruck:
    id: int
    user_id: int
    account_id: int
    truck_num: str
    is_primary: bool
    assigned_by: int
    assigned_at: str


class DriverTrucksMixin:
    """Manage multi-truck assignments for drivers."""

    async def get_user_trucks(self, user_id: int) -> list[DriverTruck]:
        """Get all truck assignments for a user, primary first."""
        async with self.acquire() as conn:
            cur = await conn.execute(
                "SELECT * FROM driver_trucks "
                "WHERE user_id = ? ORDER BY is_primary DESC, truck_num",
                (user_id,),
            )
            rows = await cur.fetchall()
        return [self._row_to_driver_truck(r) for r in rows]

    async def get_user_truck_nums(self, user_id: int) -> list[str]:
        """Get just the truck_num strings for a user."""
        async with self.acquire() as conn:
            cur = await conn.execute(
                "SELECT truck_num FROM driver_trucks "
                "WHERE user_id = ? ORDER BY is_primary DESC, truck_num",
                (user_id,),
            )
            rows = await cur.fetchall()
        return [r[0] for r in rows]

    async def assign_truck(
        self,
        user_id: int,
        account_id: int,
        truck_num: str,
        assigned_by: int = 0,
        is_primary: bool = False,
    ) -> DriverTruck:
        """Assign a truck to a user. If is_primary, demote other primaries."""
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
            "Truck assigned: user=%d truck=%s primary=%s",
            user_id, truck_num, is_primary,
        )
        return DriverTruck(
            id=cur.lastrowid, user_id=user_id, account_id=account_id,
            truck_num=truck_num, is_primary=is_primary,
            assigned_by=assigned_by, assigned_at=now,
        )

    async def unassign_truck(self, user_id: int, truck_num: str) -> bool:
        """Remove a truck assignment. Returns True if a row was deleted."""
        async with self.transaction():
            cur = await self._db.execute(
                "DELETE FROM driver_trucks WHERE user_id = ? AND truck_num = ?",
                (user_id, truck_num),
            )
        if cur.rowcount > 0:
            # If we removed the primary, promote the next one
            remaining = await self.get_user_trucks(user_id)
            if remaining and not any(t.is_primary for t in remaining):
                await self._db.execute(
                    "UPDATE driver_trucks SET is_primary = 1 WHERE id = ?",
                    (remaining[0].id,),
                )
                await self._db.commit()
                # Sync users.truck_num
                await self._db.execute(
                    "UPDATE users SET truck_num = ? WHERE id = ?",
                    (remaining[0].truck_num, user_id),
                )
                await self._db.commit()
            elif not remaining:
                # No trucks left — clear users.truck_num
                await self._db.execute(
                    "UPDATE users SET truck_num = NULL WHERE id = ?",
                    (user_id,),
                )
                await self._db.commit()
            return True
        return False

    async def set_user_trucks(
        self,
        user_id: int,
        account_id: int,
        truck_nums: list[str],
        assigned_by: int = 0,
    ) -> list[DriverTruck]:
        """Replace all truck assignments for a user. First in list is primary."""
        now = self._now()
        async with self.transaction():
            await self._db.execute(
                "DELETE FROM driver_trucks WHERE user_id = ?",
                (user_id,),
            )
            for i, tn in enumerate(truck_nums):
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
        primary = truck_nums[0].strip() if truck_nums else None
        await self._db.execute(
            "UPDATE users SET truck_num = ? WHERE id = ?",
            (primary, user_id),
        )
        await self._db.commit()

        return await self.get_user_trucks(user_id)

    @staticmethod
    def _row_to_driver_truck(row) -> DriverTruck:
        """Convert a sqlite3.Row to DriverTruck dataclass."""
        return DriverTruck(
            id=row[0],
            user_id=row[1],
            account_id=row[2],
            truck_num=row[3],
            is_primary=bool(row[4]),
            assigned_by=row[5],
            assigned_at=row[6],
        )
