"""The AI's hours-of-service answer.

Moved here from ``features/drivers/ai_tool.py`` — same tool id, so no
model, no stored feedback row and no permission grant has to learn a
new name — and repointed at the ELD feature's own store.  It lived
next to the driver roster because a driver is who the hours belong to;
it belongs here because an ELD is where they come from, and because
the answer is now assembled by a service that knows how old it is.

What the model may NOT do with this result is stated in the result
itself: the connected ELD is the system of record, and nothing here
supports computing a violation.
"""

from __future__ import annotations

from capabilities.ai.tools.registry import register_tool
from capabilities.ai.tools.scope import filter_to_scope
from features.eld.service import get_hours


def _fmt_seconds(secs: int | None) -> str:
    """``11h 30m`` — readable in chat, parseable for the model.

    ``unknown`` for a clock the provider did not report.  Deliberately
    NOT ``0h 0m``: that is what a driver out of hours reads, and the
    two must never render the same.
    """
    if secs is None or secs < 0:
        return "unknown"
    h, rem = divmod(int(secs), 3600)
    m = rem // 60
    if h and m:
        return f"{h}h {m}m"
    if h:
        return f"{h}h"
    return f"{m}m"


def _clock_text(clock: dict | None) -> str:
    return _fmt_seconds(None if clock is None else clock.get("seconds"))


@register_tool({
    "name": "get_driver_hos_status",
    "description": (
        "Get hours-of-service status for one driver (by name) or for "
        "every driver on the account.  Returns duty status (driving / "
        "on_duty / off_duty / sleeper / personal_conveyance / "
        "yard_move) and FOUR COUNTDOWNS — drive time remaining, shift "
        "remaining, cycle remaining, and time until the mandatory "
        "break is due — plus when the status last changed, the "
        "assigned truck, and HOW OLD each reading is.  Every clock is "
        "time LEFT, never time used: an ELD does not report time used, "
        "so never present these as hours already worked.  Use for "
        "'how many hours does John have left?', 'who's out of hours?', "
        "'who has to stop for a break soon?'.  Always state the "
        "reading's age when you answer."
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
                    "'sleeper', 'personal_conveyance', 'yard_move' — "
                    "restrict to drivers in that state."
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

    answer = await get_hours(db, account_id)

    if not answer["connected"]:
        # The distinction this whole feature was built around.  Nothing
        # has ever been ingested, which is NOT a statement that every
        # driver has hours remaining — and an empty list reads to a
        # model as exactly that, which on a compliance question is the
        # worst direction to be wrong in.
        return {
            "count": 0,
            "drivers": [],
            "hos_unavailable": True,
            "note": (
                "No electronic logging device is connected for this "
                "account, so no hours have been recorded. This is NOT a "
                "statement that every driver has hours remaining. Do "
                "not answer hours-of-service or compliance questions "
                "from this result — say the ELD is not connected and "
                "point the user at Integrations."
            ),
        }

    # The caller's own trucks, by the driver's assigned vehicle — the
    # same rung every other scope-aware tool uses.
    rows = filter_to_scope(answer["drivers"], tool_args, key="vehicle")

    filtered = [
        r for r in rows
        if (not name_q or name_q in (r.get("driver") or "").lower())
        and (not status_q or (r.get("duty_status") or "").lower() == status_q)
    ]

    if not filtered:
        return {
            "count": 0,
            "drivers": [],
            "note": (
                "No driver matches that filter. Hours-of-service data "
                "IS connected for this account, so this is a real "
                "'none match' — not a missing feed."
            ),
        }

    return {
        "count": len(filtered),
        "name_filter": name_q or None,
        "status_filter": status_q or None,
        # Said every time, because a model that forgets it will
        # cheerfully do arithmetic toward a violation.
        "system_of_record": (
            "The connected ELD is the system of record for hours of "
            "service. These are mirrored readings — report them with "
            "their age, and never compute or assert a violation."
        ),
        "stale_after_minutes": answer["stale_after_minutes"],
        "stale_count": answer["stale_count"],
        "drivers": [
            {
                "name": r.get("driver") or "?",
                "truck": r.get("vehicle") or "",
                "duty_status": r.get("duty_status") or "unknown",
                "drive_remaining": _clock_text(r.get("drive_remaining")),
                "shift_remaining": _clock_text(r.get("shift_remaining")),
                "cycle_remaining": _clock_text(r.get("cycle_remaining")),
                "break_due_in": _clock_text(r.get("break_in")),
                "last_status_change": r.get("since") or "",
                # The reading's own age, not our write time.  A model
                # told "2 hours left" with no age will say it as fact.
                "reading_age_minutes": r.get("age_minutes"),
                "stale": r.get("stale"),
                "source": r.get("source") or "",
            }
            for r in filtered[:50]
        ],
    }
