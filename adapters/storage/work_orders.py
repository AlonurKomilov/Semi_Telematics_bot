"""Work Orders CRUD mixin.

Owns ``work_orders``, ``work_order_parts``, and ``work_order_attachments``.
Linked to maintenance via ``maintenance_tasks.work_order_id`` — one work
order can close many maintenance tasks (a shop visit often closes oil +
tires + filter together).  See ``capabilities/work_orders/storage.py``
for the file-system layout shared by every storage backend.
"""

from __future__ import annotations

import logging
from typing import Any, Optional

logger = logging.getLogger("bot.storage")

from adapters.storage.service_taxonomy import system_rollup_case

#: THE DELEGATION RULE as SQL, rendered ONCE from
#: ``DELEGATING_SYSTEMS``.  It used to be written out by hand at the
#: three sites below (the SELECT, the GROUP BY, and the per-assembly
#: WHERE); adding a fifth delegating system would have updated the
#: frozenset and left all three literals silently stale — misfiling
#: spend with no error anywhere.  ``st`` / ``al`` are the service-task
#: and assembly-library aliases every one of those queries uses.
_SYSTEM_ROLLUP = system_rollup_case("st.system_key", "al.system_key")

# Work-order lifecycle (Fleetio-standard): open → in_progress →
# completed.  No cancelled/void state by owner decision — a mistaken WO
# is deleted, not soft-voided.  Money lives in the separate
# payment_status field.
_WO_STATUSES = ("open", "in_progress", "completed")

# Legacy values accepted at the write boundary for one release so stale
# dashboard bundles don't 422 mid-deploy (draft/submitted predate the
# Fleetio vocab; closed/void were the brief interim before completed).
_LEGACY_WO_STATUS = {
    "draft": "open", "submitted": "completed",
    "closed": "completed", "void": "completed",
}


def normalize_wo_status(value: Any) -> Optional[str]:
    """Map a caller's status onto the current vocabulary (legacy
    aliases translated); None when it isn't a recognized value so the
    caller can leave the field untouched."""
    s = str(value or "").strip().lower()
    s = _LEGACY_WO_STATUS.get(s, s)
    return s if s in _WO_STATUSES else None


# Datatruck's own work-order statuses → ours.  Synced rows are shop
# INVOICES for work that already happened, so an unrecognized/blank
# status defaults to COMPLETED (a finished repair), never open — else a
# one-time backfill would flood the working set with phantom to-dos.
_DATATRUCK_STATUS = {
    "completed": "completed", "complete": "completed", "closed": "completed",
    "paid": "completed", "invoiced": "completed",
    "open": "open", "new": "open", "pending": "open",
    "in progress": "in_progress", "in_progress": "in_progress",
    "on hold": "in_progress", "on_hold": "in_progress",
}


def map_datatruck_status(value: Any) -> str:
    return _DATATRUCK_STATUS.get(str(value or "").strip().lower(), "completed")


class WorkOrdersMixin:

    # ── Core CRUD ────────────────────────────────────────────────────────────

    async def add_work_order(
        self, account_id: int, company_code: str,
        vehicle_name: str, vendor_name: str,
        *,
        vehicle_id: str = "",
        vehicle_type: str = "",
        vendor_address: str = "",
        vendor_phone: str = "",
        vendor_id: Optional[int] = None,
        service_date: Optional[str] = None,
        odometer_at_service: Optional[float] = None,
        engine_hours_at_service: Optional[float] = None,
        labor_cost: float = 0.0,
        parts_cost: float = 0.0,
        tax_amount: float = 0.0,
        fee_amount: float = 0.0,
        total_cost: float = 0.0,
        invoice_number: str = "",
        payment_method: str = "",
        payment_status: str = "unpaid",
        status: str = "open",
        repair_priority: str = "",
        complaint: str = "",
        cause: str = "",
        correction: str = "",
        notes: str = "",
        assigned_to: str = "",
        created_by: int = 0,
    ) -> int:
        now = self._now()
        cur = await self._db.execute(
            """INSERT INTO work_orders
               (account_id, company_code, vehicle_id, vehicle_name,
                vehicle_type, vendor_name, vendor_address, vendor_phone,
                vendor_id, service_date, odometer_at_service,
                engine_hours_at_service,
                labor_cost, parts_cost, tax_amount, fee_amount, total_cost,
                invoice_number, payment_method, payment_status,
                status, repair_priority, complaint, cause, correction,
                notes, assigned_to, created_by, created_at, updated_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?,
                       ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (account_id, company_code, vehicle_id, vehicle_name,
             vehicle_type, vendor_name, vendor_address, vendor_phone,
             vendor_id, service_date, odometer_at_service,
             engine_hours_at_service,
             labor_cost, parts_cost, tax_amount, fee_amount, total_cost,
             invoice_number, payment_method, payment_status,
             status, repair_priority, complaint, cause, correction,
             notes, assigned_to, created_by, now, now),
        )
        await self._db.commit()
        return cur.lastrowid

    async def _wo_vehicle_resolver(
        self, account_id: int,
        vehicle_lookup: dict[str, tuple[str, str]] | None,
    ) -> dict[str, tuple[str, str]]:
        """plate/unit (lower) → (canonical unit, type).  Prefer a caller-
        supplied lookup (built live by the sync engine); else read the
        synced datatruck_trucks/trailers tables."""
        if vehicle_lookup is not None:
            return vehicle_lookup
        resolver: dict[str, tuple[str, str]] = {}
        for tbl, vtype in (("datatruck_trucks", "truck"),
                           ("datatruck_trailers", "trailer")):
            try:
                rc = await self._db.execute(
                    f"SELECT unit_number, plate_number FROM {tbl} "
                    "WHERE account_id = ?", (account_id,),
                )
                for vrow in (dict(x) for x in await rc.fetchall()):
                    unit = str(vrow.get("unit_number") or "").strip()
                    if not unit:
                        continue
                    for key in (unit, str(vrow.get("plate_number") or "").strip()):
                        if key:
                            resolver.setdefault(key.lower(), (unit, vtype))
            except Exception:
                pass
        return resolver

    async def _wo_company_resolver(
        self, account_id: int,
    ) -> dict[str, tuple[str, str]]:
        """carrier id (MC or USDOT, stripped) → (company code, name).

        Built from the account's OWN Companies, which own the MC/DOT — so
        a synced work order's mc_number matches to the right sub-company
        locally, no integration roster needed."""
        resolver: dict[str, tuple[str, str]] = {}
        try:
            rc = await self._db.execute(
                "SELECT code, display_name, mc_number, usdot_number "
                "FROM companies WHERE account_id = ? AND is_active = 1",
                (account_id,),
            )
            for row in (dict(x) for x in await rc.fetchall()):
                code = str(row.get("code") or "")
                name = str(row.get("display_name") or "") or code
                for cid in (row.get("mc_number"), row.get("usdot_number")):
                    cid = str(cid or "").strip()
                    if cid:
                        resolver.setdefault(cid, (code, name))
        except Exception:
            pass
        return resolver

    async def project_external_work_orders(
        self,
        account_id: int,
        rows: list[dict[str, Any]],
        *,
        source: str = "datatruck",
        vehicle_lookup: dict[str, tuple[str, str]] | None = None,
    ) -> int:
        """Project integration work orders (Datatruck) onto the module
        ``work_orders`` table so synced shop invoices appear on the Work
        Orders page beside the operator's hand-entered ones.

        The module table is the SSOT; the integration ENRICHES it — the
        same inversion the vehicle registry uses.  Reconciled on
        ``(account_id, source, external_id)``:

          * **new** external id → INSERT the full Datatruck record
            (vehicle, vendor, location, invoice #, payment type, tax,
            total, the note, and its line items), tagged ``source`` +
            ``external_id``, ``status='submitted'`` (a real upstream
            invoice, not a local draft).
          * **known** external id → REFRESH the Datatruck-owned header
            fields (vehicle, vendor, invoice #, date, tax, totals).
            Operator workflow fields (``status``, ``payment_status``,
            ``notes``) and the line-item ``parts`` are seeded once on
            insert and then PRESERVED, so a re-sync never clobbers what
            the operator changed.

        Accepts the sync normalizer's enriched work-order shape
        (``external_id``, ``vehicle_unit``, ``invoice_number``,
        ``vendor_name``, ``vendor_address``, ``vendor_phone``,
        ``payment_method``, ``note``, ``tax_amount``, ``total_cost``,
        ``line_items``).  Rows without an external id are skipped.

        Returns the number of rows inserted-or-refreshed.
        """
        if not rows:
            return 0
        # Resolver: a WO references the asset by plate (the list shape) or by
        # unit; either way we want the canonical UNIT number on the page (not
        # the plate) and the truck-vs-trailer type.  The sync engine passes a
        # ready ``vehicle_lookup`` (built live from the rosters, no separate
        # trucks/trailers sync required).  Absent it, fall back to whatever's
        # in the synced datatruck_trucks/trailers tables; empty → keep the raw
        # value (graceful fallback).
        resolver = await self._wo_vehicle_resolver(account_id, vehicle_lookup)
        # Match the WO's carrier MC number to one of the account's
        # Companies (which own the MC/DOT) → the company code.
        company_resolver = await self._wo_company_resolver(account_id)
        cur = await self._db.execute(
            "SELECT id, external_id FROM work_orders "
            "WHERE account_id = ? AND source = ? AND external_id <> ''",
            (account_id, source),
        )
        existing = {
            str(r["external_id"]): r["id"]
            for r in (dict(x) for x in await cur.fetchall())
        }

        now = self._now()
        written = 0
        async with self.transaction():
            for r in rows:
                ext = str(r.get("external_id") or "").strip()
                if not ext:
                    continue

                def _f(key: str) -> float:
                    v = r.get(key)
                    return float(v) if v not in (None, "") else 0.0

                _raw_unit = str(r.get("vehicle_unit") or "")
                _resolved = resolver.get(_raw_unit.strip().lower())
                vehicle_name = _resolved[0] if _resolved else _raw_unit
                vehicle_type = (
                    str(r.get("vehicle_type") or "")
                    or (_resolved[1] if _resolved else "")
                )
                # Match the carrier MC number to one of the account's
                # companies → its code (blank when no company owns that MC).
                _co = company_resolver.get(str(r.get("mc_number") or "").strip())
                company_code = _co[0] if _co else ""
                assigned_to = str(r.get("assigned_to") or "")
                invoice_number = str(r.get("invoice_number") or "")
                # The source system's human-readable reference
                # (Datatruck "WO-00983") — shown as a hover tip on the
                # Source badge.  Distinct from external_id (internal id).
                external_number = str(r.get("number") or "")
                vendor_name = str(r.get("vendor_name") or "")
                vendor_address = str(r.get("vendor_address") or "")
                vendor_phone = str(r.get("vendor_phone") or "")
                # Vendor registry link: exact-normalized resolve-or-create
                # (alias-aware post-merge).  The snapshot columns above
                # still store what the invoice said; the id is the
                # analytical spine.  Blank vendor → no link.
                _vend = await self.resolve_or_create_vendor(
                    account_id, vendor_name,
                    address=vendor_address, phone=vendor_phone,
                    email=str(r.get("vendor_email") or ""),
                ) if vendor_name else None
                vendor_id = _vend["id"] if _vend else None
                payment_method = str(r.get("payment_method") or "")
                service_date = str(r.get("opened_at") or "") or None
                odometer = r.get("odometer")
                odometer = float(odometer) if odometer not in (None, "") else None
                total = _f("total_cost")
                tax = _f("tax_amount")
                items = r.get("line_items") or []
                # Each line item is stored as a part row carrying its FULL
                # line total (parts + labor for that line), mirroring
                # Datatruck's "WO Line Items" table.  The header therefore
                # rolls ALL line totals into parts_cost and leaves
                # labor_cost at 0 — otherwise the dashboard, which computes
                # Total = labor_cost + Σ(part rows) + tax, would count the
                # labor lines twice (once in the rows, once in labor_cost).
                # With labor_cost=0 the recomputed Total equals Datatruck's
                # own total_price (subtotal + tax).
                parts_cost = round(
                    sum(it.get("total") or 0.0 for it in items), 2,
                )
                labor_cost = 0.0
                # Seed the initial payment status from Datatruck's
                # balance / paid_amount (the only payment signal the
                # openapi token can see — payment_type/invoice live in the
                # v2 detail view it can't reach).  paid when the balance is
                # cleared or the paid amount covers the total; else unpaid.
                # Seeded on insert AND ratcheted forward on refresh: for a
                # synced invoice the money actually moves in Datatruck
                # (EFS/bank), so a cleared balance upstream MARKS the row
                # paid here — one-way only (a paid or void row is never
                # demoted back to unpaid by a lagging feed).  The audit
                # case: 110 rows sat "Unpaid" forever because payment was
                # seed-once while their upstream balance had long cleared.
                _bal = r.get("balance")
                _paid = r.get("paid_amount")
                payment_status = "unpaid"
                if total and total > 0:
                    if _bal not in (None, "") and float(_bal) <= 0.005:
                        payment_status = "paid"
                    elif _paid not in (None, "") and float(_paid) + 0.005 >= total:
                        payment_status = "paid"
                # Lifecycle mapped from Datatruck's own status (unknown /
                # blank → closed: these are completed shop invoices).
                wo_status = map_datatruck_status(r.get("status"))

                wo_id = existing.get(ext)
                if wo_id is not None:
                    # Refresh Datatruck-owned fields only, with TWO
                    # one-way ratchets so a lagging feed never regresses
                    # an operator's or an already-final state:
                    #   payment: unpaid/partial → paid, never back.
                    #   status:  open/in_progress → completed when
                    #            upstream finishes; never reopened.
                    #            (Without this a WO synced while "in
                    #            progress" would never auto-finish and
                    #            would inflate the active set forever.)
                    await self._db.execute(
                        "UPDATE work_orders SET vehicle_name = ?, "
                        "vehicle_type = ?, company_code = ?, assigned_to = ?, "
                        "invoice_number = ?, external_number = ?, vendor_name = ?, "
                        "vendor_address = ?, vendor_phone = ?, vendor_id = ?, "
                        "payment_method = ?, service_date = ?, "
                        "odometer_at_service = ?, labor_cost = ?, "
                        "parts_cost = ?, tax_amount = ?, total_cost = ?, "
                        "payment_status = CASE "
                        "    WHEN payment_status IN ('paid', 'void') THEN payment_status "
                        "    WHEN ? = 'paid' THEN 'paid' "
                        "    ELSE payment_status END, "
                        "status = CASE "
                        "    WHEN ? = 'completed' THEN 'completed' "
                        "    ELSE status END, "
                        "updated_at = ? WHERE id = ? AND account_id = ?",
                        (vehicle_name, vehicle_type, company_code, assigned_to,
                         invoice_number, external_number, vendor_name,
                         vendor_address, vendor_phone, vendor_id, payment_method,
                         service_date, odometer, labor_cost, parts_cost,
                         tax, total, payment_status, wo_status, now, wo_id, account_id),
                    )
                    written += 1
                    continue

                # New synced invoice — seed the full record.  ON CONFLICT
                # guards against an upstream page repeating an id within
                # one batch (the pre-loaded map can't see same-tx inserts).
                await self._db.execute(
                    """INSERT INTO work_orders
                       (account_id, company_code, vehicle_id, vehicle_name,
                        vehicle_type, vendor_name, vendor_address, vendor_phone,
                        vendor_id, service_date, odometer_at_service,
                        engine_hours_at_service,
                        labor_cost, parts_cost, tax_amount, total_cost,
                        invoice_number, external_number, payment_method, payment_status,
                        status, notes, assigned_to, source, external_id,
                        created_by, created_at, updated_at)
                       VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?,
                               ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                       ON CONFLICT (account_id, source, external_id)
                       WHERE external_id <> '' DO NOTHING""",
                    (account_id, company_code, "", vehicle_name,
                     vehicle_type, vendor_name, vendor_address, vendor_phone,
                     vendor_id, service_date, odometer, None,
                     labor_cost, parts_cost, tax, total,
                     invoice_number, external_number, payment_method, payment_status,
                     wo_status, str(r.get("note") or ""), assigned_to, source, ext,
                     0, now, now),
                )
                # Resolve the freshly-inserted id and seed its line items.
                cur2 = await self._db.execute(
                    "SELECT id FROM work_orders "
                    "WHERE account_id = ? AND source = ? AND external_id = ?",
                    (account_id, source, ext),
                )
                new_row = await cur2.fetchone()
                if new_row and items:
                    new_id = dict(new_row)["id"]
                    for it in items:
                        _pname = str(it.get("name") or "")
                        # Catalog auto-link (alias-aware) — same
                        # contract as the vendor link above.
                        _part = await self.resolve_or_create_part(
                            account_id, _pname,
                        ) if _pname else None
                        await self._db.execute(
                            """INSERT INTO work_order_parts
                               (work_order_id, part_name, part_number,
                                quantity, unit_cost, total_cost,
                                warranty_months, part_id, notes)
                               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                            (new_id, _pname, "",
                             it.get("quantity") or 0.0,
                             it.get("unit_cost") or 0.0,
                             it.get("total") or 0.0, 0,
                             _part["id"] if _part else None, ""),
                        )
                written += 1
        return written

    async def plan_external_work_orders(
        self,
        account_id: int,
        rows: list[dict[str, Any]],
        *,
        source: str = "datatruck",
        vehicle_lookup: dict[str, tuple[str, str]] | None = None,
    ) -> dict[str, Any]:
        """Read-only dry-run of ``project_external_work_orders``.

        Keyed on ``external_id`` (the stable upstream id), so each
        incoming WO is either ``new`` (insert) or ``update`` (refresh the
        Datatruck-owned fields on a row already synced).  No duplicate
        class — the external id is unambiguous.
        """
        # Pull the CURRENT stored values for the Datatruck-owned fields so
        # an update can show before → after, not just "will update".
        cur = await self._db.execute(
            "SELECT external_id, vendor_name, invoice_number, vehicle_name, "
            "payment_method, total_cost, company_code FROM work_orders "
            "WHERE account_id = ? AND source = ? AND external_id <> ''",
            (account_id, source),
        )
        existing = {
            str(dict(x)["external_id"]): dict(x) for x in await cur.fetchall()
        }
        resolver = await self._wo_vehicle_resolver(account_id, vehicle_lookup)
        company_resolver = await self._wo_company_resolver(account_id)
        # company code → display name, for the Company before → after.
        code_to_name = {v[0]: v[1] for v in company_resolver.values()}

        def _num(v: Any) -> float:
            try:
                return round(float(v), 2) if v not in (None, "") else 0.0
            except (TypeError, ValueError):
                return 0.0

        new: list[dict] = []
        update: list[dict] = []
        for r in rows:
            ext = str(r.get("external_id") or "").strip()
            if not ext:
                continue
            raw_unit = str(r.get("vehicle_unit") or "")
            resolved = resolver.get(raw_unit.strip().lower())
            vehicle = resolved[0] if resolved else raw_unit
            _co = company_resolver.get(str(r.get("mc_number") or "").strip())
            base = {
                "external_id": ext,
                "number": str(r.get("number") or ""),
                "vendor": str(r.get("vendor_name") or ""),
                "vehicle": vehicle,
                "company": _co[1] if _co else "",
                "total": r.get("total_cost"),
            }
            if ext not in existing:
                new.append(base)
                continue
            # Compute per-field before → after on the refreshed fields.
            cur_row = existing[ext]
            pairs = [
                ("Vendor", cur_row.get("vendor_name"), r.get("vendor_name")),
                ("Vehicle", cur_row.get("vehicle_name"), vehicle),
                ("Invoice #", cur_row.get("invoice_number"), r.get("invoice_number")),
                ("Payment", cur_row.get("payment_method"), r.get("payment_method")),
            ]
            changes: list[dict] = []
            for field, was, now in pairs:
                was_s, now_s = str(was or ""), str(now or "")
                if was_s != now_s:
                    changes.append({"field": field, "from": was_s, "to": now_s})
            was_t, now_t = _num(cur_row.get("total_cost")), _num(r.get("total_cost"))
            if abs(was_t - now_t) > 0.005:
                changes.append({
                    "field": "Total", "from": f"{was_t:.2f}", "to": f"{now_t:.2f}",
                })
            # Company can newly populate once the operator adds MC numbers —
            # compare the stored code's name to the freshly-matched one.
            cur_code = str(cur_row.get("company_code") or "")
            new_code = _co[0] if _co else ""
            if cur_code != new_code:
                changes.append({
                    "field": "Company",
                    "from": code_to_name.get(cur_code, cur_code),
                    "to": code_to_name.get(new_code, new_code),
                })
            update.append({**base, "changes": changes})

        changed = sum(1 for u in update if u["changes"])
        return {
            "kind": "work_orders",
            "new": new,
            "update": update,
            "counts": {
                "new": len(new), "update": len(update),
                "changed": changed, "total": len(new) + len(update),
            },
        }

    async def get_work_order(
        self, work_order_id: int, account_id: int = 0,
    ) -> Optional[dict]:
        if account_id:
            cur = await self._db.execute(
                "SELECT * FROM work_orders WHERE id = ? AND account_id = ?",
                (work_order_id, account_id),
            )
        else:
            cur = await self._db.execute(
                "SELECT * FROM work_orders WHERE id = ?", (work_order_id,),
            )
        row = await cur.fetchone()
        return dict(row) if row else None

    async def list_work_orders(
        self, account_id: int,
        *,
        status: Optional[str] = None,
        payment_status: Optional[str] = None,
        vehicle_name: Optional[str] = None,
    ) -> list[dict]:
        """List work orders for an account with optional filters.

        Ordered by service_date DESC (newest shop visits first) so the
        dashboard's default view matches operator expectation.  NULL
        service_date rows (drafts) sort last via the COALESCE trick.
        """
        q = "SELECT * FROM work_orders WHERE account_id = ?"
        params: list = [account_id]
        if status:
            q += " AND status = ?"
            params.append(status)
        if payment_status:
            q += " AND payment_status = ?"
            params.append(payment_status)
        if vehicle_name:
            q += " AND vehicle_name = ?"
            params.append(vehicle_name)
        q += " ORDER BY COALESCE(service_date, '') DESC, id DESC"
        cur = await self._db.execute(q, params)
        rows = await cur.fetchall()
        return [dict(r) for r in rows]

    async def update_work_order(
        self, work_order_id: int, account_id: int = 0, **kwargs,
    ) -> bool:
        """Update mutable work-order fields.

        Allowlist intentionally excludes ``id``, ``account_id``, and
        ``created_*`` so accidental PUT payloads can't corrupt
        bookkeeping fields.  ``updated_at`` always advances on a
        successful update.
        """
        allowed = {
            "company_code", "vehicle_id", "vehicle_name", "vehicle_type",
            "vendor_name", "vendor_address", "vendor_phone", "vendor_id",
            "service_date", "odometer_at_service", "engine_hours_at_service",
            "labor_cost", "parts_cost", "tax_amount", "fee_amount", "total_cost",
            "invoice_number", "payment_method", "payment_status",
            "status", "repair_priority", "complaint", "cause", "correction",
            "notes", "assigned_to",
        }
        updates = {k: v for k, v in kwargs.items() if k in allowed}
        if not updates:
            return False
        updates["updated_at"] = self._now()
        set_clause = ", ".join(f"{k} = ?" for k in updates)
        values = list(updates.values())
        if account_id:
            values += [work_order_id, account_id]
            cur = await self._db.execute(
                f"UPDATE work_orders SET {set_clause} "
                f"WHERE id = ? AND account_id = ?", values,
            )
        else:
            values.append(work_order_id)
            cur = await self._db.execute(
                f"UPDATE work_orders SET {set_clause} WHERE id = ?", values,
            )
        await self._db.commit()
        return cur.rowcount > 0

    async def delete_work_order(
        self, work_order_id: int, account_id: int = 0,
    ) -> int:
        """Delete a work order plus its parts and attachments.

        Returns the number of work-order rows deleted (0 or 1).  The
        caller is responsible for removing physical files from the
        object store before invoking this — we don't reach into the
        storage backend from the adapter so the DB layer stays
        backend-agnostic.
        """
        # Cascade child rows first to keep referential intent clean.
        await self._db.execute(
            "DELETE FROM work_order_parts WHERE work_order_id = ?",
            (work_order_id,),
        )
        # Labor lines have no FK (migration 153) — without this DELETE
        # they'd survive as orphans and keep counting in the
        # labor_by_service_task cost rollups.
        await self._db.execute(
            "DELETE FROM work_order_labor WHERE work_order_id = ?",
            (work_order_id,),
        )
        await self._db.execute(
            "DELETE FROM work_order_attachments WHERE work_order_id = ?",
            (work_order_id,),
        )
        # Clear maintenance_tasks.work_order_id back-references so
        # closed tasks become "completed without a linked work order"
        # rather than pointing at a tombstone.
        await self._db.execute(
            "UPDATE maintenance_tasks SET work_order_id = NULL "
            "WHERE work_order_id = ?",
            (work_order_id,),
        )
        if account_id:
            cur = await self._db.execute(
                "DELETE FROM work_orders WHERE id = ? AND account_id = ?",
                (work_order_id, account_id),
            )
        else:
            cur = await self._db.execute(
                "DELETE FROM work_orders WHERE id = ?", (work_order_id,),
            )
        await self._db.commit()
        return cur.rowcount or 0

    # ── Parts (line items) ───────────────────────────────────────────────────

    async def add_work_order_part(
        self, work_order_id: int,
        *,
        part_name: str,
        part_number: str = "",
        quantity: float = 1.0,
        unit_cost: float = 0.0,
        total_cost: float = 0.0,
        warranty_months: int = 0,
        service_task: str = "",
        part_id: Optional[int] = None,
        notes: str = "",
    ) -> int:
        # Dual-write the service_tasks reference beside the legacy tag
        # (see adapters/storage/service_tasks.py).  Parts don't carry
        # account_id, so it comes from the parent work order — and only
        # when there's actually a tag to resolve.
        service_task_id = None
        if service_task:
            try:
                acur = await self._db.execute(
                    "SELECT account_id FROM work_orders WHERE id = ?",
                    (work_order_id,),
                )
                arow = await acur.fetchone()
                if arow:
                    service_task_id = await self.resolve_service_task_id(
                        int(dict(arow)["account_id"]), service_task,
                    )
            except Exception:
                logger.warning("service_task resolve failed for part tag %r",
                               service_task, exc_info=True)

        cur = await self._db.execute(
            """INSERT INTO work_order_parts
               (work_order_id, part_name, part_number, quantity,
                unit_cost, total_cost, warranty_months, service_task,
                service_task_id, part_id, notes)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (work_order_id, part_name, part_number, quantity,
             unit_cost, total_cost, warranty_months, service_task,
             service_task_id, part_id, notes),
        )
        await self._db.commit()
        return cur.lastrowid

    @staticmethod
    def _resolve_line_task(row: dict) -> dict:
        """Let the service_tasks REFERENCE win over the legacy tag.

        Same choke-point trick the maintenance reads use: the work-order
        editor groups lines by this string and the per-task cost report
        keys on it, so resolving here means both follow the reference
        without either of them changing.  A line written before the
        reference existed keeps its stored tag.
        """
        resolved = row.pop("_resolved_service_task", None)
        if resolved:
            row["service_task"] = resolved
        return row

    _LINE_TASK_SELECT = (
        ", COALESCE(NULLIF(st.canonical_key, ''), st.name) "
        "  AS _resolved_service_task "
    )

    async def list_work_order_parts(self, work_order_id: int) -> list[dict]:
        cur = await self._db.execute(
            "SELECT p.*" + self._LINE_TASK_SELECT
            + "FROM work_order_parts p "
            "LEFT JOIN service_tasks st ON st.id = p.service_task_id "
            "WHERE p.work_order_id = ? ORDER BY p.id",
            (work_order_id,),
        )
        return [self._resolve_line_task(dict(r)) for r in await cur.fetchall()]

    async def list_work_order_parts_bulk(
        self, work_order_ids: list[int],
    ) -> dict[int, list[dict]]:
        """Bulk variant — one query returning ``{work_order_id: [parts...]}``.

        Replaces the N+1 pattern where ``dot_binder`` called
        ``list_work_order_parts(wo_id)`` inside a per-work-order loop.
        """
        if not work_order_ids:
            return {}
        placeholders = ",".join("?" * len(work_order_ids))
        cur = await self._db.execute(
            f"SELECT * FROM work_order_parts "
            f"WHERE work_order_id IN ({placeholders}) "
            f"ORDER BY work_order_id, id",
            tuple(work_order_ids),
        )
        out: dict[int, list[dict]] = {wo_id: [] for wo_id in work_order_ids}
        for r in await cur.fetchall():
            row = dict(r)
            out.setdefault(int(row["work_order_id"]), []).append(row)
        return out

    async def delete_work_order_part(self, part_id: int) -> bool:
        cur = await self._db.execute(
            "DELETE FROM work_order_parts WHERE id = ?", (part_id,),
        )
        await self._db.commit()
        return cur.rowcount > 0

    # ── Labor lines (Tier-2 B1) ──────────────────────────────────────────────
    #
    # Optional itemized labor.  Unlike parts (whose costs the form
    # computes client-side into parts_cost), labor lines RECOMPUTE the
    # parent's labor_cost + total_cost server-side on every change:
    # lines present → labor_cost is derived truth; no lines → the
    # manual scalar behaves exactly as before.

    async def _recompute_labor_cost(
        self, work_order_id: int, account_id: int,
    ) -> None:
        cur = await self._db.execute(
            "SELECT COUNT(*) AS n, COALESCE(SUM(total_cost), 0) AS s "
            "FROM work_order_labor "
            "WHERE work_order_id = ? AND account_id = ?",
            (work_order_id, account_id),
        )
        row = dict(await cur.fetchone())
        if int(row["n"]) == 0:
            return  # last line deleted → leave the scalar as the user set it
        labor = round(float(row["s"]), 2)
        # Total recomputed in Python — Postgres ROUND(double, int)
        # doesn't exist, and this keeps the SQL dialect-free.
        wcur = await self._db.execute(
            "SELECT parts_cost, tax_amount, fee_amount FROM work_orders "
            "WHERE id = ? AND account_id = ?",
            (work_order_id, account_id),
        )
        wrow = await wcur.fetchone()
        if not wrow:
            return
        w = dict(wrow)
        total = round(
            labor + float(w["parts_cost"] or 0) + float(w["tax_amount"] or 0)
            + float(w.get("fee_amount") or 0), 2,
        )
        await self._db.execute(
            "UPDATE work_orders SET labor_cost = ?, total_cost = ? "
            "WHERE id = ? AND account_id = ?",
            (labor, total, work_order_id, account_id),
        )
        await self._db.commit()

    async def add_work_order_labor(
        self, work_order_id: int, account_id: int,
        *,
        description: str,
        hours: float = 0.0,
        rate: float = 0.0,
        total_cost: float = 0.0,
        service_task: str = "",
    ) -> int:
        """Add a labor line.  ``total_cost`` falls back to hours×rate
        when not supplied explicitly (flat-rate invoices send it)."""
        if not total_cost and hours and rate:
            total_cost = round(hours * rate, 2)
        # Dual-write the service_tasks reference beside the legacy tag.
        service_task_id = None
        if service_task:
            try:
                service_task_id = await self.resolve_service_task_id(
                    account_id, service_task,
                )
            except Exception:
                logger.warning("service_task resolve failed for labor tag %r",
                               service_task, exc_info=True)
        cur = await self._db.execute(
            """INSERT INTO work_order_labor
               (account_id, work_order_id, service_task, service_task_id,
                description, hours, rate, total_cost, created_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (account_id, work_order_id, service_task, service_task_id,
             description, hours, rate, total_cost, self._now()),
        )
        await self._db.commit()
        await self._recompute_labor_cost(work_order_id, account_id)
        return cur.lastrowid

    async def list_work_order_labor(
        self, work_order_id: int, account_id: int,
    ) -> list[dict]:
        cur = await self._db.execute(
            "SELECT l.*" + self._LINE_TASK_SELECT
            + "FROM work_order_labor l "
            "LEFT JOIN service_tasks st ON st.id = l.service_task_id "
            "WHERE l.work_order_id = ? AND l.account_id = ? ORDER BY l.id",
            (work_order_id, account_id),
        )
        return [self._resolve_line_task(dict(r)) for r in await cur.fetchall()]

    async def delete_work_order_labor(
        self, line_id: int, account_id: int, work_order_id: int,
    ) -> bool:
        # work_order_id is part of the predicate, not just bookkeeping:
        # the route authorizes VISIBILITY of the work order in the URL,
        # so the line must actually belong to that work order — otherwise
        # a company-scoped user could delete another work order's line
        # by nesting its id under one they can see.
        cur = await self._db.execute(
            "SELECT work_order_id FROM work_order_labor "
            "WHERE id = ? AND account_id = ? AND work_order_id = ?",
            (line_id, account_id, work_order_id),
        )
        row = await cur.fetchone()
        if not row:
            return False
        await self._db.execute(
            "DELETE FROM work_order_labor "
            "WHERE id = ? AND account_id = ? AND work_order_id = ?",
            (line_id, account_id, work_order_id),
        )
        await self._db.commit()
        await self._recompute_labor_cost(work_order_id, account_id)
        return True

    async def labor_by_service_task(
        self, account_id: int, since: Optional[str] = None,
    ) -> dict[str, float]:
        """Labor spend per service_task — merged into the per-task cost
        report so each kind of work shows parts AND labor."""
        q = (
            "SELECT COALESCE(st.name, NULLIF(l.service_task, ''), 'untagged') "
            "         AS service_task, "
            "       SUM(l.total_cost) AS labor_spent "
            "FROM work_order_labor l "
            "JOIN work_orders w ON w.id = l.work_order_id "
            "     AND w.account_id = l.account_id "
            "LEFT JOIN service_tasks st ON st.id = l.service_task_id "
            "WHERE l.account_id = ? AND w.service_date IS NOT NULL AND w.status != 'void' AND w.payment_status != 'void'"
        )
        params: list = [account_id]
        if since:
            q += " AND w.service_date >= ?"
            params.append(since)
        q += (
            " GROUP BY COALESCE(st.name, NULLIF(l.service_task, ''), 'untagged')"
        )
        cur = await self._db.execute(q, params)
        return {
            str(r["service_task"]): float(r["labor_spent"] or 0)
            for r in (dict(x) for x in await cur.fetchall())
        }

    # ── Attachments ──────────────────────────────────────────────────────────

    async def add_work_order_attachment(
        self, work_order_id: int,
        *,
        file_path: str,
        file_name: str,
        file_size: int = 0,
        content_type: str = "",
        kind: str = "other",
        uploaded_by: int = 0,
    ) -> int:
        """Record an attachment.  ``file_path`` is whatever the
        ``ObjectStore`` returned as the locator — for ``DiskObjectStore``
        that's the relative path under the store root; for
        ``GDriveObjectStore`` (planned) it's the Drive file ID.  Either
        way the adapter doesn't care — the route handler hands the right
        store the right thing to retrieve it later.
        """
        now = self._now()
        cur = await self._db.execute(
            """INSERT INTO work_order_attachments
               (work_order_id, file_path, file_name, file_size,
                content_type, kind, uploaded_by, uploaded_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
            (work_order_id, file_path, file_name, file_size,
             content_type, kind, uploaded_by, now),
        )
        await self._db.commit()
        return cur.lastrowid

    async def list_work_order_attachments(
        self, work_order_id: int,
    ) -> list[dict]:
        cur = await self._db.execute(
            "SELECT * FROM work_order_attachments WHERE work_order_id = ? "
            "ORDER BY uploaded_at",
            (work_order_id,),
        )
        return [dict(r) for r in await cur.fetchall()]

    async def count_work_order_attachments_bulk(
        self, work_order_ids: list[int],
    ) -> dict[int, int]:
        """Bulk count by work_order_id — one grouped query replaces N
        per-WO ``list_work_order_attachments`` calls when the caller
        only needs the count (DOT-binder summary)."""
        if not work_order_ids:
            return {}
        placeholders = ",".join("?" * len(work_order_ids))
        cur = await self._db.execute(
            f"SELECT work_order_id, COUNT(*) AS c "
            f"FROM work_order_attachments "
            f"WHERE work_order_id IN ({placeholders}) "
            f"GROUP BY work_order_id",
            tuple(work_order_ids),
        )
        out: dict[int, int] = {wo_id: 0 for wo_id in work_order_ids}
        for r in await cur.fetchall():
            row = dict(r)
            out[int(row["work_order_id"])] = int(row["c"])
        return out

    async def get_work_order_attachment(
        self, attachment_id: int,
    ) -> Optional[dict]:
        cur = await self._db.execute(
            "SELECT * FROM work_order_attachments WHERE id = ?",
            (attachment_id,),
        )
        row = await cur.fetchone()
        return dict(row) if row else None

    async def delete_work_order_attachment(self, attachment_id: int) -> bool:
        cur = await self._db.execute(
            "DELETE FROM work_order_attachments WHERE id = ?",
            (attachment_id,),
        )
        await self._db.commit()
        return cur.rowcount > 0

    # ── Maintenance-task linking ─────────────────────────────────────────────

    async def link_maintenance_tasks_to_work_order(
        self, account_id: int, work_order_id: int, task_ids: list[int],
    ) -> int:
        """Set ``maintenance_tasks.work_order_id`` for many tasks at once.

        Returns the number of tasks linked.  Chunked at 500 to stay
        under SQLite's parameter limit.  Account-scoped to prevent
        cross-tenant attempts.
        """
        if not task_ids:
            return 0
        touched = 0
        for i in range(0, len(task_ids), 500):
            chunk = task_ids[i:i + 500]
            placeholders = ",".join("?" * len(chunk))
            cur = await self._db.execute(
                f"UPDATE maintenance_tasks SET work_order_id = ? "
                f"WHERE account_id = ? AND id IN ({placeholders})",
                (work_order_id, account_id, *chunk),
            )
            touched += cur.rowcount or 0
        await self._db.commit()
        return touched

    async def list_tasks_for_work_order(
        self, work_order_id: int, account_id: int,
    ) -> list[dict]:
        cur = await self._db.execute(
            "SELECT * FROM maintenance_tasks "
            "WHERE work_order_id = ? AND account_id = ? "
            "ORDER BY completed_at DESC, id DESC",
            (work_order_id, account_id),
        )
        return [dict(r) for r in await cur.fetchall()]

    # ── Cost aggregation reports ─────────────────────────────────────────────

    async def cost_by_vehicle(
        self, account_id: int, since: Optional[str] = None,
    ) -> list[dict]:
        """Return ``[{vehicle_name, work_order_count, total_spent}, ...]``.

        ``since`` is an ISO timestamp; results filter to work orders
        with ``service_date >= since``.  NULL service_date rows (drafts)
        are excluded so unfilled records don't skew the total.
        """
        q = (
            "SELECT vehicle_name, COUNT(*) AS work_order_count, "
            "       SUM(total_cost) AS total_spent "
            "FROM work_orders "
            "WHERE account_id = ? AND service_date IS NOT NULL AND status != 'void' AND payment_status != 'void'"
        )
        params: list = [account_id]
        if since:
            q += " AND service_date >= ?"
            params.append(since)
        q += " GROUP BY vehicle_name ORDER BY total_spent DESC"
        cur = await self._db.execute(q, params)
        return [dict(r) for r in await cur.fetchall()]

    async def cost_by_task_type(
        self, account_id: int, since: Optional[str] = None,
    ) -> list[dict]:
        """Spend grouped by ``maintenance_tasks.task_type``.

        Joins through ``maintenance_tasks.work_order_id``.  Work orders
        with no linked tasks contribute nothing — to surface those, the
        caller queries ``list_work_orders`` and subtracts what's linked.
        """
        q = (
            "SELECT m.task_type, COUNT(DISTINCT w.id) AS work_order_count, "
            "       SUM(w.total_cost) AS total_spent "
            "FROM work_orders w "
            "JOIN maintenance_tasks m ON m.work_order_id = w.id "
            "WHERE w.account_id = ? AND w.service_date IS NOT NULL AND w.status != 'void' AND w.payment_status != 'void'"
        )
        params: list = [account_id]
        if since:
            q += " AND w.service_date >= ?"
            params.append(since)
        q += " GROUP BY m.task_type ORDER BY total_spent DESC"
        cur = await self._db.execute(q, params)
        return [dict(r) for r in await cur.fetchall()]

    async def cost_by_service_task(
        self, account_id: int, since: Optional[str] = None,
    ) -> list[dict]:
        """Spend grouped by the part lines' ``service_task`` tag.

        Sums at the PART level (unlike ``cost_by_task_type``, which
        sums whole work orders through the maintenance-task link), so a
        mixed invoice ("oil change + brake job") splits correctly per
        task.  '' rows are returned as ``untagged`` so unclassified
        spend stays visible instead of silently vanishing.

        ``total_spent`` remains PARTS spend (the original contract);
        itemized labor (work_order_labor, Tier-2 B1) merges in as the
        additive ``labor_spent`` key — so each kind of work shows its
        parts AND labor split.  Tax stays out entirely.
        """
        # Grouped by the service_tasks REFERENCE, displayed by the
        # task's name.  The LEFT JOIN carries no status filter on
        # purpose: an ARCHIVED task must still resolve its label or
        # historical rows would collapse into a nameless bucket.  Rows
        # not yet backfilled fall back to their legacy string, so the
        # report is correct throughout the dual-write window.
        q = (
            "SELECT COALESCE(st.name, NULLIF(p.service_task, ''), 'untagged') "
            "         AS service_task, "
            "       COUNT(DISTINCT w.id) AS work_order_count, "
            "       SUM(p.total_cost) AS total_spent "
            "FROM work_order_parts p "
            "JOIN work_orders w ON w.id = p.work_order_id "
            "LEFT JOIN service_tasks st ON st.id = p.service_task_id "
            "WHERE w.account_id = ? AND w.service_date IS NOT NULL AND w.status != 'void' AND w.payment_status != 'void'"
        )
        params: list = [account_id]
        if since:
            q += " AND w.service_date >= ?"
            params.append(since)
        q += (
            " GROUP BY COALESCE(st.name, NULLIF(p.service_task, ''), 'untagged')"
            " ORDER BY total_spent DESC"
        )
        cur = await self._db.execute(q, params)
        rows = [dict(r) for r in await cur.fetchall()]
        labor = await self.labor_by_service_task(account_id, since)
        by_task = {str(r["service_task"]): r for r in rows}
        for task, amount in labor.items():
            row = by_task.get(task)
            if row is None:
                row = {"service_task": task, "work_order_count": 0,
                       "total_spent": 0}
                rows.append(row)
                by_task[task] = row
            row["labor_spent"] = round(amount, 2)
        for r in rows:
            r.setdefault("labor_spent", 0)
        rows.sort(
            key=lambda r: float(r["total_spent"] or 0) + float(r["labor_spent"] or 0),
            reverse=True,
        )
        return rows

    async def cost_by_system(
        self, account_id: int, since: Optional[str] = None,
    ) -> list[dict]:
        """Spend grouped by SYSTEM — "what are brakes costing us?".

        The question a flat task list can't answer.  Parts and labor
        are summed separately (same split as the per-task report) and
        rows whose task has no system land in 'Unassigned', kept
        visible rather than dropped so the total still reconciles.
        """
        from adapters.storage.service_tasks import SYSTEM_LABELS

        totals: dict[str, dict] = {}

        def _bucket(key: str) -> dict:
            k = key or ""
            return totals.setdefault(k, {
                "system_key": k,
                "system": SYSTEM_LABELS.get(k, "Unassigned"),
                "total_spent": 0.0, "labor_spent": 0.0, "work_order_count": 0,
            })

        # THE DELEGATION RULE (advisor, 2026-07-27): labor always rolls
        # to the task's system, and parts on a COMPONENT-system task do
        # too ("task wins").  But parts on an ACTIVITY-system task
        # (pm / inspection / other / untagged) delegate to their
        # assembly's system — otherwise an oil filter bought inside a
        # PM counts as "PM spend" forever and the component systems
        # stay empty for PM-heavy fleets.
        q = (
            f"SELECT {_SYSTEM_ROLLUP} AS system_key, "
            "       COUNT(DISTINCT w.id) AS work_order_count, "
            "       SUM(p.total_cost) AS total_spent "
            "FROM work_order_parts p "
            "JOIN work_orders w ON w.id = p.work_order_id "
            "LEFT JOIN service_tasks st ON st.id = p.service_task_id "
            "LEFT JOIN parts_catalog pc ON pc.id = p.part_id "
            "     AND pc.account_id = w.account_id "
            "LEFT JOIN service_assembly_library al "
            "     ON al.key = pc.assembly_key AND pc.assembly_key <> '' "
            "WHERE w.account_id = ? AND w.service_date IS NOT NULL "
            "  AND w.status != 'void' AND w.payment_status != 'void'"
        )
        params: list = [account_id]
        if since:
            q += " AND w.service_date >= ?"
            params.append(since)
        q += (
            f" GROUP BY {_SYSTEM_ROLLUP}"
        )
        cur = await self._db.execute(q, params)
        for r in (dict(x) for x in await cur.fetchall()):
            b = _bucket(r["system_key"])
            b["total_spent"] = round(
                b["total_spent"] + float(r["total_spent"] or 0), 2)
            b["work_order_count"] += int(r["work_order_count"] or 0)

        q = (
            "SELECT COALESCE(st.system_key, '') AS system_key, "
            "       SUM(l.total_cost) AS labor_spent "
            "FROM work_order_labor l "
            "JOIN work_orders w ON w.id = l.work_order_id "
            "     AND w.account_id = l.account_id "
            "LEFT JOIN service_tasks st ON st.id = l.service_task_id "
            "WHERE l.account_id = ? AND w.service_date IS NOT NULL "
            "  AND w.status != 'void' AND w.payment_status != 'void'"
        )
        params = [account_id]
        if since:
            q += " AND w.service_date >= ?"
            params.append(since)
        q += " GROUP BY COALESCE(st.system_key, '')"
        cur = await self._db.execute(q, params)
        for r in (dict(x) for x in await cur.fetchall()):
            _bucket(r["system_key"])["labor_spent"] = round(
                float(r["labor_spent"] or 0), 2)

        rows = list(totals.values())
        rows.sort(key=lambda r: r["total_spent"] + r["labor_spent"], reverse=True)
        return rows

    async def cost_by_assembly(
        self, account_id: int, system_key: str,
        since: Optional[str] = None,
    ) -> list[dict]:
        """Parts spend within ONE system, grouped by assembly — the
        drill-down under a system bar.  PARTS ONLY by construction
        (labor has no part, so it can never reach level 2 — the UI
        labels this permanently).  Rows whose part has no assembly
        stay visible as 'Unassigned' so the parts total reconciles.

        Membership uses the same delegation rule as cost_by_system, so
        a bar and its drill-down agree about which lines they contain.
        """
        q = (
            "SELECT COALESCE(NULLIF(pc.assembly_key, ''), '') AS assembly_key, "
            "       COALESCE(al.label, NULLIF(pc.assembly_key, ''), "
            "                'Unassigned') AS assembly, "
            "       COUNT(*) AS line_count, "
            "       SUM(p.total_cost) AS total_spent "
            "FROM work_order_parts p "
            "JOIN work_orders w ON w.id = p.work_order_id "
            "LEFT JOIN service_tasks st ON st.id = p.service_task_id "
            "LEFT JOIN parts_catalog pc ON pc.id = p.part_id "
            "     AND pc.account_id = w.account_id "
            "LEFT JOIN service_assembly_library al "
            "     ON al.key = pc.assembly_key AND pc.assembly_key <> '' "
            "WHERE w.account_id = ? AND w.service_date IS NOT NULL "
            "  AND w.status != 'void' AND w.payment_status != 'void' "
            f"  AND ({_SYSTEM_ROLLUP}) = ?"
        )
        params: list = [account_id, system_key]
        if since:
            q += " AND w.service_date >= ?"
            params.append(since)
        q += (
            " GROUP BY COALESCE(NULLIF(pc.assembly_key, ''), ''), "
            "          COALESCE(al.label, NULLIF(pc.assembly_key, ''), "
            "                   'Unassigned')"
            " ORDER BY total_spent DESC"
        )
        cur = await self._db.execute(q, params)
        return [
            {**dict(r), "total_spent": round(float(r["total_spent"] or 0), 2)}
            for r in (dict(x) for x in await cur.fetchall())
        ]

    async def cost_by_part(
        self, account_id: int, since: Optional[str] = None,
        limit: int = 25,
    ) -> list[dict]:
        """Usage + spend grouped by part name — "which part keeps
        costing us".  Case-insensitive grouping so "Oil Filter" and
        "oil filter" from different vendors merge; the display name is
        the most common casing via MIN() (deterministic, portable).
        ``usage_count`` counts line occurrences, ``total_quantity``
        sums quantities (2 pads × 2 visits = 4), and the recurrence of
        a part across many work orders is the early-warning signal for
        a failing component pattern.
        """
        # Catalog id is the primary group key; unlinked residual rows
        # (part_id NULL — dead after migration 150's backfill + the
        # auto-link, kept for safety) fall back to lowercase-name
        # groups via the CASE second key (constant '' when linked, so
        # it never splits an id group).  Display name: the catalog
        # name when linked (MAX satisfies Postgres grouping — c.name
        # is constant within an id group), else the most deterministic
        # raw casing (MIN).
        q = (
            "SELECT COALESCE(MAX(c.name), MIN(p.part_name)) AS part_name, "
            "       p.part_id AS part_id, "
            "       COUNT(*) AS usage_count, "
            "       COUNT(DISTINCT w.id) AS work_order_count, "
            "       SUM(p.quantity) AS total_quantity, "
            "       SUM(p.total_cost) AS total_spent "
            "FROM work_order_parts p "
            "JOIN work_orders w ON w.id = p.work_order_id "
            "LEFT JOIN parts_catalog c "
            "     ON c.id = p.part_id AND c.account_id = w.account_id "
            "WHERE w.account_id = ? AND w.service_date IS NOT NULL AND w.status != 'void' AND w.payment_status != 'void' "
            "  AND p.part_name <> ''"
        )
        params: list = [account_id]
        if since:
            q += " AND w.service_date >= ?"
            params.append(since)
        q += (
            " GROUP BY p.part_id, "
            "   CASE WHEN p.part_id IS NULL THEN LOWER(p.part_name) ELSE '' END"
            " ORDER BY total_spent DESC"
            f" LIMIT {int(limit)}"
        )
        cur = await self._db.execute(q, params)
        return [dict(r) for r in await cur.fetchall()]

    async def cost_by_month(
        self, account_id: int, since: Optional[str] = None,
    ) -> list[dict]:
        """Spend grouped by calendar month (YYYY-MM).

        Drives the monthly-trend chart on the Cost Reports page.

        Uses ``substr(service_date, 1, 7)`` rather than ``strftime`` or
        ``to_char`` so the SQL works on both SQLite (dev) and Postgres
        (prod) without the pg_adapter translation layer.  Works because
        ``service_date`` is an ISO-8601 string whose first 7 chars are
        always ``YYYY-MM`` regardless of the rest of the format.

        Ordered oldest-first so a line chart reads left → right.  NULL
        service_date rows are filtered the same way the other reports
        filter them.
        """
        q = (
            "SELECT substr(service_date, 1, 7) AS month, "
            "       COUNT(*) AS work_order_count, "
            "       SUM(total_cost) AS total_spent "
            "FROM work_orders "
            "WHERE account_id = ? AND service_date IS NOT NULL AND status != 'void' AND payment_status != 'void'"
        )
        params: list = [account_id]
        if since:
            q += " AND service_date >= ?"
            params.append(since)
        # GROUP BY the same expression — alias references aren't
        # portable across SQLite + Postgres.
        q += (
            " GROUP BY substr(service_date, 1, 7)"
            " ORDER BY substr(service_date, 1, 7) ASC"
        )
        cur = await self._db.execute(q, params)
        return [dict(r) for r in await cur.fetchall()]

    async def cost_summary_in_window(
        self,
        account_id: int,
        since: Optional[str] = None,
        until: Optional[str] = None,
    ) -> dict:
        """Total spend, WO count, and vendor count within a date range.

        ``since`` is inclusive, ``until`` is exclusive — matches the
        idiomatic [from, to) Python interval style.  Used by the
        ``/reports/summary`` endpoint to compute both the current
        window AND the equivalent-length window before it ("prior
        period") so the dashboard can render % delta chips.

        Returns ``{total_spent, work_order_count, vendor_count}``.
        NULLs become 0 so the response is always shaped the same way
        regardless of whether the account has data in the window.
        """
        q = (
            "SELECT "
            "  COALESCE(SUM(total_cost), 0) AS total_spent, "
            "  COUNT(*)                     AS work_order_count, "
            "  COUNT(DISTINCT NULLIF(vendor_name, '')) AS vendor_count "
            "FROM work_orders "
            "WHERE account_id = ? AND service_date IS NOT NULL AND status != 'void' AND payment_status != 'void'"
        )
        params: list = [account_id]
        if since:
            q += " AND service_date >= ?"
            params.append(since)
        if until:
            q += " AND service_date < ?"
            params.append(until)
        cur = await self._db.execute(q, params)
        row = await cur.fetchone()
        if not row:
            return {"total_spent": 0.0, "work_order_count": 0, "vendor_count": 0}
        return {
            "total_spent": float(row["total_spent"] or 0),
            "work_order_count": int(row["work_order_count"] or 0),
            "vendor_count": int(row["vendor_count"] or 0),
        }

    async def attachments_total_bytes(self, account_id: int) -> int:
        """Sum of ``file_size`` across every attachment for this account.

        Drives the Drive disk-usage indicator on the Settings page.  We
        compute from our own ``work_order_attachments`` rather than
        scanning the user's Drive because:
          1. ``drive.file`` OAuth scope only sees files we created —
             so listing under the root folder would miss nothing, but
             requires N Drive round-trips per page.
          2. Our DB rows are the SSOT for "files this app uploaded";
             a deletion the user does in Drive manually drifts but
             that's the user's call.

        Joins through ``work_orders`` to make this account-scoped —
        ``work_order_attachments`` doesn't carry account_id directly.
        """
        cur = await self._db.execute(
            "SELECT COALESCE(SUM(a.file_size), 0) AS total "
            "FROM work_order_attachments a "
            "JOIN work_orders w ON w.id = a.work_order_id "
            "WHERE w.account_id = ?",
            (account_id,),
        )
        row = await cur.fetchone()
        return int(row["total"] or 0) if row else 0

    async def cost_by_vendor(
        self, account_id: int, since: Optional[str] = None,
    ) -> list[dict]:
        """Spend per vendor, keyed on the REGISTRY id so name-casing
        variants of one shop no longer split its spend.  The display
        name comes from the vendors table when linked; unlinked rows
        (vendor_id NULL — effectively none after the migration-149
        backfill + sync auto-link, kept for safety) fall back to one
        residual bucket per raw name.  ``vendor_id`` rides along so
        the dashboard can link each row to the vendor profile page."""
        q = (
            "SELECT COALESCE(v.name, w.vendor_name) AS vendor_name, "
            "       w.vendor_id AS vendor_id, "
            "       COUNT(*) AS work_order_count, "
            "       SUM(w.total_cost) AS total_spent "
            "FROM work_orders w "
            "LEFT JOIN vendors v "
            "     ON v.id = w.vendor_id AND v.account_id = w.account_id "
            "WHERE w.account_id = ? AND w.service_date IS NOT NULL AND w.status != 'void' AND w.payment_status != 'void' "
            "  AND w.vendor_name != ''"
        )
        params: list = [account_id]
        if since:
            q += " AND w.service_date >= ?"
            params.append(since)
        q += (
            " GROUP BY w.vendor_id, COALESCE(v.name, w.vendor_name)"
            " ORDER BY total_spent DESC"
        )
        cur = await self._db.execute(q, params)
        return [dict(r) for r in await cur.fetchall()]
