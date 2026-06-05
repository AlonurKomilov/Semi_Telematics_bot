"""Vehicle history tool — reads ``vehicle_state_snapshot`` (5-min cadence).

The point-in-time tools (``get_vehicle_detail``, ``get_vehicle_health``)
only show what's true right now.  This tool surfaces the warehouse-
collected history so the agent can answer trend / utilization / "when
did X last move" / idle-streak questions that need a time series.

Caps row count tightly so a 30-day query on a busy vehicle doesn't
blow the agent's context window — for trend questions the model can
re-call with a narrower ``days`` window.
"""

from __future__ import annotations

from capabilities.ai.tools.registry import register_tool


@register_tool({
    "name": "get_vehicle_history",
    "description": (
        "Get historical state snapshots for ONE vehicle over a time "
        "window.  Each row is a 5-minute snapshot of position, speed, "
        "engine state (on/off/idle), fuel %, DEF %, odometer, engine "
        "hours, fault count, driver, battery, oil, coolant, load, RPM.  "
        "Use for questions like 'when did Truck 231 last move', 'show "
        "fuel-level trend for Truck 102 this week', 'how many hours "
        "did Truck 5 drive in the last 3 days', 'what was the longest "
        "idle stretch'.  Returns newest-first; cap at 200 rows so "
        "use a narrow window for finer detail."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "vehicle_name": {
                "type": "string",
                "description": (
                    "Vehicle name or number (e.g. '231').  Required — "
                    "fleet-wide history isn't supported here because "
                    "the row count explodes; for fleet questions use "
                    "the per-vehicle tools (get_account_stats, "
                    "get_idle_vehicles, get_efficiency_summary)."
                ),
            },
            "days": {
                "type": "number",
                "description": (
                    "Lookback window in days (default 7, max 30).  "
                    "Use 1 for 'today', 7 for 'this week'."
                ),
            },
            "max_rows": {
                "type": "number",
                "description": (
                    "Max snapshots to return (default 200, max 500).  "
                    "Newest-first.  Reduce for summarization questions "
                    "where the agent only needs a sample."
                ),
            },
        },
        "required": ["vehicle_name"],
    },
})
async def get_vehicle_history(tool_args: dict, samsara_client,
                              account_id: int | None = None, db=None) -> dict:
    if not db or account_id is None:
        return {"error": "Vehicle history not available in this context"}

    vehicle = (tool_args.get("vehicle_name") or "").strip()
    if not vehicle:
        return {"error": "vehicle_name is required"}

    days = min(int(tool_args.get("days") or 7), 30)
    max_rows = min(int(tool_args.get("max_rows") or 200), 500)

    if not hasattr(db, "query_vehicle_state_history"):
        return {
            "error": (
                "Vehicle history requires the warehouse snapshot table — "
                "this account hasn't been migrated to it yet, or the "
                "snapshot ingestor hasn't run."
            ),
        }

    rows = await db.query_vehicle_state_history(
        account_id,
        vehicle_name=vehicle,
        days=days,
        max_rows=max_rows,
    )

    if not rows:
        return {
            "vehicle": vehicle, "days": days, "count": 0,
            "snapshots": [],
            "note": (
                "No snapshots found in the window.  The vehicle may not "
                "exist on this account, or the snapshot job hasn't run "
                "for this fleet yet."
            ),
        }

    # Derived summary so the agent doesn't have to roll-up itself for
    # the common "did this truck move at all" question.
    odo_vals = [
        r["odometer_mi"] for r in rows
        if r.get("odometer_mi") not in (None, 0)
    ]
    moving = [r for r in rows if (r.get("speed_mph") or 0) > 1]
    last_moved_at = moving[0]["captured_at"] if moving else None

    return {
        "vehicle": vehicle,
        "days": days,
        "count": len(rows),
        "summary": {
            "first_snapshot": rows[-1].get("captured_at"),
            "last_snapshot": rows[0].get("captured_at"),
            "miles_in_window": (
                round(max(odo_vals) - min(odo_vals), 1) if len(odo_vals) >= 2
                else None
            ),
            "moving_snapshots": len(moving),
            "last_moved_at": last_moved_at,
        },
        "snapshots": [
            {
                "captured_at": r.get("captured_at"),
                "lat": r.get("lat"),
                "lon": r.get("lon"),
                "speed_mph": r.get("speed_mph"),
                "engine_state": r.get("engine_state"),
                "fuel_pct": r.get("fuel_pct"),
                "odometer_mi": r.get("odometer_mi"),
                "engine_hours": r.get("engine_hours"),
                "fault_count": r.get("fault_count"),
                "driver_id": r.get("last_driver_id"),
            }
            for r in rows
        ],
    }
