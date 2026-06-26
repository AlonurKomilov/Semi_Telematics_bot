"""Companies CRUD mixin."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, Optional

from .models import Company

if TYPE_CHECKING:
    class _MixinBase:
        """Typing stub — attributes provided by the concrete DB class at runtime."""
        _db: Any

        @staticmethod
        def _now() -> str: ...

        def _row_to_company(self, row: Any) -> Company: ...
else:
    _MixinBase = object


class CompaniesMixin(_MixinBase):

    async def add_company(
        self, account_id: int, code: str,
        samsara_api_key: str = "", display_name: str = "",
        active_days: int = 30, mc_number: str = "", usdot_number: str = "",
    ) -> Company:
        """Add a sub-company to an account.  ``mc_number``/``usdot_number``
        are the optional federal carrier ids used to match integration
        records to this company."""
        now = self._now()
        code = code.strip().upper()
        mc_number = (mc_number or "").strip()
        usdot_number = (usdot_number or "").strip()
        from infra.crypto import encrypt as _enc
        encrypted_key = _enc(samsara_api_key)
        cur = await self._db.execute(
            """INSERT INTO companies
               (account_id, code, display_name, samsara_api_key, active_days,
                mc_number, usdot_number, created_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
            (account_id, code, display_name, encrypted_key, active_days,
             mc_number, usdot_number, now),
        )
        await self._db.commit()
        return Company(
            id=cur.lastrowid, account_id=account_id, code=code,
            display_name=display_name, samsara_api_key=samsara_api_key,
            active_days=active_days, is_active=True, created_at=now,
            mc_number=mc_number, usdot_number=usdot_number,
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

    async def count_account_companies(self, account_id: int) -> int:
        """Return the number of active companies for an account."""
        cur = await self._db.execute(
            "SELECT COUNT(*) FROM companies WHERE account_id = ? AND is_active = 1",
            (account_id,),
        )
        row = await cur.fetchone()
        return row[0] if row else 0

    async def get_company_by_code(
        self, account_id: int, code: str,
    ) -> Optional[Company]:
        cur = await self._db.execute(
            "SELECT * FROM companies WHERE account_id = ? AND code = ?",
            (account_id, code.upper()),
        )
        row = await cur.fetchone()
        return self._row_to_company(row) if row else None

    async def get_company_in_account(
        self, account_id: int, company_id: int,
    ) -> Optional[Company]:
        """Look up a company by id, scoped to an account.

        Used by handlers that accept ``company_id`` from a request body
        and need to verify the caller's account owns that company
        before acting on it (cross-tenant write protection).  Returns
        None when the id either doesn't exist or belongs to a different
        account — callers should treat both the same way (404 / refuse
        the write).
        """
        cur = await self._db.execute(
            "SELECT * FROM companies WHERE account_id = ? AND id = ?",
            (account_id, company_id),
        )
        row = await cur.fetchone()
        return self._row_to_company(row) if row else None

    async def remove_company(self, company_id: int, account_id: int = 0) -> bool:
        """Soft-delete a company.

        account_id scopes the deletion to the owning account
        (prevents cross-tenant deletion).
        """
        if account_id:
            cur = await self._db.execute(
                "UPDATE companies SET is_active = 0 WHERE id = ? AND account_id = ?",
                (company_id, account_id),
            )
        else:
            cur = await self._db.execute(
                "UPDATE companies SET is_active = 0 WHERE id = ?", (company_id,)
            )
        await self._db.commit()
        return cur.rowcount > 0

    async def update_company(self, company_id: int, account_id: int = 0, **kwargs) -> bool:
        """Update company fields. Allowed: display_name, samsara_api_key, active_days, is_active.

        account_id scopes the update to the owning account
        (prevents cross-tenant modification).
        """
        allowed = {"display_name", "samsara_api_key", "active_days",
                   "is_active", "mc_number", "usdot_number",
                   "logo_object_id", "brand_color", "website", "phone",
                   "headline", "perks", "banner_object_id",
                   "req_experience_years", "req_min_age", "req_cdl_class",
                   "form_theme", "surface_color", "header_color", "bg_color", "heading_color",
                   "legal_address", "compliance_email", "cra_name", "cra_address",
                   "cra_phone", "cra_site"}
        updates = {k: v for k, v in kwargs.items() if k in allowed}
        if not updates:
            return False
        for f in ("mc_number", "usdot_number"):
            if f in updates and updates[f] is not None:
                updates[f] = str(updates[f]).strip()
        if "samsara_api_key" in updates:
            from infra.crypto import encrypt as _enc
            updates["samsara_api_key"] = _enc(updates["samsara_api_key"])
        set_clause = ", ".join(f"{k} = ?" for k in updates)
        if account_id:
            values = list(updates.values()) + [company_id, account_id]
            cur = await self._db.execute(
                f"UPDATE companies SET {set_clause} WHERE id = ? AND account_id = ?", values,
            )
        else:
            values = list(updates.values()) + [company_id]
            cur = await self._db.execute(
                f"UPDATE companies SET {set_clause} WHERE id = ?", values,
            )
        await self._db.commit()
        return cur.rowcount > 0
