"""Vehicle-scope gate for alert DELIVERY — the per-vehicle twin of
``company_scope``.

A driver assigned to truck 229 should not be DM'd about truck 5585.  The
company gate cannot express that: it answers "which companies may this
person hear about", and a restricted driver's whole company is exactly
what they are restricted WITHIN.  So a driver assigned one truck has been
receiving alerts about every truck their company owns — vehicles they
cannot open in the dashboard, which the board already refuses to show
them.  This closes the gap on the DM side.

WHO IS RESTRICTED is deliberately the same rule the dashboard applies —
``interfaces/api/deps.get_user_vehicle_scope``: a DRIVER with at least one
assignment.  Every other role, and a driver with no assignment at all, is
unrestricted and this gate never touches them.  Keeping the two rules
identical is the point; two definitions of "restricted" is how a wall
grows a door nobody meant.

MATCHING is the identity ladder (``capabilities/permissions/vehicle_scope``):
registry id, then provider id, then exact name.  Never a substring — the
comparison it replaced let an assignment of "230" match truck 2303 and
"100" match trailer AK1001, and those are the rows a driver is ALLOWED to
see, so an over-match was a disclosure.

The two failure directions are deliberately different, and the difference
is the whole safety argument:

  • LOADING the gate fails OPEN.  If the assignment map or the registry
    cannot be read, nobody is known to be restricted, so nothing is
    tightened and delivery is exactly what it was before this module
    existed.  A database hiccup must never newly silence an alert.

  • MATCHING inside the gate fails CLOSED.  Once a user is known to be
    restricted, a vehicle their scope does not admit is not delivered.
    Being shown someone else's truck is a disclosure; missing one alert
    about a truck that was never theirs is not.
"""

from __future__ import annotations

import logging

from adapters.storage import Role
from capabilities.permissions.vehicle_scope import VehicleScope
from infra.platform import get_platform_db, get_tenant_db

logger = logging.getLogger("bot")


async def load_vehicle_gate(account_id: int) -> dict[int, VehicleScope]:
    """``user_id → VehicleScope`` for the account's RESTRICTED users only.

    Absent from the map means unrestricted — the map is a list of walls,
    not a list of people.

    Two queries for the whole account regardless of how many drivers it
    has: one for every assignment, one to resolve every assigned unit
    number against the registry at once.  Building a scope per driver
    would fire a registry query each, and this runs on the fan-out of
    every alert.

    Returns ``{}`` on any failure — see the module docstring on why
    loading fails open.
    """
    try:
        platform = get_platform_db()
        nums_map = await platform.get_account_vehicle_nums_map(account_id)
    except Exception as e:
        logger.debug("vehicle gate: assignments unavailable acct=%s: %s", account_id, e)
        return {}
    if not nums_map:
        return {}

    # Every assigned unit number across the account, resolved once.
    wanted = sorted({
        n.strip().lower()
        for names in nums_map.values() for n in (names or [])
        if n and n.strip()
    })
    if not wanted:
        return {}

    by_name: dict[str, tuple[int | None, str]] = {}
    try:
        tenant = await get_tenant_db(account_id)
        if tenant is not None:
            placeholders = ", ".join("?" for _ in wanted)
            # Active rows only.  A retired truck resolving here puts its
            # registry id and provider id into an alert's scope, which is
            # how alerts kept arriving for trucks nobody can open.  A
            # unit number is also REUSABLE — once a truck is retired its
            # door number can go on a different truck — so matching a
            # retired row by name could even resolve to the wrong truck.
            cur = await tenant._db.execute(
                f"SELECT id, telematics_ref, lower(unit_number) FROM vehicles "
                f"WHERE account_id = ? AND is_active = 1 "
                f"AND lower(unit_number) IN ({placeholders})",
                (account_id, *wanted),
            )
            for row in await cur.fetchall():
                by_name[str(row[2])] = (int(row[0]) if row[0] is not None else None,
                                        str(row[1] or ""))
    except Exception as e:
        # Name-only scopes still work (rung 3 of the ladder), so a registry
        # failure narrows precision, not correctness.
        logger.debug("vehicle gate: registry unresolved acct=%s: %s", account_id, e)

    gate: dict[int, VehicleScope] = {}
    for user_id, names in nums_map.items():
        clean = sorted({n.strip().lower() for n in (names or []) if n and n.strip()})
        if not clean:
            continue                       # no assignment = unrestricted
        registry_ids, external_ids = set(), set()
        for n in clean:
            rid, ext = by_name.get(n, (None, ""))
            if rid is not None:
                registry_ids.add(rid)
            if ext:
                external_ids.add(ext)
        gate[int(user_id)] = VehicleScope(
            registry_ids=frozenset(registry_ids),
            external_ids=frozenset(external_ids),
            names=frozenset(clean),
        )
    return gate


def user_sees_vehicle(user_id, role, vehicle: dict, gate: dict) -> bool:
    """Whether this user may be told about THIS vehicle.

    The pure core, keyed on primitives rather than a subscriber object —
    so the notification ``dispatch()`` fan-out can apply the same rule
    through an opaque predicate without importing alerting, exactly as
    ``company_scope.user_sees_company`` does.
    """
    if not gate:
        return True                                   # nobody restricted
    role_str = role.value if hasattr(role, "value") else str(role or "")
    if role_str != Role.DRIVER.value:
        return True                                   # only drivers are scoped
    try:
        scope = gate.get(int(user_id)) if user_id is not None else None
    except (TypeError, ValueError):
        # An id we cannot key on is an id we cannot prove is restricted.
        # Fail OPEN here, matching the load: this narrows nothing that
        # was not already narrowed, and a raise inside a predicate would
        # take the whole fan-out down.
        return True
    if scope is None or scope.empty:
        return True                                   # unrestricted driver
    return scope.allows(
        registry_id=vehicle.get("registry_id"),
        external_id=vehicle.get("id") or vehicle.get("vehicle_id"),
        name=vehicle.get("name") or vehicle.get("vehicle_name"),
    )


def sees_vehicle(sub, vehicle: dict, gate: dict) -> bool:
    """Subscriber-object form of :func:`user_sees_vehicle`."""
    return user_sees_vehicle(getattr(sub, "id", None), getattr(sub, "role", ""),
                             vehicle, gate)


async def filter_subscribers_by_vehicle(
    subscribers: list, vehicle: dict, account_id: int,
) -> list:
    """Drop subscribers whose vehicle assignment excludes THIS vehicle.

    No-op when there are no subscribers, no vehicle identity to match on,
    or nobody in the account is restricted.
    """
    if not subscribers or not vehicle:
        return subscribers
    gate = await load_vehicle_gate(account_id)
    if not gate:
        return subscribers
    return [s for s in subscribers if sees_vehicle(s, vehicle, gate)]
