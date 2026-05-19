"""Work Orders CRUD mixin.

Owns ``work_orders``, ``work_order_parts``, and ``work_order_attachments``.
Linked to maintenance via ``maintenance_tasks.work_order_id`` — one work
order can close many maintenance tasks (a shop visit often closes oil +
tires + filter together).  See ``capabilities/work_orders/storage.py``
for the file-system layout shared by every storage backend.
"""

from __future__ import annotations

from typing import Optional


class WorkOrdersMixin:

    # ── Core CRUD ────────────────────────────────────────────────────────────

    async def add_work_order(
        self, account_id: int, company_code: str,
        vehicle_name: str, vendor_name: str,
        *,
        vehicle_id: str = "",
        vendor_address: str = "",
        vendor_phone: str = "",
        service_date: Optional[str] = None,
        odometer_at_service: Optional[float] = None,
        engine_hours_at_service: Optional[float] = None,
        labor_cost: float = 0.0,
        parts_cost: float = 0.0,
        tax_amount: float = 0.0,
        total_cost: float = 0.0,
        invoice_number: str = "",
        payment_method: str = "",
        payment_status: str = "unpaid",
        status: str = "draft",
        notes: str = "",
        created_by: int = 0,
    ) -> int:
        now = self._now()
        cur = await self._db.execute(
            """INSERT INTO work_orders
               (account_id, company_code, vehicle_id, vehicle_name,
                vendor_name, vendor_address, vendor_phone,
                service_date, odometer_at_service, engine_hours_at_service,
                labor_cost, parts_cost, tax_amount, total_cost,
                invoice_number, payment_method, payment_status,
                status, notes, created_by, created_at, updated_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?,
                       ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (account_id, company_code, vehicle_id, vehicle_name,
             vendor_name, vendor_address, vendor_phone,
             service_date, odometer_at_service, engine_hours_at_service,
             labor_cost, parts_cost, tax_amount, total_cost,
             invoice_number, payment_method, payment_status,
             status, notes, created_by, now, now),
        )
        await self._db.commit()
        return cur.lastrowid

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
            "company_code", "vehicle_id", "vehicle_name",
            "vendor_name", "vendor_address", "vendor_phone",
            "service_date", "odometer_at_service", "engine_hours_at_service",
            "labor_cost", "parts_cost", "tax_amount", "total_cost",
            "invoice_number", "payment_method", "payment_status",
            "status", "notes",
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
        notes: str = "",
    ) -> int:
        cur = await self._db.execute(
            """INSERT INTO work_order_parts
               (work_order_id, part_name, part_number, quantity,
                unit_cost, total_cost, warranty_months, notes)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
            (work_order_id, part_name, part_number, quantity,
             unit_cost, total_cost, warranty_months, notes),
        )
        await self._db.commit()
        return cur.lastrowid

    async def list_work_order_parts(self, work_order_id: int) -> list[dict]:
        cur = await self._db.execute(
            "SELECT * FROM work_order_parts WHERE work_order_id = ? ORDER BY id",
            (work_order_id,),
        )
        return [dict(r) for r in await cur.fetchall()]

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
            "WHERE account_id = ? AND service_date IS NOT NULL"
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
            "WHERE w.account_id = ? AND w.service_date IS NOT NULL"
        )
        params: list = [account_id]
        if since:
            q += " AND w.service_date >= ?"
            params.append(since)
        q += " GROUP BY m.task_type ORDER BY total_spent DESC"
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
            "WHERE account_id = ? AND service_date IS NOT NULL"
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
            "WHERE account_id = ? AND service_date IS NOT NULL"
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
        q = (
            "SELECT vendor_name, COUNT(*) AS work_order_count, "
            "       SUM(total_cost) AS total_spent "
            "FROM work_orders "
            "WHERE account_id = ? AND service_date IS NOT NULL "
            "  AND vendor_name != ''"
        )
        params: list = [account_id]
        if since:
            q += " AND service_date >= ?"
            params.append(since)
        q += " GROUP BY vendor_name ORDER BY total_spent DESC"
        cur = await self._db.execute(q, params)
        return [dict(r) for r in await cur.fetchall()]
