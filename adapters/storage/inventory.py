"""Inventory — what physically lives in each vehicle.

The module and its mixin took the feature's name on 2026-09-10.  The
TABLES did not, and will not: their names are written into every
migration that has already run and into the ``retention_runs`` history
that records what was pruned from them.  Renaming them orphans that
history for a word nobody outside this file reads.

Two tables:

``vehicle_inventory_items``
    One row per tracked item (dashcam, fuel card, toll transponder, ELD,
    tablet, other) anchored to the vehicles REGISTRY row.  ``identifier``
    (serial / card last-4 / transponder id) is what makes loss provable.

``vehicle_inventory_events``
    The immutable accountability trail: every install / status change /
    transfer / verification, stamped with the ACTOR (who clicked) and the
    ``driver_user_id`` assigned to the truck AT THAT MOMENT — so "who had
    the truck when the tablet went missing" is answered by the row itself,
    not by archaeology.

Naming: "inventory" (what's inside this truck), deliberately NOT
"equipment" — in trucking, *equipment* means the tractors/trailers
themselves (load-board "equipment type"), a collision we avoid.
"""

from __future__ import annotations

import json
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:  # structural typing only — composed into Database
    class _MixinBase:
        _db: Any
        async def read_all(self, sql: str, params: tuple = ()) -> list: ...
        async def read_one(self, sql: str, params: tuple = ()) -> Any: ...
        @staticmethod
        def _now() -> str: ...
else:
    _MixinBase = object


# Built-in categories — the SUGGESTED vocabulary, not a closed one:
# category is a grouping label (owner decision 2026-07-18), so accounts
# may create their own ("safety_equipment").  Custom values are
# normalized snake_case via normalize_inventory_category; the frontend
# renders unknown keys with a fallback icon/label.  STATUS stays a FIXED
# vocabulary — it drives the attention badges and alert logic.
INVENTORY_CATEGORIES = (
    "camera", "fuel_card", "toll_transponder", "eld", "tablet", "other",
)


def normalize_inventory_category(raw: object) -> str:
    """Free-text category -> stored snake_case key ('' -> 'other')."""
    key = "_".join(str(raw or "").strip().lower().split())[:40]
    return key or "other"

# Lifecycle statuses.  "installed" is healthy; the warn/danger split is a
# UI concern (tones), but ATTENTION_STATUSES drives the fleet-list badge.
INVENTORY_STATUSES = (
    "installed", "needs_check", "damaged", "missing", "in_repair", "spare",
)
ATTENTION_STATUSES = ("needs_check", "damaged", "missing", "in_repair")

_ITEM_COLS = (
    "id, account_id, vehicle_id, category, label, identifier, status, "
    "notes, installed_at, last_verified_at, last_verified_by, is_active, "
    "created_at, updated_at"
)


def _row_to_dict(row) -> dict | None:
    return dict(row) if row is not None else None


class InventoryMixin(_MixinBase):
    """CRUD + event trail for per-vehicle onboard inventory."""

    # ── the expectation: what a vehicle SHOULD be carrying ───────

    async def list_expected_items(
        self, account_id: int, vehicle_type: str = "truck",
    ) -> list[dict]:
        """The template for a vehicle type.  Empty means nothing is
        expected — which is the honest answer for an account that has not
        written one, and keeps every completeness count at "n/a" rather
        than at zero."""
        rows = await self.read_all(
            "SELECT id, account_id, vehicle_type, category, label, quantity, "
            "       required, sort_order, created_at, updated_at "
            "FROM inventory_expected_items "
            "WHERE account_id = ? AND vehicle_type = ? "
            "ORDER BY sort_order, category",
            (account_id, vehicle_type),
        )
        return [dict(r) for r in rows]

    async def replace_expected_items(
        self, account_id: int, vehicle_type: str, rows: list[dict],
    ) -> int:
        """Write the whole template for one vehicle type.

        A REPLACE, not a merge: the editor sends the list it is looking
        at, and a merge would make deleting the last row impossible to
        express.  Rows are rewritten inside one transaction so a failure
        halfway does not leave an account expecting half of what it said.
        """
        now = self._now()
        await self._db.execute(
            "DELETE FROM inventory_expected_items "
            "WHERE account_id = ? AND vehicle_type = ?",
            (account_id, vehicle_type),
        )
        seen: set[str] = set()
        written = 0
        for i, raw in enumerate(rows):
            category = normalize_inventory_category(raw.get("category"))
            # The unique index would raise on a repeat; catching it here
            # means the editor's own duplicate does not cost the whole
            # save, and the LAST one wins the way a form's last field does.
            if category in seen:
                continue
            seen.add(category)
            await self._db.execute(
                """
                INSERT INTO inventory_expected_items
                    (account_id, vehicle_type, category, label, quantity,
                     required, sort_order, created_at, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (account_id, vehicle_type, category,
                 str(raw.get("label") or "")[:120],
                 max(1, int(raw.get("quantity") or 1)),
                 1 if raw.get("required", True) else 0,
                 int(raw.get("sort_order") or (i + 1)), now, now),
            )
            written += 1
        await self._db.commit()
        return written

    async def expected_coverage(
        self, account_id: int, vehicle_id: int, vehicle_type: str = "truck",
    ) -> dict:
        """What this vehicle owes its template, and what it has.

        "Has" counts every ACTIVE item in the category except ``missing``:
        a damaged dashcam is still aboard, and the attention badge already
        says it is damaged.  Two questions, two signals — reporting a
        damaged item as absent as well would double-count one fault.

        Returns ``{"expected": n, "present": n, "rows": [...]}`` where a
        row is short only when it is short; ``expected`` counts REQUIRED
        quantities alone, so a declared-but-optional transponder never
        makes a complete truck look incomplete.
        """
        template = await self.list_expected_items(account_id, vehicle_type)
        if not template:
            return {"expected": 0, "present": 0, "rows": []}

        counted = await self.read_all(
            "SELECT category, COUNT(*) AS n FROM vehicle_inventory_items "
            "WHERE account_id = ? AND vehicle_id = ? AND is_active = 1 "
            "  AND status <> 'missing' "
            "GROUP BY category",
            (account_id, vehicle_id),
        )
        have = {str(dict(r)["category"]): int(dict(r)["n"]) for r in counted}

        rows, expected, present = [], 0, 0
        for t in template:
            want = int(t["quantity"])
            got = have.get(str(t["category"]), 0)
            if t["required"]:
                expected += want
                present += min(got, want)
            rows.append({
                "category": t["category"],
                "label": t["label"],
                "quantity": want,
                "required": bool(t["required"]),
                "present": got,
                "short": max(0, want - got),
            })
        return {"expected": expected, "present": present, "rows": rows}

    # ── reads ────────────────────────────────────────────────────

    async def get_vehicle_by_unit(
        self, account_id: int, unit_number: str, company_code: str | None = None,
    ) -> dict | None:
        """Resolve a REGISTRY vehicle row by unit number (the name every
        surface uses).  Company narrows when two companies share a unit
        number; otherwise the first active match wins."""
        sql = (
            "SELECT id, account_id, company_code, unit_number, vehicle_type "
            "FROM vehicles WHERE account_id = ? AND unit_number = ? AND is_active = 1"
        )
        params: tuple = (account_id, unit_number)
        if company_code:
            sql += " AND company_code = ?"
            params += (company_code,)
        sql += " ORDER BY id LIMIT 1"
        row = await self.read_one(sql, params)
        return _row_to_dict(row)

    async def list_vehicle_inventory(
        self, account_id: int, vehicle_id: int,
    ) -> list[dict]:
        rows = await self.read_all(
            f"SELECT {_ITEM_COLS} FROM vehicle_inventory_items "
            "WHERE account_id = ? AND vehicle_id = ? AND is_active = 1 "
            "ORDER BY category, label, id",
            (account_id, vehicle_id),
        )
        return [dict(r) for r in rows]

    async def get_inventory_item(
        self, account_id: int, item_id: int,
    ) -> dict | None:
        row = await self.read_one(
            f"SELECT {_ITEM_COLS} FROM vehicle_inventory_items "
            "WHERE id = ? AND account_id = ?",
            (item_id, account_id),
        )
        return _row_to_dict(row)

    async def list_inventory_events(
        self, account_id: int, item_id: int, limit: int = 100,
    ) -> list[dict]:
        rows = await self.read_all(
            "SELECT id, item_id, event_type, from_status, to_status, "
            "       from_vehicle_id, to_vehicle_id, actor_user_id, "
            "       driver_user_id, note, changes, created_at "
            "FROM vehicle_inventory_events "
            "WHERE account_id = ? AND item_id = ? "
            "ORDER BY id DESC LIMIT ?",
            (account_id, item_id, limit),
        )
        return [dict(r) for r in rows]

    async def inventory_attention_by_vehicle(
        self, account_id: int,
    ) -> dict[int, dict]:
        """``vehicle_id → {total, attention}`` for the fleet-list badge —
        one query for the whole account."""
        marks = ", ".join("?" for _ in ATTENTION_STATUSES)
        rows = await self.read_all(
            f"""
            SELECT vehicle_id,
                   COUNT(*) AS total,
                   SUM(CASE WHEN status IN ({marks}) THEN 1 ELSE 0 END) AS attention
              FROM vehicle_inventory_items
             WHERE account_id = ? AND is_active = 1
             GROUP BY vehicle_id
            """,
            (*ATTENTION_STATUSES, account_id),
        )
        return {
            int(r["vehicle_id"]): {
                "total": int(r["total"] or 0),
                "attention": int(r["attention"] or 0),
            }
            for r in rows
        }

    async def list_account_inventory(self, account_id: int) -> list[dict]:
        """Every active item across the fleet, joined with its truck's
        unit number + company — the account-wide Inventory page's read.
        One query; company scoping is applied by the caller (router)
        against the user's allowed codes."""
        rows = await self.read_all(
            f"""
            SELECT i.{_ITEM_COLS.replace(', ', ', i.')},
                   v.unit_number, v.company_code, v.vehicle_type
              FROM vehicle_inventory_items i
              JOIN vehicles v ON v.id = i.vehicle_id
             WHERE i.account_id = ? AND i.is_active = 1
             ORDER BY v.unit_number, i.category, i.label
            """,
            (account_id,),
        )
        return [dict(r) for r in rows]

    async def list_inventory_categories(self, account_id: int) -> list[str]:
        """Suggested + in-use categories for the pickers: the built-in
        vocabulary first, then this account's custom values (from active
        items), sorted."""
        cur = await self._db.execute(
            "SELECT DISTINCT category FROM vehicle_inventory_items"
            " WHERE account_id = ? AND is_active = 1",
            (account_id,),
        )
        used = {str(r[0]) for r in await cur.fetchall() if r[0]}
        custom = sorted(used - set(INVENTORY_CATEGORIES))
        return [*INVENTORY_CATEGORIES, *custom]

    async def get_assigned_driver_for_truck(
        self, account_id: int, truck_num: str,
    ) -> int | None:
        """The driver assigned to this unit RIGHT NOW (primary first) —
        snapshotted onto every inventory event for accountability."""
        row = await self.read_one(
            """
            SELECT u.id
              FROM users u
              JOIN driver_trucks dt ON dt.user_id = u.id
             WHERE u.account_id = ? AND dt.truck_num = ?
             ORDER BY dt.is_primary DESC, u.id
             LIMIT 1
            """,
            (account_id, truck_num),
        )
        return int(row["id"]) if row else None

    # ── writes (every write appends an event) ────────────────────

    async def _append_event(
        self, account_id: int, item_id: int, event_type: str, *,
        from_status: str | None = None, to_status: str | None = None,
        from_vehicle_id: int | None = None, to_vehicle_id: int | None = None,
        actor_user_id: int | None = None, driver_user_id: int | None = None,
        note: str = "", changes: dict[str, dict] | None = None,
    ) -> None:
        await self._db.execute(
            """
            INSERT INTO vehicle_inventory_events
                (account_id, item_id, event_type, from_status, to_status,
                 from_vehicle_id, to_vehicle_id, actor_user_id,
                 driver_user_id, note, changes, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (account_id, item_id, event_type, from_status, to_status,
             from_vehicle_id, to_vehicle_id, actor_user_id,
             driver_user_id, note[:500],
             json.dumps(changes) if changes else "", self._now()),
        )

    async def add_inventory_item(
        self, account_id: int, vehicle_id: int, *, category: str,
        label: str, identifier: str = "", notes: str = "",
        status: str = "installed",
        actor_user_id: int | None = None, driver_user_id: int | None = None,
    ) -> int:
        now = self._now()
        cur = await self._db.execute(
            """
            INSERT INTO vehicle_inventory_items
                (account_id, vehicle_id, category, label, identifier,
                 status, notes, installed_at, is_active, created_at, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, 1, ?, ?)
            """,
            (account_id, vehicle_id, category, label[:120], identifier[:120],
             status, notes[:1000], now, now, now),
        )
        item_id = cur.lastrowid
        await self._append_event(
            account_id, item_id, "installed",
            to_status=status, to_vehicle_id=vehicle_id,
            actor_user_id=actor_user_id, driver_user_id=driver_user_id,
        )
        await self._db.commit()
        return int(item_id)

    async def update_inventory_item(
        self, account_id: int, item_id: int, *,
        label: str | None = None, identifier: str | None = None,
        notes: str | None = None, category: str | None = None,
        actor_user_id: int | None = None, driver_user_id: int | None = None,
    ) -> bool:
        wanted: dict[str, str] = {}
        for col, val, cap in (
            ("label", label, 120), ("identifier", identifier, 120),
            ("notes", notes, 1000), ("category", category, 40),
        ):
            if val is not None:
                wanted[col] = val[:cap]
        if not wanted:
            return False
        # Read first, so the event can say what the words USED to be.
        # Without this an edit was trailed as the bare fact "edited",
        # which cannot answer "who changed this" for the only fields a
        # rename can hide a loss behind — the label and the serial.
        before = await self.get_inventory_item(account_id, item_id) or {}
        changes = {
            col: {"from": before.get(col), "to": val}
            for col, val in wanted.items() if before.get(col) != val
        }
        sets = [f"{col} = ?" for col in wanted]
        params: list[Any] = list(wanted.values())
        sets.append("updated_at = ?")
        params.append(self._now())
        params += [item_id, account_id]
        cur = await self._db.execute(
            f"UPDATE vehicle_inventory_items SET {', '.join(sets)} "
            "WHERE id = ? AND account_id = ? AND is_active = 1",
            tuple(params),
        )
        # A save that changed no value is not an edit.  It used to leave
        # an event anyway, which is how a trail fills with rows that say
        # nothing and teach a reader to skim past the ones that do.
        if cur.rowcount and changes:
            await self._append_event(
                account_id, item_id, "edited", changes=changes,
                actor_user_id=actor_user_id, driver_user_id=driver_user_id,
            )
        await self._db.commit()
        return bool(cur.rowcount)

    async def change_inventory_status(
        self, account_id: int, item_id: int, to_status: str, *,
        note: str = "", actor_user_id: int | None = None,
        driver_user_id: int | None = None,
    ) -> bool:
        item = await self.get_inventory_item(account_id, item_id)
        if not item or not item["is_active"]:
            return False
        await self._db.execute(
            "UPDATE vehicle_inventory_items SET status = ?, updated_at = ? "
            "WHERE id = ? AND account_id = ?",
            (to_status, self._now(), item_id, account_id),
        )
        await self._append_event(
            account_id, item_id, "status_change",
            from_status=item["status"], to_status=to_status,
            actor_user_id=actor_user_id, driver_user_id=driver_user_id,
            note=note,
        )
        await self._db.commit()
        return True

    async def verify_inventory_item(
        self, account_id: int, item_id: int, *,
        actor_user_id: int | None = None, driver_user_id: int | None = None,
    ) -> bool:
        now = self._now()
        cur = await self._db.execute(
            "UPDATE vehicle_inventory_items "
            "SET last_verified_at = ?, last_verified_by = ?, updated_at = ? "
            "WHERE id = ? AND account_id = ? AND is_active = 1",
            (now, actor_user_id, now, item_id, account_id),
        )
        if cur.rowcount:
            await self._append_event(
                account_id, item_id, "verified",
                actor_user_id=actor_user_id, driver_user_id=driver_user_id,
            )
        await self._db.commit()
        return bool(cur.rowcount)

    async def transfer_inventory_item(
        self, account_id: int, item_id: int, to_vehicle_id: int, *,
        note: str = "", actor_user_id: int | None = None,
        driver_user_id: int | None = None,
    ) -> bool:
        item = await self.get_inventory_item(account_id, item_id)
        if not item or not item["is_active"]:
            return False
        await self._db.execute(
            "UPDATE vehicle_inventory_items SET vehicle_id = ?, updated_at = ? "
            "WHERE id = ? AND account_id = ?",
            (to_vehicle_id, self._now(), item_id, account_id),
        )
        await self._append_event(
            account_id, item_id, "transferred",
            from_vehicle_id=item["vehicle_id"], to_vehicle_id=to_vehicle_id,
            actor_user_id=actor_user_id, driver_user_id=driver_user_id,
            note=note,
        )
        await self._db.commit()
        return True

    async def remove_inventory_item(
        self, account_id: int, item_id: int, *,
        note: str = "", actor_user_id: int | None = None,
        driver_user_id: int | None = None,
    ) -> bool:
        """Soft-remove — the item and its trail stay queryable (the trail
        IS the point of this feature)."""
        cur = await self._db.execute(
            "UPDATE vehicle_inventory_items SET is_active = 0, updated_at = ? "
            "WHERE id = ? AND account_id = ? AND is_active = 1",
            (self._now(), item_id, account_id),
        )
        if cur.rowcount:
            await self._append_event(
                account_id, item_id, "removed",
                actor_user_id=actor_user_id, driver_user_id=driver_user_id,
                note=note,
            )
        await self._db.commit()
        return bool(cur.rowcount)

    # ── retention ────────────────────────────────────────────────

    async def prune_inventory_events(
        self, account_id: int, days_keep: int,
    ) -> int:
        """Trim the growing events table; items themselves are never
        pruned (they're the live inventory, not history).  Cutoff is
        computed Python-side and compared as text — ISO strings sort
        chronologically, so no SQL date function touches the column
        (portable across the SQLite/Postgres translation layer)."""
        from datetime import datetime, timedelta, timezone
        cutoff = (
            datetime.now(timezone.utc) - timedelta(days=int(days_keep))
        ).isoformat()
        cur = await self._db.execute(
            "DELETE FROM vehicle_inventory_events "
            "WHERE account_id = ? AND created_at < ?",
            (account_id, cutoff),
        )
        await self._db.commit()
        return cur.rowcount or 0
