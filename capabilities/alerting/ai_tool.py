"""Alerts AI tool — recent alert history.

Account-wide, so it filters its results to the caller's Vehicle-Access scope
via the shared helper (a company/vehicle-restricted user sees only their own
vehicles' alerts).
"""

from __future__ import annotations

from capabilities.ai.tools.registry import (
    register_tool, register_action_executor, tool_propose, tool_error,
)
from capabilities.ai.tools.scope import filter_to_scope, scope_vehicle_set


# The alert_type values the store actually holds.  Kept beside the
# schema that advertises them: a filter value the data never carries
# returns nothing, and nothing reads to the model as "all clear".
_ALERT_TYPES: frozenset[str] = frozenset({
    "events", "parking", "fault", "fuel", "health", "scorecard",
    "maintenance", "camera", "geofence", "documents",
})


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
                "enum": [
                    "events", "parking", "fault", "fuel", "health",
                    "scorecard", "maintenance", "camera", "geofence",
                    "documents",
                ],
                "description": (
                    "Optional filter.  Note 'events' is plural — it is "
                    "the safety-event family (harsh braking, speeding, "
                    "distraction) and the busiest type there is."
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
                "enum": ["active", "acknowledged", "cleared"],
                "description": (
                    "Optional: 'active' (unacknowledged + unresolved), "
                    "'acknowledged', 'cleared'."
                ),
            },
            "severity": {
                "type": "string",
                "enum": ["critical", "warning", "info"],
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

    from capabilities.ai.tools import tool_error

    limit = min(int(tool_args.get("limit") or 25), 100)
    alert_type = (tool_args.get("alert_type") or "").strip().lower() or None
    veh = (tool_args.get("vehicle_substring") or "").strip() or None
    status = (tool_args.get("status") or "").strip().lower() or None
    severity = (tool_args.get("severity") or "").strip().lower() or None

    # The safety-event family is stored PLURAL.  The board's own reader
    # accepts both spellings (capabilities/alerting/router.py) and this
    # tool did not, so `alert_type='event'` — the singular the schema
    # itself advertised — matched none of the 8,000-odd rows and the
    # model reported a quiet week.
    if alert_type == "event":
        alert_type = "events"
    if alert_type and alert_type not in _ALERT_TYPES:
        return tool_error(
            f"alert_type must be one of {', '.join(sorted(_ALERT_TYPES))}."
        )
    if status and status not in ("active", "acknowledged", "cleared"):
        return tool_error("status must be active, acknowledged or cleared.")
    if severity and severity not in ("critical", "warning", "info"):
        return tool_error("severity must be critical, warning or info.")

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
    # The cap goes into SQL only for an UNRESTRICTED caller.
    #
    # For a scoped one it used to, and the scope filter ran afterwards
    # in Python — so the 25 rows SQL chose were the ACCOUNT's most
    # severe (the ORDER BY is account-wide severity, then recency), the
    # filter removed the ones belonging to other trucks, and the caller
    # was told what survived. A driver scoped to one truck in a busy
    # account got the fleet's 25 worst alerts, had every one of them
    # dropped, and read "no alerts on your truck" while their own truck
    # carried open warnings below the cut.
    #
    # The REST board already knows this shape is wrong: for a driver- or
    # company-scoped caller it calls this same method with NO limit and
    # paginates in Python afterwards. Same rule here.
    scoped = scope_vehicle_set(tool_args) is not None
    rows = await db.get_active_alert_history_for_account_paged(
        account_id,
        alert_type=alert_type, vehicle_substring=veh,
        severity=severity, ack_state=ack_state,
        limit=None if scoped else limit,
    )

    # Vehicle-Access scope: only the caller's own vehicles' alerts.
    matched_before_cap = len(rows)
    rows = filter_to_scope(rows, tool_args)
    matched = len(rows)
    if scoped and matched > limit:
        rows = rows[:limit]

    return {
        "count": len(rows),
        # What the caller's own scope actually holds, so a truncated
        # answer says so instead of reading as the whole picture.
        "matched": matched if scoped else matched_before_cap,
        "truncated": bool(scoped and matched > len(rows)),
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


# ── Write action: acknowledge alerts (copilot "hands") ────────────
#
# PROPOSE: the AI names which alerts (by the ids get_alert_history
# returned) to acknowledge, validates, returns a proposal.
# EXECUTE (post-approval only): clear each logical alert, scoped to the
# account, via acknowledge_alert_history.

@register_tool({
    "name": "acknowledge_alerts",
    "description": (
        "Propose acknowledging (clearing) one or more alerts by their id. "
        "Does NOT clear them directly — asks the user to approve first. "
        "Get the ids from get_alert_history. Use when the user asks to "
        "acknowledge / clear / dismiss specific alerts."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "alert_ids": {
                "type": "array",
                "items": {"type": "number"},
                "description": "Alert ids (from get_alert_history) to acknowledge.",
            },
        },
        "required": ["alert_ids"],
    },
    "writes": True,
    "risk": "low",
    # Scope shape (enforced by tests/test_ai_write_tool_scope.py): args are
    # resource ids, so scope is enforced by filtering the ids to the caller's
    # vehicles (⇒ must be in SCOPE_AWARE_TOOLS).
    "scope": "resource_ids",
})
async def acknowledge_alerts(tool_args, samsara_client,
                             account_id=None, db=None):
    raw = tool_args.get("alert_ids") or []
    ids: list[int] = []
    for x in raw:
        try:
            ids.append(int(x))
        except (TypeError, ValueError):
            continue
    ids = ids[:25]
    if not ids:
        return tool_error("No alert ids given to acknowledge.")

    # Vehicle-Access scope: for a company/vehicle-restricted caller the
    # orchestrator injects the allowed vehicle names as ``_scope_vehicles``
    # (None = unrestricted).  Refuse the batch if any requested id is on a
    # vehicle outside that scope — counts only, never other vehicles'
    # details.  The executor + storage re-enforce this at approve time; this
    # is the early, in-chat feedback so the model never proposes a write the
    # user can't approve.
    scope = tool_args.get("_scope_vehicles")
    if scope is not None and db is not None and account_id is not None:
        allowed = {str(v).strip().lower() for v in scope if v}
        veh_by_id = await db.get_alert_history_vehicles(account_id, ids)
        out_of_scope = [
            i for i in ids
            if i in veh_by_id and veh_by_id[i].strip().lower() not in allowed
        ]
        if out_of_scope:
            n_bad = len(out_of_scope)
            return tool_error(
                f"{n_bad} of these alert{'s are' if n_bad != 1 else ' is'} on "
                f"vehicle(s) outside your access. You can only acknowledge "
                f"alerts on your own vehicles."
            )

    n = len(ids)
    summary = f"Acknowledge {n} alert{'s' if n != 1 else ''} (mark them cleared)."
    return tool_propose(
        "acknowledge_alerts", summary, {"alert_ids": ids}, risk="low",
        consequence="Marks these alerts as acknowledged and clears them from the active list.",
    )


@register_action_executor("acknowledge_alerts")
async def _execute_acknowledge_alerts(payload, account_id, user_context, db):
    """Clear each logical alert — runs only post-approval.  Each ack is
    account-scoped AND vehicle-scoped in the storage method; unknown /
    already-cleared / out-of-scope ids are silently skipped (idempotent).

    Defense-in-depth: even though the propose step scope-checks the ids, we
    re-resolve the approver's scope here and let the SQL enforce it, so a
    stale/tampered proposal can never clear a vehicle the approver can't
    access.  Scope is resolved with the SAME helper the tool gate uses."""
    from capabilities.ai.intelligence import _scoped_vehicle_set
    ids = payload.get("alert_ids") or []
    uid = int((user_context or {}).get("user_id") or 0)
    # None = unrestricted; a list = only those vehicles ([] = none).
    allowed = _scoped_vehicle_set(user_context, (user_context or {}).get("role"))
    acked = 0
    for aid in ids:
        try:
            row = await db.acknowledge_alert_history(
                int(aid), uid, account_id, allowed_vehicle_names=allowed)
            if row is not None:
                acked += 1
        except (TypeError, ValueError):
            continue
    return {
        "acknowledged": acked,
        "requested": len(ids),
        "target_type": "alert",
        "target_id": ",".join(str(i) for i in ids)[:200],
        "message": f"Acknowledged {acked} of {len(ids)} alert(s).",
    }
