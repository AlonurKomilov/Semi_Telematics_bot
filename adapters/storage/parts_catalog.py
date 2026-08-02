"""Parts catalog — per-account master data for parts (Phase B).

Component of the Work Orders feature (no standalone page yet); same
contracts as the vendor registry: exact-normalized resolve-or-create
(alias-aware, NEVER fuzzy), human-driven merge with tombstones so a
Datatruck re-sync can't resurrect a merged-away duplicate, and the
``part_name`` on line rows stays the invoice-truth snapshot.
"""

from __future__ import annotations

from typing import Optional

from capabilities.activity_trail import delete_changes, diff_rows


def part_name_key(name: str) -> str:
    """Trim, collapse inner whitespace, casefold — MUST match the
    migration-150 backfill's ``key_of`` and vendors' normalization."""
    return " ".join((name or "").split()).casefold()


def _percentile(sorted_values: list[float], q: float) -> float:
    """Linear-interpolated percentile over a PRE-SORTED list.

    Matches the market-intel rollup's p25/p75 so an account's own band
    and a published market band are computed the same way — otherwise
    showing them side by side would be comparing two different maths.
    """
    if not sorted_values:
        return 0.0
    if len(sorted_values) == 1:
        return sorted_values[0]
    pos = (len(sorted_values) - 1) * q
    lo = int(pos)
    hi = min(lo + 1, len(sorted_values) - 1)
    frac = pos - lo
    return sorted_values[lo] * (1 - frac) + sorted_values[hi] * frac


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
        # Public-catalog autolink at resolve time (honors suppression +
        # the generic blocklist) — accounts that start using a curated
        # part AFTER curation still connect.
        return await self.autolink_part_to_public(
            account_id, dict(row) if row else None,
        )

    async def create_catalog_part(
        self, account_id: int, name: str,
        *,
        part_number: str = "",
        notes: str = "",
        actor_user_id: Optional[int] = None,
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
        async with self.transaction():
            await self._db.execute(
                "INSERT INTO parts_catalog (account_id, name, name_key, "
                " part_number, notes, created_at, updated_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?) "
                "ON CONFLICT (account_id, name_key) DO NOTHING",
                (account_id, name.strip(), nkey, part_number, notes, now, now),
            )
            cur = await self._db.execute(
                "SELECT * FROM parts_catalog WHERE account_id = ? AND name_key = ?",
                (account_id, nkey),
            )
            row = await cur.fetchone()
            if row and actor_user_id is not None:
                await self.append_activity_events(account_id, [{
                    "entity_type": "part", "entity_id": dict(row)["id"],
                    "action": "create", "actor_user_id": actor_user_id,
                    "changes": diff_rows({}, {
                        "name": name.strip(), "part_number": part_number,
                        "notes": notes,
                    }),
                }])
            return (dict(row) if row else None), True

    async def update_catalog_part(
        self, part_id: int, account_id: int,
        actor_user_id: Optional[int] = None,
        trail_group_id: Optional[str] = None,
        **kwargs,
    ) -> bool:
        """Edit name/part_number/notes.  Renaming re-derives
        ``name_key``; a collision with another part's key raises (the
        caller should offer merge instead).  ``part_name`` snapshots on
        line rows stay invoice-truth — never rewritten."""
        allowed = {"name", "part_number", "notes", "assembly_key"}
        updates = {k: v for k, v in kwargs.items() if k in allowed and v is not None}
        if not updates:
            return False
        if "name" in updates:
            updates["name"] = str(updates["name"]).strip()
            updates["name_key"] = part_name_key(updates["name"])
            if not updates["name_key"]:
                return False
        if "assembly_key" in updates:
            from adapters.storage.service_assemblies import (
                normalize_assembly_key,
            )
            ak = normalize_assembly_key(str(updates["assembly_key"]))
            updates["assembly_key"] = ak
            if ak:
                current = await self.get_catalog_part(part_id, account_id)
                # Archived keys stay valid on rows that already hold
                # them; NEW assignments require an active library key
                # (advisor rule — fail closed on vocabulary typos).
                if (not current or current.get("assembly_key") != ak) and                         not await self.assembly_key_valid_for_assignment(ak):
                    return False
        async with self.transaction():
            old: dict = {}
            if actor_user_id is not None:
                cur = await self._db.execute(
                    "SELECT * FROM parts_catalog WHERE id = ? AND account_id = ?",
                    (part_id, account_id),
                )
                r = await cur.fetchone()
                old = dict(r) if r else {}
            updates["updated_at"] = self._now()
            set_clause = ", ".join(f"{k} = ?" for k in updates)
            cur = await self._db.execute(
                f"UPDATE parts_catalog SET {set_clause} WHERE id = ? AND account_id = ?",
                [*updates.values(), part_id, account_id],
            )
            touched = cur.rowcount > 0
            if touched and old:
                # name_key is derived bookkeeping — the rename shows as
                # the human-visible ``name`` change.
                changes = diff_rows(
                    old, updates,
                    fields=set(updates) - {"updated_at", "name_key"},
                )
                if changes:
                    await self.append_activity_events(account_id, [{
                        "entity_type": "part", "entity_id": part_id,
                        "action": "update", "actor_user_id": actor_user_id,
                        "changes": changes, "group_id": trail_group_id,
                    }])
            return touched

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

    async def task_assembly_hints(self, account_id: int) -> dict[int, str]:
        """part_id → the assembly of the task it was most recently
        bought under — the SECOND suggestion source for blank parts.

        The name-keyword matcher can't recognise "Mystery Clamp"; its
        purchase history can: bought during Water Pump Replacement, it
        is probably a water-pump part.  Suggestion only — written by a
        human click, same as every assembly fill.  Most recent line
        wins so a part that migrated between jobs follows its current
        life, and only assembly-specific tasks contribute (blank task
        assemblies can't hint).
        """
        cur = await self._db.execute(
            "SELECT DISTINCT ON (p.part_id) p.part_id, st.assembly_key "
            "FROM work_order_parts p "
            "JOIN work_orders w ON w.id = p.work_order_id "
            "JOIN service_tasks st ON st.id = p.service_task_id "
            "     AND st.account_id = w.account_id "
            "WHERE w.account_id = ? AND p.part_id IS NOT NULL "
            "  AND COALESCE(st.assembly_key, '') <> '' "
            "  AND w.status != 'void' AND w.payment_status != 'void' "
            "ORDER BY p.part_id, w.service_date DESC NULLS LAST, p.id DESC",
            (account_id,),
        )
        return {int(r["part_id"]): r["assembly_key"]
                for r in (dict(x) for x in await cur.fetchall())}

    async def part_price_context(
        self, account_id: int, names: list[str], *,
        company_codes: list[str] | None = None,
        vehicle_names: list[str] | None = None,
        months: int = 12,
    ) -> dict[str, dict]:
        """"Is this price normal?" — answered from the account's OWN
        buying history, keyed by part NAME.

        The same question market intelligence answers, but sourced from
        data every account already has instead of needing three
        consenting fleets.  Names (not ids) because the caller is an
        invoice being typed or scanned: the line exists before any
        catalog row is resolved.  Matching uses ``name_key``, so
        "Brake Pad Set" and "brake  pad set" are the same part.

        Returns ``{name_key: {buys, low, high, last_price,
        cheapest_vendor, cheapest_price}}`` — low/high are the typical
        band (p25–p75, same shape market ranges use, so the two read
        alike when market data eventually exists beside this).  Parts
        with fewer than 2 purchases are omitted: one prior data point
        isn't a range, and pretending otherwise would flag noise.

        ISOLATION.  An account is not one company: a user assigned to
        Company A must not learn Company B's prices, and this response
        names the cheapest VENDOR, so leaking it would disclose who
        the other company buys from.  Both restriction axes follow the
        codebase's scope convention — ``None`` means unrestricted,
        an EMPTY list means restricted to nothing and returns ``{}``
        (fail closed), never "everything".

          * ``company_codes``  — the caller's allowed companies (the
            direct axis; what the work-order list filters on).
          * ``vehicle_names``  — the AI path's equivalent, since the
            assistant carries its scope as vehicle names rather than
            company codes.
        """
        keys = {part_name_key(n) for n in (names or []) if part_name_key(n)}
        if not keys:
            return {}
        # Fail closed: a restriction that resolves to nothing means the
        # caller may see nothing, not everything.
        if company_codes is not None and not company_codes:
            return {}
        if vehicle_names is not None and not vehicle_names:
            return {}
        from datetime import datetime, timedelta, timezone
        since = (datetime.now(timezone.utc)
                 - timedelta(days=int(months) * 31)).date().isoformat()

        placeholders = ",".join("?" * len(keys))
        co_clause, co_params = "", []
        if company_codes:
            co_clause = ("  AND UPPER(COALESCE(w.company_code, '')) IN ("
                         + ",".join("?" * len(company_codes)) + ") ")
            co_params = [str(c).strip().upper() for c in company_codes]
        veh_clause, veh_params = "", []
        if vehicle_names:
            veh_clause = ("  AND LOWER(COALESCE(w.vehicle_name, '')) IN ("
                          + ",".join("?" * len(vehicle_names)) + ") ")
            veh_params = [str(v).strip().lower() for v in vehicle_names]
        cur = await self._db.execute(
            f"SELECT c.name_key AS name_key, "
            f"       {self._UNIT_PRICE} AS unit_price, "
            f"       w.service_date AS service_date, "
            f"       COALESCE(v.name, w.vendor_name) AS vendor_name "
            f"FROM work_order_parts p "
            f"JOIN work_orders w ON w.id = p.work_order_id "
            f"JOIN parts_catalog c ON c.id = p.part_id "
            f"     AND c.account_id = w.account_id "
            f"LEFT JOIN vendors v ON v.id = w.vendor_id "
            f"     AND v.account_id = w.account_id "
            f"WHERE w.account_id = ? "
            f"  AND c.name_key IN ({placeholders}) "
            f"  AND w.service_date IS NOT NULL AND w.service_date >= ? "
            f"  AND w.status != 'void' AND w.payment_status != 'void' "
            f"{co_clause}{veh_clause}"
            f"ORDER BY w.service_date DESC",
            (account_id, *sorted(keys), since, *co_params, *veh_params),
        )
        rows = [dict(r) for r in await cur.fetchall()]

        grouped: dict[str, list[dict]] = {}
        for r in rows:
            price = r.get("unit_price")
            if price is None or float(price) <= 0:
                continue
            grouped.setdefault(r["name_key"], []).append(r)

        out: dict[str, dict] = {}
        for key, entries in grouped.items():
            prices = sorted(float(e["unit_price"]) for e in entries)
            if len(prices) < 2:
                continue          # one point is a price, not a range
            cheapest = min(entries, key=lambda e: float(e["unit_price"]))
            out[key] = {
                "buys": len(prices),
                "low": round(_percentile(prices, 0.25), 2),
                "high": round(_percentile(prices, 0.75), 2),
                # ORDER BY service_date DESC put the newest first.
                "last_price": round(float(entries[0]["unit_price"]), 2),
                "cheapest_vendor": cheapest.get("vendor_name") or "",
                "cheapest_price": round(float(cheapest["unit_price"]), 2),
                "months": int(months),
            }
        return out

    async def part_analytics(
        self, part_id: int, account_id: int, purchases_limit: int = 200,
        *, company_codes: list[str] | None = None,
    ) -> Optional[dict]:
        """The part drill-down: recurrence per vehicle, price per
        vendor, and the raw purchase history (price-trend source).

        ``avg_interval_days`` is the mean gap between distinct service
        visits for that vehicle (None with fewer than 2 visits) — the
        "this truck keeps eating this part" early-warning number.

        ``company_codes`` scopes the profile to the caller's allowed
        companies, because an account is not one company: this returns
        per-VENDOR average prices and a dated purchase history, so an
        unfiltered read would tell a Company A user what Company B pays
        and to whom.  ``None`` = unrestricted (owners, unassigned
        users); an EMPTY list means restricted to nothing and yields an
        empty profile rather than everything — the same fail-closed
        convention the rest of the scope plumbing uses.
        """
        part = await self.get_catalog_part(part_id, account_id)
        if not part:
            return None
        if company_codes is not None and not company_codes:
            return {**part, "by_vehicle": [], "by_vendor": [], "purchases": []}
        co_clause, co_params = "", []
        if company_codes:
            co_clause = ("   AND UPPER(COALESCE(w.company_code, '')) IN ("
                         + ",".join("?" * len(company_codes)) + ") ")
            co_params = [str(c).strip().upper() for c in company_codes]
        args = (account_id, part_id, *co_params)

        cur = await self._db.execute(
            "SELECT w.vehicle_name, "
            "       COUNT(*) AS usage_count, "
            "       COUNT(DISTINCT w.id) AS work_order_count, "
            "       SUM(p.quantity) AS total_quantity, "
            "       SUM(p.total_cost) AS total_spent, "
            "       MIN(w.service_date) AS first_date, "
            "       MAX(w.service_date) AS last_date, "
            "       COUNT(DISTINCT w.service_date) AS visit_days "
            + self._LIVE_LINES + co_clause +
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
            + (self._LIVE_LINES + co_clause).replace(
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
            + (self._LIVE_LINES + co_clause).replace(
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
        actor_user_id: Optional[int] = None,
    ) -> bool:
        """Fold a duplicate part into the canonical one: repoint line
        rows (scoped through their parent work orders), move aliases,
        tombstone the loser's key, delete the loser.

        The trail records BOTH sides of the merge under one group: the
        loser's event carries its full body (a merge deletes a row —
        that body is the recovery record), the winner's records what it
        absorbed."""
        if loser_id == winner_id:
            return False
        loser = await self.get_catalog_part(loser_id, account_id)
        winner = await self.get_catalog_part(winner_id, account_id)
        if not loser or not winner:
            return False
        now = self._now()
        async with self.transaction():
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
            if actor_user_id is not None:
                from capabilities.activity_trail import new_group_id
                gid = new_group_id()
                await self.append_activity_events(account_id, [
                    {"entity_type": "part", "entity_id": loser_id,
                     "action": "merge_away", "actor_user_id": actor_user_id,
                     "changes": delete_changes(loser), "group_id": gid,
                     "context": {"into": winner_id, "into_name": winner.get("name")}},
                    {"entity_type": "part", "entity_id": winner_id,
                     "action": "merge_in", "actor_user_id": actor_user_id,
                     "changes": {}, "group_id": gid,
                     "context": {"from": loser_id, "from_name": loser.get("name")}},
                ])
            return True
