"""Events service — Single Source of Truth for safety/driver events data."""

from __future__ import annotations

from infra.services import get_client
from features.vehicles.service import prepare_companies


async def get_events(
    account_id: int,
    days: int = 7,
    company: str | None = None,
    vehicle_id: str | None = None,
) -> list[dict]:
    """Fetch safety events (hard brakes, speeding, etc.).

    Warehouse-first: reads ``safety_event_log`` when WAREHOUSE_READS_ENABLED=1,
    falls back to live Samsara otherwise (or on cold-start empty warehouse).

    ``vehicle_id`` is the truck's PROVIDER id, and it narrows the
    warehouse read rather than deciding membership: a caller asking
    about one truck was reading the whole account's window — every
    truck's events for up to 30 days, each row decoding its own 2 KB
    raw blob — to keep one truck's.  Rows that carry NO provider id
    come back too, because only the caller's identity ladder can judge
    those, by unit name.  The narrowing is therefore a superset of any
    one truck's rows, never a filter the caller can rely on: callers
    must still apply their own.

    It has no effect on the live-Samsara fallback, which has no
    per-vehicle endpoint here — that path stays account-wide and the
    caller's own filter does the work, as it always did.
    """
    await prepare_companies(account_id)
    client = await get_client(account_id)

    async def _live():
        return await client.get_events(days=days, company=company)

    from features.vehicles.warehouse import readers as _wh
    rows = await _wh.get_safety_events(
        account_id, days=days, samsara_fallback=_live,
        vehicle_id=vehicle_id or None,
        include_unidentified=bool(vehicle_id),
    )
    # Warehouse rows wrap the original payload under ``raw``; unwrap so
    # downstream consumers see the same shape as the live API.
    events = [r.get("raw", r) for r in rows]
    if company:
        events = [
            e for e in events
            if (e.get("_org") or e.get("company") or "") == company
        ]
    return events


def filter_events_by_access(events: list[dict], vehicle_nums: list[str]) -> list[dict]:
    """Filter events to only include those belonging to the given trucks.

    Matching is case-insensitive substring match on vehicle_name, same as
    the per-driver filter that was previously inline in bot/events.py.
    Returns an empty list when vehicle_nums is empty.
    """
    if not vehicle_nums:
        return []
    needles = [t.lower() for t in vehicle_nums]
    return [
        e for e in events
        if any(n in e.get("vehicle_name", "").lower() for n in needles)
    ]


def aggregate_events(events: list[dict]) -> dict:
    """Count events broken down by type and severity.

    Returns ``{"by_type": {event_type: count, ...}, "by_severity": {...}}``.
    """
    by_type: dict[str, int] = {}
    by_severity: dict[str, int] = {}
    for e in events:
        etype = e.get("event_type", "unknown")
        esev = e.get("severity", "mild")
        by_type[etype] = by_type.get(etype, 0) + 1
        by_severity[esev] = by_severity.get(esev, 0) + 1
    return {"by_type": by_type, "by_severity": by_severity}
