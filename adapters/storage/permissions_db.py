"""CRUD mixin for role_permissions table in PlatformDB."""

from __future__ import annotations

import json
import logging
from dataclasses import asdict
from typing import Optional

logger = logging.getLogger(__name__)


class PermissionsMixin:
    """Read/write custom role permissions per account."""

    async def get_role_permissions(
        self, account_id: int, role: str, company_id: Optional[int] = None,
    ) -> Optional[dict]:
        """Get custom permission dict for a role in an account.

        Resolution order:
        1. Company-specific override (if company_id given)
        2. Account-wide default (company_id IS NULL)
        3. None (caller falls back to hardcoded defaults)
        """
        async with self.acquire() as conn:
            if company_id is not None:
                cur = await conn.execute(
                    "SELECT permissions FROM role_permissions "
                    "WHERE account_id = ? AND role = ? AND company_id = ?",
                    (account_id, role, company_id),
                )
                row = await cur.fetchone()
                if row:
                    return json.loads(row[0])

            # Account-wide default
            cur = await conn.execute(
                "SELECT permissions FROM role_permissions "
                "WHERE account_id = ? AND role = ? AND company_id IS NULL",
                (account_id, role),
            )
            row = await cur.fetchone()
            return json.loads(row[0]) if row else None

    async def get_all_role_permissions(self, account_id: int) -> dict[str, dict]:
        """Get all account-wide role permission sets for an account.

        Returns {role_str: {perm_flag: bool, ...}, ...}
        """
        async with self.acquire() as conn:
            cur = await conn.execute(
                "SELECT role, permissions FROM role_permissions "
                "WHERE account_id = ? AND company_id IS NULL "
                "ORDER BY role",
                (account_id,),
            )
            rows = await cur.fetchall()
        return {row[0]: json.loads(row[1]) for row in rows}

    async def set_role_permissions(
        self,
        account_id: int,
        role: str,
        permissions: dict,
        updated_by: int = 0,
        company_id: Optional[int] = None,
    ) -> None:
        """Create or update permission set for a role in an account."""
        now = self._now()
        perm_json = json.dumps(permissions)
        async with self.transaction():
            # NULL != NULL in SQLite UNIQUE constraints, so ON CONFLICT
            # doesn't work for company_id=NULL. Use explicit check.
            if company_id is not None:
                await self._db.execute(
                    "INSERT INTO role_permissions "
                    "(account_id, role, company_id, permissions, updated_by, updated_at) "
                    "VALUES (?, ?, ?, ?, ?, ?) "
                    "ON CONFLICT(account_id, role, company_id) "
                    "DO UPDATE SET permissions = ?, updated_by = ?, updated_at = ?",
                    (account_id, role, company_id, perm_json, updated_by, now,
                     perm_json, updated_by, now),
                )
            else:
                cur = await self._db.execute(
                    "SELECT id FROM role_permissions "
                    "WHERE account_id = ? AND role = ? AND company_id IS NULL",
                    (account_id, role),
                )
                existing = await cur.fetchone()
                if existing:
                    await self._db.execute(
                        "UPDATE role_permissions "
                        "SET permissions = ?, updated_by = ?, updated_at = ? "
                        "WHERE id = ?",
                        (perm_json, updated_by, now, existing[0]),
                    )
                else:
                    await self._db.execute(
                        "INSERT INTO role_permissions "
                        "(account_id, role, company_id, permissions, updated_by, updated_at) "
                        "VALUES (?, ?, NULL, ?, ?, ?)",
                        (account_id, role, perm_json, updated_by, now),
                    )
        logger.info(
            "Updated role_permissions: account=%d role=%s company=%s",
            account_id, role, company_id,
        )

    async def delete_role_permissions(
        self, account_id: int, role: str, company_id: Optional[int] = None,
    ) -> bool:
        """Delete a custom permission override. Returns True if a row was deleted."""
        async with self.transaction():
            if company_id is not None:
                cur = await self._db.execute(
                    "DELETE FROM role_permissions "
                    "WHERE account_id = ? AND role = ? AND company_id = ?",
                    (account_id, role, company_id),
                )
            else:
                cur = await self._db.execute(
                    "DELETE FROM role_permissions "
                    "WHERE account_id = ? AND role = ? AND company_id IS NULL",
                    (account_id, role),
                )
        return cur.rowcount > 0

    async def seed_account_permissions(self, account_id: int) -> int:
        """Seed default permissions for a new account. Returns rows inserted."""
        from capabilities.iam.permissions import ROLE_PERMISSIONS
        now = self._now()
        inserted = 0
        async with self.transaction():
            for role, feature_set in ROLE_PERMISSIONS.items():
                cur = await self._db.execute(
                    "SELECT 1 FROM role_permissions "
                    "WHERE account_id = ? AND role = ? AND company_id IS NULL",
                    (account_id, role.value),
                )
                if await cur.fetchone():
                    continue
                perm_json = json.dumps(asdict(feature_set))
                await self._db.execute(
                    "INSERT INTO role_permissions "
                    "(account_id, role, company_id, permissions, updated_by, updated_at) "
                    "VALUES (?, ?, NULL, ?, 0, ?)",
                    (account_id, role.value, perm_json, now),
                )
                inserted += 1
        return inserted

    async def get_company_overrides(self, account_id: int) -> list[dict]:
        """Get all company-specific permission overrides for an account.

        Returns list of {role, company_id, permissions, updated_by, updated_at}.
        """
        async with self.acquire() as conn:
            cur = await conn.execute(
                "SELECT role, company_id, permissions, updated_by, updated_at "
                "FROM role_permissions "
                "WHERE account_id = ? AND company_id IS NOT NULL "
                "ORDER BY company_id, role",
                (account_id,),
            )
            rows = await cur.fetchall()
        return [
            {
                "role": r[0],
                "company_id": r[1],
                "permissions": json.loads(r[2]),
                "updated_by": r[3],
                "updated_at": r[4],
            }
            for r in rows
        ]
