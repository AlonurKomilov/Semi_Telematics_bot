"""Vehicle efficiency AI tool — per-vehicle rolling-window efficiency.

Driver-centric efficiency tools (get_driver_efficiency, get_driver_scorecard)
live in ``features/drivers/ai_tool.py`` — they're driver metrics, not vehicle
metrics, so they belong with the other driver tools.
"""

from __future__ import annotations

from capabilities.ai.tools.registry import register_tool
from capabilities.ai.tools.scope import filter_to_scope
from features.vehicles.warehouse.service import get_fleet_efficiency as _svc_fleet_eff


# Row cap: a token budget, not a statement about the fleet. The
# extremes above it are computed over every scoped vehicle so the
# cap can never change the answer to a ranking question.
MAX_ROWS = 30


@register_tool({
    "name": "get_efficiency_summary",
    "description": (
        "Efficiency report across the user's accessible vehicles.  "
        "Per-vehicle rolling-window stats: engine hours, driving "
        "hours, miles driven, MPG, eco-driving score (also exposes "
        "idle hours / idle percentage as a secondary metric).  "
        "Role-agnostic — the underlying scope is the set of vehicles "
        "the caller has permission to see, so the same tool serves "
        "an owner asking about all trucks, a driver asking about "
        "their own assigned truck, or a dispatcher asking about "
        "their company's trucks.\n\n"
        "USE THIS for questions like:\n"
        "- 'efficiency over the last week'\n"
        "- 'how are my vehicles performing'\n"
        "- 'how was my driving this week' (driver scope)\n"
        "- 'MPG report'\n"
        "- 'eco-driving score by vehicle'\n\n"
        "DO NOT use this for 'which trucks are stopped/parked N days' "
        "or 'what vehicle hasn't moved' — those questions are about "
        "long-idle vehicles parked at unsafe locations, which "
        "``get_parked_vehicles`` answers directly.  If the user's "
        "previous turn asked about stopped/parked/idle vehicles and "
        "they then say 'N days', keep calling get_parked_vehicles with "
        "min_days=N — don't switch to this tool just because it "
        "accepts a ``days`` parameter."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "days": {
                "type": "integer",
                "description": "Number of days to look back (default 7)",
            },
        },
        "required": [],
    },
})
async def get_efficiency_summary(tool_args: dict, samsara_client,
                               account_id: int | None = None, db=None) -> dict:
    days = tool_args.get("days", 7)
    if account_id is None:
        return {"error": "This tool requires account context."}
    eff = filter_to_scope(
        await _svc_fleet_eff(account_id, days=days), tool_args, key="name",
        external_key="id",
    )

    def _row(v: dict) -> dict:
        return {
            "vehicle": v.get("name"),
            "company": v.get("_org") or "",
            "engine_hours": v.get("_engine_hours"),
            "driving_hours": v.get("_driving_hours"),
            "idle_hours": v.get("_idle_hours"),
            "idle_pct": v.get("_idle_pct"),
            "miles": v.get("_miles"),
            "driver": v.get("_driver_name"),
            "mpg": v.get("_mpg"),
            "green_pct": v.get("_green_pct"),
        }

    # Order by the metric, not by the roster.
    #
    # The source list arrives sorted by (company, name) and the cap took
    # the first 30 of it, so on a multi-company account "which truck had
    # the worst MPG this week" was answered from whichever companies sort
    # first alphabetically — and `vehicle_count` reported the full total
    # beside it, which reads as "I looked at all of them". The truck the
    # question is about was routinely not in the payload at all.
    rated = [v for v in eff if v.get("_mpg") is not None]
    rows = sorted(eff, key=lambda v: (v.get("_mpg") is None, v.get("_mpg") or 0.0))
    shown = rows[:MAX_ROWS]

    def _extreme(key: str, pick):
        pool = [v for v in eff if v.get(key) is not None]
        if not pool:
            return None
        v = pick(pool, key=lambda x: x.get(key))
        return {"vehicle": v.get("name"), "company": v.get("_org") or "",
                "value": v.get(key)}

    return {
        "period_days": days,
        # The scoped total, which is NOT how many rows are below.
        "vehicle_count": len(eff),
        "vehicles_returned": len(shown),
        "truncated": len(eff) > len(shown),
        "sorted_by": "mpg ascending (worst first); vehicles with no MPG last",
        # Computed over every scoped vehicle, so a ranking question is
        # answerable even when the list below is cut.
        "worst_mpg": _extreme("_mpg", min) if rated else None,
        "best_mpg": _extreme("_mpg", max) if rated else None,
        "highest_idle_pct": _extreme("_idle_pct", max),
        "lowest_green_pct": _extreme("_green_pct", min),
        "vehicles": [_row(v) for v in shown],
    }
