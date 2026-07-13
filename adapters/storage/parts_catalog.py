"""Parts catalog — per-account master data for parts (Phase B).

Component of the Work Orders feature (no standalone page yet); same
contracts as the vendor registry: exact-normalized resolve-or-create
(alias-aware, NEVER fuzzy), human-driven merge with tombstones so a
Datatruck re-sync can't resurrect a merged-away duplicate, and the
``part_name`` on line rows stays the invoice-truth snapshot.
"""

from __future__ import annotations

from typing import Optional


def part_name_key(name: str) -> str:
    """Trim, collapse inner whitespace, casefold — MUST match the
    migration-150 backfill's ``key_of`` and vendors' normalization."""
    return " ".join((name or "").split()).casefold()


class PartsCatalogMixin:

    async def list_parts_catalog(self, account_id: int) -> list[dict]:
        """Catalog with usage rollups — powers the parts-editor
        autocomplete and (later) a management screen.  Account resolved
        through the parent work order because ``work_order_parts`` has
        no account_id of its own."""
        cur = await self._db.execute(
            """SELECT c.*,
                      COUNT(p.id)                    AS usage_count,
                      COALESCE(SUM(p.total_cost), 0) AS total_spent
               FROM parts_catalog c
               LEFT JOIN work_order_parts p ON p.part_id = c.id
               LEFT JOIN work_orders w
                    ON w.id = p.work_order_id AND w.account_id = c.account_id
               WHERE c.account_id = ?
               GROUP BY c.id
               ORDER BY usage_count DESC, c.name ASC""",
            (account_id,),
        )
        return [dict(r) for r in await cur.fetchall()]

    async def get_catalog_part(self, part_id: int, account_id: int) -> Optional[dict]:
        cur = await self._db.execute(
            "SELECT * FROM parts_catalog WHERE id = ? AND account_id = ?",
            (part_id, account_id),
        )
        row = await cur.fetchone()
        return dict(row) if row else None

    async def resolve_or_create_part(
        self, account_id: int, name: str,
        *,
        part_number: str = "",
    ) -> Optional[dict]:
        """Exact-normalized resolve, else create; alias-aware so a
        merged-away name re-resolves to the survivor."""
        nkey = part_name_key(name)
        if not nkey:
            return None
        now = self._now()
        cur = await self._db.execute(
            "SELECT part_id FROM part_aliases "
            "WHERE account_id = ? AND name_key = ?",
            (account_id, nkey),
        )
        arow = await cur.fetchone()
        if arow:
            return await self.get_catalog_part(dict(arow)["part_id"], account_id)
        await self._db.execute(
            "INSERT INTO parts_catalog (account_id, name, name_key, "
            " part_number, created_at, updated_at) "
            "VALUES (?, ?, ?, ?, ?, ?) "
            "ON CONFLICT (account_id, name_key) DO NOTHING",
            (account_id, name.strip(), nkey, part_number, now, now),
        )
        await self._db.commit()
        cur = await self._db.execute(
            "SELECT * FROM parts_catalog WHERE account_id = ? AND name_key = ?",
            (account_id, nkey),
        )
        row = await cur.fetchone()
        return dict(row) if row else None

    async def merge_catalog_parts(
        self, account_id: int, loser_id: int, winner_id: int,
    ) -> bool:
        """Fold a duplicate part into the canonical one: repoint line
        rows (scoped through their parent work orders), move aliases,
        tombstone the loser's key, delete the loser."""
        if loser_id == winner_id:
            return False
        loser = await self.get_catalog_part(loser_id, account_id)
        winner = await self.get_catalog_part(winner_id, account_id)
        if not loser or not winner:
            return False
        now = self._now()
        # work_order_parts has no account_id — scope via parent WOs.
        await self._db.execute(
            "UPDATE work_order_parts SET part_id = ? "
            "WHERE part_id = ? AND work_order_id IN "
            "  (SELECT id FROM work_orders WHERE account_id = ?)",
            (winner_id, loser_id, account_id),
        )
        await self._db.execute(
            "UPDATE part_aliases SET part_id = ? "
            "WHERE account_id = ? AND part_id = ?",
            (winner_id, account_id, loser_id),
        )
        await self._db.execute(
            "INSERT INTO part_aliases (account_id, name_key, part_id, created_at) "
            "VALUES (?, ?, ?, ?) "
            "ON CONFLICT (account_id, name_key) DO NOTHING",
            (account_id, loser["name_key"], winner_id, now),
        )
        await self._db.execute(
            "DELETE FROM parts_catalog WHERE id = ? AND account_id = ?",
            (loser_id, account_id),
        )
        await self._db.commit()
        return True
