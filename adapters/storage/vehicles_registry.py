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

# ── Source precedence (Layer 1) ───────────────────────────────────────
#
# When two integrations carry a DIFFERENT non-empty value for the same spec
# field, who wins?  An explicit, per-account-overridable priority order per
# field — NOT "whoever synced most recently".  Default: Datatruck (the TMS,
# system-of-record for paperwork) outranks Samsara (telematics) for the
# spec fields; the owner can flip any field later (e.g. Samsara-first VIN).
# ``manual`` (an operator edit) always outranks every integration.
DEFAULT_FIELD_PRECEDENCE: dict[str, tuple[str, ...]] = {
    "vin":          ("datatruck", "samsara"),
    "plate_number": ("datatruck", "samsara"),
    "make":         ("datatruck", "samsara"),
    "model":        ("datatruck", "samsara"),
    "year":         ("datatruck", "samsara"),
}
PRECEDENCE_SETTING_KEY = "vehicle_field_precedence"
_MANUAL_SOURCE = "manual"

# Integrations that can write vehicle spec fields — the choices the
# precedence UI offers, and the order used to expand a per-field "primary"
# pick into a full priority list.
VEHICLE_SPEC_SOURCES = ("datatruck", "samsara")

# Human labels for the configurable spec fields (the precedence panel).
_VEHICLE_FIELD_LABELS = {
    "vin": "VIN", "plate_number": "Plate", "make": "Make",
    "model": "Model", "year": "Year",
}


def _is_unset(v: Any) -> bool:
    """A spec value counts as 'not provided' — so it never overwrites."""
    return v is None or v == "" or v == 0


def _source_rank(source: str, order: tuple[str, ...]) -> int:
    """Lower = higher priority.  ``manual`` always wins; a source not named
    in the order is lowest (can fill a gap but can't outrank a named one)."""
    if source == _MANUAL_SOURCE:
        return -1
    try:
        return list(order).index(source)
    except ValueError:
        return len(order) + 1


def _merge_spec_fields(
    existing: "Vehicle | None",
    incoming: dict[str, Any],
    source: str,
    precedence: dict[str, tuple[str, ...]],
) -> tuple[dict[str, Any], dict[str, str], list[dict], list[str]]:
    """Decide which spec fields an integration write may change, update the
    provenance map, and surface genuine cross-source conflicts.

    Returns ``(updates, new_provenance, conflicts, cleared_fields)``:
      * ``updates``        — spec fields to write.
      * ``new_provenance`` — the merged ``{field: source}`` map.
      * ``conflicts``      — fields where two integration sources carry a
        DIFFERENT non-empty value.  Each is ``{field, current_value,
        current_source (the stored winner), incoming_value, incoming_source
        (the other)}`` — recorded so an operator can review (Layer 3).
      * ``cleared_fields`` — fields where the two sources now AGREE, so any
        prior open conflict can be cleared.

    Rules per field: blank-skip → fill-empty → agree (clear) → manual-pin →
    precedence (the configured owner wins; a manual pin beats all).  A missing
    provenance entry falls back to the row's existing ``source``.
    """
    prov: dict[str, str] = dict(getattr(existing, "field_provenance", None) or {}) if existing else {}
    updates: dict[str, Any] = {}
    conflicts: list[dict] = []
    cleared: list[str] = []
    for f in _SPEC_FILL:
        inc = incoming.get(f)
        if _is_unset(inc):
            continue
        cur = getattr(existing, f, None) if existing else None
        if _is_unset(cur):
            updates[f] = inc
            prov[f] = source
            continue
        if str(inc) == str(cur):
            cleared.append(f)            # sources agree → resolve any conflict
            continue
        owner = prov.get(f) or (getattr(existing, "source", None) if existing else None)
        if owner == _MANUAL_SOURCE:
            continue                     # operator pin — not a source conflict
        order = precedence.get(f, ())
        if _source_rank(source, order) <= _source_rank(owner or "", order):
            updates[f] = inc
            prov[f] = source
            win_val, win_src, lose_val, lose_src = inc, source, cur, owner
        else:
            win_val, win_src, lose_val, lose_src = cur, owner, inc, source
        # A genuine disagreement between two DIFFERENT integration sources.
        if owner and owner != source:
            conflicts.append({
                "field": f,
                "current_value": win_val, "current_source": win_src,
                "incoming_value": lose_val, "incoming_source": lose_src,
            })
    return updates, prov, conflicts, cleared


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
    )


_SELECT = (
    "SELECT id, account_id, company_code, unit_number, vehicle_type, vin, "
    "plate_number, make, model, year, status, source, telematics_ref, "
    "notes, is_active, created_at, updated_at, field_provenance FROM vehicles"
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
                prov[f] = _MANUAL_SOURCE
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
        ``_merge_spec_fields``): this source fills empty fields and may
        overwrite a *lower*-priority source's value, but never an operator
        pin — and it never touches the existing ``source`` /
        ``vehicle_type`` / ``status`` / ``notes``.

        Returns the number of rows inserted-or-merged.
        """
        if not rows:
            return 0
        precedence = await self.get_vehicle_field_precedence(account_id)
        existing = await self.list_vehicles(account_id)
        by_vin, by_plate, by_unit = _index_existing(existing)

        now = self._now()
        written = 0
        conflict_ops: list = []
        async with self.transaction():
            for r in rows:
                unit = str(r.get("unit_number") or "").strip()
                if not unit:
                    continue
                vin = str(r.get("vin") or "").strip()
                match, _how = _match_existing(r, by_vin, by_plate, by_unit)

                if match is not None:
                    updates, prov, conflicts, cleared = _merge_spec_fields(
                        match, r, source, precedence,
                    )
                    if updates:
                        sets = [f"{f} = ?" for f in _SPEC_FILL if f in updates]
                        params = [updates[f] for f in _SPEC_FILL if f in updates]
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
                prov = {f: source for f in _SPEC_FILL if not _is_unset(r.get(f))}
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
                        account_id, str(r.get("company_code") or ""), unit,
                        vehicle_type, vin,
                        str(r.get("plate_number") or ""),
                        str(r.get("make") or ""), str(r.get("model") or ""),
                        r.get("year"), str(r.get("status") or "active"),
                        source, "", "", json.dumps(prov), now, now,
                    ),
                )
                written += 1
        await self._sync_conflicts_batch(account_id, conflict_ops)
        return written

    async def get_vehicle_field_precedence(
        self, account_id: int,
    ) -> dict[str, tuple[str, ...]]:
        """Per-account source precedence for vehicle spec fields, merged over
        the sensible default (Datatruck owns paperwork).  Stored as a JSON
        ``{field: [source, …]}`` object under the ``vehicle_field_precedence``
        account setting; absent / invalid keys fall back to the default."""
        merged = {k: tuple(v) for k, v in DEFAULT_FIELD_PRECEDENCE.items()}
        try:
            raw = await self.get_account_setting(
                account_id, PRECEDENCE_SETTING_KEY, "",
            )
        except Exception:
            raw = ""
        if raw:
            try:
                override = json.loads(raw)
            except (TypeError, ValueError):
                override = None
            if isinstance(override, dict):
                for f, order in override.items():
                    if f in merged and isinstance(order, list) and order:
                        merged[f] = tuple(str(s) for s in order)
        return merged

    async def set_vehicle_field_precedence(
        self, account_id: int, primary_by_field: dict[str, str],
    ) -> dict[str, list[str]]:
        """Persist a per-field PRIMARY-source choice as the account's vehicle
        field precedence.  Validates field + source names; expands each
        primary into the full order ``[primary, …other known sources]`` so
        the lower-priority source still fills gaps.  Unknown / invalid keys
        are ignored.  Returns the stored ``{field: [source, …]}`` map."""
        order_map: dict[str, list[str]] = {}
        for f, primary in (primary_by_field or {}).items():
            if f not in DEFAULT_FIELD_PRECEDENCE:
                continue
            if primary not in VEHICLE_SPEC_SOURCES:
                continue
            order_map[f] = [primary] + [
                s for s in VEHICLE_SPEC_SOURCES if s != primary
            ]
        await self.set_account_setting(
            account_id, PRECEDENCE_SETTING_KEY, json.dumps(order_map),
        )
        return order_map

    async def get_vehicle_precedence_options(self, account_id: int) -> dict:
        """UI payload for the source-precedence panel: each configurable spec
        field with its label + current PRIMARY source, plus the sources to
        choose from."""
        prec = await self.get_vehicle_field_precedence(account_id)
        return {
            "sources": list(VEHICLE_SPEC_SOURCES),
            "fields": [
                {
                    "key": f,
                    "label": _VEHICLE_FIELD_LABELS.get(f, f),
                    "primary": (prec.get(f) or VEHICLE_SPEC_SOURCES)[0],
                }
                for f in DEFAULT_FIELD_PRECEDENCE
            ],
        }

    # ── Cross-source data conflicts (Layer 3) ──────────────────────
    #
    # Generic on (entity_type, entity_id) so other domains can reuse the
    # store; vehicles is the first consumer.  Recorded by the merge engine
    # when two integrations carry a different non-empty value for a field.

    async def record_field_conflict(
        self, account_id: int, entity_type: str, entity_id: int, c: dict,
    ) -> None:
        """Upsert one OPEN conflict for ``(entity, field)``; refreshes the
        values when re-detected on a later sync."""
        now = self._now()
        await self._db.execute(
            """INSERT INTO data_conflicts
               (account_id, entity_type, entity_id, field,
                current_value, current_source, incoming_value, incoming_source,
                status, detected_at, updated_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, 'open', ?, ?)
               ON CONFLICT(account_id, entity_type, entity_id, field)
               DO UPDATE SET
                   current_value   = excluded.current_value,
                   current_source  = excluded.current_source,
                   incoming_value  = excluded.incoming_value,
                   incoming_source = excluded.incoming_source,
                   status          = 'open',
                   resolved_by     = NULL,
                   resolved_value  = NULL,
                   updated_at      = excluded.updated_at""",
            (
                account_id, entity_type, entity_id, str(c.get("field") or ""),
                str(c.get("current_value") if c.get("current_value") is not None else ""),
                str(c.get("current_source") or ""),
                str(c.get("incoming_value") if c.get("incoming_value") is not None else ""),
                str(c.get("incoming_source") or ""),
                now, now,
            ),
        )

    async def clear_field_conflict(
        self, account_id: int, entity_type: str, entity_id: int, field: str,
    ) -> None:
        """Drop any OPEN conflict for ``(entity, field)`` — the two sources
        came back into agreement (auto-resolved)."""
        await self._db.execute(
            "DELETE FROM data_conflicts WHERE account_id = ? AND entity_type = ? "
            "AND entity_id = ? AND field = ? AND status = 'open'",
            (account_id, entity_type, entity_id, field),
        )

    async def list_open_conflicts(
        self, account_id: int, *, entity_type: str | None = None,
    ) -> list[dict]:
        """Open conflicts for the account, newest first."""
        where = ["account_id = ?", "status = 'open'"]
        args: list[Any] = [account_id]
        if entity_type:
            where.append("entity_type = ?")
            args.append(entity_type)
        cols = [
            "id", "entity_type", "entity_id", "field", "current_value",
            "current_source", "incoming_value", "incoming_source", "detected_at",
        ]
        cur = await self._db.execute(
            f"SELECT {', '.join(cols)} FROM data_conflicts "
            f"WHERE {' AND '.join(where)} ORDER BY detected_at DESC, id DESC",
            tuple(args),
        )
        return [dict(zip(cols, row)) for row in await cur.fetchall()]

    async def count_open_conflicts(self, account_id: int) -> int:
        cur = await self._db.execute(
            "SELECT COUNT(*) FROM data_conflicts "
            "WHERE account_id = ? AND status = 'open'",
            (account_id,),
        )
        rows = await cur.fetchall()
        return int(rows[0][0]) if rows and rows[0] else 0

    async def resolve_field_conflict(
        self, account_id: int, conflict_id: int, *,
        chosen_value: Any, resolved_by: int,
    ) -> "Vehicle | None":
        """Resolve a conflict: write the chosen value onto the entity (via
        ``update_vehicle``, which PINS it so no sync undoes the decision) and
        mark the conflict resolved (kept for audit).  Returns the updated
        vehicle, or None if the conflict isn't an open vehicle conflict."""
        cur = await self._db.execute(
            "SELECT entity_type, entity_id, field FROM data_conflicts "
            "WHERE id = ? AND account_id = ? AND status = 'open'",
            (conflict_id, account_id),
        )
        row = await cur.fetchone()
        if not row:
            return None
        entity_type, entity_id, field = str(row[0]), int(row[1]), str(row[2])
        if entity_type != "vehicle":
            return None
        value: Any = chosen_value
        if field == "year":           # year is an INTEGER column
            try:
                value = int(chosen_value) if str(chosen_value).strip() else None
            except (TypeError, ValueError):
                value = None
        await self.update_vehicle(account_id, entity_id, **{field: value})
        await self._db.execute(
            "UPDATE data_conflicts SET status = 'resolved', resolved_by = ?, "
            "resolved_value = ?, updated_at = ? WHERE id = ? AND account_id = ?",
            (resolved_by, str(chosen_value), self._now(), conflict_id, account_id),
        )
        await self._db.commit()
        return await self.get_vehicle(account_id, entity_id)

    async def _sync_conflicts_batch(self, account_id: int, ops: list) -> None:
        """Record/clear cross-source conflicts AFTER the vehicle writes commit
        — best-effort + isolated, so the conflict store (auxiliary) can never
        abort or fail a sync.  ``ops`` = list of ``(entity_id, conflicts,
        cleared)``."""
        if not ops:
            return
        try:
            for entity_id, conflicts, cleared in ops:
                for c in conflicts:
                    await self.record_field_conflict(
                        account_id, "vehicle", entity_id, c,
                    )
                for fld in cleared:
                    await self.clear_field_conflict(
                        account_id, "vehicle", entity_id, fld,
                    )
            await self._db.commit()
        except Exception as e:
            logger.debug("conflict sync skipped acct=%d: %s", account_id, e)
            try:
                await self._db.rollback()
            except Exception:
                pass

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
        """Upsert an integration's vehicle roster, keyed on
        ``(account_id, company_code, unit_number)``.

        Used by the migration-105 backfill, the 60s Samsara ingestor, and any
        roster sync.  Spec fields (vin / plate / make / model / year) are
        merged by SOURCE PRECEDENCE (see ``_merge_spec_fields``): an empty
        value never wipes; an empty field is filled; a field owned by a
        higher-priority source — or pinned by an operator (``manual``) — is
        never overwritten by a lower-priority sync.  ``telematics_ref`` is the
        live link and fill-don't-wipe; ``vehicle_type`` / operator ``status``
        / ``notes`` are never touched here.  Rows without a unit_number are
        skipped.  Returns the number of rows written.
        """
        if not rows:
            return 0
        precedence = await self.get_vehicle_field_precedence(account_id)
        existing = await self.list_vehicles(account_id)
        by_key = {
            (v.company_code, v.unit_number.strip().lower()): v for v in existing
        }
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

                if match is None:
                    # Net-new row — provenance starts as this source for each
                    # non-empty spec field it carries.
                    prov = {
                        f: source for f in _SPEC_FILL if not _is_unset(r.get(f))
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
                updates, prov, conflicts, cleared = _merge_spec_fields(
                    match, r, source, precedence,
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
        await self._sync_conflicts_batch(account_id, conflict_ops)
        return written
