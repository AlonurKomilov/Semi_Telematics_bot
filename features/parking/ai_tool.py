"""Parking AI tool — long-idle / parked vehicles (wraps the parking tracker).

Co-located with the parking feature.  Account-wide, so it filters its results
to the caller's Vehicle-Access scope via the shared ``filter_to_scope``,
which decides each row by the strongest identity rung it and the scope share.
"""

from __future__ import annotations

from capabilities.ai.tools.registry import register_tool
from capabilities.ai.tools.scope import filter_to_scope


@register_tool({
    "name": "get_parked_vehicles",
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
async def get_parked_vehicles(tool_args: dict, samsara_client,
                            account_id: int | None = None, db=None) -> dict:
    if not db or account_id is None:
        return {"error": "Parking data not available in this context"}

    min_days = float(tool_args.get("min_days") or 1)
    include_safe = bool(tool_args.get("include_safe") or False)
    company = (tool_args.get("company") or "").strip()

    # Read through the feature's own contract, not the ACTIVE-only
    # adapter.  The tracker resolves a stop once the truck moves, so the
    # unresolved table only ever holds short stays — in production its
    # longest was 65 hours, under three days — while 5,189 resolved
    # events reached thirteen. The description tells the model to pass
    # 7 and 10, and promises "this tool has the history"; against the
    # active table those questions could only ever answer zero, and zero
    # reads as "no truck has been sitting".
    #
    # `days` bounds the history window: a stop cannot be longer than the
    # window it is read from, so it has to cover what was asked for.
    #
    # Composed from the two adapter reads on the INJECTED db rather than
    # through features.parking.service.get_events, which resolves its
    # own tenant handle from the global singleton.  The dispatcher hands
    # this tool a db precisely so it uses that one; reaching past it is
    # how a tool ends up reading a connection nobody scoped.
    window = max(int(min_days) + 1, 30)
    active = await db.get_active_parking_events(account_id, attention_only=False)
    active_ids = {ev.get("id") for ev in active}
    history = await db.get_parking_history(account_id, days=window, limit=500)
    events = list(active) + list(history)
    for ev in events:
        ev["_still_parked"] = ev.get("id") in active_ids
    if not include_safe:
        from features.parking.service import needs_attention
        events = [ev for ev in events if needs_attention(ev)]

    # Vehicle-Access scope, by the strongest rung the rows carry.
    #
    # This compared lowercased vehicle NAMES and dropped the injected
    # `_scope_identities` on the floor, so a same-numbered truck in
    # another company walked in and the caller's OWN truck dropped out
    # the moment the provider renamed it. parking_events rows carry
    # `vehicle_id` — the provider id, and the column the ladder reads
    # by default — so rung 2 tells the twins apart.
    #
    # filter_to_scope rather than a per-row test inside the loop below:
    # it builds the caller's scope once for the list.
    events = filter_to_scope(events, tool_args, key="vehicle_name")

    min_hours = min_days * 24.0
    filtered: list[dict] = []
    for ev in events:
        dur = float(ev.get("duration_hours") or 0)
        if dur < min_hours:
            continue
        if company and (ev.get("company_code") or "").upper() != company.upper():
            continue
        filtered.append(ev)

    return {
        "min_days": min_days,
        "include_safe": include_safe,
        "company_filter": company or None,
        # The window actually searched, so the model can say "in the last
        # N days" instead of implying it looked at all of history.
        "window_days": window,
        "count": len(filtered),
        "still_parked_now": sum(
            1 for ev in filtered if ev.get("_still_parked")
        ),
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
                # A stop that has ended is still the answer to "what
                # truck sat for ten days" — say which ones are over.
                "still_parked": bool(ev.get("_still_parked")),
            }
            for ev in filtered[:25]
        ],
    }
