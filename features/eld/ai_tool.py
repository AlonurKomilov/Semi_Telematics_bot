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
from capabilities.ai.tools.scope import scope_from_args
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



# The four clock ids, in the order a surface reads them, paired with the
# key each one wears in this tool's output.
_CLOCK_KEYS = {
    "drive": "drive_remaining",
    "shift": "shift_remaining",
    "cycle": "cycle_remaining",
    "break": "break_due_in",
}


def _coverage_note(answer: dict) -> str | None:
    """What to tell the model about clocks the ELD cannot report.

    Without this a model handed twenty-two drivers whose every clock
    reads ``unknown`` has two ways to be wrong and no way to be right.
    It can refuse and blame the data, or — much worse — reason that
    nobody shows as out of hours and answer "everyone has time left".
    That second failure is the one this feature exists to prevent, and
    it does not stop being the failure just because the silence comes
    from the device rather than from us.

    Judged PER PROVIDER, never on the account-wide union.  The union was
    the first version and it went quiet in exactly the case that needs
    it most: an account running a full-clock ELD alongside a duty-status
    -only one unions to all four clocks, so nothing was missing, so
    nothing was said — while half the drivers in the same answer showed
    ``unknown`` across the board with no explanation of which device
    they came from.

    A provider that is no longer registered reports ``None`` and gets no
    sentence. Unknown is not the same claim as "reports nothing", and
    only one of them may be printed.

    Returns ``None`` when every device reports every clock, which is the
    single-Samsara case and needs no extra words.
    """
    providers = answer.get("providers") or {}
    known = {
        pid: f for pid, f in providers.items()
        if f.get("clocks_reported") is not None
    }
    limited = {
        pid: f for pid, f in known.items()
        if set(f["clocks_reported"]) != set(_CLOCK_KEYS)
    }
    if not limited:
        return None

    def _names(pids) -> str:
        return " and ".join(known[p]["name"] for p in pids)

    def _lacks(pid) -> str:
        have = set(known[pid]["clocks_reported"])
        return ", ".join(
            _CLOCK_KEYS[c].replace("_", " ")
            for c in _CLOCK_KEYS if c not in have
        )

    blind = [p for p in limited if not limited[p]["clocks_reported"]]

    # Every device we can speak for reports no countdown at all.  The
    # dispatch question cannot be answered from this result and the
    # model is told so by name.
    if len(blind) == len(known) and blind:
        who = _names(blind)
        return (
            f"{who} reports duty status only — it does not publish "
            "remaining drive, shift, cycle or break time, so every "
            "countdown below is 'unknown'. That is a limitation of the "
            "device, NOT a statement that these drivers have hours "
            "remaining and NOT a statement that none are near a limit. "
            "Do not answer 'how many hours are left', 'who is out of "
            "hours' or 'who can take this load' from this result: say "
            f"{who} does not report remaining hours and that duty "
            "status and its age are what is available."
        )

    # Mixed: some drivers carry real countdowns and some carry none.
    # The danger here is a whole-fleet conclusion drawn from the half
    # that happens to have numbers, so the sentence names the split and
    # points at the per-driver field that identifies the device.
    parts = [
        f"{known[pid]['name']} does not publish {_lacks(pid)}"
        for pid in sorted(limited)
    ]
    return (
        "This account has more than one electronic logging device and "
        "they do not report the same clocks: "
        + "; ".join(parts)
        + ". Those read 'unknown' for that device's drivers — a "
        "limitation of the device, not a reading of zero. Each driver's "
        "'source' field names the device they came from. Never answer "
        "'who is out of hours' or 'who can take this load' for the "
        "whole account from this result: it can only be answered for "
        "the drivers whose device reports the clock you are using."
    )



@register_tool({
    "name": "get_driver_hos_status",
    "description": (
        "Get hours-of-service status for one driver (by name) or for "
        "every driver on the account.  Always returns duty status "
        "(driving / on_duty / off_duty / sleeper / "
        "personal_conveyance / yard_move), when it last changed, the "
        "assigned truck, and HOW OLD each reading is.  Where the "
        "connected ELD publishes them, it also returns four "
        "COUNTDOWNS — drive time remaining, shift remaining, cycle "
        "remaining, and time until the mandatory break is due.  Not "
        "every ELD publishes those; the result says which ones this "
        "account's device reports, and a clock it does not report "
        "comes back as 'unknown'.  Every clock is time LEFT, never "
        "time used: an ELD does not report time used, so never "
        "present these as hours already worked.  Use for 'how many "
        "hours does John have left?', 'who's out of hours?', 'who has "
        "to stop for a break soon?'.  Always state the reading's age "
        "when you answer."
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

    # Scope goes IN, so the service applies the one identity ladder both
    # surfaces share.  It used to filter the projected rows afterwards on
    # the truck NAME, which cannot split two companies' same-numbered
    # trucks — a scoped caller was shown the twin company's driver.
    answer = await get_hours(
        db, account_id, vehicle_scope=scope_from_args(tool_args))

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

    filtered = [
        r for r in answer["drivers"]
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

    coverage = _coverage_note(answer)

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
        # Which countdowns this account's ELD actually publishes, and
        # the sentence the model must use when some of them are absent.
        # Omitted entirely when all four are reported — a note that
        # says "nothing is missing" is noise the model has to read.
        **({"clock_coverage": coverage} if coverage else {}),
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
