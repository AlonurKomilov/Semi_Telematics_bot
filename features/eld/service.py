"""ELD reads — the mirror, with its age attached.

Every answer this feature gives carries how old it is, because an
hours-of-service reading without an age is a claim nobody can check.
A duty clock changes by the second and the ingest runs every five
minutes, so a reading is *always* somewhat behind: the honest surface
is not one that hides the lag but one that states it.

The service is also where the account's data becomes one caller's
data.  Duty status is FMCSA-regulated information about a named
person, so nothing here returns rows the caller is not entitled to,
and the narrowing happens before anything is counted.
"""

from __future__ import annotations

import logging
from typing import Any, Optional

from capabilities.data_lifecycle.staleness import data_age_minutes

logger = logging.getLogger(__name__)


#: Past this, a reading stops being shown as current.  Two ingest ticks
#: plus a margin: one missed poll is a hiccup, three is a feed that
#: stopped, and the difference matters because the second is the case
#: where a dispatcher must go and look at the ELD itself.
STALE_AFTER_MINUTES = 15


def _clock(seconds: Optional[int]) -> Optional[dict]:
    """One clock, in the two units a human and a machine each want.

    ``None`` passes straight through.  It means the provider did not
    report this clock — a different fact from zero, which means the
    driver has run out, and the two must never render the same.
    """
    if seconds is None:
        return None
    return {"seconds": int(seconds), "hours": round(int(seconds) / 3600, 1)}


def _scope_admits(scope, row: dict) -> bool:
    """Is ANY truck this driver is assigned to inside the scope?

    A driver with no assignment at all — every unlinked driver, and a
    linked one between trucks — is denied to a scoped caller.  There is
    no truck to place them on, and admitting them would hand a
    company-restricted dispatcher a person they cannot account for.
    """
    for v in row.get("vehicles") or []:
        if scope.allows(registry_id=v.get("registry_id"),
                        name=v.get("name") or None):
            return True
    return False


def project(row: dict, *, now=None) -> dict:
    """One stored row → the shape every ELD surface reads.

    ``age_minutes`` and ``stale`` are computed from ``source_ts`` (when
    the PROVIDER observed it), never from our write time.  A row we
    stored thirty seconds ago can describe a driver who has been
    driving for the last twenty minutes.
    """
    age = data_age_minutes(row.get("source_ts"), now=now)
    return {
        "driver": row.get("display_name") or "",
        "user_id": row.get("user_id"),
        "linked": bool(row.get("linked")),
        "vehicle": row.get("truck_num") or "",
        "duty_status": row.get("duty_status") or "unknown",
        "since": row.get("last_status_change") or "",
        # Four countdowns, named for what they are.  There is no
        # "used today" here because an ELD does not report one, and
        # deriving it needs the ruleset limit — the computation this
        # feature refuses to do.
        "drive_remaining": _clock(row.get("drive_remaining_seconds")),
        "shift_remaining": _clock(row.get("shift_remaining_seconds")),
        "cycle_remaining": _clock(row.get("cycle_remaining_seconds")),
        "break_in": _clock(row.get("break_in_seconds")),
        "source": row.get("provider_id") or "",
        "as_of": row.get("source_ts") or "",
        # None when we cannot read the timestamp at all — unknown age is
        # not the same as fresh, and a surface must be able to say so.
        "age_minutes": None if age is None else round(age, 1),
        "stale": True if age is None else age > STALE_AFTER_MINUTES,
    }


async def get_hours(
    db: Any,
    account_id: int,
    *,
    user_id: Optional[int] = None,
    vehicle_scope: Optional[list[str]] = None,
) -> dict:
    """The account's duty clocks, narrowed to this caller.

    Returns ``{"connected", "drivers", "stale_count", ...}``.

    ``connected`` is the load-bearing field and it is NOT derived from
    the row count.  Zero drivers because no ELD was ever connected and
    zero drivers because everyone is filtered out are different
    answers, and on a compliance question the difference is the whole
    answer: an empty list read as "nobody is near their limit" is the
    failure this feature exists to avoid.

    ``vehicle_scope`` is a :class:`VehicleScope`, not a list of names.
    It narrows by the driver's ASSIGNED trucks through the full identity
    ladder, because a bare unit number cannot decide this: numbers are
    reused across the companies inside one account, so a caller scoped
    to one company's "103" would otherwise be shown the other
    company's driver — live duty status, for a person outside their
    wall.  ``None`` means unrestricted; an empty scope means nothing,
    and is honoured as nothing.

    A driver is admitted when ANY of their assigned trucks is, which is
    the same rule ``features/drivers/ai_tool.py::_drivers_in_scope``
    already applies to driver rows.  It lives HERE rather than in each
    surface so the route and the AI tool cannot drift apart.
    """
    ever = await db.count_driver_hos_live(account_id)
    rows = await db.get_driver_hos_live(account_id, user_id=user_id)

    if vehicle_scope is not None:
        rows = [r for r in rows if _scope_admits(vehicle_scope, r)]

    drivers = [project(r) for r in rows]
    return {
        "connected": ever > 0,
        "count": len(drivers),
        "drivers": drivers,
        "stale_count": sum(1 for d in drivers if d["stale"]),
        "stale_after_minutes": STALE_AFTER_MINUTES,
        # Said once, here, so no surface has to remember to say it: we
        # are not the system of record and nothing downstream may
        # compute a violation from these numbers.
        "record_of": (
            "The connected ELD is the system of record for hours of "
            "service. This is a read-only mirror of what it reported."
        ),
    }
