"""Loads feature — the data contract.

KPI, reports, and AI tools read loads THROUGH here, never by querying the
table directly (service-contract rule, docs/FEATURES.md).  The visibility
scope lives here too, so every consumer applies the same rule: callers with
``can_loads_all`` see the account's loads; own-scope callers (drivers) see
only loads linked to their user id.
"""

from __future__ import annotations

from typing import Any

from infra.platform import get_tenant_db


def load_to_dict(l: Any, line_item_sums: dict[str, float] | None = None) -> dict:
    """Serialize a Load row; derived metrics (RPM, total miles, gross) are
    computed here at read time — never stored.

    ``line_item_sums`` is this load's extra pay & costs aggregate
    (``{"driver_pay": Σ, "expense": Σ}`` from the line-items table) —
    TONU/bonus/tolls etc. land in gross through it."""
    li = line_item_sums or {}
    extra_driver_pay = float(li.get("driver_pay") or 0)
    extra_costs = float(li.get("expense") or 0)
    # Deductions are withheld from the DRIVER's net (payroll), not a
    # company cost — they never reduce the load's gross / KPI.
    extra_deductions = float(li.get("deduction") or 0)
    loaded = float(l.loaded_miles or 0)
    empty = float(l.empty_miles or 0)
    total_miles = loaded + empty
    rate = float(l.total_rate or 0)
    costs = (
        float(l.driver_pay or 0) + float(l.other_costs or 0)
        + extra_driver_pay + extra_costs
    )
    return {
        "id": l.id,
        "seq": l.seq,
        "load_number": l.load_number,
        "status": l.status,
        "payment_status": l.payment_status,
        "customer": l.customer,
        "company_code": l.company_code,
        "pickup_location": l.pickup_location,
        "pickup_date": l.pickup_date,
        "delivery_location": l.delivery_location,
        "delivery_date": l.delivery_date,
        "driver_user_id": l.driver_user_id,
        "driver_name": l.driver_name,
        "dispatcher_user_id": l.dispatcher_user_id,
        "dispatcher_name": l.dispatcher_name,
        "vehicle_unit": l.vehicle_unit,
        "trailer_unit": l.trailer_unit,
        "total_rate": l.total_rate,
        "loaded_miles": l.loaded_miles,
        "empty_miles": l.empty_miles,
        "driver_pay": l.driver_pay,
        "other_costs": l.other_costs,
        "extra_driver_pay": round(extra_driver_pay, 2) or None,
        "extra_costs": round(extra_costs, 2) or None,
        "extra_deductions": round(extra_deductions, 2) or None,
        "other_pay": getattr(l, "other_pay", None),
        "settlement_ref": getattr(l, "settlement_ref", ""),
        "settlement_status": getattr(l, "settlement_status", ""),
        "total_miles": total_miles or None,
        "rpm": round(rate / total_miles, 2) if rate and total_miles else None,
        "gross": round(rate - costs, 2) if rate else None,
        "source": l.source,
        "notes": l.notes,
    }


async def get_loads(
    account_id: int,
    *,
    scope_driver_user_id: int | None = None,
    status: str | None = None,
    since: str | None = None,
    until: str | None = None,
    company_codes: list[str] | None = None,
    limit: int | None = 500,
) -> list[dict]:
    """Loads for the account, serialized.  ``scope_driver_user_id`` (set for
    own-scope callers) restricts to that driver's loads; ``company_codes``
    applies the caller's company restriction.  Aggregation consumers (KPI,
    reports, AI tools) must pass ``limit=None`` — a row cap silently
    understates their totals."""
    tenant = await get_tenant_db(account_id)
    if tenant is None:
        return []
    rows = await tenant.list_loads(
        account_id,
        status=status,
        driver_user_id=scope_driver_user_id,
        company_codes=company_codes,
        since=since, until=until,
        limit=limit,
    )
    # Extra pay & costs (line items) aggregate in ONE query for the whole
    # page — each load's TONU/tolls/bonus money rides into its gross.
    sums = await tenant.sum_load_line_items(account_id, [l.id for l in rows])
    return [load_to_dict(l, sums.get(l.id)) for l in rows]


async def get_load_counts(
    account_id: int, *, scope_driver_user_id: int | None = None,
    company_codes: list[str] | None = None,
) -> dict[str, int]:
    """Active-load counts per status (the tab badges), same scoping rules."""
    tenant = await get_tenant_db(account_id)
    if tenant is None:
        return {}
    return await tenant.count_loads_by_status(
        account_id, driver_user_id=scope_driver_user_id,
        company_codes=company_codes,
    )


def line_item_to_dict(i: Any) -> dict:
    return {
        "id": i.id,
        "load_id": i.load_id,
        "driver_user_id": i.driver_user_id,
        "dispatcher_user_id": i.dispatcher_user_id,
        "kind": i.kind,
        "bucket": i.bucket,
        "amount": i.amount,
        "item_date": i.item_date,
        "notes": i.notes,
    }


async def get_off_load_line_items(
    account_id: int, *, since: str | None = None, until: str | None = None,
) -> list[dict]:
    """The no-load rows (layover: driver + date, dispatcher attributed) in
    a window — the KPI consumer charges them to the responsible
    dispatcher even though no load exists to carry them."""
    tenant = await get_tenant_db(account_id)
    if tenant is None:
        return []
    items = await tenant.list_load_line_items(
        account_id, off_load_only=True, since=since, until=until,
    )
    return [line_item_to_dict(i) for i in items]


async def get_settlement_items(
    account_id: int, *, since: str, until: str,
) -> list[dict]:
    """Driver-pay (additions) + deduction line items (on-load + off-load)
    in a window, each tagged with its bucket — the payroll engine itemizes
    these as the statement's ADDITIONS and DEDUCTIONS lines."""
    tenant = await get_tenant_db(account_id)
    if tenant is None:
        return []
    return await tenant.list_driver_settlement_items(
        account_id, since=since, until=until,
    )
