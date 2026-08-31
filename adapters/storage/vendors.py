"""Vendor registry — per-account master data for repair shops.

Phase A of capabilities/platform/docs/vendor-parts-master-data.md: one row per
real-world vendor, keyed by a normalized ``name_key`` so reports and
the picker stop splitting "Bob's Repair" / "bobs repair" spend.

Contract highlights:
  • ``resolve_or_create_vendor`` is the ONLY write path callers should
    use for name-based entry (form free-type + Datatruck sync) — exact
    normalized match or create; NEVER fuzzy.  Upsert on
    ``(account_id, name_key)`` keeps concurrent syncs duplicate-free.
  • ``merge_vendors`` is the human fix for typo-duplicates: repoints
    ``work_orders.vendor_id`` and records the loser's ``name_key`` as
    an alias (kept as a vendor row tombstone? no — alias column-free
    v1: the loser row is deleted; the WINNER gains the loser's key via
    ``vendor_aliases`` so a later sync re-resolves to the winner
    instead of recreating the loser).
  • Work-order ``vendor_name/address/phone`` snapshots are never
    rewritten by vendor edits — invoice truth stays put.
"""

from __future__ import annotations

from typing import Optional

from capabilities.activity_trail import delete_changes, diff_rows


def vendor_name_key(name: str) -> str:
    """Trim, collapse inner whitespace, casefold.  MUST stay in sync
    with the migration-149 backfill's ``key_of``."""
    return " ".join((name or "").split()).casefold()


class VendorsMixin:
    # ── Reads ────────────────────────────────────────────────────

    async def list_vendors(self, account_id: int) -> list[dict]:
        """All vendors for the account with usage rollups (work-order
        count + total spend + last visit) in one query — powers the
        Vendors list page without N+1s."""
        cur = await self._db.execute(
            """SELECT v.*,
                      COUNT(w.id)              AS work_order_count,
                      COALESCE(SUM(w.total_cost), 0) AS total_spent,
                      MAX(w.service_date)      AS last_service_date
               FROM vendors v
               LEFT JOIN work_orders w
                    ON w.vendor_id = v.id AND w.account_id = v.account_id
               WHERE v.account_id = ?
               GROUP BY v.id
               ORDER BY total_spent DESC, v.name ASC""",
            (account_id,),
        )
        return [dict(r) for r in await cur.fetchall()]

    async def get_vendor(self, vendor_id: int, account_id: int) -> Optional[dict]:
        cur = await self._db.execute(
            "SELECT * FROM vendors WHERE id = ? AND account_id = ?",
            (vendor_id, account_id),
        )
        row = await cur.fetchone()
        return dict(row) if row else None

    # ── Writes ───────────────────────────────────────────────────

    async def create_vendor(
        self, account_id: int, name: str,
        *,
        address: str = "",
        phone: str = "",
        email: str = "",
        notes: str = "",
        actor_user_id: Optional[int] = None,
    ) -> Optional[dict]:
        """Explicit create (idempotent on the normalized name — returns
        the existing row rather than erroring, same UX contract as
        maintenance custom task types)."""
        return await self.resolve_or_create_vendor(
            account_id, name,
            address=address, phone=phone, email=email, notes=notes,
            actor_user_id=actor_user_id,
        )

    async def resolve_or_create_vendor(
        self, account_id: int, name: str,
        *,
        address: str = "",
        phone: str = "",
        email: str = "",
        notes: str = "",
        actor_user_id: Optional[int] = None,
    ) -> Optional[dict]:
        """Exact-normalized-match resolve, else create.  Returns the
        vendor row (never None for a non-empty name).

        Alias-aware: a name whose key was merged away re-resolves to
        the surviving vendor, so re-syncs after a merge don't recreate
        the loser.
        """
        nkey = vendor_name_key(name)
        if not nkey:
            return None
        now = self._now()
        # 1. Alias hit (post-merge re-sync path).
        cur = await self._db.execute(
            "SELECT vendor_id FROM vendor_aliases "
            "WHERE account_id = ? AND name_key = ?",
            (account_id, nkey),
        )
        arow = await cur.fetchone()
        if arow:
            return await self.get_vendor(dict(arow)["vendor_id"], account_id)
        # 2. Upsert-then-select — safe under concurrent syncs.
        async with self.transaction():
            existed = False
            if actor_user_id is not None:
                cur = await self._db.execute(
                    "SELECT 1 FROM vendors WHERE account_id = ? AND name_key = ?",
                    (account_id, nkey),
                )
                existed = (await cur.fetchone()) is not None
            await self._db.execute(
                "INSERT INTO vendors (account_id, name, name_key, address, "
                " phone, email, notes, created_at, updated_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?) "
                "ON CONFLICT (account_id, name_key) DO NOTHING",
                (account_id, name.strip(), nkey, address, phone, email, notes, now, now),
            )
            cur = await self._db.execute(
                "SELECT * FROM vendors WHERE account_id = ? AND name_key = ?",
                (account_id, nkey),
            )
            row = await cur.fetchone()
            if row and actor_user_id is not None and not existed:
                await self.append_activity_events(account_id, [{
                    "entity_type": "vendor", "entity_id": dict(row)["id"],
                    "action": "create", "actor_user_id": actor_user_id,
                    "changes": diff_rows({}, {
                        "name": name.strip(), "address": address,
                        "phone": phone, "email": email, "notes": notes,
                    }),
                }])
        if not row:
            return None
        vendor = dict(row)
        # Enrich-on-save: an EXISTING vendor learns contact info it
        # doesn't have yet from whatever the caller carries (work-order
        # form fields, Datatruck sync).  Only EMPTY fields fill — a
        # value someone deliberately set is never overwritten, and
        # work-order snapshots stay untouched either way.
        fills = {
            k: v for k, v in
            (("address", address), ("phone", phone), ("email", email))
            if v and not (vendor.get(k) or "").strip()
        }
        if fills:
            fills["updated_at"] = now
            set_clause = ", ".join(f"{k} = ?" for k in fills)
            await self._db.execute(
                f"UPDATE vendors SET {set_clause} WHERE id = ? AND account_id = ?",
                [*fills.values(), vendor["id"], account_id],
            )
            await self._db.commit()
            vendor.update(fills)
        # Auto pipeline: identity flows to the platform review queue the
        # moment it's complete enough (address present); links itself
        # when the directory already knows this shop.  Idempotent.
        await self.autosuggest_vendor(account_id, vendor)
        return vendor

    async def update_vendor(
        self, vendor_id: int, account_id: int,
        actor_user_id: Optional[int] = None, **kwargs,
    ) -> bool:
        """Update contact/notes/name.  Renaming re-derives ``name_key``;
        a collision with another vendor's key raises (the caller should
        offer merge instead).  Never touches work-order snapshots."""
        allowed = {"name", "address", "phone", "email", "notes"}
        updates = {k: v for k, v in kwargs.items() if k in allowed and v is not None}
        if not updates:
            return False
        if "name" in updates:
            updates["name"] = str(updates["name"]).strip()
            updates["name_key"] = vendor_name_key(updates["name"])
            if not updates["name_key"]:
                return False
        async with self.transaction():
            old: dict = {}
            if actor_user_id is not None:
                cur = await self._db.execute(
                    "SELECT * FROM vendors WHERE id = ? AND account_id = ?",
                    (vendor_id, account_id),
                )
                r = await cur.fetchone()
                old = dict(r) if r else {}
            updates["updated_at"] = self._now()
            set_clause = ", ".join(f"{k} = ?" for k in updates)
            cur = await self._db.execute(
                f"UPDATE vendors SET {set_clause} WHERE id = ? AND account_id = ?",
                [*updates.values(), vendor_id, account_id],
            )
            if cur.rowcount > 0 and old:
                changes = diff_rows(
                    old, updates,
                    fields=set(updates) - {"updated_at", "name_key"},
                )
                if changes:
                    await self.append_activity_events(account_id, [{
                        "entity_type": "vendor", "entity_id": vendor_id,
                        "action": "update", "actor_user_id": actor_user_id,
                        "changes": changes,
                    }])
        if cur.rowcount > 0:
            # Edits can complete the identity (user adds the address in
            # the Edit dialog) — feed the auto pipeline.  Idempotent.
            vendor = await self.get_vendor(vendor_id, account_id)
            if vendor:
                await self.autosuggest_vendor(account_id, vendor)
        return cur.rowcount > 0

    async def merge_vendors(
        self, account_id: int, loser_id: int, winner_id: int,
        actor_user_id: Optional[int] = None,
    ) -> bool:
        """Fold ``loser`` into ``winner``: repoint work orders, record
        the loser's name_key (and its aliases) as aliases of the
        winner, delete the loser.  Both ids MUST belong to the account
        — cross-account ids return False without touching anything."""
        if loser_id == winner_id:
            return False
        loser = await self.get_vendor(loser_id, account_id)
        winner = await self.get_vendor(winner_id, account_id)
        if not loser or not winner:
            return False
        now = self._now()
        # One transaction: repoints + aliases + delete + trail commit
        # together (pool proxy auto-commits bare statements otherwise).
        async with self.transaction():
            # The directory link survives the merge: if only the loser was
            # linked, the survivor inherits it (deleting the loser row would
            # otherwise silently drop enrichment + review eligibility).
            if loser.get("global_vendor_id") and not winner.get("global_vendor_id"):
                await self._db.execute(
                    "UPDATE vendors SET global_vendor_id = ?, updated_at = ? "
                    "WHERE id = ? AND account_id = ?",
                    (loser["global_vendor_id"], now, winner_id, account_id),
                )
            await self._db.execute(
                "UPDATE work_orders SET vendor_id = ? "
                "WHERE account_id = ? AND vendor_id = ?",
                (winner_id, account_id, loser_id),
            )
            # Loser's key + any aliases it had accumulated move to winner.
            await self._db.execute(
                "UPDATE vendor_aliases SET vendor_id = ? "
                "WHERE account_id = ? AND vendor_id = ?",
                (winner_id, account_id, loser_id),
            )
            await self._db.execute(
                "INSERT INTO vendor_aliases (account_id, name_key, vendor_id, created_at) "
                "VALUES (?, ?, ?, ?) "
                "ON CONFLICT (account_id, name_key) DO NOTHING",
                (account_id, loser["name_key"], winner_id, now),
            )
            await self._db.execute(
                "DELETE FROM vendors WHERE id = ? AND account_id = ?",
                (loser_id, account_id),
            )
            # Trail: both sides of the merge under one group — the loser's
            # full body is the recovery record for the deleted row.
            if actor_user_id is not None:
                from capabilities.activity_trail import new_group_id
                gid = new_group_id()
                await self.append_activity_events(account_id, [
                    {"entity_type": "vendor", "entity_id": loser_id,
                     "action": "merge_away", "actor_user_id": actor_user_id,
                     "changes": delete_changes(loser), "group_id": gid,
                     "context": {"into": winner_id, "into_name": winner.get("name")}},
                    {"entity_type": "vendor", "entity_id": winner_id,
                     "action": "merge_in", "actor_user_id": actor_user_id,
                     "changes": {}, "group_id": gid,
                     "context": {"from": loser_id, "from_name": loser.get("name")}},
                ])
        return True

    # ── Profile detail ───────────────────────────────────────────

    async def vendor_work_orders(
        self, vendor_id: int, account_id: int, limit: int = 200,
    ) -> list[dict]:
        """The vendor page's history: this vendor's work orders, newest
        first (drives 'every part bought, at what price, with what
        labor' via the standard WO detail links)."""
        cur = await self._db.execute(
            "SELECT * FROM work_orders "
            "WHERE account_id = ? AND vendor_id = ? "
            "ORDER BY COALESCE(service_date, created_at) DESC "
            f"LIMIT {int(limit)}",
            (account_id, vendor_id),
        )
        return [dict(r) for r in await cur.fetchall()]
