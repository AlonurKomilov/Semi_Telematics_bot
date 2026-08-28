"""Vehicle registry CRUD mixin — the single source of truth for vehicles.

A vehicle exists because the account *owns* it, in OUR Postgres DB —
not because Samsara happens to report it.  Integrations enrich rows
here (Samsara live state, Datatruck TMS) but do not define the fleet.
Trailers and not-yet-telemetered trucks live here just like any other
vehicle, with no live-state match.

Identity / dedup: ``UNIQUE(account_id, company_code, unit_number)`` —
the same key ``vehicle_state`` uses, so the live-state enrichment join
in ``warehouse_reader`` is 1:1.

``source`` records who created the row:
  * ``manual``    — the operator added it on the Vehicles page.
  * ``samsara``   — the 60s ingestor saw it in Samsara and upserted it.
  * ``datatruck`` — (Phase 2) projected from the datatruck_* sync tables.

Tenant isolation is the ``account_id`` filter on every query (and
Postgres RLS when ``ENABLE_RLS=1``).
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any, Optional

from capabilities.activity_trail import diff_rows
from capabilities import source as recon
from capabilities.source import MANUAL_SOURCE, is_unset

logger = logging.getLogger(__name__)

if TYPE_CHECKING:
    class _MixinBase:
        """Typing stub — provided by the concrete Database at runtime."""
        _db: Any
        def transaction(self) -> Any: ...
        async def read_all(self, sql: str, params: tuple = ()) -> list: ...
        async def read_one(self, sql: str, params: tuple = ()) -> Any: ...
        async def append_activity_events(
            self, account_id: int, events: list[dict],
        ) -> None: ...
        @staticmethod
        def _now() -> str: ...
else:
    _MixinBase = object


# The promoted columns an upsert/update touches, in stable order.
_FIELDS = (
    "company_code", "unit_number", "vehicle_type", "vin", "plate_number",
    "make", "model", "year", "status", "source", "telematics_ref", "notes",
)

_VALID_TYPES = ("truck", "trailer", "other")

# Spec fields an integration sync may fill on an existing (matched) row.
_SPEC_FILL = ("vin", "plate_number", "make", "model", "year", "gateway_serial")


def _model_year(value: Any) -> int | None:
    """A model year we can safely bind to an INTEGER column.

    Providers are casual about this one: the same roster carries 2024,
    "2024", "" and "N/A" depending on how each record was entered.
    asyncpg refuses a str for an int4 parameter, and because the whole
    roster upserts inside ONE transaction, a single such row used to
    roll back every other vehicle with it — silently, on every tick.
    Anything that is not plausibly a year becomes None, which the
    merge treats as "no opinion" rather than as a value to write.
    """
    if value is None or isinstance(value, bool):
        return None
    try:
        year = int(str(value).strip())
    except (TypeError, ValueError):
        return None
    return year if 1900 <= year <= 2100 else None

# ── Source precedence + reconciliation (Layer 1) ──────────────────────
#
# The merge / precedence / conflict MECHANISM lives in the shared, integration-
# owned hub (``capabilities/integrations/reconciliation``).  Here we only
# declare the vehicle entity's reconcilable shape + how to APPLY a resolution;
# the write paths below call ``recon.merge_fields`` / ``recon.sync_batch`` and
# read precedence via ``recon.get_precedence``.
#
# Default precedence: Datatruck (the TMS, system-of-record for paperwork)
# outranks Samsara (telematics) for the spec fields; the owner can flip any
# field later.  A ``manual`` operator edit always outranks every integration.
DEFAULT_FIELD_PRECEDENCE: dict[str, tuple[str, ...]] = {
    "vin":          ("datatruck", "samsara"),
    "plate_number": ("datatruck", "samsara"),
    "make":         ("datatruck", "samsara"),
    "model":        ("datatruck", "samsara"),
    "year":         ("datatruck", "samsara"),
}
# Integrations that can write vehicle spec fields (the precedence UI choices).
VEHICLE_SPEC_SOURCES = ("datatruck", "samsara")
# Human labels for the configurable spec fields (the precedence panel).
_VEHICLE_FIELD_LABELS = {
    "vin": "VIN", "plate_number": "Plate", "make": "Make",
    "model": "Model", "year": "Year",
}


async def _apply_vehicle_field(
    db: Any, account_id: int, entity_id: int, field: str, value: Any,
) -> None:
    """Write a resolved-conflict value onto a vehicle + PIN it: ``update_vehicle``
    stamps the field ``manual`` in provenance so no later sync undoes the
    operator's choice.  Registered as the 'vehicle' resolution applier."""
    if field == "year":           # year is an INTEGER column
        try:
            value = int(value) if str(value).strip() else None
        except (TypeError, ValueError):
            value = None
    await db.update_vehicle(account_id, entity_id, **{field: value})


# Declare 'vehicle' with the shared hub — this is the whole "by feature" hook.
recon.register_reconciled_entity(
    "vehicle",
    fields=_SPEC_FILL,
    default_precedence=DEFAULT_FIELD_PRECEDENCE,
    field_labels=_VEHICLE_FIELD_LABELS,
    sources=VEHICLE_SPEC_SOURCES,
    apply_resolution=_apply_vehicle_field,
)


def _index_existing(existing: list["Vehicle"]) -> tuple[dict, dict, dict]:
    """Build VIN / plate / unit lookups over the current registry, so a
    planner and the projection match identically.

    IDENTITY vs LABEL, the same split the ref match makes.  A VIN and a
    plate name a physical asset, so a retired row still owns them and
    still matches — that is what stops an archived truck reappearing as
    a brand-new row on the next sync.  A unit number is a LABEL people
    REUSE: once a truck is retired its door number can legitimately go
    on a different truck, and matching THAT by name would merge two
    vehicles' histories into one row.  So labels index live rows only.
    """
    by_vin: dict[str, Vehicle] = {}
    by_plate: dict[str, Vehicle] = {}
    by_unit: dict[str, list[Vehicle]] = {}
    for v in existing:
        if v.vin:
            by_vin.setdefault(v.vin.strip().upper(), v)
        if v.plate_number:
            by_plate.setdefault(v.plate_number.strip().upper(), v)
        if v.is_active:
            by_unit.setdefault(v.unit_number.strip().lower(), []).append(v)
    return by_vin, by_plate, by_unit


def _match_existing(
    r: dict[str, Any], by_vin: dict, by_plate: dict, by_unit: dict,
) -> tuple["Vehicle | None", str]:
    """Reconcile one incoming row against the registry indexes.

    Priority: VIN exact → plate exact → unique unit number.  Returns
    ``(vehicle, how)`` where ``how`` is ``'vin' | 'plate' | 'unit'`` on a
    match, ``'ambiguous'`` when the unit exists on >1 row (can't safely
    pick), or ``'none'`` when nothing matched.
    """
    vin = str(r.get("vin") or "").strip()
    if vin and vin.upper() in by_vin:
        return by_vin[vin.upper()], "vin"
    plate = str(r.get("plate_number") or "").strip()
    if plate and plate.upper() in by_plate:
        return by_plate[plate.upper()], "plate"
    cands = by_unit.get(str(r.get("unit_number") or "").strip().lower(), [])
    if len(cands) == 1:
        return cands[0], "unit"
    if len(cands) > 1:
        return None, "ambiguous"
    return None, "none"


@dataclass
class Vehicle:
    id: int
    account_id: int
    company_code: str
    unit_number: str
    vehicle_type: str
    vin: str
    plate_number: str
    make: str
    model: str
    year: int | None
    status: str
    source: str
    telematics_ref: str
    notes: str
    is_active: bool
    created_at: str
    updated_at: str
    # {spec_field: source} — who last authoritatively set each spec field.
    field_provenance: dict = field(default_factory=dict)
    # Datatruck-side asset binding (like telematics_ref for Samsara).
    datatruck_ref: str = ""
    # Telematics gateway hardware serial — an identity anchor the
    # ingest's identity watch compares each tick (gateway_swap events).
    gateway_serial: str = ""
    #: Why this row is inactive — ``'operator'`` (a person retired it),
    #: ``'sweep'`` (the badge went silent), or ``''`` while active.
    archived_reason: str = ""
    #: The operator status archiving overwrote, so a restore can put it
    #: back.  Empty for sweep-retired rows: their status was never
    #: touched, so the live value already IS the pre-archive one.
    status_before_archive: str = ""


def _parse_provenance(raw: Any) -> dict:
    if isinstance(raw, dict):
        return raw
    try:
        return json.loads(raw) if raw else {}
    except (TypeError, ValueError):
        return {}


#: The value ``archived_reason`` carries when a PERSON retired the row.
#: The other is ``'sweep'`` — the departure sweep dropping a badge that
#: went silent, which the ingest is meant to revive when it reports
#: again.  Gating anything on bare ``is_active`` conflates the two and
#: permanently zombies a swept truck whose gateway comes back.
ARCHIVED_BY_OPERATOR = "operator"


def _operator_retired(v: Vehicle) -> bool:
    """Did a PERSON retire this row, or did the departure sweep?

    Reads the column that says so.  This used to infer it from
    ``status == 'inactive'`` — encoding lifecycle in a free-text
    operator field, which is the side-channel that made the two
    indistinguishable to begin with.  Migration 201 moved the answer
    into ``archived_reason`` and the heuristic survives only inside
    that migration.
    """
    return not v.is_active and v.archived_reason == ARCHIVED_BY_OPERATOR


def _row_to_vehicle(r) -> Vehicle:
    return Vehicle(
        id=r[0], account_id=r[1], company_code=r[2] or "",
        unit_number=r[3] or "", vehicle_type=r[4] or "truck",
        vin=r[5] or "", plate_number=r[6] or "", make=r[7] or "",
        model=r[8] or "", year=r[9], status=r[10] or "active",
        source=r[11] or "manual", telematics_ref=r[12] or "",
        notes=r[13] or "", is_active=bool(r[14]),
        created_at=r[15] or "", updated_at=r[16] or "",
        field_provenance=_parse_provenance(r[17] if len(r) > 17 else None),
        datatruck_ref=(r[18] or "") if len(r) > 18 else "",
        gateway_serial=(r[19] or "") if len(r) > 19 else "",
        archived_reason=(r[20] or "") if len(r) > 20 else "",
        status_before_archive=(r[21] or "") if len(r) > 21 else "",
    )


_SELECT = (
    "SELECT id, account_id, company_code, unit_number, vehicle_type, vin, "
    "plate_number, make, model, year, status, source, telematics_ref, "
    "notes, is_active, created_at, updated_at, field_provenance, "
    "datatruck_ref, gateway_serial, archived_reason, status_before_archive "
    "FROM vehicles"
)


class VehiclesRegistryMixin(_MixinBase):

    # ── Create ────────────────────────────────────────────────────

    async def add_vehicle(
        self,
        account_id: int,
        *,
        unit_number: str,
        vehicle_type: str = "truck",
        company_code: str = "",
        vin: str = "",
        plate_number: str = "",
        make: str = "",
        model: str = "",
        year: Optional[int] = None,
        status: str = "active",
        source: str = "manual",
        telematics_ref: str = "",
        notes: str = "",
        actor_user_id: Optional[int] = None,
    ) -> int:
        """Create a vehicle.  Returns the new row id.

        Raises ``ValueError`` on a blank unit_number or an unknown
        vehicle_type — the route maps these to a 400.
        """
        unit_number = (unit_number or "").strip()
        if not unit_number:
            raise ValueError("unit_number is required")
        if vehicle_type not in _VALID_TYPES:
            raise ValueError(
                f"vehicle_type must be one of {_VALID_TYPES}",
            )
        now = self._now()
        # One transaction: row + trail commit together (pool proxy
        # auto-commits bare statements; commit() there is a no-op).
        async with self.transaction():
            cur = await self._db.execute(
                """INSERT INTO vehicles
                   (account_id, company_code, unit_number, vehicle_type, vin,
                    plate_number, make, model, year, status, source,
                    telematics_ref, notes, is_active, created_at, updated_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 1, ?, ?)""",
                (
                    account_id, company_code, unit_number, vehicle_type, vin,
                    plate_number, make, model, year, status, source,
                    telematics_ref, notes, now, now,
                ),
            )
            vehicle_id = cur.lastrowid
            # Trail (capabilities/activity_trail): human creations only —
            # integration upserts ride the project/ingest paths, and any
            # caller without an actor stays un-evented by contract
            # (people, not machines).
            if actor_user_id is not None:
                await self.append_activity_events(account_id, [{
                    "entity_type": "vehicle", "entity_id": vehicle_id,
                    "action": "create", "actor_user_id": actor_user_id,
                    "changes": diff_rows({}, {
                        "unit_number": unit_number, "vehicle_type": vehicle_type,
                        "company_code": company_code, "vin": vin,
                        "make": make, "model": model, "year": year,
                    }),
                }])
            return vehicle_id

    async def split_vehicle_identity(
        self,
        account_id: int,
        *,
        old_vehicle_id: int,
        new_company_code: str,
        new_unit_number: str,
        new_vin: str,
        restore_vin: str = "",
        archive_old: bool = False,
        actor_user_id: Optional[int] = None,
    ) -> int:
        """Resolve a vin_change as "a DIFFERENT truck is behind this
        telematics id": mint the new truck and move the link to it.

        The old unit keeps every stored mile — warehouse rows are
        stamped with registry_id at ingest, so history binds to the
        identity that produced it and the timeline simply forks here.

        In one transaction: (1) create the new unit carrying the new
        VIN and the telematics_ref; (2) strip the ref from the old unit
        and restore its true VIN (the ingest's auto-follow overwrote it
        with the new truck's); (3) optionally retire the old unit.
        Returns the new vehicle id.  ValueError on a missing/linkless
        old row or a (company, unit) collision — routes map it to 400.
        """
        new_unit_number = (new_unit_number or "").strip()
        if not new_unit_number:
            raise ValueError("unit_number is required")
        old = await self.get_vehicle(account_id, old_vehicle_id)
        if old is None:
            raise ValueError("vehicle not found")
        if not old.telematics_ref:
            raise ValueError("vehicle has no telematics link to move")
        # archived-ok, and load-bearing: the unique index spans RETIRED
        # rows, so a collision check that skipped them would let the
        # INSERT below hit the constraint and fail the split.
        dup = await self.read_one(
            "SELECT id FROM vehicles WHERE account_id = ? "
            "AND company_code = ? AND unit_number = ?",
            (account_id, new_company_code, new_unit_number),
        )
        if dup:
            raise ValueError(
                f"unit {new_unit_number or '?'} already exists in "
                f"company {new_company_code or '(none)'}"
            )
        now = self._now()
        async with self.transaction():
            # Spec fields (make/model/year) start empty ON PURPOSE: they
            # describe the NEW physical truck, and the next ingest tick
            # fills them from the provider (matched by the moved ref/VIN).
            cur = await self._db.execute(
                """INSERT INTO vehicles
                   (account_id, company_code, unit_number, vehicle_type,
                    vin, status, source, telematics_ref,
                    is_active, created_at, updated_at)
                   VALUES (?, ?, ?, ?, ?, 'active', 'samsara', ?, 1, ?, ?)""",
                (account_id, new_company_code, new_unit_number,
                 old.vehicle_type, str(new_vin or ""),
                 old.telematics_ref, now, now),
            )
            new_id = cur.lastrowid
            sets = ["telematics_ref = ''", "updated_at = ?"]
            params: list[Any] = [now]
            if restore_vin:
                sets.append("vin = ?")
                params.append(str(restore_vin))
            if archive_old:
                sets.append("is_active = 0")
                sets.append(
                    "status_before_archive = CASE WHEN status_before_archive "
                    "= '' THEN status ELSE status_before_archive END")
                sets.append("status = 'inactive'")
                sets.append(f"archived_reason = '{ARCHIVED_BY_OPERATOR}'")
            await self._db.execute(
                f"UPDATE vehicles SET {', '.join(sets)} "
                "WHERE id = ? AND account_id = ?",
                (*params, old_vehicle_id, account_id),
            )
            if actor_user_id is not None:
                await self.append_activity_events(account_id, [
                    {
                        "entity_type": "vehicle", "entity_id": new_id,
                        "action": "create", "actor_user_id": actor_user_id,
                        "changes": diff_rows({}, {
                            "unit_number": new_unit_number,
                            "company_code": new_company_code,
                            "vin": str(new_vin or ""),
                            "telematics_ref": old.telematics_ref,
                        }),
                    },
                    {
                        "entity_type": "vehicle", "entity_id": old_vehicle_id,
                        "action": "update", "actor_user_id": actor_user_id,
                        "changes": diff_rows(
                            {"telematics_ref": old.telematics_ref,
                             "vin": old.vin,
                             "status": old.status},
                            {"telematics_ref": "",
                             "vin": restore_vin or old.vin,
                             "status": "inactive" if archive_old
                             else old.status},
                        ),
                    },
                ])
            return new_id

    # ── Read ──────────────────────────────────────────────────────

    async def list_vehicles(
        self,
        account_id: int,
        *,
        vehicle_type: Optional[str] = None,
        company_code: Optional[str] = None,
        include_inactive: bool = False,
    ) -> list[Vehicle]:
        """All registry vehicles for an account, newest first.  Filters
        are optional; ``include_inactive`` surfaces soft-deleted rows
        for an audit view."""
        clauses = ["account_id = ?"]
        params: list[Any] = [account_id]
        if not include_inactive:
            clauses.append("is_active = 1")
        if vehicle_type:
            clauses.append("vehicle_type = ?")
            params.append(vehicle_type)
        if company_code:
            clauses.append("company_code = ?")
            params.append(company_code)
        rows = await self.read_all(
            f"{_SELECT} WHERE {' AND '.join(clauses)} "
            "ORDER BY unit_number, id",
            tuple(params),
        )
        return [_row_to_vehicle(r) for r in rows]

    async def get_vehicle(
        self, account_id: int, vehicle_id: int,
    ) -> Vehicle | None:
        row = await self.read_one(
            f"{_SELECT} WHERE id = ? AND account_id = ?",
            (vehicle_id, account_id),
        )
        return _row_to_vehicle(row) if row else None

    async def count_vehicles(self, account_id: int) -> int:
        """Active-row count — the registry-first read path uses this to
        decide whether to take the registry spine or fall back to the
        legacy pure-Samsara path on an un-backfilled account."""
        row = await self.read_one(
            "SELECT COUNT(*) FROM vehicles "
            "WHERE account_id = ? AND is_active = 1",
            (account_id,),
        )
        return int(row[0] or 0) if row else 0

    # ── Update ────────────────────────────────────────────────────

    async def update_vehicle(
        self, account_id: int, vehicle_id: int,
        actor_user_id: Optional[int] = None, **fields: Any,
    ) -> bool:
        """Partial update — only the keys present in ``fields`` (and in
        ``_FIELDS``) are written.  Returns True when a row changed.

        Operator edits PIN the spec fields they touch: each edited spec field
        is stamped ``manual`` in ``field_provenance`` so no later integration
        sync overwrites the human's correction (Layer 2)."""
        sets: list[str] = []
        params: list[Any] = []
        edited_spec: list[str] = []
        for key in _FIELDS:
            if key in fields and fields[key] is not None:
                if key == "vehicle_type" and fields[key] not in _VALID_TYPES:
                    raise ValueError(
                        f"vehicle_type must be one of {_VALID_TYPES}",
                    )
                sets.append(f"{key} = ?")
                params.append(fields[key])
                if key in _SPEC_FILL:
                    edited_spec.append(key)
        if not sets:
            return False
        async with self.transaction():
            # Trail: the pre-edit row — values, not field names.
            old: dict = {}
            if actor_user_id is not None:
                # archived-ok: by primary key, for the trail's before-image.
                # A retired truck can still be edited.
                cur = await self._db.execute(
                    "SELECT * FROM vehicles WHERE id = ? AND account_id = ?",
                    (vehicle_id, account_id),
                )
                r = await cur.fetchone()
                old = dict(r) if r else {}
            if edited_spec:
                # Pin the edited spec fields so syncs can't undo the correction.
                # archived-ok: by primary key, reading one row's own provenance.
                cur = await self._db.execute(
                    "SELECT field_provenance FROM vehicles "
                    "WHERE id = ? AND account_id = ?",
                    (vehicle_id, account_id),
                )
                row = await cur.fetchone()
                prov = _parse_provenance(dict(row).get("field_provenance")) if row else {}
                for f in edited_spec:
                    prov[f] = MANUAL_SOURCE
                sets.append("field_provenance = ?")
                params.append(json.dumps(prov))
            sets.append("updated_at = ?")
            params.extend([self._now(), vehicle_id, account_id])
            cur = await self._db.execute(
                f"UPDATE vehicles SET {', '.join(sets)} "
                "WHERE id = ? AND account_id = ?",
                tuple(params),
            )
            touched = cur.rowcount > 0
            if touched and old:
                written = {k: v for k, v in fields.items()
                           if k in _FIELDS and v is not None}
                changes = diff_rows(old, written, fields=written.keys())
                if changes:
                    await self.append_activity_events(account_id, [{
                        "entity_type": "vehicle", "entity_id": vehicle_id,
                        "action": "update", "actor_user_id": actor_user_id,
                        "changes": changes,
                    }])
            return touched

    async def deactivate_vehicle(
        self, account_id: int, vehicle_id: int,
        actor_user_id: Optional[int] = None,
    ) -> bool:
        """Soft delete — keeps history intact (maintenance, fuel, etc.
        still reference the unit by name).  Returns True if a row
        flipped."""
        async with self.transaction():
            old_status = None
            unit = ""
            ref = ""
            # Read unconditionally: the trail entry needs the status and
            # unit, and the live-row drop below needs the ref whether or
            # not there is an actor to record.
            # archived-ok: this IS the archive path, reading the row it is
            # about to retire.
            cur = await self._db.execute(
                "SELECT status, unit_number, telematics_ref FROM vehicles "
                "WHERE id = ? AND account_id = ?",
                (vehicle_id, account_id),
            )
            r = await cur.fetchone()
            if r:
                old_status, unit, ref = r[0], r[1], (r[2] or "")
            # `status = 'inactive'` stays — existing readers depend on
            # it — but the value it overwrites is kept, so a restore can
            # put the truck back the way it was instead of guessing
            # 'active' over a truck that was in the shop.
            cur = await self._db.execute(
                "UPDATE vehicles SET is_active = 0, "
                "status_before_archive = CASE WHEN status_before_archive = '' "
                "  THEN status ELSE status_before_archive END, "
                "status = 'inactive', archived_reason = ?, "
                "updated_at = ? WHERE id = ? AND account_id = ?",
                (ARCHIVED_BY_OPERATOR, self._now(), vehicle_id, account_id),
            )
            touched = cur.rowcount > 0
            # Drop the live row NOW, rather than leaving it to the
            # departure sweep 30 days from now.  The ingest gate stops
            # NEW rows, but the last one already written keeps a fresh
            # `captured_at` — and billing counts exactly that, so the
            # customer would go on paying for a truck they retired until
            # the sweep collected it.  Every staleness gate downstream
            # closes on the same act.  History (minute/hour/day tiers)
            # is untouched; this is the "now" row only, and the ingest
            # rebuilds it the moment the truck is restored.
            if touched and ref:
                try:
                    # Close what is already on the board.  Archiving
                    # stops NEW alerts, but the hourly re-escalation
                    # reads alert_history alone and never consults the
                    # registry — so a fault raised last week went on
                    # paging people about a truck retired yesterday.
                    # Rows are kept; they stop being active, and WHY is
                    # in the trail entry below.
                    closed = await self.close_alerts_for_retired_vehicle(
                        account_id, ref)
                    if closed:
                        logger.info(
                            "archive: closed %d open alert(s) for vehicle "
                            "%d acct=%d", closed, vehicle_id, account_id,
                        )
                except Exception:
                    logger.warning(
                        "archive: open alerts not closed for vehicle %d "
                        "acct=%d — they will keep escalating",
                        vehicle_id, account_id, exc_info=True,
                    )
                try:
                    # Through the warehouse mixin, never raw SQL from
                    # here: physical warehouse tables are machinery-
                    # internal and CI enforces it
                    # (tests/test_layer_boundaries.py).
                    await self.drop_live_state(account_id, ref)
                except Exception:
                    # The archive itself stands; the row goes stale on
                    # its own once the gate stops refreshing it.
                    logger.warning(
                        "archive: live row not cleared for vehicle %d "
                        "acct=%d — it will age out instead",
                        vehicle_id, account_id, exc_info=True,
                    )
            if touched and actor_user_id is not None:
                await self.append_activity_events(account_id, [{
                    "entity_type": "vehicle", "entity_id": vehicle_id,
                    "action": "deactivate", "actor_user_id": actor_user_id,
                    "changes": {"status": {"from": old_status, "to": "inactive"}},
                    "context": {"unit_number": unit},
                }])
            return touched

    # ── Integration upsert (backfill + ongoing sync) ──────────────

    async def project_external_vehicles(
        self,
        account_id: int,
        rows: list[dict[str, Any]],
        *,
        vehicle_type: str,
        source: str,
    ) -> int:
        """Project a TMS/integration vehicle list (Datatruck trucks or
        trailers) onto the registry, reconciling against vehicles that
        may already exist from another source.

        Why this differs from ``upsert_from_integration`` (which keys
        strictly on company_code+unit_number): a Datatruck vehicle has
        the VIN/plate/make Samsara's warehouse lacks, but NO company
        scoping (the Datatruck token is account-wide).  A naive insert
        would duplicate every truck Samsara already registered.

        Match priority per incoming row:
          1. **VIN exact** — unambiguous; the same physical asset.
          2. **Unique unit_number** across the account (case-insensitive)
             — when exactly one registry row carries that unit.  More
             than one (the legal "two trucks named 103 in different
             orgs" case) is ambiguous, so we don't guess.
          3. **No match** → insert a new row (source=given, the given
             vehicle_type).

        On a match we MERGE by source precedence (see
        ``recon.merge_fields``): this source fills empty fields and may
        overwrite a *lower*-priority source's value, but never an operator
        pin — and it never touches the existing ``source`` /
        ``vehicle_type`` / ``status`` / ``notes``.

        Returns the number of rows inserted-or-merged.
        """
        if not rows:
            return 0
        precedence = await recon.get_precedence(self, account_id, "vehicle")
        # INCLUDING retired rows, for the same reason as
        # `upsert_from_integration`: a retired row still owns its VIN,
        # its plate and its (company, unit) in the unique index.  This
        # sibling kept the active-only blind spot after that one was
        # fixed, so a truck archived here reappeared as a brand-new
        # active row on the next roster sync — the archive silently
        # undone, and a second row for one physical asset.
        existing = await self.list_vehicles(account_id, include_inactive=True)
        by_vin, by_plate, by_unit = _index_existing(existing)
        # Ref-first: a stamped datatruck_ref decides identity outright on
        # every re-sync; the natural keys below are DISCOVERY for the first
        # link (after which the ref takes over).
        by_dt_ref = {v.datatruck_ref: v for v in existing if v.datatruck_ref}

        now = self._now()
        written = 0
        conflict_ops: list = []
        async with self.transaction():
            for r in rows:
                unit = str(r.get("unit_number") or "").strip()
                if not unit:
                    continue
                vin = str(r.get("vin") or "").strip()
                ref = str(r.get("external_id") or "").strip()
                match = by_dt_ref.get(ref) if ref else None
                if match is None:
                    match, _how = _match_existing(r, by_vin, by_plate, by_unit)

                if match is not None:
                    mr = recon.merge_fields(
                        current={f: getattr(match, f) for f in _SPEC_FILL},
                        provenance=match.field_provenance,
                        owner_fallback=match.source,
                        incoming=r, source=source,
                        fields=_SPEC_FILL, precedence=precedence,
                    )
                    updates, prov, conflicts, cleared = (
                        mr.updates, mr.provenance, mr.conflicts, mr.cleared,
                    )
                    stamp_ref = ref and match.datatruck_ref != ref
                    if updates or stamp_ref:
                        sets = [f"{f} = ?" for f in _SPEC_FILL if f in updates]
                        params = [updates[f] for f in _SPEC_FILL if f in updates]
                        if stamp_ref:
                            sets.append("datatruck_ref = ?")
                            params.append(ref)
                        sets.append("field_provenance = ?")
                        params.append(json.dumps(prov))
                        sets.append("updated_at = ?")
                        params.extend([now, match.id, account_id])
                        await self._db.execute(
                            f"UPDATE vehicles SET {', '.join(sets)} "
                            "WHERE id = ? AND account_id = ?",
                            tuple(params),
                        )
                        written += 1
                    if conflicts or cleared:
                        conflict_ops.append((match.id, conflicts, cleared))
                    continue

                # No match → insert new (provenance = this source per non-empty spec).
                prov = {f: source for f in _SPEC_FILL if not is_unset(r.get(f))}
                await self._db.execute(
                    """INSERT INTO vehicles
                       (account_id, company_code, unit_number, vehicle_type,
                        vin, plate_number, make, model, year, status, source,
                        telematics_ref, notes, is_active, field_provenance,
                        datatruck_ref, created_at, updated_at)
                       VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 1, ?, ?, ?, ?)
                       ON CONFLICT(account_id, company_code, unit_number)
                       DO NOTHING""",
                    (
                        account_id, str(r.get("company_code") or ""), unit,
                        vehicle_type, vin,
                        str(r.get("plate_number") or ""),
                        str(r.get("make") or ""), str(r.get("model") or ""),
                        r.get("year"), str(r.get("status") or "active"),
                        source, "", "", json.dumps(prov), ref, now, now,
                    ),
                )
                written += 1
        await recon.sync_batch(self, account_id, "vehicle", conflict_ops)
        return written

    async def company_code_for_unit(
        self, account_id: int, unit_number: str,
    ) -> str:
        """The company that owns a unit, by its number.  '' when unknown.

        The registry is the SSOT for which company a truck belongs to,
        so any writer that lost the company on the way in (a telematics
        snapshot without an org tag, say) can recover it here instead of
        filing the record under a placeholder.  Ambiguous units — the
        same number under two companies — return '' rather than guess:
        a wrong company is worse than an admitted unknown.
        """
        unit = (unit_number or "").strip()
        if not unit:
            return ""
        rows = await self.read_all(
            "SELECT DISTINCT company_code FROM vehicles "
            "WHERE account_id = ? AND lower(unit_number) = lower(?) "
            "AND is_active = 1 AND company_code <> ''",
            (account_id, unit),
        )
        return str(rows[0][0]) if len(rows) == 1 else ""

    async def find_duplicate_vehicles(self, account_id: int) -> list[dict]:
        """Active vehicles that share a non-empty VIN — an unambiguous
        duplicate (the same physical asset registered twice, e.g. from before
        VIN reconciliation, or imported by hand).  One entry per VIN with the
        row ids + units, so an operator merge tool can fold them."""
        existing = await self.list_vehicles(account_id)
        by_vin: dict[str, list] = {}
        for v in existing:
            if v.is_active and v.vin:
                by_vin.setdefault(v.vin.strip().upper(), []).append(v)
        return [
            {
                "vin": vs[0].vin,
                "vehicle_ids": [v.id for v in vs],
                "units": [v.unit_number for v in vs],
            }
            for vs in by_vin.values() if len(vs) > 1
        ]

    async def plan_external_vehicles(
        self,
        account_id: int,
        rows: list[dict[str, Any]],
        *,
        vehicle_type: str,
    ) -> dict[str, Any]:
        """Read-only dry-run of ``project_external_vehicles``.

        Classifies each incoming row against the current registry using
        the SAME matcher the projection uses, WITHOUT writing — so the
        preview the operator approves is exactly what apply will do:

          * ``new``     — no match → would be inserted.
          * ``enrich``  — matched → would fill the listed empty fields.
          * ``review``  — would insert, but the unit number already
            exists on >1 vehicle (ambiguous) — a possible duplicate.
          * unchanged   — matched, nothing to fill (counted only).
        """
        existing = await self.list_vehicles(account_id)
        by_vin, by_plate, by_unit = _index_existing(existing)
        by_dt_ref = {v.datatruck_ref: v for v in existing if v.datatruck_ref}
        new: list[dict] = []
        enrich: list[dict] = []
        review: list[dict] = []
        unchanged = 0
        for r in rows:
            unit = str(r.get("unit_number") or "").strip()
            if not unit:
                continue
            ref = str(r.get("external_id") or "").strip()
            match, how = (by_dt_ref[ref], "ref") if ref and ref in by_dt_ref \
                else _match_existing(r, by_vin, by_plate, by_unit)
            if match is not None:
                fills = [
                    f for f in _SPEC_FILL
                    if getattr(match, f) in (None, "", 0)
                    and r.get(f) not in (None, "")
                ]
                if fills:
                    enrich.append({
                        "unit": unit, "matched_unit": match.unit_number,
                        "by": how, "fills": fills,
                    })
                else:
                    unchanged += 1
            else:
                entry = {
                    "unit": unit,
                    "vin": str(r.get("vin") or ""),
                    "plate": str(r.get("plate_number") or ""),
                }
                if how == "ambiguous":
                    review.append({
                        **entry,
                        "reason": f"unit {unit!r} already exists on multiple "
                        "vehicles — may be a duplicate",
                    })
                else:
                    new.append(entry)
        return {
            "kind": "vehicles",
            "vehicle_type": vehicle_type,
            "new": new,
            "enrich": enrich,
            "review": review,
            "counts": {
                "new": len(new), "enrich": len(enrich),
                "review": len(review), "unchanged": unchanged,
                "total": len(new) + len(enrich) + len(review) + unchanged,
            },
        }

    async def operator_archived_refs(self, account_id: int) -> set[str]:
        """Telematics refs belonging to trucks a PERSON archived.

        The ingest drops state rows for these: no new live/minute rows,
        so every downstream staleness gate closes on its own and the
        billed count — which reads ``vehicle_state_live.captured_at``,
        not the registry — stops counting a truck that left.

        ``archived_reason = 'operator'`` and NOT bare ``is_active = 0``.
        A SWEPT badge must keep ingesting the moment it reports again,
        which is the departure sweep's documented contract; gating it
        here would zombie it permanently.

        The ref itself is never cleared — that is identity, and this is
        lifecycle.  Keeping it is what lets a restore re-attach the
        truck to its own device in one act.
        """
        cur = await self._db.execute(
            "SELECT telematics_ref FROM vehicles "
            "WHERE account_id = ? AND telematics_ref <> '' "
            "AND is_active = 0 AND archived_reason = ?",
            (account_id, ARCHIVED_BY_OPERATOR),
        )
        return {str(r[0]) for r in await cur.fetchall() if r[0]}

    async def restore_vehicle(
        self, account_id: int, vehicle_id: int,
        actor_user_id: Optional[int] = None,
    ) -> bool:
        """Bring a retired truck back, the way it was.

        The reverse of ``deactivate_vehicle``, and it can be one act
        only because archiving never destroyed anything: the telematics
        ref was left alone, so the ingest gate simply stops dropping the
        truck's rows and telemetry resumes on the next tick — no
        re-linking, no re-uploading documents, nothing to rebuild.

        ``status`` goes back to what it was BEFORE archiving rather than
        a guessed 'active': a truck retired out of the shop should come
        back to the shop.  Rows archived before that value was recorded
        fall back to 'active', which is honest — we do not know, so we
        do not invent something specific.

        Alerts closed by the archive are NOT re-opened.  They were
        cleared, and if the conditions still hold the checks raise them
        again within the hour; resurrecting week-old alerts about a
        truck that just came back would be noise, not history.
        """
        async with self.transaction():
            cur = await self._db.execute(
                "SELECT status_before_archive FROM vehicles "
                "WHERE id = ? AND account_id = ? AND is_active = 0",
                (vehicle_id, account_id),
            )
            row = await cur.fetchone()
            if row is None:
                return False
            previous = (row[0] or "").strip() or "active"
            cur = await self._db.execute(
                "UPDATE vehicles SET is_active = 1, status = ?, "
                "archived_reason = '', status_before_archive = '', "
                "updated_at = ? WHERE id = ? AND account_id = ? "
                "AND is_active = 0",
                (previous, self._now(), vehicle_id, account_id),
            )
            touched = (getattr(cur, "rowcount", 0) or 0) > 0
            if touched and actor_user_id is not None:
                await self.append_activity_events(account_id, [{
                    "entity_type": "vehicle", "entity_id": vehicle_id,
                    "action": "restore", "actor_user_id": actor_user_id,
                    "changes": {"status": {"from": "inactive",
                                           "to": previous}},
                }])
            return touched

    async def list_archived_vehicles(self, account_id: int) -> list[Vehicle]:
        """Retired trucks, newest first — the Archived view's rows.

        Its own method rather than a flag on ``list_vehicles``: the
        default there is active-only and dozens of callers depend on
        that, so a parameter would be one typo away from putting
        retired trucks back into a picker.
        """
        rows = await self.read_all(
            f"{_SELECT} WHERE account_id = ? AND is_active = 0 "
            "ORDER BY updated_at DESC, unit_number",
            (account_id,),
        )
        return [_row_to_vehicle(r) for r in rows]

    async def retired_vehicle_named(
        self, account_id: int, name: str,
    ) -> dict | None:
        """The retired truck answering to ``name``, when NO live one does.

        Returns ``None`` the moment any ACTIVE row carries that unit
        number — including the case where a retired truck's door number
        has since gone onto a different truck.  A live truck always wins
        the name, so asking about it can never be refused on account of
        its predecessor.

        Used to refuse a LIVE assistant question about a truck that left
        the account, rather than answering it with stale readings as
        though they were current.  Historical questions do not consult
        this at all — the record is the reason archiving exists.
        """
        needle = str(name or "").strip().lower()
        if not needle:
            return None
        cur = await self._db.execute(
            "SELECT id, unit_number, company_code, is_active, "
            "archived_reason, status_before_archive, updated_at "
            "FROM vehicles WHERE account_id = ? AND lower(unit_number) = ?",
            (account_id, needle),
        )
        rows = [
            {
                "id": r[0], "unit_number": r[1], "company_code": r[2] or "",
                "is_active": bool(r[3]), "archived_reason": r[4] or "",
                "status_before_archive": r[5] or "", "updated_at": r[6] or "",
            }
            for r in await cur.fetchall()
        ]
        if any(r["is_active"] for r in rows):
            return None
        return rows[0] if rows else None

    async def active_unit_names(self, account_id: int) -> set[str]:
        """Lowercased unit numbers of trucks still in service.

        An ALLOW-list, deliberately, for the surfaces that identify a
        vehicle by NAME rather than by ref.  Excluding archived names
        instead would be unsafe: a door number is reusable, so a
        retired truck's number can already belong to a live truck, and
        a deny-list would silence both.  Keeping only names an ACTIVE
        row still claims is right either way round.
        """
        cur = await self._db.execute(
            "SELECT lower(unit_number) FROM vehicles "
            "WHERE account_id = ? AND is_active = 1 AND unit_number <> ''",
            (account_id,),
        )
        return {str(r[0]) for r in await cur.fetchall() if r[0]}

    async def archived_refs(self, account_id: int) -> set[str]:
        """Telematics refs of every retired truck, however it retired.

        The ALERTING predicate, and deliberately wider than
        ``operator_archived_refs``: nothing about notifying a person
        wants the sweep-vs-operator distinction — a truck that is not on
        the vehicle list should not be paging anyone, whichever way it
        left.  The ingest gate is the narrow one, because a swept badge
        must be allowed back in the moment it reports.
        """
        cur = await self._db.execute(
            "SELECT telematics_ref FROM vehicles "
            "WHERE account_id = ? AND telematics_ref <> '' AND is_active = 0",
            (account_id,),
        )
        return {str(r[0]) for r in await cur.fetchall() if r[0]}

    async def registry_ids_by_telematics_ref(
        self, account_id: int,
    ) -> dict[str, int]:
        """Provider external id → our ``vehicles.id``, for one account.

        The resolution map for stamping ``registry_id`` at ingest.  An
        empty ``telematics_ref`` never resolves — a blank pointer
        matching a blank pointer is how phantom identities are born.
        The roster is small (hundreds of rows), so callers take the
        whole map per tick rather than caching across ticks and going
        stale mid-rename.
        """
        # archived-ok: a retired row keeps its ref, so its rows keep being
        # stamped with the right registry id.  What stops an archived truck
        # is the ingest GATE dropping its rows, not this map failing.
        cur = await self._db.execute(
            "SELECT telematics_ref, id FROM vehicles "
            "WHERE account_id = ? AND telematics_ref <> ''",
            (account_id,),
        )
        return {
            str(row[0]): int(row[1])
            for row in await cur.fetchall()
            if row[0]
        }

    async def upsert_from_integration(
        self,
        account_id: int,
        rows: list[dict[str, Any]],
        *,
        source: str,
    ) -> int:
        """Upsert an integration's vehicle roster, keyed on
        ``(account_id, company_code, unit_number)``.

        Used by the migration-105 backfill, the 60s Samsara ingestor, and any
        roster sync.  Spec fields (vin / plate / make / model / year) are
        merged by SOURCE PRECEDENCE (see ``recon.merge_fields``): an empty
        value never wipes; an empty field is filled; a field owned by a
        higher-priority source — or pinned by an operator (``manual``) — is
        never overwritten by a lower-priority sync.  ``telematics_ref`` is the
        live link and fill-don't-wipe; ``vehicle_type`` / operator ``status``
        / ``notes`` are never touched here.  Rows without a unit_number are
        skipped.  Returns the number of rows written.
        """
        if not rows:
            return 0
        precedence = await recon.get_precedence(self, account_id, "vehicle")
        # INCLUDING retired rows.  A row that is no longer active still
        # OWNS its telematics ref, its VIN and its (company, unit) in the
        # unique index — those identities are taken whether the row shows
        # on a page or not.  Indexing only active rows meant a retired
        # row was invisible to the match and the provider's next tick
        # inserted a second row for the same physical truck: that is how
        # one account came to have four devices claimed by two rows each,
        # with the work-order history on one and the door number on the
        # other.
        existing = await self.list_vehicles(account_id, include_inactive=True)
        # IDENTITY vs LABEL — the two indexes are scoped differently on
        # purpose.  A telematics ref and a VIN name a physical thing, so
        # they match a retired row too.  A unit number is a LABEL people
        # reuse: once a truck is retired its door number can legitimately
        # go on a different truck, and matching that by name would merge
        # two vehicles' histories.  Labels match live rows only.
        by_key = {
            (v.company_code, v.unit_number.strip().lower()): v
            for v in existing if v.is_active
        }
        # VIN index for reconciliation — a Datatruck-first row (no company
        # scoping) must be ENRICHED, not duplicated, when Samsara later reports
        # the same physical truck under a company.
        by_vin = {v.vin.strip().upper(): v for v in existing if v.vin}
        # Live-link index, matched FIRST: the telematics ref is the one
        # identity the provider cannot rename.  After a vin_change split
        # moves a ref onto a new unit, the provider still displays the
        # OLD unit number — a name-first match would re-link the old row
        # and silently undo the operator's split on the next tick.
        by_ref: dict[str, Vehicle] = {}
        for v in existing:
            if not v.telematics_ref:
                continue
            clash = by_ref.get(v.telematics_ref)
            if clash is None:
                by_ref[v.telematics_ref] = v
                continue
            # Two rows claiming one device — the state this account
            # spent four trucks in.  Nothing here can decide which is
            # real (only the number on the door can), so the choice is
            # the conservative one: keep syncing the row that carries
            # the history, so answering it later does not have to move
            # work orders as well as identity.  ACTIVE beats retired,
            # then the LOWER id, which is the row that existed first.
            keep = min(
                (clash, v),
                key=lambda x: (not x.is_active, x.id),
            )
            by_ref[v.telematics_ref] = keep
            # Said out loud every tick, because the alternative is what
            # happened: one physical truck quietly counted twice, its
            # work orders on one row and its door number on the other,
            # found only when someone went looking for something else.
            logger.warning(
                "vehicle identity: device %s is claimed by %d rows "
                "(units %s / %s) — syncing id=%d, the other is a "
                "duplicate that needs merging",
                v.telematics_ref, 2, clash.unit_number, v.unit_number, keep.id,
            )
        now = self._now()
        written = 0
        conflict_ops: list = []
        async with self.transaction():
            for r in rows:
                unit = str(r.get("unit_number") or "").strip()
                if not unit:
                    continue
                company = str(r.get("company_code") or "")
                match = by_ref.get(str(r.get("telematics_ref") or "") or None)
                if match is None:
                    match = by_key.get((company, unit.lower()))
                matched_by_vin = False
                if match is None:
                    # VIN reconciliation — if this source carries a VIN that
                    # already exists on another row (e.g. a Datatruck-created,
                    # company-less row), enrich THAT row instead of inserting a
                    # duplicate of the same physical truck.
                    inc_vin = str(r.get("vin") or "").strip().upper()
                    if inc_vin and inc_vin in by_vin:
                        match = by_vin[inc_vin]
                        matched_by_vin = True

                if match is None:
                    # Net-new row — provenance starts as this source for each
                    # non-empty spec field it carries.
                    prov = {
                        f: source for f in _SPEC_FILL if not is_unset(r.get(f))
                    }
                    cur_ins = await self._db.execute(
                        """INSERT INTO vehicles
                           (account_id, company_code, unit_number, vehicle_type,
                            vin, plate_number, make, model, year, gateway_serial,
                            status, source,
                            telematics_ref, notes, is_active, field_provenance,
                            created_at, updated_at)
                           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 1, ?, ?, ?)
                           ON CONFLICT(account_id, company_code, unit_number)
                           DO NOTHING""",
                        (
                            account_id, company, unit,
                            str(r.get("vehicle_type") or "truck"),
                            str(r.get("vin") or ""),
                            str(r.get("plate_number") or ""),
                            str(r.get("make") or ""),
                            str(r.get("model") or ""),
                            _model_year(r.get("year")),
                            str(r.get("gateway_serial") or ""),
                            str(r.get("status") or "active"),
                            source,
                            str(r.get("telematics_ref") or ""),
                            str(r.get("notes") or ""),
                            json.dumps(prov), now, now,
                        ),
                    )
                    # ON CONFLICT DO NOTHING covers a real collision the
                    # match above cannot see: the unique index spans
                    # RETIRED rows too, so a genuinely different truck
                    # taking a retired truck's door number lands here and
                    # writes nothing.  Silently counting that as written
                    # is how a vehicle goes missing with no error
                    # anywhere; say so instead.
                    if getattr(cur_ins, "rowcount", 1) == 0:
                        logger.warning(
                            "vehicle upsert: %s/%s not inserted — the unit "
                            "number is held by a retired row (source=%s)",
                            company, unit, source,
                        )
                        continue
                    written += 1
                    continue

                # Existing row — merge spec fields by precedence + provenance.
                mr = recon.merge_fields(
                    current={f: getattr(match, f) for f in _SPEC_FILL},
                    provenance=match.field_provenance,
                    owner_fallback=match.source,
                    incoming=r, source=source,
                    fields=_SPEC_FILL, precedence=precedence,
                )
                updates, prov, conflicts, cleared = (
                    mr.updates, mr.provenance, mr.conflicts, mr.cleared,
                )
                sets: list[str] = []
                params: list[Any] = []
                for f in _SPEC_FILL:
                    if f in updates:
                        value = updates[f]
                        if f == "year":
                            value = _model_year(value)
                            if value is None:
                                continue
                        sets.append(f"{f} = ?")
                        params.append(value)
                # telematics_ref is the live link (Samsara's id) — fill-don't-wipe.
                tref = str(r.get("telematics_ref") or "")
                if tref:
                    sets.append("telematics_ref = ?")
                    params.append(tref)
                # VIN-reconciled a company-less row → adopt the real company
                # this source provides (safe: an exact (company, unit) match
                # would have been found above, so no UNIQUE clash).
                if matched_by_vin and not match.company_code and company:
                    sets.append("company_code = ?")
                    params.append(company)
                # source refreshes to the latest integration to touch the row.
                sets.append("source = ?")
                params.append(source)
                sets.append("field_provenance = ?")
                params.append(json.dumps(prov))
                # Revival, but only of what the SWEEP retired.
                #
                # Two different things set is_active = 0, and they are
                # told apart by the operator's `status` column, which
                # the sweep deliberately never writes:
                #
                #   swept   is_active=0, status untouched — the gateway
                #           went silent.  vehicle_departure's contract
                #           is explicit that "a badge that reports again
                #           is re-upserted and its registry row
                #           reactivated", so reviving here IS the
                #           promise, and it was going unkept.
                #   deleted is_active=0 AND status='inactive' — a person
                #           retired the truck, or archived the old unit
                #           of a split.  Reviving that would undo an
                #           audited human decision on the next 60-second
                #           tick, which is why the row is matched (never
                #           duplicated) but left hidden.
                if not _operator_retired(match):
                    sets.append("is_active = 1")
                elif not match.is_active:
                    logger.info(
                        "vehicle upsert: %s reports into retired unit %s/%s "
                        "(id=%d) — merged, left hidden",
                        source, match.company_code, match.unit_number, match.id,
                    )
                sets.append("updated_at = ?")
                params.append(now)
                params.extend([match.id, account_id])
                await self._db.execute(
                    f"UPDATE vehicles SET {', '.join(sets)} "
                    "WHERE id = ? AND account_id = ?",
                    tuple(params),
                )
                written += 1
                if conflicts or cleared:
                    conflict_ops.append((match.id, conflicts, cleared))
        await recon.sync_batch(self, account_id, "vehicle", conflict_ops)
        return written

    async def get_identity_map(self, account_id: int) -> dict:
        """``{telematics_ref: {vin, gateway_serial, registry_id,
        unit_number, company_code}}`` — the identity anchors the ingest
        compares each tick to turn silent hardware changes into
        recorded events (device_event_log)."""
        # archived-ok, DELIBERATELY: the watch must keep anchoring a retired
        # truck.  If a gateway is pulled out of one and bolted into another,
        # that VIN change has to be recorded — otherwise the retired row
        # keeps a ref that now names a different physical truck and restoring
        # it re-attaches the wrong vehicle.  The NOTICES are filtered instead
        # (samsara/sync.py): recorded for every truck, announced only for
        # live ones.
        cur = await self._db.execute(
            "SELECT telematics_ref, vin, gateway_serial, id, unit_number, "
            "company_code FROM vehicles "
            "WHERE account_id = ? AND telematics_ref <> ''",
            (account_id,),
        )
        cols = ("telematics_ref", "vin", "gateway_serial", "id",
                "unit_number", "company_code")
        out = {}
        for r in await cur.fetchall():
            d = dict(zip(cols, r))
            out[str(d["telematics_ref"])] = {
                "vin": str(d["vin"] or ""),
                "gateway_serial": str(d["gateway_serial"] or ""),
                "registry_id": d["id"],
                "unit_number": str(d["unit_number"] or ""),
                "company_code": str(d["company_code"] or ""),
            }
        return out

