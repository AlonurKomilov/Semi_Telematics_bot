"""Alerts AI tool — recent alert history.

Account-wide, so it filters its results to the caller's Vehicle-Access scope
via the shared helper (a company/vehicle-restricted user sees only their own
vehicles' alerts).
"""

from __future__ import annotations

from capabilities.ai.tools.registry import (
    register_tool, register_action_executor, tool_propose, tool_error,
)
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
