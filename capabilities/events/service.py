"""Events service — Single Source of Truth for safety/driver events data."""

from __future__ import annotations

from core.services import get_client
from capabilities.vehicle_catalog.service import prepare_companies


async def get_events(
    account_id: str,
    days: int = 7,
    company: str | None = None,
) -> list[dict]:
    """Fetch safety events (hard brakes, speeding, etc.).

    Returns list of event dicts from the Samsara API.
    """
    await prepare_companies(account_id)
    client = await get_client(account_id)
    return await client.get_events(days=days, company=company)


def filter_events_by_access(events: list[dict], truck_nums: list[str]) -> list[dict]:
    """Filter events to only include those belonging to the given trucks.

    Matching is case-insensitive substring match on vehicle_name, same as
    the per-driver filter that was previously inline in bot/events.py.
    Returns an empty list when truck_nums is empty.
    """
    if not truck_nums:
        return []
    needles = [t.lower() for t in truck_nums]
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
