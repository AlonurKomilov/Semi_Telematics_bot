"""Global vendor directory — PLATFORM-owned master identities (Phase C1).

One row per real-world repair shop, shared across every account.
Identity fields only — never any account's transactions.  Curated by
platform operators (system.4truck.us); accounts contribute suggestions
(status='pending') and link their private vendor records via
``vendors.global_vendor_id``.

``suggested_by_account`` is audit-only for operators; account-facing
reads never expose it (one account must not learn another suggested a
shop — that's transactional metadata by implication).
"""

from __future__ import annotations

from typing import Optional

from .vendors import vendor_name_key


class VendorDirectoryMixin:

    # ── Operator-side (system console) ──────────────────────────

    async def list_vendor_directory(
        self, status: Optional[str] = None,
    ) -> list[dict]:
        q = "SELECT * FROM vendor_directory"
        params: list = []
        if status:
            q += " WHERE status = ?"
            params.append(status)
        q += " ORDER BY (status = 'pending') DESC, name ASC"
        cur = await self._db.execute(q, params)
        return [dict(r) for r in await cur.fetchall()]

    async def get_directory_entry(self, entry_id: int) -> Optional[dict]:
        cur = await self._db.execute(
            "SELECT * FROM vendor_directory WHERE id = ?", (entry_id,),
        )
        row = await cur.fetchone()
        return dict(row) if row else None

    async def create_directory_entry(
        self, name: str,
        *,
        address: str = "",
        phone: str = "",
        email: str = "",
        website: str = "",
        services: str = "",
        notes: str = "",
        status: str = "active",
        source: str = "operator",
        suggested_by_account: Optional[int] = None,
    ) -> Optional[dict]:
        """Idempotent on the GLOBAL name_key: a duplicate name returns
        the existing entry (operators + concurrent suggestions can't
        fork one shop into two identities)."""
        nkey = vendor_name_key(name)
        if not nkey:
            return None
        now = self._now()
        await self._db.execute(
            "INSERT INTO vendor_directory (name, name_key, address, phone, "
            " email, website, services, notes, status, source, "
            " suggested_by_account, created_at, updated_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?) "
            "ON CONFLICT (name_key) DO NOTHING",
            (name.strip(), nkey, address, phone, email, website, services,
             notes, status, source, suggested_by_account, now, now),
        )
        await self._db.commit()
        cur = await self._db.execute(
            "SELECT * FROM vendor_directory WHERE name_key = ?", (nkey,),
        )
        row = await cur.fetchone()
        return dict(row) if row else None

    async def update_directory_entry(self, entry_id: int, **kwargs) -> bool:
        allowed = {
            "name", "address", "phone", "email", "website",
            "services", "notes", "status",
        }
        updates = {k: v for k, v in kwargs.items() if k in allowed and v is not None}
        if not updates:
            return False
        if "name" in updates:
            updates["name"] = str(updates["name"]).strip()
            updates["name_key"] = vendor_name_key(updates["name"])
            if not updates["name_key"]:
                return False
        updates["updated_at"] = self._now()
        set_clause = ", ".join(f"{k} = ?" for k in updates)
        cur = await self._db.execute(
            f"UPDATE vendor_directory SET {set_clause} WHERE id = ?",
            [*updates.values(), entry_id],
        )
        await self._db.commit()
        return cur.rowcount > 0

    # ── Account-side ─────────────────────────────────────────────

    async def search_directory_active(self, q: str, limit: int = 20) -> list[dict]:
        """ACTIVE entries only, identity fields only — what account
        users may see.  Substring match on the normalized key so
        casing/whitespace don't matter."""
        needle = f"%{vendor_name_key(q)}%" if q else "%"
        cur = await self._db.execute(
            "SELECT id, name, address, phone, email, website, services "
            "FROM vendor_directory "
            "WHERE status = 'active' AND name_key LIKE ? "
            "ORDER BY name ASC "
            f"LIMIT {int(limit)}",
            (needle,),
        )
        return [dict(r) for r in await cur.fetchall()]

    async def link_vendor_to_directory(
        self, account_id: int, vendor_id: int, entry_id: Optional[int],
    ) -> bool:
        """Set (or clear, entry_id=None) the account vendor's global
        link.  Only ACTIVE entries are linkable."""
        if entry_id is not None:
            entry = await self.get_directory_entry(entry_id)
            if not entry or entry["status"] != "active":
                return False
        cur = await self._db.execute(
            "UPDATE vendors SET global_vendor_id = ?, updated_at = ? "
            "WHERE id = ? AND account_id = ?",
            (entry_id, self._now(), vendor_id, account_id),
        )
        await self._db.commit()
        return cur.rowcount > 0
