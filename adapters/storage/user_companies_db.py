"""User-company access control mixin.

The `user_companies` junction table restricts which companies a user can
access within their account.  An empty set means "all companies" (no
restriction).  Owners always have access to all companies regardless.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Optional


@dataclass
class UserCompany:
    id: int
    user_id: int
    account_id: int
    company_id: int
    company_code: str
    assigned_by: int
    assigned_at: str


class UserCompaniesMixin:

    # ── Reads ────────────────────────────────────────────────────

    async def get_user_companies(self, user_id: int) -> list[UserCompany]:
        """Get all company assignments for a user (ordered by company code)."""
        async with self.acquire() as conn:
            cur = await conn.execute(
                """SELECT uc.id, uc.user_id, uc.account_id, uc.company_id,
                          c.code AS company_code, uc.assigned_by, uc.assigned_at
                   FROM user_companies uc
                   JOIN companies c ON c.id = uc.company_id
                   WHERE uc.user_id = ?
                   ORDER BY c.code""",
                (user_id,),
            )
            rows = await cur.fetchall()
        return [UserCompany(**dict(r)) for r in rows]

    async def get_user_company_ids(self, user_id: int) -> list[int]:
        """Get the company IDs a user is restricted to.

        Returns empty list if user has no restrictions (all companies).
        """
        async with self.acquire() as conn:
            cur = await conn.execute(
                "SELECT company_id FROM user_companies WHERE user_id = ?",
                (user_id,),
            )
            rows = await cur.fetchall()
        return [r["company_id"] for r in rows]

    async def get_user_company_codes(self, user_id: int) -> list[str]:
        """Get the company codes a user is restricted to.

        Returns empty list if user has no restrictions (all companies).
        """
        async with self.acquire() as conn:
            cur = await conn.execute(
                """SELECT c.code
                   FROM user_companies uc
                   JOIN companies c ON c.id = uc.company_id
                   WHERE uc.user_id = ?
                   ORDER BY c.code""",
                (user_id,),
            )
            rows = await cur.fetchall()
        return [r["code"] for r in rows]

    # ── Writes ───────────────────────────────────────────────────

    async def assign_user_company(
        self,
        user_id: int,
        account_id: int,
        company_id: int,
        assigned_by: int = 0,
    ) -> None:
        """Grant a user access to a specific company."""
        now = self._now()
        await self._db.execute(
            """INSERT OR IGNORE INTO user_companies
               (user_id, account_id, company_id, assigned_by, assigned_at)
               VALUES (?, ?, ?, ?, ?)""",
            (user_id, account_id, company_id, assigned_by, now),
        )
        await self._db.commit()

    async def unassign_user_company(
        self, user_id: int, company_id: int,
    ) -> bool:
        """Remove a user's access to a specific company."""
        cur = await self._db.execute(
            "DELETE FROM user_companies WHERE user_id = ? AND company_id = ?",
            (user_id, company_id),
        )
        await self._db.commit()
        return cur.rowcount > 0

    async def set_user_companies(
        self,
        user_id: int,
        account_id: int,
        company_ids: list[int],
        assigned_by: int = 0,
    ) -> None:
        """Replace all company assignments for a user.

        Pass an empty list to remove restrictions (grant all companies).
        """
        now = self._now()
        await self._db.execute(
            "DELETE FROM user_companies WHERE user_id = ?",
            (user_id,),
        )
        for cid in company_ids:
            await self._db.execute(
                """INSERT INTO user_companies
                   (user_id, account_id, company_id, assigned_by, assigned_at)
                   VALUES (?, ?, ?, ?, ?)""",
                (user_id, account_id, cid, assigned_by, now),
            )
        await self._db.commit()
