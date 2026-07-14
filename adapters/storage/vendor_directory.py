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

    # ── Reviews (Phase C2 — anonymous stars/comments, moderated) ──

    async def review_eligible(self, account_id: int, entry_id: int) -> bool:
        """'Verified usage' gate: the account may review a shop only if
        at least one of its work orders links to a vendor that links to
        this directory entry.  Keeps reviews grounded in real visits
        and blocks drive-by spam."""
        cur = await self._db.execute(
            "SELECT 1 FROM work_orders w "
            "JOIN vendors v ON v.id = w.vendor_id AND v.account_id = w.account_id "
            "WHERE w.account_id = ? AND v.global_vendor_id = ? LIMIT 1",
            (account_id, entry_id),
        )
        return (await cur.fetchone()) is not None

    async def upsert_vendor_review(
        self, account_id: int, entry_id: int, rating: int, comment: str = "",
    ) -> Optional[dict]:
        """One review per (shop, account); resubmitting UPDATES it and
        sends it back through moderation (status resets to pending) so
        an approved review can't be silently edited into something
        else.  Entry must be active."""
        entry = await self.get_directory_entry(entry_id)
        if not entry or entry.get("status") != "active":
            return None
        rating = max(1, min(5, int(rating)))
        now = self._now()
        await self._db.execute(
            "INSERT INTO vendor_reviews (entry_id, account_id, rating, "
            " comment, status, created_at, updated_at) "
            "VALUES (?, ?, ?, ?, 'pending', ?, ?) "
            "ON CONFLICT (entry_id, account_id) DO UPDATE SET "
            " rating = excluded.rating, comment = excluded.comment, "
            " status = 'pending', updated_at = excluded.updated_at",
            (entry_id, account_id, rating, comment.strip(), now, now),
        )
        await self._db.commit()
        cur = await self._db.execute(
            "SELECT * FROM vendor_reviews WHERE entry_id = ? AND account_id = ?",
            (entry_id, account_id),
        )
        row = await cur.fetchone()
        return dict(row) if row else None

    async def get_my_vendor_review(
        self, account_id: int, entry_id: int,
    ) -> Optional[dict]:
        cur = await self._db.execute(
            "SELECT id, rating, comment, status, updated_at "
            "FROM vendor_reviews WHERE entry_id = ? AND account_id = ?",
            (entry_id, account_id),
        )
        row = await cur.fetchone()
        return dict(row) if row else None

    async def approved_reviews_for_entry(
        self, entry_id: int, limit: int = 5,
    ) -> list[dict]:
        """ANONYMIZED approved reviews — rating, comment, month.  No
        account attribution of any kind leaves this method."""
        cur = await self._db.execute(
            "SELECT rating, comment, created_at FROM vendor_reviews "
            "WHERE entry_id = ? AND status = 'approved' "
            "ORDER BY updated_at DESC "
            f"LIMIT {int(limit)}",
            (entry_id,),
        )
        return [
            {"rating": r["rating"], "comment": r["comment"],
             "month": str(r["created_at"])[:7]}
            for r in (dict(x) for x in await cur.fetchall())
        ]

    async def review_aggregate_for_entry(self, entry_id: int) -> dict:
        """Approved-only average + count."""
        cur = await self._db.execute(
            "SELECT COUNT(*) AS n, AVG(rating) AS avg_rating "
            "FROM vendor_reviews WHERE entry_id = ? AND status = 'approved'",
            (entry_id,),
        )
        row = dict(await cur.fetchone())
        n = int(row.get("n") or 0)
        avg = round(float(row["avg_rating"]), 1) if n else None
        return {"rating_count": n, "rating_avg": avg}

    async def list_reviews_moderation(
        self, status: str = "pending",
    ) -> list[dict]:
        """Operator queue: reviews + shop name + bare account id (audit)."""
        cur = await self._db.execute(
            "SELECT r.*, d.name AS entry_name "
            "FROM vendor_reviews r "
            "JOIN vendor_directory d ON d.id = r.entry_id "
            "WHERE r.status = ? ORDER BY r.updated_at ASC",
            (status,),
        )
        return [dict(r) for r in await cur.fetchall()]

    async def set_review_status(self, review_id: int, status: str) -> bool:
        cur = await self._db.execute(
            "UPDATE vendor_reviews SET status = ?, updated_at = ? WHERE id = ?",
            (status, self._now(), review_id),
        )
        await self._db.commit()
        return cur.rowcount > 0
