"""``datatruck_orders`` storage mixin.

Loads / orders synced from the Datatruck TMS.  Dispatch-shaped data:
pickup, delivery, rate, status, assigned driver + truck.  This is by
far the biggest resource (the live tenant we probed had ~17k orders),
hence the status + date promoted columns so list screens can filter
without JSON-parsing payloads.
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
    table="datatruck_orders",
    columns=(
        "order_number", "status",
        "pickup_date", "delivery_date",
        "origin", "destination",
        "driver_external_id", "truck_external_id",
        "total_rate",
    ),
    nullable=frozenset({"total_rate"}),
)


@dataclass
class DatatruckOrder:
    id: int
    account_id: int
    external_id: str
    order_number: str
    status: str
    pickup_date: str
    delivery_date: str
    origin: str
    destination: str
    driver_external_id: str
    truck_external_id: str
    total_rate: float | None
    payload: str
    first_seen_at: str
    synced_at: str


class DatatruckOrdersMixin(_MixinBase):
    async def upsert_datatruck_orders(
        self, account_id: int, rows: list[dict[str, Any]],
    ) -> int:
        return await upsert_rows(self, _SPEC, account_id, rows)

    async def list_datatruck_orders(
        self, account_id: int, *, limit: int = 200, offset: int = 0,
    ) -> list[DatatruckOrder]:
        raw = await list_rows(
            self, _SPEC, account_id, limit=limit, offset=offset,
        )
        return [
            DatatruckOrder(
                id=r[0], account_id=r[1], external_id=r[2],
                order_number=r[3] or "", status=r[4] or "",
                pickup_date=r[5] or "", delivery_date=r[6] or "",
                origin=r[7] or "", destination=r[8] or "",
                driver_external_id=r[9] or "",
                truck_external_id=r[10] or "",
                total_rate=r[11],
                payload=r[12] or "{}",
                first_seen_at=r[13] or "", synced_at=r[14] or "",
            )
            for r in raw
        ]

    async def datatruck_orders_stats(self, account_id: int) -> dict:
        return await resource_stats(self, _SPEC, account_id)
