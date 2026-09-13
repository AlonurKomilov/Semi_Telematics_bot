"""The operator's health card — the message the system bot sends on boot.

Its one job is to let the owner notice that a number MOVED. The first
attempt at that put the census in a <pre> block for column alignment,
and two things were wrong with it. Telegram renders <pre> as a CODE
block, with a "copy" bar above it — so a status card arrived looking
like a source listing, and the grey box dominated the message. And the
alignment it bought did not do the job anyway: two boot cards are never
adjacent in a chat, so lining their columns up helps nobody compare them.

What does the job is the DELTA. `4 real (−1)` says in one glyph what
re-reading yesterday's card could not, and it says it about the only
number here that costs money. So the card carries plain emoji lines
again, and remembers the last census to subtract from.
"""
from __future__ import annotations

import json
import logging

logger = logging.getLogger(__name__)

# Where the previous census is kept between boots.
CENSUS_KEY = "system_card.last_census"


def _delta(now: int, was: int | None) -> str:
    """The change since the last card, or nothing to say.

    Rendered with a real minus sign (U+2212), not a hyphen: at this size
    a hyphen next to a digit reads as a dash in the sentence rather than
    as a sign on the number.
    """
    if was is None or now == was:
        return ""
    return f" (+{now - was})" if now > was else f" (−{was - now})"


def format_system_card(
    census: dict,
    previous: dict | None = None,
    console_host: str = "system.4truck.us",
) -> str:
    """Render the boot card from :meth:`account_census` output.

    ``previous`` is the census from the last boot; omit it on a first
    run and the card simply carries no deltas rather than inventing
    zeroes.
    """
    acc, usr, sec = census["accounts"], census["users"], census["security"]
    p_acc = (previous or {}).get("accounts", {})
    p_usr = (previous or {}).get("users", {})
    p_sec = (previous or {}).get("security", {})

    def d(section: dict, key: str, value: int) -> str:
        return _delta(value, section.get(key))

    lines = [
        "━━━━━━━━━━━━━━━━━━━━━",
        "  ⚙️  <b>Bot is Online</b>",
        "━━━━━━━━━━━━━━━━━━━━━",
        "",
        # The customer number is the one that costs money, so it is the
        # one in bold, and it leads.
        f"  🏢  <b>{acc['real']}</b> real{d(p_acc, 'real', acc['real'])}"
        f"  ·  {acc['test']} test{d(p_acc, 'test', acc['test'])}",
        f"  👥  <b>{usr['real']}</b> real{d(p_usr, 'real', usr['real'])}"
        f"  ·  {usr['test']} test{d(p_usr, 'test', usr['test'])}",
        _security_line(sec, p_sec),
        "",
        f"  Operator console: <code>{console_host}</code>",
    ]
    return "\n".join(lines)


def _security_line(sec: dict, prev: dict) -> str:
    """Accounts by security standing — the third row of the same table.

    It reads as a breakdown like the two above it, because it IS one: a
    population split by an attribute, exactly as accounts are split into
    real and test. An earlier version wrote it as a sentence ("38 of 48
    accounts watched"), which made one row in three invent its own shape
    — the very thing this card's audit said not to do.

    TWO values inline, not three, because that is what makes it parallel:
    the rows above show two each. All three in the project's own
    vocabulary measure 45 characters and wrap mid-phrase on a phone,
    and shortening `monitored`/`quarantined` to make them fit would put
    a second name on a value the column and the console already name.

    `quarantined` is the exception rather than a column: printed as 0 on
    every boot it becomes something the eye learns to skip, and the day
    it turns into 1 nothing about the line would change. On its own line
    it cannot be missed.
    """
    normal = sec.get("normal", 0)
    watched = sec.get("monitored", 0)
    held = sec.get("quarantined", 0)
    if not watched and not held:
        return "  🛡  nothing flagged"

    line = (f"  🛡  {normal} normal{_delta(normal, prev.get('normal'))}"
            f"  ·  {watched} monitored{_delta(watched, prev.get('monitored'))}")
    if held:
        line += f"\n  ⛔  <b>{held} quarantined</b>{_delta(held, prev.get('quarantined'))}"
    return line


async def read_last_census(db) -> dict | None:
    """The census stored by the previous boot, or None on the first."""
    try:
        raw = await db.get_platform_setting(CENSUS_KEY, "")
        return json.loads(raw) if raw else None
    except Exception:
        # A card with no deltas is a small loss; a boot that fails
        # because it could not read a nicety is not acceptable.
        logger.warning("system card: could not read the last census", exc_info=True)
        return None


async def store_census(db, census: dict) -> None:
    """Remember this census so the next boot can subtract from it."""
    try:
        await db.set_platform_setting(CENSUS_KEY, json.dumps(census))
    except Exception:
        logger.warning("system card: could not store the census", exc_info=True)
