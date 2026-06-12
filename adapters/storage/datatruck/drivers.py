"""``datatruck_drivers`` storage mixin.

Drivers synced from the Datatruck TMS.  Promoted columns cover what
the workforce screens join on; everything else rides in ``payload``.
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
    table="datatruck_drivers",
    columns=(
        "first_name", "last_name", "display_name",
        "phone", "email", "status",
    ),
)


@dataclass
class DatatruckDriver:
    id: int
    account_id: int
    external_id: str
    first_name: str
    last_name: str
    display_name: str
    phone: str
    email: str
    status: str
    payload: str
    first_seen_at: str
    synced_at: str


class DatatruckDriversMixin(_MixinBase):
    async def upsert_datatruck_drivers(
        self, account_id: int, rows: list[dict[str, Any]],
    ) -> int:
        return await upsert_rows(self, _SPEC, account_id, rows)

    async def list_datatruck_drivers(
        self, account_id: int, *, limit: int = 200, offset: int = 0,
    ) -> list[DatatruckDriver]:
        raw = await list_rows(
            self, _SPEC, account_id, limit=limit, offset=offset,
        )
        return [
            DatatruckDriver(
                id=r[0], account_id=r[1], external_id=r[2],
                first_name=r[3] or "", last_name=r[4] or "",
                display_name=r[5] or "", phone=r[6] or "",
                email=r[7] or "", status=r[8] or "",
                payload=r[9] or "{}",
                first_seen_at=r[10] or "", synced_at=r[11] or "",
            )
            for r in raw
        ]

    async def datatruck_drivers_stats(self, account_id: int) -> dict:
        return await resource_stats(self, _SPEC, account_id)
