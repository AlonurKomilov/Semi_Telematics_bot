"""Safety event tools: truck events and fleet event summary."""

from __future__ import annotations

from capabilities.ai.tools.registry import register_tool


@register_tool({
    "name": "get_truck_events",
    "description": (
        "Get safety events (harsh braking, harsh acceleration, crash, "
        "speeding, rolling stop, distracted driving, etc.) for a specific "
        "truck over a given number of days. Always state the time period "
        "you checked in your answer."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "truck_name": {
                "type": "string",
                "description": "The truck name or number",
            },
            "days": {
                "type": "integer",
                "description": "Number of days to look back (1-30, default 7)",
            },
        },
        "required": ["truck_name"],
    },
})
async def get_truck_events(tool_args: dict, samsara_client,
                           account_id: int | None = None, db=None) -> dict:
    truck = tool_args.get("truck_name", "")
    days = min(max(tool_args.get("days", 7), 1), 30)
    events = await samsara_client.get_events(days=days)
    truck_events = [
        e for e in events
        if e.get("vehicle_name", "").lower() == truck.lower()
    ]
    return {
        "truck": truck,
        "period_days": days,
        "total_events": len(truck_events),
        "events": [
            {
                "type": e.get("event_name", "Unknown"),
                "driver": e.get("driver_name", "Unassigned"),
                "time": e.get("time", ""),
                "g_force": e.get("g_force", 0),
                "coaching_state": e.get("coaching_state", ""),
            }
            for e in truck_events[:20]
        ],
    }


@register_tool({
    "name": "get_events_summary",
    "description": (
        "Get an account-wide safety event summary: total counts by event type "
        "(harsh brake, crash, speeding, etc.), top drivers by event count, "
        "and the 10 most severe events by g-force. Always state the time "
        "period you checked in your answer."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "days": {
                "type": "integer",
                "description": "Number of days to look back (1-30, default 7)",
            },
        },
        "required": [],
    },
})
async def get_events_summary(tool_args: dict, samsara_client,
                                   account_id: int | None = None, db=None) -> dict:
    days = min(max(tool_args.get("days", 7), 1), 30)
    events = await samsara_client.get_events(days=days)
    # Counts by type
    by_type: dict[str, int] = {}
    by_driver: dict[str, int] = {}
    for e in events:
        etype = e.get("event_name", "Unknown")
        by_type[etype] = by_type.get(etype, 0) + 1
        dname = e.get("driver_name", "Unassigned")
        by_driver[dname] = by_driver.get(dname, 0) + 1
    # Top drivers sorted by count
    top_drivers = sorted(by_driver.items(), key=lambda x: x[1], reverse=True)[:10]
    # Most severe by g-force
    severe = sorted(events, key=lambda e: e.get("g_force", 0), reverse=True)[:10]
    return {
        "period_days": days,
        "total_events": len(events),
        "events_by_type": by_type,
        "top_drivers_by_events": [
            {"driver": d, "count": c} for d, c in top_drivers
        ],
        "most_severe": [
            {
                "type": e.get("event_name", "Unknown"),
                "truck": e.get("vehicle_name", "?"),
                "driver": e.get("driver_name", "Unassigned"),
                "g_force": e.get("g_force", 0),
                "time": e.get("time", ""),
            }
            for e in severe
        ],
    }
