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

``VehicleScope`` is a set of VEHICLES, one ``VehicleIdentity`` per
truck the person may see, and each decides membership on a ladder,
most reliable rung first, consulted only when BOTH sides carry it:

  1. ``registry_id``  — the identity we own (vehicles.id).
  2. external id      — the provider's stable vehicle id.
  3. exact name       — lowercased equality, never substring.

The both-sides rule is what keeps the transition safe: rows written
before the identity backfill carry no registry_id, and treating that
absence as a verdict would either hide a driver's own truck or show
somebody else's.  Wrong-hidden is an annoyance; wrong-shown is a
breach — so a vehicle with no usable rung for a row denies it.

PER VEHICLE is the load-bearing word, and it was not always so.  The
scope used to hold three FLATTENED sets — every registry id, every
provider id, every name, pooled — and picked its rung from what the
POOL carried rather than from what the row's own truck carried.  A
driver holding one linked truck and one not-yet-linked one therefore
had a non-empty provider-id set, so the ladder committed to rung 2 for
the unlinked truck's row too, missed, and stopped without trying the
name that would have matched: the driver lost their own truck.  It was
invisible wherever rows carry a registry id and total on
``/map/vehicles/live``, where the raw provider payload carries none.

Falling through to the name on a miss would have fixed that case and
opened a worse one — unit numbers are REUSED across companies in one
account, so another company's truck of the same number would have been
admitted.  Asking each assigned vehicle separately fixes the first
without opening the second: a LINKED truck still refuses a row whose
provider id differs, whatever the pool contains.

What this shape does NOT fix, and is not meant to: ``build_vehicle_scope``
resolves an assignment string against ``unit_number`` account-wide, so
a driver assigned "230" already receives both companies' "230" into
their scope.  That is the assignment model's to answer, not the
ladder's.

Pure logic only (no I/O), like the alerting access helpers — the
builder that resolves assignment strings through the registry lives
beside it and takes the tenant DB explicitly.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


def _as_id(value: Any) -> int | None:
    if value is None:
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


@dataclass(frozen=True)
class VehicleIdentity:
    """ONE vehicle, on however many rungs we could resolve for it.

    Build with :meth:`make`, which normalises: ids to int, the provider
    id stripped, the name stripped and lowercased.  Two identities for
    the same truck must compare equal or the set would hold both.
    """

    registry_id: int | None = None
    external_id: str = ""
    name: str = ""

    @staticmethod
    def make(
        registry_id: Any = None, external_id: Any = None, name: Any = None,
    ) -> "VehicleIdentity":
        return VehicleIdentity(
            _as_id(registry_id),
            str(external_id or "").strip(),
            str(name or "").strip().lower(),
        )

    @property
    def empty(self) -> bool:
        """A vehicle we can name on no rung admits nothing, so it is not
        a wall with a hole — it is not a wall at all."""
        return self.registry_id is None and not self.external_id and not self.name

    def allows(
        self,
        *,
        registry_id: Any = None,
        external_id: Any = None,
        name: Any = None,
    ) -> bool:
        """Is the row THIS vehicle?  By the strongest rung we both carry.

        A rung both sides carry is the answer, including when the answer
        is no: a truck whose registry id we know is not the row whose
        registry id differs, and dropping to the name there would admit
        another company's truck of the same number.
        """
        rid = _as_id(registry_id)
        if self.registry_id is not None and rid is not None:
            return rid == self.registry_id
        ext = str(external_id or "").strip()
        if self.external_id and ext:
            return ext == self.external_id
        nm = str(name or "").strip().lower()
        return bool(self.name and nm and nm == self.name)


@dataclass(frozen=True)
class VehicleScope:
    """One user's allowed vehicles — a set of them, asked one at a time.

    Constructed through :meth:`of` or :meth:`from_names`.  The flattened
    ``registry_ids=``/``external_ids=``/``names=`` keywords are gone on
    purpose: a call site that rebuilt three pooled sets would restore
    the pooled ladder this shape exists to end, so a missed one must
    raise rather than quietly work.
    """

    vehicles: frozenset[VehicleIdentity] = field(default_factory=frozenset)

    @classmethod
    def of(cls, *identities: VehicleIdentity) -> "VehicleScope":
        """A scope from identities, dropping any that name no rung."""
        return cls(frozenset(i for i in identities if not i.empty))

    @classmethod
    def from_names(cls, names) -> "VehicleScope":
        """A NAME-ONLY scope — assignments the registry could not
        resolve, and the shape most tests want."""
        return cls.of(*(VehicleIdentity.make(name=n) for n in (names or [])))

    @property
    def empty(self) -> bool:
        return not self.vehicles

    # The three pooled views, derived.  Read-only, and for describing a
    # scope (logs, the AI tool wire) — never for deciding membership,
    # which is what pooling got wrong.
    @property
    def registry_ids(self) -> frozenset[int]:
        return frozenset(v.registry_id for v in self.vehicles if v.registry_id is not None)

    @property
    def external_ids(self) -> frozenset[str]:
        return frozenset(v.external_id for v in self.vehicles if v.external_id)

    @property
    def names(self) -> frozenset[str]:
        return frozenset(v.name for v in self.vehicles if v.name)

    def allows(
        self,
        *,
        registry_id: Any = None,
        external_id: Any = None,
        name: Any = None,
    ) -> bool:
        """Membership: does ANY vehicle in this scope claim the row."""
        return any(
            v.allows(registry_id=registry_id, external_id=external_id, name=name)
            for v in self.vehicles
        )

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


Assigned = "str | tuple[str, int | None]"


async def build_vehicle_scope(
    tenant, account_id: int, assigned_names: list,
) -> VehicleScope:
    """Resolve assignment strings into a full-ladder scope.

    Each assigned unit number is looked up in the vehicles registry, so
    the scope carries the truck's registry id and provider id alongside
    the raw string.  That is what lets an assignment of "229" keep
    matching a vehicle the provider renamed to "229 Idris Ahmed": the
    renamed row carries the registry id, and rung 1 decides.

    Unknown assignment strings (a typo, a truck the registry has never
    seen) stay name-only — rung 3 still honours them by exact equality,
    and because each vehicle is asked separately that is true even when
    a SIBLING assignment did resolve to a provider id.

    One unit number can resolve to more than one registry row — numbers
    are reused across companies.  An element may therefore be a plain
    name OR a ``(name, registry_id)`` pair.  A pair names ONE truck: that
    row alone joins the scope, and NO name-only rung is added for it —
    otherwise the twin walks straight back in through rung 3.  A bare
    name keeps the old meaning: every row answering to it joins, exactly
    as before, until a human picks in Team Management.
    """
    pinned: dict[int, str] = {}      # registry_id -> the name it was assigned as
    loose: set[str] = set()
    for a in (assigned_names or []):
        if isinstance(a, (tuple, list)):
            n = str(a[0] or "").strip().lower()
            rid = a[1] if len(a) > 1 else None
            if not n:
                continue
            if rid is not None:
                pinned[int(rid)] = n
            else:
                loose.add(n)
        elif a and str(a).strip():
            loose.add(str(a).strip().lower())
    names = sorted(loose)
    if not names and not pinned:
        return VehicleScope()

    identities: list[VehicleIdentity] = []
    resolved: set[str] = set()
    if pinned:
        ph = ", ".join("?" for _ in pinned)
        # archived-ok: a scope is a PERMISSION, not a liveness check —
        # same stance as the name query below; a pinned truck that has
        # since been retired must keep answering for its history.
        cur = await tenant._db.execute(
            f"SELECT id, telematics_ref, lower(unit_number) FROM vehicles "
            f"WHERE account_id = ? AND id IN ({ph})",
            (account_id, *pinned),
        )
        found = set()
        for row in await cur.fetchall():
            found.add(int(row[0]))
            identities.append(VehicleIdentity.make(
                registry_id=row[0], external_id=row[1], name=str(row[2] or "")))
        # A pinned id the registry no longer has (hard-deleted) falls back
        # to its name — the assignment still means what it said.
        for rid, n in pinned.items():
            if rid not in found:
                loose.add(n)
        names = sorted(loose)
    if not names:
        return VehicleScope.of(*identities)
    placeholders = ", ".join("?" for _ in names)
    # archived-ok: a scope is a PERMISSION, not a liveness check.
    # Someone scoped to a truck must keep reaching its records after it
    # is retired — that history is the reason archiving exists.  Whether
    # a retired truck may raise an ALERT is decided by the alerting
    # sites, which filter for themselves.
    # ``unit_number`` joins the SELECT so each row can be tied back to
    # the assignment it answers: the identity is per vehicle now, and a
    # query that returned only ids could not say which name was which.
    cur = await tenant._db.execute(
        f"SELECT id, telematics_ref, lower(unit_number) FROM vehicles "
        f"WHERE account_id = ? AND lower(unit_number) IN ({placeholders})",
        (account_id, *names),
    )
    for row in await cur.fetchall():
        unit = str(row[2] or "")
        resolved.add(unit)
        identities.append(VehicleIdentity.make(
            registry_id=row[0], external_id=row[1], name=unit))

    # An assignment the registry did not answer keeps its name rung, so
    # a typo or a truck we have never seen still means what it said.
    identities.extend(
        VehicleIdentity.make(name=n) for n in names if n not in resolved)

    return VehicleScope.of(*identities)
