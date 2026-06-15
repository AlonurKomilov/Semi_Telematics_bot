"""Alert-history tool — wraps the alert_history acknowledgment table.

Lets the AI agent answer "any alerts last week?", "which truck alerts
the most?", "any unacknowledged alerts right now?" — the same data
that drives the dashboard's /alerts page, just queryable from chat.
"""

from __future__ import annotations

from capabilities.ai.tools.registry import register_tool


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
    status = (tool_args.get("status") or "").strip() or None
    severity = (tool_args.get("severity") or "").strip() or None

    rows = await db.get_alert_history(
        account_id, limit=limit,
        alert_type=alert_type, vehicle_substring=veh,
        status=status, severity=severity,
    )

    # Vehicle-Access scope: a company/vehicle-restricted caller only sees
    # alerts for their own vehicles (orchestrator injects the allowed set;
    # None = unrestricted, even-empty = restrict to exactly it).
    scope = tool_args.get("_scope_vehicles")
    if scope is not None:
        scope_set = {str(v).strip().lower() for v in scope if v}
        rows = [
            r for r in rows
            if (r.get("vehicle_name") or "").strip().lower() in scope_set
        ]

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
                "first_seen": r.get("created_at") or r.get("first_seen") or "",
                "last_seen": r.get("last_seen") or "",
                "occurrence_count": r.get("occurrence_count") or 1,
                "location": r.get("location") or "",
                "detail": r.get("last_detail") or r.get("message") or "",
                "acknowledged_by": r.get("acknowledged_by_name") or "",
                "acknowledged_at": r.get("acknowledged_at") or "",
            }
            for r in rows
        ],
    }
