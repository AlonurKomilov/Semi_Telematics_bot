"""Which vehicles a restricted user may see — decided by identity, not text.

Driver assignments are stored as unit-number strings, and for years the
visibility walls compared them to vehicle names by SUBSTRING: a driver
assigned truck 230 also matched 2303, 100 matched trailer AK1001, and
301 matched five different trailers.  Those checks gate what a driver
sees — alerts, work orders, the overview — so an over-match is a
disclosure, not a cosmetic bug.  The substring existed as a workaround
for provider renames ("229 Idris Ahmed" still had to match an
assignment of "229"); the registry link now solves the rename properly,
which is what lets the substring die.

``VehicleScope`` decides membership on a ladder, most reliable rung
first, and a rung is consulted only when BOTH sides carry it:

  1. ``registry_id``  — the identity we own (vehicles.id).
  2. external id      — the provider's stable vehicle id.
  3. exact name       — lowercased equality, never substring.

The both-sides rule is what keeps the transition safe: rows written
before the identity backfill carry no registry_id, and treating that
absence as a verdict would either hide a driver's own truck or show
somebody else's.  Wrong-hidden is an annoyance; wrong-shown is a
breach — so a scope with no usable rung for a row denies it.

Pure logic only (no I/O), like the alerting access helpers — the
builder that resolves assignment strings through the registry lives
beside it and takes the tenant DB explicitly.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass(frozen=True)
class VehicleScope:
    """One user's allowed vehicles, expressed on every rung we know."""

    registry_ids: frozenset[int] = field(default_factory=frozenset)
    external_ids: frozenset[str] = field(default_factory=frozenset)
    names: frozenset[str] = field(default_factory=frozenset)

    @property
    def empty(self) -> bool:
        return not (self.registry_ids or self.external_ids or self.names)

    def allows(
        self,
        *,
        registry_id: Any = None,
        external_id: Any = None,
        name: Any = None,
    ) -> bool:
        """Membership by the strongest rung both sides share."""
        if self.registry_ids and registry_id is not None:
            try:
                return int(registry_id) in self.registry_ids
            except (TypeError, ValueError):
                pass
        ext = str(external_id or "").strip()
        if self.external_ids and ext:
            return ext in self.external_ids
        nm = str(name or "").strip().lower()
        if self.names and nm:
            return nm in self.names
        return False

    def allows_row(
        self,
        row: dict[str, Any],
        *,
        name_key: str = "name",
        external_key: str = "vehicle_id",
    ) -> bool:
        return self.allows(
            registry_id=row.get("registry_id"),
            external_id=row.get(external_key) or row.get("id"),
            name=row.get(name_key) or row.get("vehicle_name"),
        )


async def build_vehicle_scope(
    tenant, account_id: int, assigned_names: list[str],
) -> VehicleScope:
    """Resolve assignment strings into a full-ladder scope.

    Each assigned unit number is looked up in the vehicles registry, so
    the scope carries the truck's registry id and provider id alongside
    the raw string.  That is what lets an assignment of "229" keep
    matching a vehicle the provider renamed to "229 Idris Ahmed": the
    renamed row carries the registry id, and rung 1 decides.

    Unknown assignment strings (a typo, a truck the registry has never
    seen) stay name-only — rung 3 still honours them by exact equality.
    """
    names = sorted({
        n.strip().lower() for n in (assigned_names or []) if n and n.strip()
    })
    if not names:
        return VehicleScope()

    registry_ids: set[int] = set()
    external_ids: set[str] = set()
    placeholders = ", ".join("?" for _ in names)
    # archived-ok: a scope is a PERMISSION, not a liveness check.
    # Someone scoped to a truck must keep reaching its records after it
    # is retired — that history is the reason archiving exists.  Whether
    # a retired truck may raise an ALERT is decided by the alerting
    # sites, which filter for themselves.
    cur = await tenant._db.execute(
        f"SELECT id, telematics_ref FROM vehicles "
        f"WHERE account_id = ? AND lower(unit_number) IN ({placeholders})",
        (account_id, *names),
    )
    for row in await cur.fetchall():
        registry_ids.add(int(row[0]))
        if row[1]:
            external_ids.add(str(row[1]))

    return VehicleScope(
        registry_ids=frozenset(registry_ids),
        external_ids=frozenset(external_ids),
        names=frozenset(names),
    )
