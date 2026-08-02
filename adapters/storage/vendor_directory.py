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
        chain: str = "",
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
            " suggested_by_account, chain, created_at, updated_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?) "
            "ON CONFLICT (name_key) DO NOTHING",
            (name.strip(), nkey, address, phone, email, website, services,
             notes, status, source, suggested_by_account,
             chain.strip(), now, now),
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
            "services", "notes", "status", "chain",
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

    async def set_directory_geo(
        self, entry_id: int, lat: Optional[float], lng: Optional[float],
    ) -> bool:
        """Set (or clear — both None) an entry's coordinates.  The only
        writer of geo is the operator geocode/pin-confirm flow; generic
        field updates never touch coordinates.  Partial pairs and
        out-of-range values are rejected."""
        if (lat is None) != (lng is None):
            return False
        if lat is not None and lng is not None:
            if not (-90.0 <= float(lat) <= 90.0 and -180.0 <= float(lng) <= 180.0):
                return False
        cur = await self._db.execute(
            "UPDATE vendor_directory SET lat = ?, lng = ?, updated_at = ? "
            "WHERE id = ?",
            (lat, lng, self._now(), entry_id),
        )
        await self._db.commit()
        return cur.rowcount > 0

    async def import_directory_entries(self, rows: list[dict]) -> dict:
        """Operator bulk import (chains, curated lists).  Each row:
        name (required), chain, address, phone, website, services,
        lat, lng.  Entries are born ACTIVE (this is operator curation,
        not a suggestion), geocoded when coordinates are provided, and
        every account's matching vendors are adopted immediately.
        Idempotent on the global name_key — existing names are skipped
        and reported, never overwritten."""
        created = skipped = adopted = 0
        skipped_names: list[str] = []
        for row in rows:
            name = str(row.get("name") or "").strip()
            if not name:
                skipped += 1
                continue
            nkey = vendor_name_key(name)
            cur = await self._db.execute(
                "SELECT id FROM vendor_directory WHERE name_key = ?", (nkey,),
            )
            if await cur.fetchone():
                skipped += 1
                skipped_names.append(name)
                continue
            entry = await self.create_directory_entry(
                name,
                address=str(row.get("address") or ""),
                phone=str(row.get("phone") or ""),
                website=str(row.get("website") or ""),
                services=str(row.get("services") or ""),
                status="active", source="operator",
                chain=str(row.get("chain") or ""),
            )
            if not entry:
                skipped += 1
                continue
            created += 1
            lat, lng = row.get("lat"), row.get("lng")
            if lat is not None and lng is not None:
                await self.set_directory_geo(entry["id"], float(lat), float(lng))
            adopted += await self.adopt_matching_vendors(entry["id"])
        return {
            "created": created, "skipped": skipped,
            "vendors_adopted": adopted,
            "skipped_names": skipped_names[:50],
        }

    # ── Auto pipeline (no user ceremony) ─────────────────────────
    #
    # The directory collects itself: every account vendor whose
    # identity is complete enough to verify (non-empty address) is
    # auto-suggested to the operator queue; when the operator approves
    # an entry, every account's matching vendors auto-link.  Identity
    # fields only ever travel; suggested_by_account stays operator-
    # audit-only.  Users never click "suggest" or "link".

    async def get_identity_sharing(self, account_id: int) -> bool:
        """Account-level consent for contributing vendor IDENTITIES to
        the public directory (default ON — inspectable in Settings)."""
        cur = await self._db.execute(
            "SELECT share_vendor_identities FROM accounts WHERE id = ?",
            (account_id,),
        )
        row = await cur.fetchone()
        return bool(dict(row)["share_vendor_identities"]) if row else True

    async def set_identity_sharing(
        self, account_id: int, enabled: bool,
        actor_user_id: Optional[int] = None,
    ) -> bool:
        async with self.transaction():
            old = None
            if actor_user_id is not None:
                old = await self.get_identity_sharing(account_id)
            cur = await self._db.execute(
                "UPDATE accounts SET share_vendor_identities = ? WHERE id = ?",
                (1 if enabled else 0, account_id),
            )
            if cur.rowcount > 0 and actor_user_id is not None and old != enabled:
                await self.append_activity_events(account_id, [{
                    "entity_type": "sharing_settings",
                    "entity_id": "vendor_identity",
                    "action": "update", "actor_user_id": actor_user_id,
                    "changes": {"enabled": {"from": old, "to": enabled}},
                }])
            return cur.rowcount > 0

    async def autosuggest_vendor(self, account_id: int, vendor: dict) -> None:
        """Feed a vendor's IDENTITY into the directory pipeline.

        No-ops unless the account consents (share_vendor_identities,
        default ON), the vendor has an address (nothing to verify
        otherwise) and isn't linked yet.  Idempotent: the global
        name_key dedup returns the existing entry (pending, rejected
        tombstone, or active); when it's already ACTIVE we auto-link —
        which also back-fills the vendor's empty contact fields."""
        if vendor.get("global_vendor_id"):
            return
        if not (vendor.get("address") or "").strip():
            return
        if not await self.get_identity_sharing(account_id):
            # Consent OFF stops CONTRIBUTION (nothing enters the review
            # queue) — but linking to an entry that is ALREADY public is
            # pure consumption and stays on: the account keeps receiving
            # directory value without sharing anything.
            nkey = vendor_name_key(vendor["name"])
            cur = await self._db.execute(
                "SELECT id FROM vendor_directory "
                "WHERE name_key = ? AND status = 'active'",
                (nkey,),
            )
            row = await cur.fetchone()
            if row:
                await self.link_vendor_to_directory(
                    account_id, vendor["id"], dict(row)["id"],
                )
            return
        entry = await self.create_directory_entry(
            vendor["name"],
            address=vendor.get("address") or "",
            phone=vendor.get("phone") or "",
            email=vendor.get("email") or "",
            status="pending", source="suggestion",
            suggested_by_account=account_id,
        )
        if entry and entry.get("status") == "active":
            await self.link_vendor_to_directory(
                account_id, vendor["id"], entry["id"],
            )

    async def adopt_matching_vendors(self, entry_id: int) -> int:
        """Approve-time fan-out: link every account's unlinked vendor
        whose normalized name matches this ACTIVE entry, and fill their
        empty contact fields from the curated identity.  Returns the
        number of vendors linked."""
        entry = await self.get_directory_entry(entry_id)
        if not entry or entry.get("status") != "active":
            return 0
        now = self._now()
        cur = await self._db.execute(
            "UPDATE vendors SET global_vendor_id = ?, "
            " address = CASE WHEN TRIM(address) = '' THEN ? ELSE address END, "
            " phone   = CASE WHEN TRIM(phone)   = '' THEN ? ELSE phone   END, "
            " email   = CASE WHEN TRIM(email)   = '' THEN ? ELSE email   END, "
            " updated_at = ? "
            "WHERE name_key = ? AND global_vendor_id IS NULL",
            (entry_id, entry.get("address") or "", entry.get("phone") or "",
             entry.get("email") or "", now, entry["name_key"]),
        )
        await self._db.commit()
        return cur.rowcount

    # ── Account-side ─────────────────────────────────────────────

    async def search_directory_active(self, q: str, limit: int = 20) -> list[dict]:
        """ACTIVE entries only, identity fields only — what account
        users may see.  Substring match on the normalized key so
        casing/whitespace don't matter."""
        needle = f"%{vendor_name_key(q)}%" if q else "%"
        cur = await self._db.execute(
            "SELECT id, name, address, phone, email, website, services, "
            "       lat, lng, chain "
            "FROM vendor_directory "
            "WHERE status = 'active' AND name_key LIKE ? "
            "ORDER BY name ASC "
            f"LIMIT {int(limit)}",
            (needle,),
        )
        return [dict(r) for r in await cur.fetchall()]

    async def browse_directory(
        self, account_id: int, q: str = "", limit: int = 2000,
    ) -> list[dict]:
        """Account-facing directory BROWSE: active entries with the
        anonymous rating aggregate and this account's link status.
        Identity + community signal only — the caller's own link is the
        single account-specific fact, and it's the caller's own."""
        needle = f"%{vendor_name_key(q)}%" if q else "%"
        cur = await self._db.execute(
            "SELECT d.id, d.name, d.address, d.phone, d.email, d.website, "
            "       d.services, d.lat, d.lng, d.chain, "
            "       (SELECT COUNT(*) FROM vendor_reviews r "
            "         WHERE r.entry_id = d.id AND r.status = 'approved') AS rating_count, "
            "       (SELECT AVG(r.rating) FROM vendor_reviews r "
            "         WHERE r.entry_id = d.id AND r.status = 'approved') AS rating_avg, "
            "       (SELECT v.id FROM vendors v "
            "         WHERE v.account_id = ? AND v.global_vendor_id = d.id "
            "         ORDER BY v.id LIMIT 1) AS linked_vendor_id, "
            "       (SELECT v.name FROM vendors v "
            "         WHERE v.account_id = ? AND v.global_vendor_id = d.id "
            "         ORDER BY v.id LIMIT 1) AS linked_vendor_name "
            "FROM vendor_directory d "
            "WHERE d.status = 'active' AND d.name_key LIKE ? "
            "ORDER BY d.name ASC "
            f"LIMIT {int(limit)}",
            (account_id, account_id, needle),
        )
        out = []
        for r in (dict(x) for x in await cur.fetchall()):
            r["rating_avg"] = (
                round(float(r["rating_avg"]), 1)
                if r.get("rating_avg") is not None else None
            )
            out.append(r)
        return out

    async def my_vendor_entries_in_bbox(
        self,
        account_id: int,
        south: float,
        west: float,
        north: float,
        east: float,
        limit: int = 500,
    ) -> list[dict]:
        """THIS account's shops on the map: geocoded ACTIVE directory
        entries that one of the caller's vendors links to (links happen
        automatically — service recorded → identity approved → linked).
        Identity + the caller's own vendor name; never spend."""
        cur = await self._db.execute(
            "SELECT d.id, d.name, d.address, d.phone, d.website, "
            "       d.services, d.lat, d.lng, d.chain, "
            "       v.name AS my_vendor_name, v.id AS my_vendor_id "
            "FROM vendors v "
            "JOIN vendor_directory d ON d.id = v.global_vendor_id "
            "WHERE v.account_id = ? AND d.status = 'active' "
            "  AND d.lat IS NOT NULL AND d.lng IS NOT NULL "
            "  AND d.lat BETWEEN ? AND ? AND d.lng BETWEEN ? AND ? "
            "ORDER BY d.name ASC "
            f"LIMIT {int(limit)}",
            (account_id, south, north, west, east),
        )
        return [dict(r) for r in await cur.fetchall()]

    async def directory_entries_in_bbox(
        self,
        south: float,
        west: float,
        north: float,
        east: float,
        limit: int = 500,
    ) -> list[dict]:
        """ACTIVE, geocoded entries inside a map viewport — the data
        source for the live-map POI layer.  Identity fields only (same
        exposure rule as search_directory_active); entries without
        operator-confirmed coordinates never appear."""
        cur = await self._db.execute(
            "SELECT id, name, address, phone, website, services, lat, lng, chain "
            "FROM vendor_directory "
            "WHERE status = 'active' "
            "  AND lat IS NOT NULL AND lng IS NOT NULL "
            "  AND lat BETWEEN ? AND ? AND lng BETWEEN ? AND ? "
            "ORDER BY name ASC "
            f"LIMIT {int(limit)}",
            (south, north, west, east),
        )
        return [dict(r) for r in await cur.fetchall()]

    async def link_vendor_to_directory(
        self, account_id: int, vendor_id: int, entry_id: Optional[int],
        actor_user_id: Optional[int] = None,
    ) -> bool:
        """Set (or clear, entry_id=None) the account vendor's global
        link.  Only ACTIVE entries are linkable.

        Linking also ENRICHES the private vendor record: the curated
        entry's identity fields fill the vendor's EMPTY address/phone/
        email (operator-verified data flowing down for free).  Fields
        the account already set are never overwritten."""
        entry: Optional[dict] = None
        if entry_id is not None:
            entry = await self.get_directory_entry(entry_id)
            if not entry or entry["status"] != "active":
                return False
        async with self.transaction():
            old_gid = None
            if actor_user_id is not None:
                gcur = await self._db.execute(
                    "SELECT global_vendor_id FROM vendors "
                    "WHERE id = ? AND account_id = ?",
                    (vendor_id, account_id),
                )
                r = await gcur.fetchone()
                old_gid = r[0] if r else None
            cur = await self._db.execute(
                "UPDATE vendors SET global_vendor_id = ?, updated_at = ? "
                "WHERE id = ? AND account_id = ?",
                (entry_id, self._now(), vendor_id, account_id),
            )
            if cur.rowcount > 0 and actor_user_id is not None and old_gid != entry_id:
                await self.append_activity_events(account_id, [{
                    "entity_type": "vendor", "entity_id": vendor_id,
                    "action": "link_public" if entry_id else "unlink_public",
                    "actor_user_id": actor_user_id,
                    "changes": {"global_vendor_id": {"from": old_gid, "to": entry_id}},
                    "context": ({"entry_name": entry.get("name") or ""} if entry else {}),
                }])
        if cur.rowcount > 0 and entry:
            vcur = await self._db.execute(
                "SELECT address, phone, email FROM vendors "
                "WHERE id = ? AND account_id = ?",
                (vendor_id, account_id),
            )
            vrow = await vcur.fetchone()
            if vrow:
                v = dict(vrow)
                fills = {
                    k: (entry.get(k) or "") for k in ("address", "phone", "email")
                    if (entry.get(k) or "").strip() and not (v.get(k) or "").strip()
                }
                if fills:
                    set_clause = ", ".join(f"{k} = ?" for k in fills)
                    await self._db.execute(
                        f"UPDATE vendors SET {set_clause} WHERE id = ? AND account_id = ?",
                        [*fills.values(), vendor_id, account_id],
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
        actor_user_id: Optional[int] = None,
    ) -> Optional[dict]:
        """One review per (shop, account); resubmitting UPDATES it and
        sends it back through moderation (status resets to pending) so
        an approved review can't be silently edited into something
        else.  Entry must be active.

        Trail: rating only, never the comment text — reviews are
        anonymous OUTWARD (cross-account) but attributable INWARD (the
        account's own audit view), same balance the old thin log kept.
        """
        entry = await self.get_directory_entry(entry_id)
        if not entry or entry.get("status") != "active":
            return None
        rating = max(1, min(5, int(rating)))
        now = self._now()
        async with self.transaction():
            old_rating = None
            if actor_user_id is not None:
                cur = await self._db.execute(
                    "SELECT rating FROM vendor_reviews "
                    "WHERE entry_id = ? AND account_id = ?",
                    (entry_id, account_id),
                )
                r = await cur.fetchone()
                old_rating = r[0] if r else None
            await self._db.execute(
                "INSERT INTO vendor_reviews (entry_id, account_id, rating, "
                " comment, status, created_at, updated_at) "
                "VALUES (?, ?, ?, ?, 'pending', ?, ?) "
                "ON CONFLICT (entry_id, account_id) DO UPDATE SET "
                " rating = excluded.rating, comment = excluded.comment, "
                " status = 'pending', updated_at = excluded.updated_at",
                (entry_id, account_id, rating, comment.strip(), now, now),
            )
            if actor_user_id is not None:
                await self.append_activity_events(account_id, [{
                    "entity_type": "vendor_directory_entry",
                    "entity_id": entry_id,
                    "action": "review_submit", "actor_user_id": actor_user_id,
                    "changes": {"rating": {"from": old_rating, "to": rating}},
                    "context": {"entry_name": entry.get("name") or ""},
                }])
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
