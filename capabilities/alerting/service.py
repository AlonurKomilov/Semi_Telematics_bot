"""Alerting service — access-control helpers for alert data.

Pure functions (no I/O) so they can be shared by the bot and API layers.
Async fetching of truck assignments is done in the caller before calling
these helpers.
"""

from __future__ import annotations


def filter_alerts_by_access(
    alerts: list[dict],
    vehicle_nums: list[str],
    scope=None,
) -> list[dict]:
    """Return only alerts the assignment actually covers.

    Pass an empty *vehicle_nums* list to receive an empty result (the caller
    has confirmed the user has no assigned vehicles).

    With a ``VehicleScope`` the decision rides the identity ladder
    (registry id → provider id → exact name).  Without one, exact
    lowercased equality — never the old substring, which let an
    assignment of 230 read alerts for 2303.  This wall decides what a
    driver may SEE, so over-matching here was a disclosure.
    """
    if scope is not None:
        if scope.empty:
            return []
        return [a for a in alerts if scope.allows_row(a, name_key="vehicle_name")]
    if not vehicle_nums:
        return []
    allowed = {t.strip().lower() for t in vehicle_nums if t}
    return [
        a for a in alerts
        if (a.get("vehicle_name") or "").strip().lower() in allowed
    ]
