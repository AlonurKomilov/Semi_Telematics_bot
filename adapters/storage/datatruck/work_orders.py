"""``datatruck_work_orders`` storage mixin.

Shop work orders synced from the Datatruck TMS.  Distinct from the
existing first-party Work Orders module (``work_orders`` table) —
this one mirrors Datatruck's records so operators can see both feeds
side-by-side until a reconciliation strategy is chosen.  The table
name carries the ``datatruck_`` prefix precisely so the two can
never be confused in a query.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from ._base import TableSpec, list_rows, resource_stats, upsert_rows

if TYPE_CHECKING:
    class _MixinBase:
        _db: Any
        def transaction(self) -> Any: ...
        async def read_all(self, sql: str, params: tuple = ()) -> list: ...
        async def read_one(self, sql: str, params: tuple = ()) -> Any: ...
        @staticmethod
        def _now() -> str: ...
else:
    _MixinBase = object


_SPEC = TableSpec(
    table="datatruck_work_orders",
    columns=(
        "number", "status", "vehicle_unit",
        "opened_at", "closed_at", "total_cost",
    ),
    nullable=frozenset({"total_cost"}),
)


@dataclass
class DatatruckWorkOrder:
    id: int
    account_id: int
    external_id: str
    number: str
    status: str
    vehicle_unit: str
    opened_at: str
    closed_at: str
    total_cost: float | None
    payload: str
    first_seen_at: str
    synced_at: str


class DatatruckWorkOrdersMixin(_MixinBase):
    async def upsert_datatruck_work_orders(
        self, account_id: int, rows: list[dict[str, Any]],
    ) -> int:
        return await upsert_rows(self, _SPEC, account_id, rows)

    async def list_datatruck_work_orders(
        self, account_id: int, *, limit: int = 200, offset: int = 0,
    ) -> list[DatatruckWorkOrder]:
        raw = await list_rows(
            self, _SPEC, account_id, limit=limit, offset=offset,
        )
        return [
            DatatruckWorkOrder(
                id=r[0], account_id=r[1], external_id=r[2],
                number=r[3] or "", status=r[4] or "",
                vehicle_unit=r[5] or "",
                opened_at=r[6] or "", closed_at=r[7] or "",
                total_cost=r[8],
                payload=r[9] or "{}",
                first_seen_at=r[10] or "", synced_at=r[11] or "",
            )
            for r in raw
        ]

    async def datatruck_work_orders_stats(self, account_id: int) -> dict:
        return await resource_stats(self, _SPEC, account_id)
