"""Safety event tools: truck events and fleet event summary."""

from __future__ import annotations

from capabilities.ai.tools.registry import register_tool
from features.vehicles.resolve import resolve_for_tool, company_for, row_company
from capabilities.ai.tools.scope import filter_to_scope
from features.events.service import get_events as _svc_events


@register_tool({
    "name": "get_vehicle_events",
    "description": (
        "Get safety events (harsh braking, harsh acceleration, crash, "
        "speeding, rolling stop, distracted driving, etc.) for a specific "
        "vehicle over a given number of days. Always state the time period "
        "you checked in your answer."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "vehicle_name": {
                "type": "string",
                "description": "The vehicle name or number",
            },
            "company": {
                "type": "string",
                "description": (
                    "Optional company code (e.g. 'OSY', 'G1') — needed only "
                    "when more than one truck shares the number."
                ),
            },
            "days": {
                "type": "integer",
                "description": "Number of days to look back (1-30, default 7)",
            },
        },
        "required": [],
    },
})
async def get_vehicle_events(tool_args: dict, samsara_client,
                             account_id: int | None = None, db=None) -> dict:
    vehicle = tool_args.get("vehicle_name", "")
    days = min(max(tool_args.get("days", 7), 1), 30)
    if not vehicle:
        return {"error": "Please specify a vehicle name to get its safety events."}
    if account_id is None:
        return {"error": "This tool requires account context."}
    resolved, err = await resolve_for_tool(db, account_id, tool_args)
    if err:
        return err
    co = company_for(resolved, tool_args)
    # Once the registry has named the truck, ask the store for its
    # window instead of the account's.  The narrowing is a superset —
    # rows with no provider id come back too, because the ladder below
    # decides those by unit name — so the membership test is unchanged;
    # only the volume is.  Unresolved (retired, unregistered, mistyped)
    # keeps the account-wide read the name path depends on.
    _ref = (getattr(resolved, "telematics_ref", "") or "").strip() or None
    events = await _svc_events(account_id, days=days, vehicle_id=_ref)
    if resolved is not None:
        # Identity, not label. The registry keeps a truck's unit number
        # across a provider rename (the upsert matches on the telematics
        # ref), so "229" must still claim the rows the provider now
        # calls "229 Idris Ahmed" — and exact name equality reported
        # zero safety events for a truck that had them, which reads as
        # a clean week. Rung 2 decides: these rows carry vehicle_id.
        from capabilities.permissions.vehicle_scope import (
            VehicleIdentity, VehicleScope,
        )
        target = VehicleScope.of(VehicleIdentity.make(
            registry_id=getattr(resolved, "id", None),
            external_id=(getattr(resolved, "telematics_ref", "") or None),
            name=getattr(resolved, "unit_number", None),
        ))
        vehicle_events = [
            e for e in events if target.allows_row(e, name_key="vehicle_name")
        ]
    else:
        # The registry could not say — a retired, unregistered or
        # mistyped truck. Keep the name-and-company path, which is what
        # the archived-vehicle contract depends on.
        vehicle_events = [
            e for e in events
            if e.get("vehicle_name", "").lower() == vehicle.lower()
            and (not co or row_company(e) == co)
        ]
    return {
        "vehicle": vehicle,
        "period_days": days,
        "total_events": len(vehicle_events),
        "events": [
            {
                "type": e.get("event_name", "Unknown"),
                "driver": e.get("driver_name", "Unassigned"),
                "time": e.get("time", ""),
                "g_force": e.get("g_force", 0),
                "coaching_state": e.get("coaching_state", ""),
            }
            for e in vehicle_events[:20]
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
    if account_id is None:
        return {"error": "This tool requires account context."}
    events = await _svc_events(account_id, days=days)
    # Scope to the caller's vehicles before aggregating.
    events = filter_to_scope(events, tool_args, key="vehicle_name")
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
                "vehicle": e.get("vehicle_name", "?"),
                "driver": e.get("driver_name", "Unassigned"),
                "g_force": e.get("g_force", 0),
                "time": e.get("time", ""),
            }
            for e in severe
        ],
    }
