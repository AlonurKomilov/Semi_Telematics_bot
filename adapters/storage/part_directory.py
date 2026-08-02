"""Public parts catalog — platform-curated CANONICAL part identities.

The parts sibling of ``vendor_directory``, minus everything a part
name can't have: no geo, no reviews, no chain, and — deliberately —
NO user contribution pipeline.  A part name has no "address present"
quality gate, so auto-suggesting synced names would flood the operator
queue with "SHOP SUPPLY FEE" garbage; curation is top-down only
(console create / import / one-click PROMOTE from the cross-account
candidates view).

Guardrails (advisor-ruled 2026-07-16):
  * GENERIC-KEY BLOCKLIST — "labor" / "shop supplies" / "fee" mean a
    different thing at every shop; promoting one to canonical would
    make the adopt fan-out pool unlike things into one identity and
    later poison Phase D price ranges.  Blocklisted keys are excluded
    from the candidates view, from adoption, and from entry creation.
  * DISMISSAL TOMBSTONES — dismissed candidate keys drop out of the
    queue and stay out.
  * ALIAS ≠ ENTRY KEY — an alias key may never equal any entry's
    name_key (resolution would be ambiguous); write-time rejected.
  * UNLINK SUPPRESSION — adopt re-fires on activate/alias-add, so
    unlink sets ``public_link_suppressed`` on the account's part row
    and adoption skips suppressed rows; explicit re-link clears it.
  * Fill-empty on link/adopt: ``part_number`` ONLY.  Category is
    displayed through the join, never copied; name/notes stay user
    vocabulary (stricter than the vendors precedent, deliberately).
"""

from __future__ import annotations

from typing import Optional

from .parts_catalog import part_name_key

# Names too generic to be one canonical identity — every shop means
# something different by them.  Excluded from candidates, adoption and
# entry creation.  Keys are part_name_key() outputs (casefolded).
GENERIC_PART_KEYS: frozenset[str] = frozenset({
    "labor", "labour", "shop supplies", "shop supply", "supplies",
    "shop supply/environmental fee", "environmental fee", "misc",
    "miscellaneous", "fee", "fees", "service", "services",
    "service supplies", "disposal", "tax", "freight", "parts",
    "product item", "sublet", "core charge", "hazmat",
})


class PartDirectoryMixin:

    # ── Operator side (system console) ───────────────────────────

    async def list_part_directory(
        self, status: str = "", q: str = "",
    ) -> list[dict]:
        """Console list: entries + their alias keys + adoption count."""
        where, params = [], []
        if status:
            where.append("d.status = ?")
            params.append(status)
        if q:
            where.append("d.name_key LIKE ?")
            params.append(f"%{part_name_key(q)}%")
        clause = ("WHERE " + " AND ".join(where)) if where else ""
        cur = await self._db.execute(
            "SELECT d.*, "
            "  (SELECT COUNT(*) FROM parts_catalog p "
            "    WHERE p.global_part_id = d.id) AS linked_parts "
            f"FROM part_directory d {clause} "
            "ORDER BY d.status ASC, d.name ASC LIMIT 2000",
            params,
        )
        entries = [dict(r) for r in await cur.fetchall()]
        if entries:
            ids = tuple(e["id"] for e in entries)
            acur = await self._db.execute(
                "SELECT id, entry_id, name_key FROM part_directory_aliases "
                f"WHERE entry_id IN ({','.join('?' * len(ids))})",
                list(ids),
            )
            by_entry: dict[int, list[dict]] = {}
            for a in (dict(r) for r in await acur.fetchall()):
                by_entry.setdefault(a["entry_id"], []).append(
                    {"id": a["id"], "name_key": a["name_key"]},
                )
            for e in entries:
                e["aliases"] = by_entry.get(e["id"], [])
        return entries

    async def get_part_directory_entry(self, entry_id: int) -> Optional[dict]:
        cur = await self._db.execute(
            "SELECT * FROM part_directory WHERE id = ?", (entry_id,),
        )
        row = await cur.fetchone()
        return dict(row) if row else None

    async def _part_key_conflicts(self, nkey: str) -> bool:
        """True if *nkey* already exists as an entry key OR alias key."""
        cur = await self._db.execute(
            "SELECT 1 FROM part_directory WHERE name_key = ? "
            "UNION ALL "
            "SELECT 1 FROM part_directory_aliases WHERE name_key = ? "
            "LIMIT 1",
            (nkey, nkey),
        )
        return (await cur.fetchone()) is not None

    async def create_part_directory_entry(
        self, name: str,
        *,
        category: str = "",
        part_number: str = "",
        description: str = "",
        source: str = "manual",
    ) -> Optional[dict]:
        """Create a canonical entry.  Returns None for empty names;
        raises ValueError for blocklisted or already-taken keys (the
        console shows the message verbatim)."""
        nkey = part_name_key(name)
        if not nkey:
            return None
        if nkey in GENERIC_PART_KEYS:
            raise ValueError(
                "Too generic to be one canonical part — every shop means "
                "something different by this name."
            )
        if await self._part_key_conflicts(nkey):
            raise ValueError("This name already exists as an entry or alias.")
        now = self._now()
        await self._db.execute(
            "INSERT INTO part_directory (name, name_key, category, "
            " part_number, description, status, source, created_at, updated_at) "
            "VALUES (?, ?, ?, ?, ?, 'active', ?, ?, ?)",
            (name.strip(), nkey, category.strip(), part_number.strip(),
             description.strip(), source, now, now),
        )
        await self._db.commit()
        cur = await self._db.execute(
            "SELECT * FROM part_directory WHERE name_key = ?", (nkey,),
        )
        row = await cur.fetchone()
        entry = dict(row) if row else None
        if entry:
            await self.adopt_matching_parts(entry["id"])
        return entry

    async def update_part_directory_entry(
        self, entry_id: int, **kwargs,
    ) -> bool:
        """Edit name/category/part_number/description/status.  Renames
        re-derive name_key (collision → ValueError).  Activating an
        archived entry re-fires adoption; archiving stops future
        adoption but existing links survive."""
        allowed = {"name", "category", "part_number", "description", "status"}
        updates = {k: v for k, v in kwargs.items() if k in allowed and v is not None}
        if not updates:
            return False
        if "status" in updates and updates["status"] not in ("active", "archived"):
            raise ValueError("status must be active or archived")
        if "name" in updates:
            updates["name"] = str(updates["name"]).strip()
            nkey = part_name_key(updates["name"])
            if not nkey:
                return False
            if nkey in GENERIC_PART_KEYS:
                raise ValueError("Too generic to be one canonical part.")
            entry = await self.get_part_directory_entry(entry_id)
            if entry and nkey != entry["name_key"] and await self._part_key_conflicts(nkey):
                raise ValueError("This name already exists as an entry or alias.")
            updates["name_key"] = nkey
        updates["updated_at"] = self._now()
        set_clause = ", ".join(f"{k} = ?" for k in updates)
        cur = await self._db.execute(
            f"UPDATE part_directory SET {set_clause} WHERE id = ?",
            [*updates.values(), entry_id],
        )
        await self._db.commit()
        if cur.rowcount > 0 and updates.get("status") == "active":
            await self.adopt_matching_parts(entry_id)
        return cur.rowcount > 0

    async def add_part_directory_alias(
        self, entry_id: int, raw_name: str,
    ) -> bool:
        """Map a name variant to a canonical entry, then re-fire
        adoption so accounts using the variant link up.  Alias keys may
        never equal any entry's key, collide with another alias, or be
        blocklisted-generic."""
        nkey = part_name_key(raw_name)
        if not nkey:
            return False
        if nkey in GENERIC_PART_KEYS:
            raise ValueError("Too generic to alias — see the blocklist rule.")
        if await self._part_key_conflicts(nkey):
            raise ValueError("This key already exists as an entry or alias.")
        entry = await self.get_part_directory_entry(entry_id)
        if not entry:
            return False
        await self._db.execute(
            "INSERT INTO part_directory_aliases (name_key, entry_id, created_at) "
            "VALUES (?, ?, ?)",
            (nkey, entry_id, self._now()),
        )
        await self._db.commit()
        if entry.get("status") == "active":
            await self.adopt_matching_parts(entry_id)
        return True

    async def delete_part_directory_alias(self, alias_id: int) -> bool:
        cur = await self._db.execute(
            "DELETE FROM part_directory_aliases WHERE id = ?", (alias_id,),
        )
        await self._db.commit()
        return cur.rowcount > 0

    # ── Candidates queue (operator signal, zero user ceremony) ────

    async def part_directory_candidates(
        self, min_accounts: int = 2, limit: int = 100,
    ) -> list[dict]:
        """Cross-account part name_keys worth curating: used by ≥ N
        accounts, matching no entry/alias, not dismissed, not generic.
        OPERATOR-ONLY data (name + counts cross accounts)."""
        cur = await self._db.execute(
            "SELECT p.name_key, MIN(p.name) AS sample_name, "
            "       COUNT(DISTINCT p.account_id) AS account_count, "
            "       COUNT(*) AS catalog_rows, "
            "       COALESCE(SUM(u.usage_count), 0) AS usage_count "
            "FROM parts_catalog p "
            "LEFT JOIN (SELECT part_id, COUNT(*) AS usage_count "
            "             FROM work_order_parts GROUP BY part_id) u "
            "       ON u.part_id = p.id "
            "WHERE p.global_part_id IS NULL "
            "  AND p.name_key NOT IN (SELECT name_key FROM part_directory) "
            "  AND p.name_key NOT IN (SELECT name_key FROM part_directory_aliases) "
            "  AND p.name_key NOT IN (SELECT name_key FROM part_directory_dismissals) "
            "GROUP BY p.name_key "
            "HAVING COUNT(DISTINCT p.account_id) >= ? "
            "ORDER BY account_count DESC, usage_count DESC "
            f"LIMIT {int(limit)}",
            (min_accounts,),
        )
        rows = [dict(r) for r in await cur.fetchall()]
        return [r for r in rows if r["name_key"] not in GENERIC_PART_KEYS]

    async def promote_part_candidate(
        self, raw_key: str, canonical_name: str,
        *,
        category: str = "",
        part_number: str = "",
        description: str = "",
    ) -> Optional[dict]:
        """One-click curation: create the canonical entry (source
        'promoted'), record the raw key as an alias when it differs,
        and let creation's adopt fan-out link every account."""
        entry = await self.create_part_directory_entry(
            canonical_name, category=category, part_number=part_number,
            description=description, source="promoted",
        )
        if not entry:
            return None
        rk = part_name_key(raw_key)
        if rk and rk != entry["name_key"]:
            try:
                await self.add_part_directory_alias(entry["id"], raw_key)
            except ValueError:
                pass  # raced into existence elsewhere — adoption already ran
        return entry

    async def dismiss_part_candidate(self, raw_key: str) -> bool:
        """Tombstone: this key stops appearing in the candidates queue
        ('SHOP SUPPLY FEE' shouldn't sit at the top forever)."""
        nkey = part_name_key(raw_key)
        if not nkey:
            return False
        await self._db.execute(
            "INSERT INTO part_directory_dismissals (name_key, created_at) "
            "VALUES (?, ?) ON CONFLICT (name_key) DO NOTHING",
            (nkey, self._now()),
        )
        await self._db.commit()
        return True

    # ── Adoption fan-out (same cross-tenant path as vendors') ────

    async def adopt_matching_parts(self, entry_id: int) -> int:
        """Link every account's unlinked, UNSUPPRESSED parts whose
        name_key matches this ACTIVE entry or one of its aliases;
        fill-empty ``part_number`` only.  Blocklisted keys never adopt
        (defense in depth — creation already rejects them)."""
        entry = await self.get_part_directory_entry(entry_id)
        if not entry or entry.get("status") != "active":
            return 0
        if entry["name_key"] in GENERIC_PART_KEYS:
            return 0
        now = self._now()
        cur = await self._db.execute(
            "UPDATE parts_catalog SET global_part_id = ?, "
            " part_number = CASE WHEN TRIM(part_number) = '' THEN ? "
            "                    ELSE part_number END, "
            " updated_at = ? "
            "WHERE global_part_id IS NULL "
            "  AND NOT public_link_suppressed "
            "  AND (name_key = ? OR name_key IN "
            "        (SELECT name_key FROM part_directory_aliases "
            "          WHERE entry_id = ?))",
            (entry_id, entry.get("part_number") or "", now,
             entry["name_key"], entry_id),
        )
        await self._db.commit()
        return cur.rowcount

    async def autolink_part_to_public(
        self, account_id: int, part: Optional[dict],
    ) -> Optional[dict]:
        """Resolve-time counterpart of the adopt fan-out: a part whose
        key matches an ACTIVE entry/alias links on save, so accounts
        that start using a curated part AFTER curation still connect.
        Honors suppression and the generic blocklist; fill-empty
        part_number only.  Returns the (possibly updated) row."""
        if not part or part.get("global_part_id") or part.get("public_link_suppressed"):
            return part
        nkey = part.get("name_key") or ""
        if not nkey or nkey in GENERIC_PART_KEYS:
            return part
        cur = await self._db.execute(
            "SELECT d.* FROM part_directory d "
            "WHERE d.status = 'active' AND (d.name_key = ? OR d.id = "
            "  (SELECT a.entry_id FROM part_directory_aliases a "
            "    WHERE a.name_key = ?)) "
            "LIMIT 1",
            (nkey, nkey),
        )
        row = await cur.fetchone()
        if not row:
            return part
        entry = dict(row)
        await self._db.execute(
            "UPDATE parts_catalog SET global_part_id = ?, "
            " part_number = CASE WHEN TRIM(part_number) = '' THEN ? "
            "                    ELSE part_number END, "
            " updated_at = ? "
            "WHERE id = ? AND account_id = ?",
            (entry["id"], entry.get("part_number") or "", self._now(),
             part["id"], account_id),
        )
        await self._db.commit()
        return await self.get_catalog_part(part["id"], account_id)

    # ── Account side ─────────────────────────────────────────────

    async def browse_part_directory(
        self, account_id: int, q: str = "", limit: int = 2000,
    ) -> list[dict]:
        """Account-facing browse: ACTIVE canonical identities only,
        plus the caller's own link state — never usage data, never
        other accounts' anything."""
        needle = f"%{part_name_key(q)}%" if q else "%"
        cur = await self._db.execute(
            "SELECT d.id, d.name, d.category, d.part_number, d.description, "
            "       (SELECT p.id FROM parts_catalog p "
            "         WHERE p.account_id = ? AND p.global_part_id = d.id "
            "         ORDER BY p.id LIMIT 1) AS linked_part_id, "
            "       (SELECT p.name FROM parts_catalog p "
            "         WHERE p.account_id = ? AND p.global_part_id = d.id "
            "         ORDER BY p.id LIMIT 1) AS linked_part_name "
            "FROM part_directory d "
            "WHERE d.status = 'active' AND d.name_key LIKE ? "
            "ORDER BY d.name ASC "
            f"LIMIT {int(limit)}",
            (account_id, account_id, needle),
        )
        return [dict(r) for r in await cur.fetchall()]

    async def link_part_to_public(
        self, account_id: int, part_id: int, entry_id: Optional[int],
        actor_user_id: Optional[int] = None,
    ) -> bool:
        """The dedup dialog's Link verb (entry_id set) or Unlink
        (entry_id None).  Link requires an ACTIVE entry, clears the
        suppression marker, and fills an empty part_number.  Unlink
        SETS suppression so the adopt fan-out honors the user's call."""
        if entry_id is None:
            async with self.transaction():
                old_gid = None
                if actor_user_id is not None:
                    cur = await self._db.execute(
                        "SELECT global_part_id FROM parts_catalog "
                        "WHERE id = ? AND account_id = ?",
                        (part_id, account_id),
                    )
                    r = await cur.fetchone()
                    old_gid = r[0] if r else None
                cur = await self._db.execute(
                    "UPDATE parts_catalog SET global_part_id = NULL, "
                    " public_link_suppressed = TRUE, updated_at = ? "
                    "WHERE id = ? AND account_id = ?",
                    (self._now(), part_id, account_id),
                )
                if cur.rowcount > 0 and actor_user_id is not None:
                    await self.append_activity_events(account_id, [{
                        "entity_type": "part", "entity_id": part_id,
                        "action": "unlink_public",
                        "actor_user_id": actor_user_id,
                        "changes": {"global_part_id": {"from": old_gid, "to": None}},
                    }])
                return cur.rowcount > 0
        entry = await self.get_part_directory_entry(entry_id)
        if not entry or entry.get("status") != "active":
            return False
        async with self.transaction():
            cur = await self._db.execute(
                "UPDATE parts_catalog SET global_part_id = ?, "
                " public_link_suppressed = FALSE, "
                " part_number = CASE WHEN TRIM(part_number) = '' THEN ? "
                "                    ELSE part_number END, "
                " updated_at = ? "
                "WHERE id = ? AND account_id = ?",
                (entry_id, entry.get("part_number") or "", self._now(),
                 part_id, account_id),
            )
            if cur.rowcount > 0 and actor_user_id is not None:
                await self.append_activity_events(account_id, [{
                    "entity_type": "part", "entity_id": part_id,
                    "action": "link_public",
                    "actor_user_id": actor_user_id,
                    "changes": {"global_part_id": {"from": None, "to": entry_id}},
                    "context": {"entry_name": entry.get("name") or ""},
                }])
            return cur.rowcount > 0
