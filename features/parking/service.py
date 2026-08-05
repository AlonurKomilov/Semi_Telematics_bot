"""Parking feature — the read contract.

Every consumer reads parking events THROUGH here rather than querying
``parking_events`` directly (service-contract rule, docs/FEATURES.md) —
the same arrangement ``features/loads`` and ``features/vehicles`` already
use.  The point is not tidiness: it is that VISIBILITY SCOPE lives in one
place, so a list and a detail view of the same row cannot disagree about
who may see it.

They did disagree.  Before this module the scope was copy-pasted across
six endpoints in router.py, and the copies had drifted into three
distinct defects:

  1. LIST ENDPOINTS LEAKED TO UNASSIGNED DRIVERS.  ``/active`` and
     ``/history`` guarded with ``if trucks:`` — so a ``can_parking_vehicle``
     caller whose truck list came back EMPTY got no filter at all and saw
     the whole account.  An empty list is reachable and ordinary: a
     newly-invited driver, or one whose assignment was just removed
     (``get_user_vehicle_nums`` returns [] with no driver_trucks rows and
     no truck_num fallback).  The single-event endpoints used
     ``if not trucks or not any(...)`` and correctly denied, and the bot's
     ``_filter_to_own_vehicles`` denied too — so two of three paths were
     right and the most-used one was wrong.

  2. TWO ENDPOINTS SKIPPED THE COMPANY FILTER.  ``/stats/summary`` and
     ``/{id}/map-image`` applied the truck check but never
     ``filter_by_allowed_companies``.  A company-restricted caller was
     therefore blocked from an event's DETAIL while its map IMAGE — the
     photo of the same stop — was served, and the summary counted other
     companies' trucks.

  3. NOTHING TESTED THE COMPANY AXIS.  The isolation suite covered
     driver/truck thoroughly but every fixture used one company, so (2)
     could not have been caught.

The predicate below is therefore written ONCE and used by both the list
and the single-row paths, so the two cannot drift apart again.

Scope model, stated plainly:
  * company_codes == []  -> unrestricted (owner, or a user with no
    company restriction).  This is the documented contract of
    ``get_user_company_codes``.
  * truck_names is None  -> caller is not truck-scoped (``can_parking_all``).
  * truck_names == []    -> caller IS truck-scoped but has no trucks:
    sees NOTHING.  Never "sees everything".
"""

from __future__ import annotations

from typing import Any, Iterable

from infra.platform import get_tenant_db

# Classes that need no operator attention.  Kept here rather than inline
# so the SQL variant in adapters/storage/parking.py and the Python
# narrowing below cannot describe "attention" differently.
#
# CURRENTLY MATCHES NOTHING, BY DESIGN — read before "fixing" a count that
# looks unfiltered.  ``parking_events`` is an EXCEPTIONS table, not a stop
# log: features/parking/check.py returns early (resolving any existing row)
# for geofence stops, safe-keyword stops, and stops the AI confirms are safe
# with HIGH/MEDIUM confidence.  Only ``unsafe`` and ``unknown`` are ever
# upserted, so on live data this tuple excludes 0 of 4,536 rows.
#
# The predicate is kept rather than deleted because it states the intent
# correctly and costs nothing: if the storage policy ever changes to record
# safe stops, every consumer narrows automatically instead of silently
# starting to count them.  What it must NOT be used for is a user-facing
# SPLIT — a tab or filter built on it renders as a duplicate of "All".
# Urgency splits belong on ``alert_level`` (see Parking.tsx segments).
NO_ATTENTION_CLASSES = ("safe", "geofence")


def needs_attention(ev: dict) -> bool:
    return (ev.get("location_class") or "") not in NO_ATTENTION_CLASSES


def is_visible(
    ev: dict,
    *,
    company_codes: list[str] | None,
    truck_names: list[str] | None,
    scope: Any = None,
) -> bool:
    """THE visibility predicate.  One implementation, every path.

    Truck matching was SUBSTRING, and the reasoning was sound at the
    time: a truck assigned as "107" had to match an event whose
    vehicle_name is "Truck-107A", so tightening it would have hidden
    events from drivers who could see them.  The cost was that "107"
    also matched "1107" and "230" matched "2303" — on a visibility wall
    that is a disclosure, and it is the same defect corrected across
    alerting, work orders, maintenance and events.

    What makes tightening safe now is the identity ladder: ``scope``
    matches on registry id, then the provider's vehicle id, and only
    then on an exact name.  A renamed or oddly-named unit is caught by
    an id rung rather than by a text prefix, which is what the substring
    was standing in for.

    ``scope=None`` keeps the legacy list path working, but by EXACT name
    — never substring, so the old over-match cannot come back through
    the side door.
    """
    if company_codes:
        if (ev.get("company_code") or "") not in company_codes:
            return False
    if scope is not None:
        return scope.allows_row(
            ev, name_key="vehicle_name", external_key="vehicle_id",
        )
    if truck_names is not None:
        # Empty list => truck-scoped caller with no trucks => nothing.
        name = (ev.get("vehicle_name") or "").strip().lower()
        if not any((t or "").strip().lower() == name for t in truck_names):
            return False
    return True


def scope_events(
    events: Iterable[dict],
    *,
    company_codes: list[str] | None,
    truck_names: list[str] | None,
    scope: Any = None,
) -> list[dict]:
    """List form of :func:`is_visible` — same predicate, no second copy."""
    return [
        e for e in events
        if is_visible(
            e, company_codes=company_codes, truck_names=truck_names, scope=scope,
        )
    ]


async def get_events(
    account_id: int,
    *,
    days: int = 30,
    include_resolved: bool = True,
    attention_only: bool = False,
    company_codes: list[str] | None = None,
    truck_names: list[str] | None = None,
    scope: Any = None,
    vehicle: str | None = None,
    limit: int = 500,
) -> dict:
    """Unified read: active + resolved in one shape, already scoped.

    The dashboard renders ONE grid with a Status column rather than an
    Active tab and a History tab, so it needs one dataset.  Composed from
    the two existing adapter queries rather than a new SQL union — the
    row counts here are bounded by ``days`` and ``limit``, and a union
    would duplicate the ordering rules that already live in each query.

    Returns counts alongside the rows so the UI can say what it is
    hiding: ``total`` is everything visible in the window, ``count`` is
    what survived the narrowing.
    """
    tenant = await get_tenant_db(account_id)
    if tenant is None:
        return {"events": [], "count": 0, "total": 0, "active": 0}

    rows: list[dict] = await tenant.get_active_parking_events(
        account_id, attention_only=False,
    )
    active_ids = {r.get("id") for r in rows}
    if include_resolved:
        rows = rows + await tenant.get_parking_history(
            account_id, days=days, limit=limit,
        )

    rows = scope_events(
        rows, company_codes=company_codes, truck_names=truck_names, scope=scope,
    )
    if vehicle:
        q = vehicle.lower()
        rows = [r for r in rows if q in (r.get("vehicle_name") or "").lower()]

    total = len(rows)
    active = sum(1 for r in rows if r.get("id") in active_ids)
    if attention_only:
        rows = [r for r in rows if needs_attention(r)]

    rows = rows[:limit]

    # How many FLAGGED stops this vehicle has on record — unsafe plus
    # unverified, the same predicate as ``needs_attention``.  Decorated
    # here, AFTER the page has been narrowed, so the count query covers
    # only the vehicles actually being returned.
    #
    # Bounded by the parking retention window (see
    # capabilities/data_lifecycle/retention), not lifetime.
    counts = await tenant.count_flagged_parking_events_by_vehicle(
        account_id, sorted({r.get("vehicle_id") for r in rows if r.get("vehicle_id")}),
    )
    for r in rows:
        r["flagged_count"] = counts.get(r.get("vehicle_id"), 0)

    return {
        "events": rows,
        "count": len(rows),
        "total": total,
        "active": active,
    }


async def get_vehicle_history(
    account_id: int,
    vehicle_id: str,
    *,
    company_codes: list[str] | None = None,
    truck_names: list[str] | None = None,
    scope: Any = None,
    limit: int = 50,
) -> list[dict]:
    """One vehicle's parking events, newest first — the detail drawer's
    "is this truck always parking badly?" panel.

    Scoped through the SAME predicate as every other read: a driver
    cannot read another truck's history by passing its vehicle_id, and a
    company-restricted caller cannot read across companies.
    """
    tenant = await get_tenant_db(account_id)
    if tenant is None:
        return []
    rows = await tenant.get_parking_events_for_vehicle(
        account_id, vehicle_id, limit=limit,
    )
    return scope_events(
        rows, company_codes=company_codes, truck_names=truck_names, scope=scope,
    )


async def scope_for(user: dict, deps: Any):
    """Resolve a request user into ``(company_codes, truck_names, scope)``.

    ``deps`` is the interfaces.api.deps module, passed in rather than
    imported: this module is service-layer and must not depend on the
    interface layer (docs/FEATURES.md — only router.py may import deps).
    """
    company_codes = await deps.get_user_company_codes(user)
    truck_names: list[str] | None = None
    scope = None
    if user.get("_matched_perm") == "can_parking_vehicle":
        truck_names = await deps.get_user_vehicle_nums(user)
        # Resolve the assignment strings into the identity ladder so the
        # wall matches on registry / provider id before falling back to
        # an exact name.  An empty assignment list yields an EMPTY scope,
        # which denies every row — the same "truck-scoped caller with no
        # trucks sees nothing" rule the list path already had.
        from capabilities.permissions.vehicle_scope import (
            VehicleScope, build_vehicle_scope,
        )
        if truck_names:
            tenant = await get_tenant_db(user["account_id"])
            scope = await build_vehicle_scope(
                tenant, user["account_id"], truck_names,
            )
        else:
            scope = VehicleScope()
    return company_codes, truck_names, scope
