"""Companies CRUD mixin."""

from __future__ import annotations

from typing import Optional

from encryption import encrypt as _enc

from .models import Company


class CompaniesMixin:

    async def add_company(
        self, account_id: int, code: str,
        samsara_api_key: str, display_name: str = "",
        active_days: int = 30,
    ) -> Company:
        """Add a Samsara company to an account."""
        now = self._now()
        code = code.strip().upper()
        encrypted_key = _enc(samsara_api_key)
        cur = await self._db.execute(
            """INSERT INTO companies
               (account_id, code, display_name, samsara_api_key, active_days, created_at)
               VALUES (?, ?, ?, ?, ?, ?)""",
            (account_id, code, display_name, encrypted_key, active_days, now),
        )
        await self._db.commit()
        return Company(
            id=cur.lastrowid, account_id=account_id, code=code,
            display_name=display_name, samsara_api_key=samsara_api_key,
            active_days=active_days, is_active=True, created_at=now,
        )

    async def get_account_companies(
        self, account_id: int, active_only: bool = True,
    ) -> list[Company]:
        """List all companies belonging to an account."""
        q = "SELECT * FROM companies WHERE account_id = ?"
        params: list = [account_id]
        if active_only:
            q += " AND is_active = 1"
        q += " ORDER BY code"
        cur = await self._db.execute(q, params)
        rows = await cur.fetchall()
        return [self._row_to_company(r) for r in rows]

    async def get_company_by_code(
        self, account_id: int, code: str,
    ) -> Optional[Company]:
        cur = await self._db.execute(
            "SELECT * FROM companies WHERE account_id = ? AND code = ?",
            (account_id, code.upper()),
        )
        row = await cur.fetchone()
        return self._row_to_company(row) if row else None

    async def remove_company(self, company_id: int) -> bool:
        """Soft-delete a company."""
        await self._db.execute(
            "UPDATE companies SET is_active = 0 WHERE id = ?", (company_id,)
        )
        await self._db.commit()
        return True

    async def update_company(self, company_id: int, **kwargs) -> bool:
        """Update company fields. Allowed: display_name, samsara_api_key, active_days, is_active."""
        allowed = {"display_name", "samsara_api_key", "active_days", "is_active"}
        updates = {k: v for k, v in kwargs.items() if k in allowed}
        if not updates:
            return False
        if "samsara_api_key" in updates:
            updates["samsara_api_key"] = _enc(updates["samsara_api_key"])
        set_clause = ", ".join(f"{k} = ?" for k in updates)
        values = list(updates.values()) + [company_id]
        await self._db.execute(
            f"UPDATE companies SET {set_clause} WHERE id = ?", values,
        )
        await self._db.commit()
        return True
