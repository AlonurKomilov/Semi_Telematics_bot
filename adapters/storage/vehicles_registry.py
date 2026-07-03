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

from capabilities.integrations import reconciliation as recon
from capabilities.integrations.reconciliation import MANUAL_SOURCE, is_unset

logger = logging.getLogger(__name__)

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
    # {spec_field: source} — who last authoritatively set each spec field.
    field_provenance: dict = field(default_factory=dict)
    # Datatruck-side asset binding (like telematics_ref for Samsara).
    datatruck_ref: str = ""


def _parse_provenance(raw: Any) -> dict:
    if isinstance(raw, dict):
        return raw
    try:
        return json.loads(raw) if raw else {}
    except (TypeError, ValueError):
        return {}


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
    )


_SELECT = (
    "SELECT id, account_id, company_code, unit_number, vehicle_type, vin, "
    "plate_number, make, model, year, status, source, telematics_ref, "
    "notes, is_active, created_at, updated_at, field_provenance, "
    "datatruck_ref FROM vehicles"
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
        if edited_spec:
            # Pin the edited spec fields so syncs can't undo the correction.
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
        existing = await self.list_vehicles(account_id)
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
        existing = await self.list_vehicles(account_id)
        by_key = {
            (v.company_code, v.unit_number.strip().lower()): v for v in existing
        }
        # VIN index for reconciliation — a Datatruck-first row (no company
        # scoping) must be ENRICHED, not duplicated, when Samsara later reports
        # the same physical truck under a company.
        by_vin = {v.vin.strip().upper(): v for v in existing if v.vin}
        now = self._now()
        written = 0
        conflict_ops: list = []
        async with self.transaction():
            for r in rows:
                unit = str(r.get("unit_number") or "").strip()
                if not unit:
                    continue
                company = str(r.get("company_code") or "")
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
                    await self._db.execute(
                        """INSERT INTO vehicles
                           (account_id, company_code, unit_number, vehicle_type,
                            vin, plate_number, make, model, year, status, source,
                            telematics_ref, notes, is_active, field_provenance,
                            created_at, updated_at)
                           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 1, ?, ?, ?)
                           ON CONFLICT(account_id, company_code, unit_number)
                           DO NOTHING""",
                        (
                            account_id, company, unit,
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
                            json.dumps(prov), now, now,
                        ),
                    )
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
                        sets.append(f"{f} = ?")
                        params.append(updates[f])
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
                sets.append("is_active = 1")
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
