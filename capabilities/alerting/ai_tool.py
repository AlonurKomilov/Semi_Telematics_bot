"""Alerts AI tool — recent alert history.

Account-wide, so it filters its results to the caller's Vehicle-Access scope
via the shared helper (a company/vehicle-restricted user sees only their own
vehicles' alerts).
"""

from __future__ import annotations

from capabilities.ai.tools.registry import register_tool
from capabilities.ai.tools.scope import filter_to_scope


@register_tool({
    "name": "get_alert_history",
    "description": (
        "List recent alerts for the account.  Returns alert type, "
        "severity (critical/warning/info), vehicle, status (active / "
        "acknowledged / cleared), when first fired, how many times "
        "it's fired without being cleared, who acknowledged it (if "
        "anyone), and the most recent detail message.  Use for "
        "questions like 'any unacknowledged alerts', 'which truck "
        "alerted most this week', 'what alerts fired overnight', "
        "'show critical alerts from yesterday'."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "limit": {
                "type": "number",
                "description": (
                    "Max alerts to return (default 25, max 100).  "
                    "Newest-first."
                ),
            },
            "alert_type": {
                "type": "string",
                "description": (
                    "Optional filter: 'fault', 'health', 'fuel', "
                    "'parking', 'event', 'maintenance'."
                ),
            },
            "vehicle_substring": {
                "type": "string",
                "description": (
                    "Optional partial vehicle name to filter by "
                    "(e.g. '231' matches 'Truck 231')."
                ),
            },
            "status": {
                "type": "string",
                "description": (
                    "Optional: 'active' (unacknowledged + unresolved), "
                    "'acknowledged', 'cleared'."
                ),
            },
            "severity": {
                "type": "string",
                "description": (
                    "Optional: 'critical', 'warning', 'info'."
                ),
            },
        },
        "required": [],
    },
})
async def get_alert_history(tool_args: dict, samsara_client,
                            account_id: int | None = None, db=None) -> dict:
    if not db or account_id is None:
        return {"error": "Alert history not available in this context"}

    limit = min(int(tool_args.get("limit") or 25), 100)
    alert_type = (tool_args.get("alert_type") or "").strip() or None
    veh = (tool_args.get("vehicle_substring") or "").strip() or None
    status = (tool_args.get("status") or "").strip().lower() or None
    severity = (tool_args.get("severity") or "").strip().lower() or None

    # Same source the dashboard's Alerts page reads (``alert_history``
    # with the severity/occurrence columns + acknowledged_by_name join)
    # — the AI's answers must match what the user sees on screen.  The
    # old read hit ``alert_acknowledgments``, which has NO severity /
    # occurrence_count / first_seen / location columns: a severity
    # filter crashed the tool outright, and every returned alert
    # claimed severity "info".  ``status`` maps onto the page's
    # ack-state semantics: 'active', 'acknowledged'/'cleared' (both
    # mean "no longer active" in alert_history), or all states.
    ack_state = "all"
    if status == "active":
        ack_state = "active"
    elif status in ("acknowledged", "cleared"):
        ack_state = "acknowledged"
    rows = await db.get_active_alert_history_for_account_paged(
        account_id,
        alert_type=alert_type, vehicle_substring=veh,
        severity=severity, ack_state=ack_state,
        limit=limit,
    )

    # Vehicle-Access scope: only the caller's own vehicles' alerts.
    rows = filter_to_scope(rows, tool_args)

    return {
        "count": len(rows),
        "filters": {
            "alert_type": alert_type, "vehicle_substring": veh,
            "status": status, "severity": severity,
        },
        "alerts": [
            {
                "id": r.get("id"),
                "vehicle": r.get("vehicle_name") or "",
                "type": r.get("alert_type") or "",
                "severity": r.get("severity") or "info",
                "status": r.get("status") or "",
                "first_seen": r.get("first_seen") or "",
                "last_seen": r.get("last_seen") or "",
                "occurrence_count": r.get("occurrence_count") or 1,
                "location": r.get("location") or "",
                "detail": r.get("last_detail") or "",
                "acknowledged_by": r.get("acknowledged_by_name") or "",
                "acknowledged_at": r.get("acknowledged_at") or "",
            }
            for r in rows
        ],
    }
