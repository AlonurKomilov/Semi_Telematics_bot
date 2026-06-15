"""Idle-vehicles tool — wraps the parking-event tracker.

Surfaces "which trucks have been sitting in unsafe / unknown locations
for N+ days" to the AI agent.  The same data drives the dashboard's
Parking page; this tool gives the chat side a way to answer
"which truck was off 10 days?" without the agent inventing data.
"""

from __future__ import annotations

from capabilities.ai.tools.registry import register_tool


@register_tool({
    "name": "get_idle_vehicles",
    "description": (
        "List vehicles that have been parked / stopped / not driving "
        "for at least the requested number of days.  Returns vehicle "
        "name, parked location (address + lat/lon), how long it's "
        "been parked (in hours and days), location safety "
        "classification, and the company code.\n\n"
        "USE THIS TOOL when the user asks any of:\n"
        "- 'which trucks have been sitting more than a week'\n"
        "- 'where is truck X parked'\n"
        "- 'show long-idle trucks'\n"
        "- 'what vehicle was stopped N days'\n"
        "- 'what truck hasn't moved in N days / hours'\n"
        "- 'which vehicles have been without driving for N days'\n"
        "- 'what trucks are off the road / out of service'\n"
        "- 'how long has truck X been parked'\n"
        "DO NOT refuse these questions claiming you only have "
        "'real-time data' — this tool has the history."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "min_days": {
                "type": "number",
                "description": (
                    "Minimum idle duration in days (default 1). "
                    "Use 7 for 'a week', 10 for 'ten days off', etc."
                ),
            },
            "include_safe": {
                "type": "boolean",
                "description": (
                    "If true, include vehicles parked in 'safe' or "
                    "geofenced locations.  Default false — those are "
                    "the depot/yard idle which usually isn't what the "
                    "user is asking about."
                ),
            },
            "company": {
                "type": "string",
                "description": (
                    "Optional company code filter (e.g. 'PTG', 'OSY')."
                ),
            },
        },
        "required": [],
    },
})
async def get_idle_vehicles(tool_args: dict, samsara_client,
                            account_id: int | None = None, db=None) -> dict:
    if not db or account_id is None:
        return {"error": "Parking data not available in this context"}

    min_days = float(tool_args.get("min_days") or 1)
    include_safe = bool(tool_args.get("include_safe") or False)
    company = (tool_args.get("company") or "").strip()

    # Vehicle-Access scope: for a company/vehicle-restricted caller the
    # orchestrator injects the allowed vehicle names; we return only those.
    # ``None`` = unrestricted; an (even empty) set = filter to exactly it.
    scope = tool_args.get("_scope_vehicles")
    scope_set = (
        {str(v).strip().lower() for v in scope if v}
        if scope is not None else None
    )

    events = await db.get_active_parking_events(
        account_id, attention_only=not include_safe,
    )

    min_hours = min_days * 24.0
    filtered: list[dict] = []
    for ev in events:
        dur = float(ev.get("duration_hours") or 0)
        if dur < min_hours:
            continue
        if company and (ev.get("company_code") or "").upper() != company.upper():
            continue
        if scope_set is not None and (
            (ev.get("vehicle_name") or "").strip().lower() not in scope_set
        ):
            continue
        filtered.append(ev)

    return {
        "min_days": min_days,
        "include_safe": include_safe,
        "company_filter": company or None,
        "count": len(filtered),
        "vehicles": [
            {
                "vehicle": ev.get("vehicle_name", "?"),
                "company": ev.get("company_code", ""),
                "address": ev.get("address", ""),
                "lat": ev.get("latitude"),
                "lon": ev.get("longitude"),
                "duration_hours": round(float(ev.get("duration_hours") or 0), 1),
                "duration_days": round(float(ev.get("duration_hours") or 0) / 24.0, 1),
                "location_class": ev.get("location_class", "unknown"),
                "first_seen": ev.get("first_seen") or ev.get("created_at", ""),
            }
            for ev in filtered[:25]
        ],
    }
