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

from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, Optional

if TYPE_CHECKING:
    class _MixinBase:
        """Typing stub — provided by the concrete Database at runtime."""
        _db: Any
        def transaction(self) -> Any: ...
        async def read_all(self, sql: str, params: tuple = ()) -> list: ...
        async def read_one(self, sql: str, params: tuple = ()) -> Any: ...
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
_SPEC_FILL = ("vin", "plate_number", "make", "model", "year")


def _index_existing(existing: list["Vehicle"]) -> tuple[dict, dict, dict]:
    """Build VIN / plate / unit lookups over the current registry, so a
    planner and the projection match identically."""
    by_vin: dict[str, Vehicle] = {}
    by_plate: dict[str, Vehicle] = {}
    by_unit: dict[str, list[Vehicle]] = {}
    for v in existing:
        if v.vin:
            by_vin.setdefault(v.vin.strip().upper(), v)
        if v.plate_number:
            by_plate.setdefault(v.plate_number.strip().upper(), v)
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


def _row_to_vehicle(r) -> Vehicle:
    return Vehicle(
        id=r[0], account_id=r[1], company_code=r[2] or "",
        unit_number=r[3] or "", vehicle_type=r[4] or "truck",
        vin=r[5] or "", plate_number=r[6] or "", make=r[7] or "",
        model=r[8] or "", year=r[9], status=r[10] or "active",
        source=r[11] or "manual", telematics_ref=r[12] or "",
        notes=r[13] or "", is_active=bool(r[14]),
        created_at=r[15] or "", updated_at=r[16] or "",
    )


_SELECT = (
    "SELECT id, account_id, company_code, unit_number, vehicle_type, vin, "
    "plate_number, make, model, year, status, source, telematics_ref, "
    "notes, is_active, created_at, updated_at FROM vehicles"
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
        await self._db.commit()
        return cur.lastrowid

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
        self, account_id: int, vehicle_id: int, **fields: Any,
    ) -> bool:
        """Partial update — only the keys present in ``fields`` (and in
        ``_FIELDS``) are written.  Returns True when a row changed."""
        sets: list[str] = []
        params: list[Any] = []
        for key in _FIELDS:
            if key in fields and fields[key] is not None:
                if key == "vehicle_type" and fields[key] not in _VALID_TYPES:
                    raise ValueError(
                        f"vehicle_type must be one of {_VALID_TYPES}",
                    )
                sets.append(f"{key} = ?")
                params.append(fields[key])
        if not sets:
            return False
        sets.append("updated_at = ?")
        params.extend([self._now(), vehicle_id, account_id])
        cur = await self._db.execute(
            f"UPDATE vehicles SET {', '.join(sets)} "
            "WHERE id = ? AND account_id = ?",
            tuple(params),
        )
        await self._db.commit()
        return cur.rowcount > 0

    async def deactivate_vehicle(
        self, account_id: int, vehicle_id: int,
    ) -> bool:
        """Soft delete — keeps history intact (maintenance, fuel, etc.
        still reference the unit by name).  Returns True if a row
        flipped."""
        cur = await self._db.execute(
            "UPDATE vehicles SET is_active = 0, status = 'inactive', "
            "updated_at = ? WHERE id = ? AND account_id = ?",
            (self._now(), vehicle_id, account_id),
        )
        await self._db.commit()
        return cur.rowcount > 0

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

        On a match we ENRICH — fill only the spec fields the existing
        row is missing (this is how a Datatruck sync backfills the VIN
        onto a Samsara-sourced truck) without clobbering operator edits,
        the existing ``source``, ``vehicle_type``, ``status`` or notes.

        Returns the number of rows inserted-or-enriched.
        """
        if not rows:
            return 0
        existing = await self.list_vehicles(account_id)
        by_vin, by_plate, by_unit = _index_existing(existing)

        now = self._now()
        written = 0
        async with self.transaction():
            for r in rows:
                unit = str(r.get("unit_number") or "").strip()
                if not unit:
                    continue
                vin = str(r.get("vin") or "").strip()
                match, _how = _match_existing(r, by_vin, by_plate, by_unit)

                if match is not None:
                    # Enrich: fill only empty spec fields.
                    sets: list[str] = []
                    params: list[Any] = []
                    for f in _SPEC_FILL:
                        incoming = r.get(f)
                        cur = getattr(match, f)
                        empty = cur in (None, "", 0)
                        if empty and incoming not in (None, ""):
                            sets.append(f"{f} = ?")
                            params.append(incoming)
                    if sets:
                        sets.append("updated_at = ?")
                        params.extend([now, match.id, account_id])
                        await self._db.execute(
                            f"UPDATE vehicles SET {', '.join(sets)} "
                            "WHERE id = ? AND account_id = ?",
                            tuple(params),
                        )
                        written += 1
                    continue

                # No match → insert new.
                await self._db.execute(
                    """INSERT INTO vehicles
                       (account_id, company_code, unit_number, vehicle_type,
                        vin, plate_number, make, model, year, status, source,
                        telematics_ref, notes, is_active, created_at, updated_at)
                       VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 1, ?, ?)
                       ON CONFLICT(account_id, company_code, unit_number)
                       DO NOTHING""",
                    (
                        account_id, str(r.get("company_code") or ""), unit,
                        vehicle_type, vin,
                        str(r.get("plate_number") or ""),
                        str(r.get("make") or ""), str(r.get("model") or ""),
                        r.get("year"), str(r.get("status") or "active"),
                        source, "", "", now, now,
                    ),
                )
                written += 1
        return written

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
        new: list[dict] = []
        enrich: list[dict] = []
        review: list[dict] = []
        unchanged = 0
        for r in rows:
            unit = str(r.get("unit_number") or "").strip()
            if not unit:
                continue
            match, how = _match_existing(r, by_vin, by_plate, by_unit)
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

    async def upsert_from_integration(
        self,
        account_id: int,
        rows: list[dict[str, Any]],
        *,
        source: str,
    ) -> int:
        """Idempotent bulk upsert keyed on
        ``(account_id, company_code, unit_number)``.

        Used by (a) the migration-105 backfill from ``vehicle_state``
        and (b) the 60s ingestor as it sees Samsara vehicles, and (c) any
        integration projecting a roster.  Integration-owned spec columns
        (vin / plate / make / model / year / telematics_ref) FILL-DON'T-WIPE:
        a source overwrites them only when it carries a NON-EMPTY value, so
        two sources complete each other (Samsara has no VIN, Datatruck does)
        instead of one blanking the other's data every tick.  ``vehicle_type``
        and operator-set ``status``/``notes`` are PRESERVED (omitted from the
        update) — an operator who reclassified a unit or added a note keeps it
        across syncs.  Rows without a unit_number are skipped.

        Returns the number of rows written.
        """
        if not rows:
            return 0
        now = self._now()
        written = 0
        async with self.transaction():
            for r in rows:
                unit = str(r.get("unit_number") or "").strip()
                if not unit:
                    continue
                await self._db.execute(
                    """INSERT INTO vehicles
                       (account_id, company_code, unit_number, vehicle_type,
                        vin, plate_number, make, model, year, status, source,
                        telematics_ref, notes, is_active, created_at, updated_at)
                       VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 1, ?, ?)
                       ON CONFLICT(account_id, company_code, unit_number)
                       DO UPDATE SET
                           -- Fill-don't-wipe: a source overwrites a spec
                           -- field only when it actually has a value, so a
                           -- Samsara tick (no VIN/make) can't blank out what
                           -- Datatruck's projection filled in — and vice
                           -- versa.  Mirrors the enrich-only manner
                           -- project_external_vehicles already uses.
                           vin            = COALESCE(NULLIF(excluded.vin, ''),             vehicles.vin),
                           plate_number   = COALESCE(NULLIF(excluded.plate_number, ''),   vehicles.plate_number),
                           make           = COALESCE(NULLIF(excluded.make, ''),           vehicles.make),
                           model          = COALESCE(NULLIF(excluded.model, ''),          vehicles.model),
                           year           = COALESCE(excluded.year,                       vehicles.year),
                           telematics_ref = COALESCE(NULLIF(excluded.telematics_ref, ''), vehicles.telematics_ref),
                           -- source DOES refresh: it marks the latest
                           -- integration that wrote spec (a manual row adopts
                           -- its integration on first sync).  Operator
                           -- vehicle_type/status/notes preserved by omission.
                           source         = excluded.source,
                           is_active      = 1,
                           updated_at     = excluded.updated_at""",
                    (
                        account_id,
                        str(r.get("company_code") or ""),
                        unit,
                        str(r.get("vehicle_type") or "truck"),
                        str(r.get("vin") or ""),
                        str(r.get("plate_number") or ""),
                        str(r.get("make") or ""),
                        str(r.get("model") or ""),
                        r.get("year"),
                        str(r.get("status") or "active"),
                        source,
                        str(r.get("telematics_ref") or ""),
                        str(r.get("notes") or ""),
                        now, now,
                    ),
                )
                written += 1
        return written
