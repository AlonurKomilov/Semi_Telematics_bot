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

from adapters.telematics.catalog import PROVIDER_CATALOG
from adapters.telematics.protocol import Capability, HosClock
from adapters.telematics.registry import get_provider, is_registered
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




async def feed_status(account_id: int) -> Optional[dict]:
    """Which ELD is actually SERVING hours of service for this account.

    ``connected`` used to mean "we have ingested at least one row", and
    that collapsed two states a reader needs kept apart.  An account
    that just connected its first ELD — keys set, every company green,
    the first poll not yet due — was shown "No electronic logging device
    is connected".  That is not a cautious answer, it is a false one,
    and it is false on the page whose entire purpose is not making false
    statements about hours of service.

    It also could not explain the quieter case.  Two connected providers
    may both offer hours of service, the resolver takes the first in
    CATALOG order, and the loser is simply never asked.  An operator who
    has just wired five ORIENT keys and sees nothing has no way to learn
    that a telematics integration further up the catalog is the one
    being polled — the fact lives in one log line.

    So this asks the integrations layer directly, and reports:

      ``serving``    the provider that will actually be polled, or None
      ``connected``  every connected provider offering the capability
      ``shadowed``   connected, offering it, and NOT being asked

    Returns ``None`` when the integrations layer cannot be reached.  Not
    ``{}``: unknown is a third answer, and a caller must not read a
    failed lookup as "nothing is connected" — that is the same mistake
    one layer up.
    """
    try:
        from capabilities.integrations.shared.resolver import (
            providers_offering, resolve_provider_for,
        )
        from infra.platform import get_platform_db

        db = get_platform_db()
        offering = providers_offering(Capability.DRIVER_HOS)
        connected: list[str] = []
        for pid in offering:
            row = await db.get_account_integration(account_id, pid)
            if row is not None and getattr(row, "status", "") == "connected":
                connected.append(pid)
        resolved = await resolve_provider_for(account_id, Capability.DRIVER_HOS)
        serving = resolved.provider_id if resolved else None
    except (NameError, AttributeError, ImportError):
        # A broken import or a typo is OUR bug, not an unreachable
        # integrations layer, and swallowing it here made this function
        # return None on every call while every test stayed green —
        # which would have silently un-fixed the thing it was written
        # for.  Let it crash loudly in the caller's log instead of
        # quietly answering "unknown" forever.
        raise
    except Exception:
        # A real outage: the platform DB is down, a row is malformed.
        # Unknown is the honest answer and the caller treats it as a
        # third state, never as "nothing is connected".
        logger.exception("eld: feed status lookup failed acct=%d", account_id)
        return None

    def _name(pid: str) -> str:
        entry = PROVIDER_CATALOG.get(pid)
        return entry.display_name if entry else pid

    return {
        "serving": serving,
        "serving_name": _name(serving) if serving else "",
        "connected": [{"id": p, "name": _name(p)} for p in connected],
        # Connected, offers hours of service, and is not the one being
        # polled.  Named so the page can say WHICH integration to switch
        # off rather than leaving the operator to compare two cards.
        "shadowed": [
            {"id": p, "name": _name(p)} for p in connected if p != serving
        ],
    }


def _provider_facts(rows: list[dict]) -> tuple[dict, list[str]]:
    """Which ELDs are behind these rows, and which clocks they report.

    Not every electronic logging device reports remaining hours.  Some
    publish duty status and nothing else — a real product, not a broken
    integration — and a page that renders four empty columns for one of
    them is saying "zero hours left" four times over.  So the answer
    carries what each provider is CAPABLE of, and the surface states it
    instead of leaving the reader to infer it from blanks.

    Resolved through the registry, never by comparing a provider id: the
    third ELD may report two of the four clocks, and a surface written
    around "is it ORIENT" would be wrong about it on day one.

    ``clocks_reported`` is ``None`` — not ``[]`` — for a provider that
    is no longer registered.  Unknown is not the same statement as
    "reports none", and only one of them may be printed.

    Returns ``(per_provider, union)``.  The union is what decides which
    columns exist.  It has a floor: a clock that actually carries a
    value in these rows is always included, even if nobody declared it.
    That floor can only ADD a column, never remove one, so the worst
    case is a column that shows real data next to a stale declaration —
    where the alternative would be hiding data we hold.
    """
    facts: dict[str, dict] = {}
    union: set[str] = set()
    for pid in sorted({str(r.get("provider_id") or "") for r in rows} - {""}):
        entry = PROVIDER_CATALOG.get(pid)
        clocks: Optional[list[str]] = None
        if is_registered(pid):
            declared = getattr(
                get_provider(pid), "hos_clocks_reported", frozenset())
            clocks = sorted(declared)
            union |= set(declared)
        facts[pid] = {
            "name": entry.display_name if entry else pid,
            "clocks_reported": clocks,
        }
    for clock_id, field in HosClock.FIELDS.items():
        if any(r.get(field) is not None for r in rows):
            union.add(clock_id)
    return facts, sorted(union)


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
        # Which of the account's carriers this driver works for.  An
        # account is several legal companies, and every other grid on
        # the platform carries this column; hours of service omitted it
        # only because the feature shipped against one company.
        "company": row.get("company_code") or "",
        "linked": bool(row.get("linked")),
        # OUR roster's truck first, the DEVICE's second.
        #
        # Sixty-nine drivers, sixty-nine dashes: the column read only
        # our roster, and an unlinked driver has no roster truck — while
        # the ELD had been telling us which truck they were on all
        # along.  Preferring ours is still right when we have it: an
        # assignment is a decision somebody made, where the device's
        # answer is wherever the tractor happens to be.
        #
        # ``vehicle_from`` says which one this is, because a surface
        # must not present a device's guess and an operator's decision
        # as the same fact.
        "vehicle": (row.get("truck_num") or "").strip()
                   or (row.get("provider_vehicle") or "").strip(),
        "vehicle_from": (
            "roster" if (row.get("truck_num") or "").strip()
            else "eld" if (row.get("provider_vehicle") or "").strip()
            else ""
        ),
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
    feed: Optional[dict] = None,
) -> dict:
    """The account's duty clocks, narrowed to this caller.

    Returns ``{"connected", "drivers", "clocks_reported", ...}``.

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
    # Passed in by a caller that already has it; looked up otherwise.
    # ``None`` survives as None — a lookup that failed is not an
    # account with nothing connected.
    if feed is None:
        feed = await feed_status(account_id)

    before_scope = len(rows)
    if vehicle_scope is not None:
        rows = [r for r in rows if _scope_admits(vehicle_scope, r)]
    hidden_by_scope = before_scope - len(rows)

    drivers = [project(r) for r in rows]
    # Read from the rows the caller can actually see: a provider whose
    # every driver the scope removed is not one this caller is looking
    # at, and naming it would leak which ELDs the account runs.
    providers, clocks_reported = _provider_facts(rows)
    # An ELD counts as connected because it IS connected, not because
    # it has spoken yet.  Rows remain sufficient on their own: if we
    # hold readings, something produced them, whatever the integrations
    # layer says today.
    has_provider = bool((feed or {}).get("connected"))
    return {
        "connected": ever > 0 or has_provider,
        # Connected, and nothing has arrived yet.  The honest reading of
        # a fresh connect, and the state that used to be reported as "no
        # device is connected".
        "awaiting_first_reading": has_provider and ever == 0,
        # Which integration is actually polled for this, and which
        # connected ones are not.  ``None`` when we could not ask.
        "feed": feed,
        "count": len(drivers),
        # Which of the four countdowns any connected ELD here reports,
        # and which ELD is behind each row.  A surface uses the first to
        # decide which columns exist at all, and the second to say whose
        # limitation it is — "ORIENT ELD reports duty status only" is an
        # answer; four blank columns is a trap.
        "clocks_reported": clocks_reported,
        "providers": providers,
        # The carriers present in what this caller can see.  Sorted and
        # blank-free: a single-company account sends ``[]`` and the page
        # renders no Company column, rather than a column of blanks.
        "companies": sorted(
            {str(r.get("company_code") or "") for r in rows} - {""}
        ),
        # How many drivers the caller's own vehicle access removed.
        #
        # A surface that shows three of ten drivers and says nothing is
        # under-reporting silently, which on a compliance page is the
        # same failure class as answering zero — the reader concludes
        # they are looking at the whole picture.  The empty state
        # already names the constraint when NOTHING survives; this is
        # what lets a surface say it in between.
        #
        # Counted after the user_id filter, so asking about one driver
        # never reads as "nine hidden from you".
        "hidden_by_scope": hidden_by_scope,
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
