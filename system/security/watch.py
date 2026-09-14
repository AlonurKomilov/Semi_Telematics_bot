"""The detector, running when nobody is looking — and waking somebody.

The detector has been advisory since it was written: it ranks subjects
and a person clicks.  That stays exactly as it is.  What this module
adds is the one thing the 2026-09-08 run showed was missing: the
detector only ever ran when somebody had already opened the page, so a
forty-seven-minute probe went unseen for two days — not for want of
signal, for want of anyone being told.

So: every night, run the detector over the last day, and if something
serious is in it, send the operators a Telegram message naming it.  A
person opens the page in the morning and decides.

What this module deliberately does NOT do is promote anyone.  Marking a
subject ``monitored`` starts recording their requests, and for a paying
customer that is a privacy decision.  A machine making it unattended
is defensible only at a scale where no human can watch the page; at
four real customers the honest fix for "nobody was looking" is to tell
the person whose job it is to look.  The limits an unattended promoter
would need — two rules agreeing, a hard cap per run, a severity floor —
are the limits this module applies to what it is WILLING TO WAKE
SOMEONE FOR, and no more.

**Operators only, for now.**  The account owner is a different audience
with a different message (``owner_notice``): they are told when a
person is put under closer watch by an operator's hand, never by the
detector's, because a machine's suspicion repeated to a customer as
"we noticed something" is an accusation nobody has yet made.
"""

from __future__ import annotations

import logging
from typing import Any

logger = logging.getLogger(__name__)

#: The detector's own ranking, not a second opinion.  ``med`` and
#: ``low`` stay on the page for a person who is already looking.
WAKE_SEVERITY = "high"

#: One rule firing is one argument.  Two unrelated rules landing on the
#: same subject is a case, and a case is worth somebody's morning.
WAKE_MIN_RULES = 2

#: A night with more subjects than this in it is not a busy night; it
#: is a rule that has started describing everybody.  The message says
#: so instead of listing them, because a list of forty is a list nobody
#: reads and the number IS the finding.
TOO_MANY = 8

#: The window.  One day, because the job runs daily and a subject that
#: was quiet all day is not news.  (The console's own view is a week —
#: a patient probe reads as one story only at that range — and that is
#: where the operator goes after being woken.)
WINDOW_HOURS = 24

_CONSOLE_HINT = "→ system.4truck.us/security"


def worth_waking_for(candidate: dict) -> bool:
    """Whether one candidate clears the bar for a night message.

    Strict on purpose.  Everything this refuses is still on the page;
    the only cost of refusing is that a person finds it in the morning
    rather than being woken for it.
    """
    if candidate.get("severity") != WAKE_SEVERITY:
        return False
    if len(candidate.get("rules") or []) < WAKE_MIN_RULES:
        return False
    # Already watched, or held: an operator has ALREADY decided about
    # this subject.  Every request from it is being recorded; waking
    # them again to say so is noise, and noise is how a real alert
    # gets muted.
    if (candidate.get("security") or "normal") != "normal":
        return False
    return True


def _who(c: dict) -> str:
    """One line naming the subject the way an operator would."""
    name = c.get("name") or c.get("subject") or "?"
    if c.get("account_id"):
        kind = c.get("kind") or "?"
        people = c.get("people") or []
        tail = f" · {len(people)} people" if len(people) > 1 else ""
        return f"{name} ({kind}{tail})"
    # No account — a refusal storm from an IP with no valid login.
    return f"{name} (no account)"


def compose(candidates: list[dict], *, failed: bool = False,
            failed_rules: tuple[str, ...] = (), total_rules: int = 0) -> str | None:
    """The message, or None when there is nothing worth sending.

    Plain Telegram HTML.  Short, because a bubble holds thirty-odd
    characters a line and the reader is on a phone at 7am: who, which
    rules, and where to look.  Never the evidence — a payload string in
    a Telegram message is a payload string in a Telegram message.

    ``failed`` is the detector raising or every rule failing: the night
    was not watched.  ``failed_rules`` short of ``total_rules`` is the
    realistic case — one rule's query breaks after a schema change and
    the other eight keep running — and it is said every night it stays
    true.  That nags.  A nag is the point: a rule silently dead for
    months is exactly what this module exists to prevent.
    """
    if failed or (failed_rules and total_rules and len(failed_rules) >= total_rules):
        # The empty result means broken, not clean — the detector's own
        # rule, and the reason this path exists at all.  A night the
        # detector could not run is a night nobody was watching, and an
        # operator has to know that at least as much as any finding.
        return ("<b>🛡 Security watch could not run</b>\n\n"
                "The detector pass failed overnight, so nothing was\n"
                "checked. The empty result means broken, not clean.\n"
                f"{_CONSOLE_HINT}")

    hits = [c for c in candidates if worth_waking_for(c)]
    partial = (f"{len(failed_rules)} of {total_rules} rules did not run: "
               f"{_esc(', '.join(failed_rules))}") if failed_rules else ""
    if not hits:
        if not partial:
            return None
        return (f"<b>🛡 Security watch: incomplete</b>\n\n"
                f"{partial}.\nNothing found by the rest — but the rest\n"
                f"is not all of it.\n{_CONSOLE_HINT}")

    if len(hits) > TOO_MANY:
        return (f"<b>🛡 Security watch: {len(hits)} subjects overnight</b>\n\n"
                f"That many in one night is a rule describing\n"
                f"everybody, not {len(hits)} attackers.\n"
                f"Nothing was changed. Look before trusting\n"
                f"any of them.\n"
                f"{_CONSOLE_HINT}")

    lines = [f"<b>🛡 Security watch: {len(hits)} to look at</b>", ""]
    for c in hits:
        rules = ", ".join(c.get("rules") or [])
        lines.append(f"• <b>{_esc(_who(c))}</b>")
        lines.append(f"   {_esc(rules)}")
    lines += ["", "Nothing was changed. Recording starts only",
              "when you mark someone."]
    if partial:
        lines += ["", f"⚠ {partial}.", "This list is incomplete."]
    lines.append(_CONSOLE_HINT)
    return "\n".join(lines)


def _esc(s: Any) -> str:
    return (str(s).replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;"))


async def _send_to_operators(app, text: str) -> int:
    """Telegram, to whoever holds SYSTEM_OWNER_IDS.  Returns how many
    were reached; zero is logged, never raised — the candidates are
    still on the page, which is the thing an operator can still find."""
    from capabilities.permissions import roles
    ids = getattr(roles, "SYSTEM_OWNER_IDS", set()) or set()
    bot_app = app
    if bot_app is None or getattr(bot_app, "bot", None) is None:
        try:
            from infra.bot_registry import get_system_app
            bot_app = get_system_app()
        except Exception:
            bot_app = None
    bot = getattr(bot_app, "bot", None)
    if bot is None or not ids:
        logger.warning("security watch had no operator to tell: %s", text)
        return 0
    sent = 0
    for tg_id in sorted(ids):
        try:
            await bot.send_message(chat_id=tg_id, text=text, parse_mode="HTML",
                                   disable_web_page_preview=True)
            sent += 1
        except Exception as e:
            logger.warning("security watch to operator %s failed: %s", tg_id, e)
    return sent


async def run(db, app=None) -> dict:
    """One night's pass.  Returns what happened, for tests and the log."""
    result: dict = {"checked": 0, "woke_for": 0, "sent_to": 0, "message": None}
    failed = False
    candidates: list[dict] = []
    failed_rules: tuple[str, ...] = ()
    total_rules = 0
    try:
        from system.security.detector import run_detector
        run_ = await run_detector(db, hours=WINDOW_HOURS)
        candidates = run_.candidates
        failed_rules, total_rules = run_.failed_rules, run_.total_rules
        # Every rule failing does not raise — the detector swallows each
        # one so a broken rule cannot blind the rest — so ``compose``
        # reads it from the RESULT (failed_rules == total_rules).  The
        # first version of this job only knew a failure that raised, and
        # the one failure mode the "could not run" message exists for
        # was the one it could never see.  ``failed`` here is only the
        # raise; the result is the other half, and compose owns it.
    except Exception:
        logger.exception("security watch: the detector pass failed")
        failed = True
    result["checked"] = len(candidates)
    result["failed_rules"] = list(failed_rules)

    text = compose(candidates, failed=failed,
                   failed_rules=failed_rules, total_rules=total_rules)
    if text is None:
        logger.info("security watch: %d candidates, none worth waking for",
                    len(candidates))
        return result
    result["woke_for"] = sum(1 for c in candidates if worth_waking_for(c))
    result["message"] = text
    result["sent_to"] = await _send_to_operators(app, text)
    logger.info("security watch: told %d operator(s) about %d subject(s)",
                result["sent_to"], result["woke_for"])
    return result


async def job_security_watch(app=None) -> None:
    """Scheduler entry — nightly."""
    import infra.platform as platform
    db = getattr(platform, "_db", None)
    if db is None:
        return
    try:
        await run(db, app)
    except Exception:
        logger.exception("security watch pass failed")
