"""Loads mixin — the canonical load/shipment model (single source of truth).

A load exists because the account moves it, in OUR Postgres DB — not because
a TMS happens to report it.  Operators enter loads by hand
(``source='manual'``); a connected TMS (Datatruck) projects its orders in
(``source='datatruck'``, keyed by ``external_ref``) — the same inversion the
vehicles registry and work-orders module use.

Driver / dispatcher are ``users`` ids where the person is a 4truck user
(``datatruck_driver_id`` linking makes that automatic for synced drivers),
with a free-text name fallback otherwise.  Financials (rate / miles / pay /
costs) live on the row; derived metrics (RPM, gross) are computed at read
time by consumers — never stored.

Tenant isolation is the ``account_id`` filter on every query (and Postgres
RLS when ``ENABLE_RLS=1``).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any, Optional

if TYPE_CHECKING:
    class _MixinBase:
        """Typing stub — provided by the concrete Database at runtime."""
        _db: Any
        def transaction(self) -> Any: ...
        @staticmethod
        def _now() -> str: ...
else:
    _MixinBase = object


LOAD_STATUSES = ("upcoming", "dispatched", "in_transit", "delivered", "canceled")
PAYMENT_STATUSES = ("", "unpaid", "paid")

# Columns a partial update may touch (defensive allow-list, same pattern as
# the driver-profile and vehicle mixins).
_LOAD_FIELDS = (
    "load_number", "status", "payment_status", "customer", "company_code",
    "pickup_location", "pickup_date", "delivery_location", "delivery_date",
    "driver_user_id", "driver_name", "dispatcher_user_id", "dispatcher_name",
    "vehicle_unit", "trailer_unit",
    "total_rate", "loaded_miles", "empty_miles", "driver_pay", "other_costs",
    "notes",
)


@dataclass
class Load:
    id: int
    account_id: int
    load_number: str
    status: str
    payment_status: str
    customer: str
    company_code: str
    pickup_location: str
    pickup_date: str
    delivery_location: str
    delivery_date: str
    driver_user_id: int | None
    driver_name: str
    dispatcher_user_id: int | None
    dispatcher_name: str
    vehicle_unit: str
    trailer_unit: str
    total_rate: float | None
    loaded_miles: float | None
    empty_miles: float | None
    driver_pay: float | None
    other_costs: float | None
    source: str
    external_ref: str
    notes: str
    is_active: bool
    created_at: str
    updated_at: str
    field_provenance: dict = field(default_factory=dict)


_SELECT = (
    "SELECT id, account_id, load_number, status, payment_status, customer, "
    "company_code, pickup_location, pickup_date, delivery_location, "
    "delivery_date, driver_user_id, driver_name, dispatcher_user_id, "
    "dispatcher_name, vehicle_unit, trailer_unit, total_rate, loaded_miles, "
    "empty_miles, driver_pay, other_costs, source, external_ref, notes, "
    "is_active, created_at, updated_at FROM loads"
)


def _row_to_load(r) -> Load:
    return Load(
        id=r[0], account_id=r[1], load_number=r[2] or "",
        status=r[3] or "upcoming", payment_status=r[4] or "",
        customer=r[5] or "", company_code=r[6] or "",
        pickup_location=r[7] or "", pickup_date=r[8] or "",
        delivery_location=r[9] or "", delivery_date=r[10] or "",
        driver_user_id=r[11], driver_name=r[12] or "",
        dispatcher_user_id=r[13], dispatcher_name=r[14] or "",
        vehicle_unit=r[15] or "", trailer_unit=r[16] or "",
        total_rate=r[17], loaded_miles=r[18], empty_miles=r[19],
        driver_pay=r[20], other_costs=r[21],
        source=r[22] or "manual", external_ref=r[23] or "",
        notes=r[24] or "", is_active=bool(r[25]),
        created_at=r[26] or "", updated_at=r[27] or "",
    )


class LoadsMixin(_MixinBase):

    # ── Create ────────────────────────────────────────────────────

    async def add_load(self, account_id: int, **f: Any) -> int:
        """Insert a manual load.  ``status`` must be a known lifecycle value;
        everything else is optional.  Returns the new id."""
        status = str(f.get("status") or "upcoming")
        if status not in LOAD_STATUSES:
            raise ValueError(f"status must be one of {LOAD_STATUSES}")
        pay = str(f.get("payment_status") or "")
        if pay not in PAYMENT_STATUSES:
            raise ValueError(f"payment_status must be one of {PAYMENT_STATUSES}")
        now = self._now()
        cur = await self._db.execute(
            """INSERT INTO loads
               (account_id, load_number, status, payment_status, customer,
                company_code, pickup_location, pickup_date, delivery_location,
                delivery_date, driver_user_id, driver_name, dispatcher_user_id,
                dispatcher_name, vehicle_unit, trailer_unit, total_rate,
                loaded_miles, empty_miles, driver_pay, other_costs, source,
                external_ref, notes, is_active, created_at, updated_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 1, ?, ?)
               RETURNING id""",
            (
                account_id,
                str(f.get("load_number") or ""), status, pay,
                str(f.get("customer") or ""),
                str(f.get("company_code") or ""),
                str(f.get("pickup_location") or ""),
                str(f.get("pickup_date") or ""),
                str(f.get("delivery_location") or ""),
                str(f.get("delivery_date") or ""),
                f.get("driver_user_id"),
                str(f.get("driver_name") or ""),
                f.get("dispatcher_user_id"),
                str(f.get("dispatcher_name") or ""),
                str(f.get("vehicle_unit") or ""),
                str(f.get("trailer_unit") or ""),
                f.get("total_rate"), f.get("loaded_miles"),
                f.get("empty_miles"), f.get("driver_pay"),
                f.get("other_costs"),
                str(f.get("source") or "manual"),
                str(f.get("external_ref") or ""),
                str(f.get("notes") or ""),
                now, now,
            ),
        )
        row = await cur.fetchone()
        await self._db.commit()
        return int(row[0])

    # ── Read ──────────────────────────────────────────────────────

    async def list_loads(
        self,
        account_id: int,
        *,
        status: str | None = None,
        driver_user_id: int | None = None,
        dispatcher_user_id: int | None = None,
        since: str | None = None,
        until: str | None = None,
        include_inactive: bool = False,
        limit: int = 500,
    ) -> list[Load]:
        """Loads for the account, newest pickup first.  ``since``/``until``
        bound the pickup_date window (ISO prefixes compare correctly)."""
        where = ["account_id = ?"]
        args: list[Any] = [account_id]
        if not include_inactive:
            where.append("is_active = 1")
        if status:
            where.append("status = ?")
            args.append(status)
        if driver_user_id is not None:
            where.append("driver_user_id = ?")
            args.append(driver_user_id)
        if dispatcher_user_id is not None:
            where.append("dispatcher_user_id = ?")
            args.append(dispatcher_user_id)
        if since:
            where.append("pickup_date >= ?")
            args.append(since)
        if until:
            where.append("pickup_date <= ?")
            args.append(until)
        args.append(int(limit))
        cur = await self._db.execute(
            f"{_SELECT} WHERE {' AND '.join(where)} "
            "ORDER BY pickup_date DESC, id DESC LIMIT ?",
            tuple(args),
        )
        return [_row_to_load(r) for r in await cur.fetchall()]

    async def get_load(self, account_id: int, load_id: int) -> Optional[Load]:
        cur = await self._db.execute(
            f"{_SELECT} WHERE id = ? AND account_id = ?",
            (load_id, account_id),
        )
        row = await cur.fetchone()
        return _row_to_load(row) if row else None

    async def count_loads_by_status(
        self, account_id: int, *, driver_user_id: int | None = None,
    ) -> dict[str, int]:
        """Active-load counts per status — powers the tab badges."""
        where = ["account_id = ?", "is_active = 1"]
        args: list[Any] = [account_id]
        if driver_user_id is not None:
            where.append("driver_user_id = ?")
            args.append(driver_user_id)
        cur = await self._db.execute(
            f"SELECT status, COUNT(*) FROM loads WHERE {' AND '.join(where)} "
            "GROUP BY status",
            tuple(args),
        )
        out = {s: 0 for s in LOAD_STATUSES}
        for r in await cur.fetchall():
            out[str(r[0])] = int(r[1])
        return out

    # ── Update / delete ───────────────────────────────────────────

    async def update_load(
        self, account_id: int, load_id: int, **fields: Any,
    ) -> bool:
        """Partial update — only allow-listed keys are written."""
        sets: list[str] = []
        params: list[Any] = []
        for key in _LOAD_FIELDS:
            if key in fields and fields[key] is not None:
                if key == "status" and fields[key] not in LOAD_STATUSES:
                    raise ValueError(f"status must be one of {LOAD_STATUSES}")
                if key == "payment_status" and fields[key] not in PAYMENT_STATUSES:
                    raise ValueError(
                        f"payment_status must be one of {PAYMENT_STATUSES}",
                    )
                sets.append(f"{key} = ?")
                params.append(fields[key])
        if not sets:
            return False
        sets.append("updated_at = ?")
        params.extend([self._now(), load_id, account_id])
        cur = await self._db.execute(
            f"UPDATE loads SET {', '.join(sets)} "
            "WHERE id = ? AND account_id = ?",
            tuple(params),
        )
        await self._db.commit()
        return cur.rowcount > 0

    async def deactivate_load(self, account_id: int, load_id: int) -> bool:
        """Soft delete — history (KPI, reports) keeps counting delivered
        work; the row just leaves the operational tabs."""
        cur = await self._db.execute(
            "UPDATE loads SET is_active = 0, updated_at = ? "
            "WHERE id = ? AND account_id = ?",
            (self._now(), load_id, account_id),
        )
        await self._db.commit()
        return cur.rowcount > 0

    # ── Retention ─────────────────────────────────────────────────

    async def prune_loads(self, account_id: int, *, days_keep: int = 730) -> int:
        """Drop loads whose pickup_date fell out of the retention window
        (business records — long window by default)."""
        from datetime import datetime, timedelta, timezone
        cutoff = (
            datetime.now(timezone.utc) - timedelta(days=days_keep)
        ).date().isoformat()
        cur = await self._db.execute(
            "DELETE FROM loads WHERE account_id = ? "
            "AND pickup_date <> '' AND pickup_date < ?",
            (account_id, cutoff),
        )
        await self._db.commit()
        return getattr(cur, "rowcount", 0) or 0
