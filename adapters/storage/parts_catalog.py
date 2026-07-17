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


def _avg_interval_days(
    first_date: Optional[str], last_date: Optional[str], visit_days: int,
) -> Optional[float]:
    """Mean gap between distinct service days: span / (visits - 1).
    None with fewer than 2 visits or unparseable dates."""
    if visit_days < 2 or not first_date or not last_date:
        return None
    from datetime import datetime
    try:
        first = datetime.fromisoformat(str(first_date)[:10])
        last = datetime.fromisoformat(str(last_date)[:10])
    except ValueError:
        return None
    span = (last - first).days
    if span <= 0:
        return None
    return round(span / (visit_days - 1), 1)


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

    async def create_catalog_part(
        self, account_id: int, name: str,
        *,
        part_number: str = "",
        notes: str = "",
    ) -> tuple[Optional[dict], bool]:
        """Explicit Add-part: resolve semantics (alias-aware) with an
        honest ``created`` flag.  When the name already resolves, the
        EXISTING row comes back unchanged — the caller must tell the
        user their typed part_number/notes were not applied, never
        pretend a create happened."""
        nkey = part_name_key(name)
        if not nkey:
            return None, False
        cur = await self._db.execute(
            "SELECT part_id FROM part_aliases "
            "WHERE account_id = ? AND name_key = ?",
            (account_id, nkey),
        )
        arow = await cur.fetchone()
        if arow:
            return await self.get_catalog_part(
                dict(arow)["part_id"], account_id,
            ), False
        cur = await self._db.execute(
            "SELECT * FROM parts_catalog WHERE account_id = ? AND name_key = ?",
            (account_id, nkey),
        )
        row = await cur.fetchone()
        if row:
            return dict(row), False
        now = self._now()
        await self._db.execute(
            "INSERT INTO parts_catalog (account_id, name, name_key, "
            " part_number, notes, created_at, updated_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?) "
            "ON CONFLICT (account_id, name_key) DO NOTHING",
            (account_id, name.strip(), nkey, part_number, notes, now, now),
        )
        await self._db.commit()
        cur = await self._db.execute(
            "SELECT * FROM parts_catalog WHERE account_id = ? AND name_key = ?",
            (account_id, nkey),
        )
        row = await cur.fetchone()
        return (dict(row) if row else None), True

    async def update_catalog_part(
        self, part_id: int, account_id: int, **kwargs,
    ) -> bool:
        """Edit name/part_number/notes.  Renaming re-derives
        ``name_key``; a collision with another part's key raises (the
        caller should offer merge instead).  ``part_name`` snapshots on
        line rows stay invoice-truth — never rewritten."""
        allowed = {"name", "part_number", "notes"}
        updates = {k: v for k, v in kwargs.items() if k in allowed and v is not None}
        if not updates:
            return False
        if "name" in updates:
            updates["name"] = str(updates["name"]).strip()
            updates["name_key"] = part_name_key(updates["name"])
            if not updates["name_key"]:
                return False
        updates["updated_at"] = self._now()
        set_clause = ", ".join(f"{k} = ?" for k in updates)
        cur = await self._db.execute(
            f"UPDATE parts_catalog SET {set_clause} WHERE id = ? AND account_id = ?",
            [*updates.values(), part_id, account_id],
        )
        await self._db.commit()
        return cur.rowcount > 0

    # Void invoices never count toward analytics (same rule as every
    # cost report) and drafts (no service_date) don't either.
    _LIVE_LINES = (
        "FROM work_order_parts p "
        "JOIN work_orders w ON w.id = p.work_order_id "
        "WHERE w.account_id = ? AND p.part_id = ? "
        "  AND w.service_date IS NOT NULL "
        "  AND w.status != 'void' AND w.payment_status != 'void'"
    )
    # Effective per-unit price: the explicit unit_cost when set, else
    # derived from the line total (NULL when neither is usable, and
    # AVG/MIN/MAX skip NULLs).
    _UNIT_PRICE = (
        "CASE WHEN p.unit_cost > 0 THEN p.unit_cost "
        "     WHEN p.quantity > 0 THEN p.total_cost / p.quantity END"
    )

    async def part_analytics(
        self, part_id: int, account_id: int, purchases_limit: int = 200,
    ) -> Optional[dict]:
        """The part drill-down: recurrence per vehicle, price per
        vendor, and the raw purchase history (price-trend source).

        ``avg_interval_days`` is the mean gap between distinct service
        visits for that vehicle (None with fewer than 2 visits) — the
        "this truck keeps eating this part" early-warning number.
        """
        part = await self.get_catalog_part(part_id, account_id)
        if not part:
            return None
        args = (account_id, part_id)

        cur = await self._db.execute(
            "SELECT w.vehicle_name, "
            "       COUNT(*) AS usage_count, "
            "       COUNT(DISTINCT w.id) AS work_order_count, "
            "       SUM(p.quantity) AS total_quantity, "
            "       SUM(p.total_cost) AS total_spent, "
            "       MIN(w.service_date) AS first_date, "
            "       MAX(w.service_date) AS last_date, "
            "       COUNT(DISTINCT w.service_date) AS visit_days "
            + self._LIVE_LINES +
            " GROUP BY w.vehicle_name "
            " ORDER BY usage_count DESC, total_spent DESC",
            args,
        )
        by_vehicle = []
        for r in (dict(x) for x in await cur.fetchall()):
            r["avg_interval_days"] = _avg_interval_days(
                r.get("first_date"), r.get("last_date"), r.get("visit_days") or 0,
            )
            by_vehicle.append(r)

        cur = await self._db.execute(
            "SELECT COALESCE(v.name, w.vendor_name) AS vendor_name, "
            "       MAX(w.vendor_id) AS vendor_id, "
            "       COUNT(*) AS purchases, "
            "       SUM(p.quantity) AS total_quantity, "
            "       SUM(p.total_cost) AS total_spent, "
            f"      AVG({self._UNIT_PRICE}) AS avg_unit_price, "
            f"      MIN({self._UNIT_PRICE}) AS min_unit_price, "
            f"      MAX({self._UNIT_PRICE}) AS max_unit_price, "
            "       MAX(w.service_date) AS last_date "
            + self._LIVE_LINES.replace(
                "JOIN work_orders w ON w.id = p.work_order_id",
                "JOIN work_orders w ON w.id = p.work_order_id "
                "LEFT JOIN vendors v ON v.id = w.vendor_id "
                "     AND v.account_id = w.account_id",
            ) +
            "  AND COALESCE(v.name, w.vendor_name) <> '' "
            " GROUP BY COALESCE(v.name, w.vendor_name) "
            " ORDER BY purchases DESC, total_spent DESC",
            args,
        )
        by_vendor = []
        for r in (dict(x) for x in await cur.fetchall()):
            for k in ("avg_unit_price", "min_unit_price", "max_unit_price"):
                r[k] = round(float(r[k]), 2) if r.get(k) is not None else None
            by_vendor.append(r)

        cur = await self._db.execute(
            "SELECT w.id AS work_order_id, w.service_date, w.vehicle_name, "
            "       COALESCE(v.name, w.vendor_name) AS vendor_name, "
            "       p.quantity, p.unit_cost, p.total_cost, p.service_task, "
            f"      {self._UNIT_PRICE} AS effective_unit_price "
            + self._LIVE_LINES.replace(
                "JOIN work_orders w ON w.id = p.work_order_id",
                "JOIN work_orders w ON w.id = p.work_order_id "
                "LEFT JOIN vendors v ON v.id = w.vendor_id "
                "     AND v.account_id = w.account_id",
            ) +
            " ORDER BY w.service_date DESC, p.id DESC "
            f"LIMIT {int(purchases_limit)}",
            args,
        )
        purchases = []
        for r in (dict(x) for x in await cur.fetchall()):
            if r.get("effective_unit_price") is not None:
                r["effective_unit_price"] = round(float(r["effective_unit_price"]), 2)
            purchases.append(r)

        return {
            "part": part,
            "by_vehicle": by_vehicle,
            "by_vendor": by_vendor,
            "purchases": purchases,
        }

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
