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


def load_to_dict(l: Any) -> dict:
    """Serialize a Load row; derived metrics (RPM, total miles, gross) are
    computed here at read time — never stored."""
    loaded = float(l.loaded_miles or 0)
    empty = float(l.empty_miles or 0)
    total_miles = loaded + empty
    rate = float(l.total_rate or 0)
    costs = float(l.driver_pay or 0) + float(l.other_costs or 0)
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
) -> list[dict]:
    """Loads for the account, serialized.  ``scope_driver_user_id`` (set for
    own-scope callers) restricts to that driver's loads."""
    tenant = await get_tenant_db(account_id)
    if tenant is None:
        return []
    rows = await tenant.list_loads(
        account_id,
        status=status,
        driver_user_id=scope_driver_user_id,
        since=since, until=until,
    )
    return [load_to_dict(l) for l in rows]


async def get_load_counts(
    account_id: int, *, scope_driver_user_id: int | None = None,
) -> dict[str, int]:
    """Active-load counts per status (the tab badges), same scoping rule."""
    tenant = await get_tenant_db(account_id)
    if tenant is None:
        return {}
    return await tenant.count_loads_by_status(
        account_id, driver_user_id=scope_driver_user_id,
    )
