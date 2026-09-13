"""Guard: work orders are the caller's own trucks, not the twin's too.

get_recent_work_orders is in VEHICLE_SPECIFIC_TOOLS, so the dispatcher
hands it `_scope_vehicles` and `_scope_identities` — and the handler
read neither. The only narrowing was SQL `vehicle_name = ?`, which
cannot split same-numbered trucks across companies, so a caller scoped
to one company's unit 234 was shown the other company's 234 work
orders: their shop, their parts, their money, added into the same
total.

The rows carry `vehicle_id` (the provider id, and the column the ladder
reads by default), so rung 2 splits the twins even where no registry id
is on the row.
"""

import pytest

from features.work_orders.ai_tool import get_recent_work_orders


def _wo(wo_id, vehicle_id, name="234", cost=1000.0):
    return {
        "id": wo_id, "vehicle_id": vehicle_id, "vehicle_name": name,
        "service_date": "2026-09-01", "total_cost": cost,
        "status": "completed", "payment_status": "paid",
        "vendor_name": "Shop", "description": "brakes",
    }


class _DB:
    def __init__(self, rows):
        self._rows = rows

    async def list_work_orders(self, account_id, status=None,
                               payment_status=None, vehicle_name=None):
        rows = self._rows
        if vehicle_name:
            rows = [r for r in rows if r["vehicle_name"] == vehicle_name]
        return list(rows)


PINNED = {
    "vehicle_name": "234",
    "_scope_vehicles": ["234"],
    "_scope_identities": [[42, "sam_42", "234"]],   # OSY's 234
}


@pytest.mark.asyncio
async def test_the_twins_work_orders_do_not_come_back():
    db = _DB([_wo(1, "sam_42"), _wo(2, "sam_99")])   # sam_99 is the other company
    res = await get_recent_work_orders(dict(PINNED), None, account_id=1, db=db)
    assert res["count"] == 1, res
    assert res["work_orders"][0]["id"] == 1


@pytest.mark.asyncio
async def test_the_money_is_the_callers_own():
    """The filter runs BEFORE the totals, so cost describes the caller's
    rows rather than the account's."""
    db = _DB([_wo(1, "sam_42", cost=1000.0), _wo(2, "sam_99", cost=9000.0)])
    res = await get_recent_work_orders(dict(PINNED), None, account_id=1, db=db)
    assert res["total_cost_dollars"] == 1000.0, res


@pytest.mark.asyncio
async def test_an_unrestricted_caller_sees_everything():
    db = _DB([_wo(1, "sam_42"), _wo(2, "sam_99")])
    res = await get_recent_work_orders(
        {"vehicle_name": "234"}, None, account_id=1, db=db)
    assert res["count"] == 2


@pytest.mark.asyncio
async def test_an_empty_scope_fails_closed():
    db = _DB([_wo(1, "sam_42"), _wo(2, "sam_99")])
    res = await get_recent_work_orders(
        {"vehicle_name": "234", "_scope_vehicles": []}, None, account_id=1, db=db)
    assert res["count"] == 0
