"""Alerting service — access-control helpers for alert data.

Pure functions (no I/O) so they can be shared by the bot and API layers.
Async fetching of truck assignments is done in the caller before calling
these helpers.
"""

from __future__ import annotations


def filter_alerts_by_access(alerts: list[dict], truck_nums: list[str]) -> list[dict]:
    """Return only alerts whose vehicle_name matches one of *truck_nums*.

    Pass an empty *truck_nums* list to receive an empty result (the caller
    has confirmed the user has no assigned vehicles).
    """
    if not truck_nums:
        return []
    needles = [t.lower() for t in truck_nums]
    return [
        a for a in alerts
        if any(n in (a.get("vehicle_name") or "").lower() for n in needles)
    ]
