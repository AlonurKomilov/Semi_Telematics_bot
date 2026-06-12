"""``datatruck_trailers`` storage mixin.

Trailers synced from the Datatruck TMS.
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
    table="datatruck_trailers",
    columns=(
        "unit_number", "plate_number", "vin",
        "make", "model", "year", "status",
    ),
    nullable=frozenset({"year"}),
)


@dataclass
class DatatruckTrailer:
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
    payload: str
    first_seen_at: str
    synced_at: str


class DatatruckTrailersMixin(_MixinBase):
    async def upsert_datatruck_trailers(
        self, account_id: int, rows: list[dict[str, Any]],
    ) -> int:
        return await upsert_rows(self, _SPEC, account_id, rows)

    async def list_datatruck_trailers(
        self, account_id: int, *, limit: int = 200, offset: int = 0,
    ) -> list[DatatruckTrailer]:
        raw = await list_rows(
            self, _SPEC, account_id, limit=limit, offset=offset,
        )
        return [
            DatatruckTrailer(
                id=r[0], account_id=r[1], external_id=r[2],
                unit_number=r[3] or "", plate_number=r[4] or "",
                vin=r[5] or "", make=r[6] or "", model=r[7] or "",
                year=r[8], status=r[9] or "",
                payload=r[10] or "{}",
                first_seen_at=r[11] or "", synced_at=r[12] or "",
            )
            for r in raw
        ]

    async def datatruck_trailers_stats(self, account_id: int) -> dict:
        return await resource_stats(self, _SPEC, account_id)
