"""DOT compliance binder — data assembly.

Pulls every datum the PDF binder needs into one async function so the
renderer (``dot_binder_pdf.py``) can stay pure layout — no DB calls in
the platypus story-building paths.

What goes in:

* The list of vehicles seen in ``vehicle_state`` for the account
  (canonical fleet roster — drivers' assigned trucks may be stale)
* For each vehicle, ALL maintenance tasks within the coverage window
  plus any that are still pending/overdue (open tasks must show in
  the binder regardless of completion date)
* For each vehicle, the work orders in the window — vendor, totals,
  invoice numbers, parts
* Attestation names resolved from the platform users table

The result is a plain dataclass tree consumed by the renderer.  This
keeps the renderer testable with synthetic data (no DB) and means
swapping the data source (e.g. future S3 archive lookups) won't touch
the rendering code.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Optional

logger = logging.getLogger(__name__)


@dataclass
class BinderPart:
    name: str
    part_number: str
    quantity: float
    total_cost: float
    warranty_months: int


@dataclass
class BinderWorkOrder:
    id: int
    service_date: Optional[str]
    vendor_name: str
    total_cost: float
    invoice_number: str
    payment_status: str
    parts: list[BinderPart] = field(default_factory=list)
    attachment_count: int = 0


@dataclass
class BinderTask:
    id: int
    task_type: str
    description: str
    status: str
    priority: str
    due_date: Optional[str]
    due_miles: Optional[float]
    due_engine_hours: Optional[float]
    completed_at: Optional[str]
    last_odometer: Optional[float]
    last_engine_hours: Optional[float]
    attested_by_name: Optional[str]
    attested_at: Optional[str]
    work_order_id: Optional[int]


@dataclass
class BinderVehicle:
    vehicle_name: str
    vehicle_id: str
    company_code: str
    odometer_mi: Optional[float]
    engine_state: str
    open_tasks: list[BinderTask] = field(default_factory=list)
    completed_tasks: list[BinderTask] = field(default_factory=list)
    work_orders: list[BinderWorkOrder] = field(default_factory=list)
    # DOT inspection rows surface separately so the binder can call
    # them out at the top of the vehicle section (regulators look here
    # first).  Sourced from ``completed_tasks`` where task_type ==
    # 'dot_inspection' — we materialise it once during assembly to
    # avoid filtering in the renderer.
    dot_inspections: list[BinderTask] = field(default_factory=list)


@dataclass
class BinderSummary:
    total_vehicles: int
    completed_services: int
    open_tasks: int
    overdue_tasks: int
    work_order_count: int
    total_spend: float
    unique_vendors: int
    dot_inspections_completed: int


@dataclass
class DOTBinder:
    """Top-level binder payload — one report = one of these."""
    account_id: int
    account_name: str
    generated_at: str
    generated_by_name: str
    coverage_start: str
    coverage_end: str
    summary: BinderSummary
    vehicles: list[BinderVehicle]


# ── Assembly ─────────────────────────────────────────────────────────────────


async def build_dot_binder(
    *,
    account_id: int,
    account_name: str,
    tenant_db,
    platform_db,
    generated_by_id: int,
    days: int = 365,
    vehicle_name_filter: Optional[str] = None,
) -> DOTBinder:
    """Gather every datum the binder needs.

    ``days`` is the coverage window (default 365 — the DOT 12-month
    retention requirement).  ``vehicle_name_filter`` lets a caller
    generate a single-vehicle binder (useful when one truck is being
    audited or sold to another carrier).
    """
    now = datetime.now(timezone.utc)
    cutoff = (now - timedelta(days=days)).isoformat()
    coverage_start = (now - timedelta(days=days)).strftime("%Y-%m-%d")
    coverage_end = now.strftime("%Y-%m-%d")

    # Resolve generator name + global user-name map up-front.  One DB
    # query gives us names for every attestation across the whole
    # binder — vs N queries if we resolved per-row.
    user_name_map: dict[int, str] = {}
    generated_by_name = f"user {generated_by_id}"
    try:
        users = await platform_db.list_account_users(account_id)
        for u in users:
            name = u.display_name or str(u.telegram_id)
            user_name_map[int(u.telegram_id)] = name
            if int(u.telegram_id) == int(generated_by_id):
                generated_by_name = name
    except Exception as e:
        logger.warning("DOT binder user-map fetch failed: %s", e)

    # Fleet roster.  vehicle_state is the warehouse truth — drivers'
    # assigned trucks may be stale or unset.
    state_rows = await tenant_db.get_vehicle_state(account_id)

    # Every vehicle answers to its REGISTRY unit number as well as the
    # provider's display name.  This binder goes in front of an
    # inspector, and a provider rename used to make records vanish from
    # it: the state row became "229 Idris Ahmed" while every work order
    # stayed filed under "229", so the truck printed with an empty
    # service history.  The registry name is the one WE own — records
    # are collected under both spellings and the page is titled with
    # the canonical name.
    canonical: dict = {}
    try:
        for rv in await tenant_db.list_vehicles(account_id):
            canonical[rv.id] = (rv.unit_number or "").strip()
    except Exception:
        canonical = {}

    def _keys(row: dict) -> set[str]:
        keys = {(row.get("vehicle_name") or "").strip().lower()}
        canon = canonical.get(row.get("registry_id"))
        if canon:
            keys.add(canon.lower())
        keys.discard("")
        return keys

    if vehicle_name_filter:
        # Exact match on either spelling — a filter for truck 230 must
        # not also print 2303's binder.
        needle = vehicle_name_filter.strip().lower()
        state_rows = [r for r in state_rows if needle in _keys(r)]

    # Bulk task pull — one query, filter per vehicle in Python.  Avoids
    # N round trips on large fleets.  Includes ALL tasks; we partition
    # open vs completed by status afterwards.
    all_tasks = await tenant_db.get_maintenance_tasks(account_id)
    tasks_by_vehicle: dict[str, list[dict]] = {}
    for t in all_tasks:
        v = (t.get("vehicle_name") or "").strip().lower()
        tasks_by_vehicle.setdefault(v, []).append(t)

    # Work orders + parts + attachment counts.  Pull all in-window
    # orders once, then group by vehicle.  Parts and attachment counts
    # are fetched in *two* bulk queries (one per table) up front —
    # previously the binder hit the DB 2 × per work order inside the
    # per-vehicle loop, an N+1 that scaled badly on large fleets.
    all_work_orders = await tenant_db.list_work_orders(account_id)
    in_window = [
        w for w in all_work_orders
        if (w.get("service_date") or "") >= coverage_start
        or w.get("status") != "void"
    ]
    work_orders_by_vehicle: dict[str, list[dict]] = {}
    for w in in_window:
        v = (w.get("vehicle_name") or "").strip().lower()
        work_orders_by_vehicle.setdefault(v, []).append(w)

    in_window_ids = [int(w["id"]) for w in in_window if w.get("id") is not None]
    parts_by_wo = await tenant_db.list_work_order_parts_bulk(in_window_ids)
    attach_counts = await tenant_db.count_work_order_attachments_bulk(in_window_ids)

    vehicles: list[BinderVehicle] = []
    total_completed = 0
    total_open = 0
    total_overdue = 0
    total_wo = 0
    total_spend = 0.0
    vendor_set: set[str] = set()
    total_dot = 0

    for v in state_rows:
        keys = _keys(v)
        if not keys:
            continue
        # Title the page with the registry's unit number when we have
        # it — the inspector should read "229", not the driver-suffixed
        # display string the provider invented.
        vname = (canonical.get(v.get("registry_id"))
                 or (v.get("vehicle_name") or "").strip())
        v_tasks = [t for k in sorted(keys)
                   for t in tasks_by_vehicle.get(k, [])]
        v_wos = [w for k in sorted(keys)
                 for w in work_orders_by_vehicle.get(k, [])]

        # Partition tasks.  "Open" = still actionable (pending, overdue,
        # in_progress).  "Completed" = closed within the window.  We
        # ignore cancelled tasks in this binder — they're not service
        # records the inspector cares about.
        open_tasks: list[BinderTask] = []
        completed_tasks: list[BinderTask] = []
        dot_tasks: list[BinderTask] = []
        for t in v_tasks:
            status = t.get("status") or ""
            completed_at = t.get("completed_at")
            if status in ("completed", "done"):
                if completed_at and completed_at < cutoff:
                    continue  # outside window
                task = _to_binder_task(t, user_name_map)
                completed_tasks.append(task)
                if (t.get("task_type") or "") == "dot_inspection":
                    dot_tasks.append(task)
            elif status in ("pending", "overdue", "in_progress"):
                open_tasks.append(_to_binder_task(t, user_name_map))
                if status == "overdue":
                    total_overdue += 1
            # other statuses (cancelled, void) intentionally dropped

        # Sort: completed newest-first, open earliest due date first.
        completed_tasks.sort(
            key=lambda t: (t.completed_at or t.due_date or "", t.id),
            reverse=True,
        )
        open_tasks.sort(key=lambda t: (t.due_date or "9999", t.id))

        # Work orders with parts.  Sorted newest service first so a
        # quick scan of the section starts with the most recent work.
        binder_wos: list[BinderWorkOrder] = []
        for w in sorted(
            v_wos,
            key=lambda x: (x.get("service_date") or "", x.get("id") or 0),
            reverse=True,
        ):
            wo_id = int(w["id"])
            parts_rows = parts_by_wo.get(wo_id, [])
            binder_wos.append(BinderWorkOrder(
                id=wo_id,
                service_date=w.get("service_date"),
                vendor_name=w.get("vendor_name") or "",
                total_cost=float(w.get("total_cost") or 0),
                invoice_number=w.get("invoice_number") or "",
                payment_status=w.get("payment_status") or "",
                parts=[
                    BinderPart(
                        name=p.get("part_name") or "",
                        part_number=p.get("part_number") or "",
                        quantity=float(p.get("quantity") or 0),
                        total_cost=float(p.get("total_cost") or 0),
                        warranty_months=int(p.get("warranty_months") or 0),
                    ) for p in parts_rows
                ],
                attachment_count=attach_counts.get(wo_id, 0),
            ))
            vendor = (w.get("vendor_name") or "").strip()
            if vendor:
                vendor_set.add(vendor)
            total_spend += float(w.get("total_cost") or 0)
            total_wo += 1

        total_completed += len(completed_tasks)
        total_open += len(open_tasks)
        total_dot += len(dot_tasks)

        vehicles.append(BinderVehicle(
            vehicle_name=vname,
            vehicle_id=v.get("vehicle_id") or "",
            company_code=v.get("company_code") or "",
            odometer_mi=v.get("odometer_mi"),
            engine_state=v.get("engine_state") or "",
            open_tasks=open_tasks,
            completed_tasks=completed_tasks,
            work_orders=binder_wos,
            dot_inspections=dot_tasks,
        ))

    # Sort vehicles by name for deterministic page ordering — the
    # inspector flips through alphabetically, not by some internal id.
    vehicles.sort(key=lambda v: v.vehicle_name.lower())

    return DOTBinder(
        account_id=account_id,
        account_name=account_name,
        generated_at=now.strftime("%Y-%m-%d %H:%M UTC"),
        generated_by_name=generated_by_name,
        coverage_start=coverage_start,
        coverage_end=coverage_end,
        summary=BinderSummary(
            total_vehicles=len(vehicles),
            completed_services=total_completed,
            open_tasks=total_open,
            overdue_tasks=total_overdue,
            work_order_count=total_wo,
            total_spend=round(total_spend, 2),
            unique_vendors=len(vendor_set),
            dot_inspections_completed=total_dot,
        ),
        vehicles=vehicles,
    )


def _to_binder_task(
    row: dict, user_name_map: dict[int, str],
) -> BinderTask:
    attester_id = row.get("attested_by")
    attester_name: Optional[str] = None
    if attester_id:
        attester_name = user_name_map.get(int(attester_id))
    return BinderTask(
        id=int(row.get("id") or 0),
        task_type=row.get("task_type") or "custom",
        description=row.get("description") or "",
        status=row.get("status") or "",
        priority=row.get("priority") or "medium",
        due_date=row.get("due_date"),
        due_miles=row.get("due_miles"),
        due_engine_hours=row.get("due_engine_hours"),
        completed_at=row.get("completed_at"),
        last_odometer=row.get("last_odometer"),
        last_engine_hours=row.get("last_engine_hours"),
        attested_by_name=attester_name,
        attested_at=row.get("attested_at"),
        work_order_id=row.get("work_order_id"),
    )
