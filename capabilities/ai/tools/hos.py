"""Driver HOS / duty-status tool — wraps the driver_hos_status cache.

Answers "who's out of hours", "how many hours does X have left",
"which drivers are on-duty right now" from the locally-cached HOS
table the sync job refreshes from Samsara.  No live API call.
"""

from __future__ import annotations

from capabilities.ai.tools.registry import register_tool


def _fmt_seconds(secs: int | None) -> str:
    """Format ``11h 30m`` style — readable in chat, parseable for the AI."""
    if secs is None or secs < 0:
        return "unknown"
    h, rem = divmod(int(secs), 3600)
    m = rem // 60
    if h and m:
        return f"{h}h {m}m"
    if h:
        return f"{h}h"
    return f"{m}m"


@register_tool({
    "name": "get_driver_hos_status",
    "description": (
        "Get hours-of-service status for one driver (by name) or for "
        "every driver on the account.  Returns duty status (driving / "
        "on_duty / off_duty / sleeper), drive hours used today, on-duty "
        "hours used today, cycle hours remaining (typically 70-hour "
        "cycle), shift hours remaining, when the status last changed, "
        "and the assigned truck.  Use for questions like 'how many "
        "hours does John have left?', 'who's out of hours?', 'is the "
        "truck 102 driver still on shift?'."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "driver_name": {
                "type": "string",
                "description": (
                    "Optional case-insensitive substring match against "
                    "driver display name.  Omit for the full account roster."
                ),
            },
            "status_filter": {
                "type": "string",
                "description": (
                    "Optional: 'driving', 'on_duty', 'off_duty', "
                    "'sleeper' — restrict to drivers in that state."
                ),
            },
        },
        "required": [],
    },
})
async def get_driver_hos_status(tool_args: dict, samsara_client,
                                account_id: int | None = None, db=None) -> dict:
    if not db or account_id is None:
        return {"error": "HOS data not available in this context"}

    name_q = (tool_args.get("driver_name") or "").strip().lower()
    status_q = (tool_args.get("status_filter") or "").strip().lower()

    rows = await db.get_driver_hos_status(account_id)

    filtered: list[dict] = []
    for r in rows:
        if name_q and name_q not in (r.get("display_name") or "").lower():
            continue
        if status_q and (r.get("duty_status") or "").lower() != status_q:
            continue
        filtered.append(r)

    if not filtered:
        return {
            "count": 0,
            "drivers": [],
            "note": (
                "No HOS data found for the requested filter.  Either "
                "no drivers match, or the HOS sync job hasn't populated "
                "this account yet — manual ELD entries in Samsara only "
                "appear here after the next sync run."
            ),
        }

    return {
        "count": len(filtered),
        "name_filter": name_q or None,
        "status_filter": status_q or None,
        "drivers": [
            {
                "name": r.get("display_name") or "?",
                "truck": r.get("truck_num") or "",
                "duty_status": r.get("duty_status") or "unknown",
                "drive_today": _fmt_seconds(r.get("drive_seconds_today")),
                "on_duty_today": _fmt_seconds(r.get("on_duty_seconds_today")),
                "cycle_remaining": _fmt_seconds(r.get("cycle_seconds_remaining")),
                "shift_remaining": _fmt_seconds(r.get("shift_seconds_remaining")),
                "last_status_change": r.get("last_status_change") or "",
                "updated_at": r.get("updated_at") or "",
            }
            for r in filtered[:50]
        ],
    }
