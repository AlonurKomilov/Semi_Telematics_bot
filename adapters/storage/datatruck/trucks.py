"""``datatruck_trucks`` storage mixin.

Trucks synced from the Datatruck TMS.  Promoted columns mirror the
Datatruck fleet board (unit number, plate, VIN, make/model/year,
owner/operator, odometer); the rest rides in ``payload``.
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
    table="datatruck_trucks",
    columns=(
        "unit_number", "plate_number", "vin",
        "make", "model", "year",
        "status", "owner_name", "operator_name", "odometer",
    ),
    nullable=frozenset({"year", "odometer"}),
)


@dataclass
class DatatruckTruck:
    id: int
    account_id: int
    external_id: str
    unit_number: str
    plate_number: str
    vin: str
    make: str
    model: str
    year: int | None
    status: str
    owner_name: str
    operator_name: str
    odometer: float | None
    payload: str
    first_seen_at: str
    synced_at: str


class DatatruckTrucksMixin(_MixinBase):
    async def upsert_datatruck_trucks(
        self, account_id: int, rows: list[dict[str, Any]],
    ) -> int:
        return await upsert_rows(self, _SPEC, account_id, rows)

    async def list_datatruck_trucks(
        self, account_id: int, *, limit: int = 200, offset: int = 0,
    ) -> list[DatatruckTruck]:
        raw = await list_rows(
            self, _SPEC, account_id, limit=limit, offset=offset,
        )
        return [
            DatatruckTruck(
                id=r[0], account_id=r[1], external_id=r[2],
                unit_number=r[3] or "", plate_number=r[4] or "",
                vin=r[5] or "", make=r[6] or "", model=r[7] or "",
                year=r[8], status=r[9] or "",
                owner_name=r[10] or "", operator_name=r[11] or "",
                odometer=r[12],
                payload=r[13] or "{}",
                first_seen_at=r[14] or "", synced_at=r[15] or "",
            )
            for r in raw
        ]

    async def datatruck_trucks_stats(self, account_id: int) -> dict:
        return await resource_stats(self, _SPEC, account_id)
