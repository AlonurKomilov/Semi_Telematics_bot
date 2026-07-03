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

import json
import logging
import re
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any, Optional

from capabilities.integrations import reconciliation as recon

logger = logging.getLogger(__name__)

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
    # Per-account sequential Load ID (1, 2, 3…) — display identity, distinct
    # from load_number (the broker/BOL reference).
    seq: int | None = None
    field_provenance: dict = field(default_factory=dict)


_SELECT = (
    "SELECT id, account_id, load_number, status, payment_status, customer, "
    "company_code, pickup_location, pickup_date, delivery_location, "
    "delivery_date, driver_user_id, driver_name, dispatcher_user_id, "
    "dispatcher_name, vehicle_unit, trailer_unit, total_rate, loaded_miles, "
    "empty_miles, driver_pay, other_costs, source, external_ref, notes, "
    "is_active, created_at, updated_at, field_provenance, seq FROM loads"
)


def _parse_load_provenance(raw: Any) -> dict:
    if isinstance(raw, dict):
        return raw
    try:
        return json.loads(raw) if raw else {}
    except (TypeError, ValueError):
        return {}


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
        field_provenance=_parse_load_provenance(r[28] if len(r) > 28 else None),
        seq=r[29] if len(r) > 29 else None,
    )


# ── Integration reconciliation (Datatruck projection) ──────────────────
#
# A synced load merges through the shared hub: the TMS owns its load data
# (owner_fallback='datatruck' on rows it created), an operator's hand-edit
# PINS the field ('manual' beats every integration), and a genuine
# disagreement is recorded for the review panel.  ``status`` is deliberately
# NOT reconciled — the TMS is authoritative on a synced load's lifecycle
# (that's the point of syncing), so it refreshes on every pass.
LOAD_RECON_FIELDS = (
    "customer", "pickup_location", "pickup_date",
    "delivery_location", "delivery_date",
    "total_rate", "loaded_miles", "empty_miles", "driver_pay",
)
DEFAULT_LOAD_PRECEDENCE = {f: ("datatruck",) for f in LOAD_RECON_FIELDS}
_LOAD_FIELD_LABELS = {
    "customer": "Customer", "pickup_location": "Pickup",
    "pickup_date": "Pickup date", "delivery_location": "Delivery",
    "delivery_date": "Delivery date", "total_rate": "Rate",
    "loaded_miles": "Loaded miles", "empty_miles": "Empty miles",
    "driver_pay": "Driver pay",
}
_LOAD_NUMERIC_FIELDS = frozenset({
    "total_rate", "loaded_miles", "empty_miles", "driver_pay",
})

# TMS status vocabulary → our lifecycle (+ payment).  Tolerant of vendor
# spellings; unknown values land as 'upcoming' so nothing is dropped.
_DT_STATUS_MAP = {
    "upcoming": "upcoming", "pending": "upcoming", "planned": "upcoming",
    "booked": "upcoming", "new": "upcoming", "offer": "upcoming",
    "dispatched": "dispatched", "assigned": "dispatched",
    "in_transit": "in_transit", "in_progress": "in_transit",
    "enroute": "in_transit",
    "en_route": "in_transit", "picked_up": "in_transit",
    "delivered": "delivered", "completed": "delivered", "done": "delivered",
    "canceled": "canceled", "cancelled": "canceled", "void": "canceled",
}


def _map_load_status(raw: Any) -> tuple[str, str]:
    """(lifecycle status, payment_status) from a TMS status string.  The
    'unpaid'/'paid' tabs are billing states of a DELIVERED load."""
    s = str(raw or "").strip().lower().replace("-", "_").replace(" ", "_")
    if s in ("unpaid", "invoiced"):
        return "delivered", "unpaid"
    if s == "paid":
        return "delivered", "paid"
    return _DT_STATUS_MAP.get(s, "upcoming"), ""


def _norm_company(v: str) -> str:
    """Normalize a company name for matching: lowercase, alnum+space only,
    common suffixes dropped ("Premier Trucking Group Inc" == "premier
    trucking group")."""
    n = re.sub(r"[^a-z0-9 ]+", " ", str(v or "").lower())
    n = re.sub(r"\b(inc|llc|corp|co|ltd)\b", " ", n)
    return re.sub(r"\s+", " ", n).strip()


async def _apply_load_field(
    db: Any, account_id: int, entity_id: int, field: str, value: Any,
) -> None:
    """Resolution applier for the 'load' entity: write the chosen value and
    PIN it so no later sync undoes the operator's decision."""
    if field in _LOAD_NUMERIC_FIELDS:
        try:
            value = float(value) if str(value).strip() else None
        except (TypeError, ValueError):
            value = None
    await db.write_load_field_pinned(account_id, entity_id, field, value)


recon.register_reconciled_entity(
    "load",
    fields=LOAD_RECON_FIELDS,
    default_precedence=DEFAULT_LOAD_PRECEDENCE,
    field_labels=_LOAD_FIELD_LABELS,
    sources=("datatruck",),
    apply_resolution=_apply_load_field,
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
            "SELECT COALESCE(MAX(seq), 0) + 1 FROM loads WHERE account_id = ?",
            (account_id,),
        )
        next_seq = int((await cur.fetchone())[0])
        cur = await self._db.execute(
            """INSERT INTO loads
               (seq, account_id, load_number, status, payment_status, customer,
                company_code, pickup_location, pickup_date, delivery_location,
                delivery_date, driver_user_id, driver_name, dispatcher_user_id,
                dispatcher_name, vehicle_unit, trailer_unit, total_rate,
                loaded_miles, empty_miles, driver_pay, other_costs, source,
                external_ref, notes, is_active, created_at, updated_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 1, ?, ?)
               RETURNING id""",
            (
                next_seq, account_id,
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
        # Operator edits PIN the reconcilable fields they touch (stamp
        # 'manual' in field_provenance) so a later TMS sync can't revert
        # the human's correction — same Layer-2 semantic as vehicles.
        pin = [k for k in fields if k in LOAD_RECON_FIELDS and fields[k] is not None]
        if pin:
            try:
                cur = await self._db.execute(
                    "SELECT field_provenance FROM loads "
                    "WHERE id = ? AND account_id = ?",
                    (load_id, account_id),
                )
                row = await cur.fetchone()
                prov = _parse_load_provenance(row[0]) if row else {}
                for f in pin:
                    prov[f] = recon.MANUAL_SOURCE
                sets.append("field_provenance = ?")
                params.append(json.dumps(prov))
            except Exception as e:
                logger.debug("load provenance pin skipped: %s", e)
        sets.append("updated_at = ?")
        params.extend([self._now(), load_id, account_id])
        cur = await self._db.execute(
            f"UPDATE loads SET {', '.join(sets)} "
            "WHERE id = ? AND account_id = ?",
            tuple(params),
        )
        await self._db.commit()
        return cur.rowcount > 0

    async def write_load_field_pinned(
        self, account_id: int, load_id: int, field: str, value: Any,
    ) -> None:
        """Write one reconcilable field onto a load and PIN it
        (provenance='manual').  Used by conflict resolution."""
        cur = await self._db.execute(
            "SELECT field_provenance FROM loads WHERE id = ? AND account_id = ?",
            (load_id, account_id),
        )
        row = await cur.fetchone()
        if not row:
            return
        prov = _parse_load_provenance(row[0])
        prov[field] = recon.MANUAL_SOURCE
        async with self.transaction():
            await self._db.execute(
                f"UPDATE loads SET {field} = ?, field_provenance = ?, "
                "updated_at = ? WHERE id = ? AND account_id = ?",
                (value, json.dumps(prov), self._now(), load_id, account_id),
            )

    # ── Integration projection (TMS orders → loads) ────────────────

    async def project_external_loads(
        self, account_id: int, rows: list[dict], *, source: str = "datatruck",
    ) -> int:
        """Project a synced TMS order list onto the canonical ``loads``
        table — keyed on ``(account, source, external_ref)``.

          * NEW ref → INSERT a load: status mapped from the TMS vocabulary
            (unpaid/paid land as delivered + payment_status), driver resolved
            via ``users.datatruck_driver_id`` (the Increment-A link), truck /
            trailer units resolved from the synced rosters, financials
            promoted where present.
          * KNOWN ref → lifecycle/payment REFRESH (the TMS is authoritative
            on a synced load's status) + data fields merged through the
            reconciliation hub: operator pins win, genuine disagreements are
            recorded for the review panel.

        Never touches manual loads (no external_ref).  Returns rows
        inserted-or-updated.
        """
        if not rows:
            return 0
        # externalid → unit lookups from the synced rosters.
        async def _unit_map(table: str) -> dict[str, str]:
            try:
                cur = await self._db.execute(
                    f"SELECT external_id, unit_number FROM {table} "
                    "WHERE account_id = ?",
                    (account_id,),
                )
                return {
                    str(r[0]): str(r[1] or "") for r in await cur.fetchall()
                }
            except Exception:
                return {}
        truck_units = await _unit_map("datatruck_trucks")
        trailer_units = await _unit_map("datatruck_trailers")
        # Company NAME → code (the order carries mc_number__company_name).
        cur = await self._db.execute(
            "SELECT code, display_name FROM companies "
            "WHERE account_id = ? AND is_active = 1",
            (account_id,),
        )
        companies = [(str(r[0] or ""), _norm_company(r[1])) for r in await cur.fetchall()]

        def _company_code(name: str) -> str:
            n = _norm_company(name)
            if not n:
                return ""
            for code, disp in companies:
                if disp and (n == disp or n.startswith(disp) or disp.startswith(n)):
                    return code
            return ""

        # Datatruck driver id → (user_id, display_name)  (Increment A link).
        cur = await self._db.execute(
            "SELECT id, datatruck_driver_id, display_name FROM users "
            "WHERE account_id = ? AND role = 'driver' "
            "AND datatruck_driver_id IS NOT NULL AND datatruck_driver_id <> ''",
            (account_id,),
        )
        driver_by_ref = {
            str(r[1]): (int(r[0]), str(r[2] or "")) for r in await cur.fetchall()
        }
        # Existing synced loads by external_ref.
        cur = await self._db.execute(
            f"{_SELECT} WHERE account_id = ? AND source = ? AND external_ref <> ''",
            (account_id, source),
        )
        by_ref = {l.external_ref: l for l in
                  (_row_to_load(r) for r in await cur.fetchall())}

        precedence = await recon.get_precedence(self, account_id, "load")
        now = self._now()
        cur = await self._db.execute(
            "SELECT COALESCE(MAX(seq), 0) FROM loads WHERE account_id = ?",
            (account_id,),
        )
        next_seq = int((await cur.fetchone())[0])
        written = 0
        conflict_ops: list = []
        async with self.transaction():
            for r in rows:
                ref = str(r.get("external_id") or "").strip()
                if not ref:
                    continue
                status, payment = _map_load_status(r.get("status"))
                duid, dname = driver_by_ref.get(
                    str(r.get("driver_external_id") or ""), (None, ""),
                )
                if not dname:
                    dname = str(r.get("driver_name") or "")
                # The openapi order carries the truck UNIT directly
                # (trip.truck__unit_number); the roster lookup by external
                # id stays as the fallback for older/other shapes.
                vunit = str(r.get("truck_unit") or "") or \
                    truck_units.get(str(r.get("truck_external_id") or ""), "")
                tunit = str(r.get("trailer_unit") or "") or \
                    trailer_units.get(str(r.get("trailer_external_id") or ""), "")
                ccode = _company_code(str(r.get("company_name") or ""))
                incoming = {
                    "customer":          r.get("customer"),
                    "pickup_location":   r.get("origin"),
                    "pickup_date":       r.get("pickup_date"),
                    "delivery_location": r.get("destination"),
                    "delivery_date":     r.get("delivery_date"),
                    "total_rate":        r.get("total_rate"),
                    "loaded_miles":      r.get("loaded_miles"),
                    "empty_miles":       r.get("empty_miles"),
                    "driver_pay":        r.get("driver_pay"),
                }
                existing = by_ref.get(ref)

                if existing is None:
                    prov = {
                        f: source for f, v in incoming.items()
                        if not recon.is_unset(v)
                    }
                    next_seq += 1
                    await self._db.execute(
                        """INSERT INTO loads
                           (seq, account_id, load_number, status, payment_status,
                            customer, company_code, pickup_location, pickup_date,
                            delivery_location, delivery_date, driver_user_id,
                            driver_name, dispatcher_user_id, dispatcher_name,
                            vehicle_unit, trailer_unit, total_rate, loaded_miles,
                            empty_miles, driver_pay, other_costs, source,
                            external_ref, field_provenance, notes, is_active,
                            created_at, updated_at)
                           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 1, ?, ?)
                           ON CONFLICT(account_id, source, external_ref)
                           WHERE external_ref <> '' DO NOTHING""",
                        (
                            next_seq, account_id,
                            str(r.get("order_number") or ""), status, payment,
                            str(r.get("customer") or ""), ccode,
                            str(r.get("origin") or ""),
                            str(r.get("pickup_date") or ""),
                            str(r.get("destination") or ""),
                            str(r.get("delivery_date") or ""),
                            duid, dname,
                            None, str(r.get("dispatcher_name") or ""),
                            vunit, tunit,
                            r.get("total_rate"), r.get("loaded_miles"),
                            r.get("empty_miles"), r.get("driver_pay"),
                            None, source, ref, json.dumps(prov), "",
                            now, now,
                        ),
                    )
                    written += 1
                    continue

                # Known ref — the TMS refreshes lifecycle + linkage; the
                # data fields merge through the hub (pins + conflicts).
                mr = recon.merge_fields(
                    current={f: getattr(existing, f) for f in LOAD_RECON_FIELDS},
                    provenance=existing.field_provenance,
                    owner_fallback=source,
                    incoming=incoming, source=source,
                    fields=LOAD_RECON_FIELDS, precedence=precedence,
                )
                sets: list[str] = []
                params: list[Any] = []
                for f in LOAD_RECON_FIELDS:
                    if f in mr.updates:
                        sets.append(f"{f} = ?")
                        params.append(mr.updates[f])
                if existing.status != status:
                    sets.append("status = ?")
                    params.append(status)
                if payment and existing.payment_status != payment:
                    sets.append("payment_status = ?")
                    params.append(payment)
                if duid is not None and existing.driver_user_id != duid:
                    sets.append("driver_user_id = ?")
                    params.append(duid)
                    sets.append("driver_name = ?")
                    params.append(dname)
                if vunit and existing.vehicle_unit != vunit:
                    sets.append("vehicle_unit = ?")
                    params.append(vunit)
                if tunit and existing.trailer_unit != tunit:
                    sets.append("trailer_unit = ?")
                    params.append(tunit)
                onum = str(r.get("order_number") or "")
                if onum and existing.load_number != onum:
                    sets.append("load_number = ?")
                    params.append(onum)
                if dname and existing.driver_name != dname:
                    sets.append("driver_name = ?")
                    params.append(dname)
                dpname = str(r.get("dispatcher_name") or "")
                if dpname and existing.dispatcher_name != dpname:
                    sets.append("dispatcher_name = ?")
                    params.append(dpname)
                if ccode and existing.company_code != ccode:
                    sets.append("company_code = ?")
                    params.append(ccode)
                if not sets and not mr.cleared:
                    continue
                sets.append("field_provenance = ?")
                params.append(json.dumps(mr.provenance))
                sets.append("updated_at = ?")
                params.extend([now, existing.id, account_id])
                await self._db.execute(
                    f"UPDATE loads SET {', '.join(sets)} "
                    "WHERE id = ? AND account_id = ?",
                    tuple(params),
                )
                written += 1
                if mr.conflicts or mr.cleared:
                    conflict_ops.append((existing.id, mr.conflicts, mr.cleared))
        await recon.sync_batch(self, account_id, "load", conflict_ops)
        return written

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
