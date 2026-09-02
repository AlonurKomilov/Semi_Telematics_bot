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

    async def get_role_vehicle_scope(
        self, account_id: int, role: str,
    ) -> Optional[str]:
        """The account's ROLE-level unit width, or None for "built-in".

        Team Management's layer between the per-member override and the
        role's built-in default.  Absent means absent — the caller
        (User.resolved_vehicle_scope) owns the default, so this never
        guesses one.
        """
        async with self.acquire() as conn:
            cur = await conn.execute(
                "SELECT scope FROM role_vehicle_scope "
                "WHERE account_id = ? AND role = ?",
                (account_id, role),
            )
            row = await cur.fetchone()
        return row[0] if row and row[0] in ("all", "assigned") else None

    async def get_all_role_vehicle_scopes(
        self, account_id: int,
    ) -> dict[str, str]:
        """{role: scope} for every role this account has narrowed."""
        async with self.acquire() as conn:
            cur = await conn.execute(
                "SELECT role, scope FROM role_vehicle_scope "
                "WHERE account_id = ?",
                (account_id,),
            )
            rows = await cur.fetchall()
        return {r[0]: r[1] for r in rows if r[1] in ("all", "assigned")}

    async def set_role_vehicle_scope(
        self, account_id: int, role: str, scope: Optional[str],
        updated_by: int = 0,
    ) -> None:
        """Set or clear the role's width.  None deletes the row, which
        restores the built-in default — the same "absent means default"
        rule the read side keeps."""
        if scope not in (None, "all", "assigned"):
            raise ValueError(f"invalid vehicle scope: {scope!r}")
        now = self._now()
        async with self.transaction():
            if scope is None:
                await self._db.execute(
                    "DELETE FROM role_vehicle_scope "
                    "WHERE account_id = ? AND role = ?",
                    (account_id, role),
                )
                return
            await self._db.execute(
                "INSERT INTO role_vehicle_scope "
                "(account_id, role, scope, updated_by, updated_at) "
                "VALUES (?, ?, ?, ?, ?) "
                "ON CONFLICT(account_id, role) DO UPDATE SET "
                "scope = excluded.scope, updated_by = excluded.updated_by, "
                "updated_at = excluded.updated_at",
                (account_id, role, scope, updated_by, now),
            )

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
        from capabilities.permissions.roles import ROLE_PERMISSIONS
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

    # ── Role AI guidance ────────────────────────────────────────────

    async def get_role_ai_guidance(
        self, account_id: int, role: str,
    ) -> Optional[str]:
        """Return custom AI guidance text for a role, or None if not set."""
        async with self.acquire() as conn:
            cur = await conn.execute(
                "SELECT guidance FROM role_ai_guidance "
                "WHERE account_id = ? AND role = ?",
                (account_id, role),
            )
            row = await cur.fetchone()
        return row[0] if row else None

    async def get_all_role_ai_guidance(self, account_id: int) -> dict[str, str]:
        """Return all custom AI guidance overrides for an account."""
        async with self.acquire() as conn:
            cur = await conn.execute(
                "SELECT role, guidance FROM role_ai_guidance "
                "WHERE account_id = ? ORDER BY role",
                (account_id,),
            )
            rows = await cur.fetchall()
        return {row[0]: row[1] for row in rows}

    async def set_role_ai_guidance(
        self, account_id: int, role: str, guidance: str,
    ) -> None:
        """Upsert custom AI guidance text for a role."""
        now = self._now()
        async with self.transaction():
            cur = await self._db.execute(
                "SELECT 1 FROM role_ai_guidance "
                "WHERE account_id = ? AND role = ?",
                (account_id, role),
            )
            if await cur.fetchone():
                await self._db.execute(
                    "UPDATE role_ai_guidance SET guidance = ?, updated_at = ? "
                    "WHERE account_id = ? AND role = ?",
                    (guidance, now, account_id, role),
                )
            else:
                await self._db.execute(
                    "INSERT INTO role_ai_guidance (account_id, role, guidance, updated_at) "
                    "VALUES (?, ?, ?, ?)",
                    (account_id, role, guidance, now),
                )

    async def delete_role_ai_guidance(
        self, account_id: int, role: str,
    ) -> bool:
        """Delete a custom AI guidance override. Returns True if deleted."""
        async with self.transaction():
            cur = await self._db.execute(
                "DELETE FROM role_ai_guidance WHERE account_id = ? AND role = ?",
                (account_id, role),
            )
        return cur.rowcount > 0
