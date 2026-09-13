"""The operator's health card — the message the system bot sends on boot.

Its one job is to let the owner notice that a number MOVED. Everything
here follows from that: the census sits in a <pre> block because that is
the only construct Telegram renders fixed-width, so the figures form a
column that can be compared against the card two bubbles up instead of
read line by line.

Lives here rather than inline in interfaces/bot/app.py so it can be
tested — the card has branches now, and a card is exactly the kind of
thing that rots silently because nobody looks at a message they have
already learned to skim.
"""
from __future__ import annotations

# A phone renders a Telegram bubble around 32–38 characters wide in a
# monospace face, and <pre> SCROLLS sideways rather than wrapping — so a
# row past this width hides its own tail. Ordinary text lines wrap
# instead, which is survivable; <pre> rows are not.
PRE_WIDTH_BUDGET = 38


def format_system_card(census: dict, console_host: str = "system.4truck.us") -> str:
    """Render the boot card from :meth:`account_census` output."""
    acc = census["accounts"]
    usr = census["users"]
    sec = census["security"]

    # Two census rows, one template, right-aligned figures. `people`
    # rather than `users` so the two labels differ at their first letter
    # — `accounts`/`users` share no prefix either, but the eye picks up
    # ascenders, and p/a is a cleaner pair than u/a at this size.
    rows = [
        f"accounts  {acc['real']:>4} real {acc['test']:>4} test",
        f"people    {usr['real']:>4} real {usr['test']:>4} test",
    ]

    return (
        "━━━━━━━━━━━━━━━━━━━━━\n"
        "  ⚙️  <b>Bot is Online</b>\n"
        "━━━━━━━━━━━━━━━━━━━━━\n"
        "\n"
        f"<pre>{chr(10).join(rows)}</pre>\n"
        f"{_security_line(sec, acc['total'])}\n"
        "\n"
        f"  Operator console: <code>{console_host}</code>"
    )


def _security_line(sec: dict, total_accounts: int) -> str:
    """The security state — deliberately NOT a third census row.

    "38 monitored" printed under "75 users" reads as users; these are
    accounts, and a state is a different kind of statement from a count.
    So it stands outside the block, names its own noun, and carries the
    total as an anchor — a bare "38" says nothing about whether that is
    most of them or a handful.

    `normal` is left out: it is the total minus the other two, it is the
    resting state, and including it pushed the line to 49 characters.

    `quarantined` appears only when it is not zero. Printed as "0" on
    every boot it becomes something the eye learns to skip, and the day
    it turns into 1 nothing about the line changes. Left out, the line
    CHANGES SHAPE the moment it matters, which is the only signal this
    canvas can give.
    """
    watched = sec.get("monitored", 0)
    held = sec.get("quarantined", 0)

    if not watched and not held:
        return "  🛡 nothing flagged"

    line = f"  🛡 {watched} of {total_accounts} accounts watched"
    if held:
        line += f"\n  ⛔ <b>{held} quarantined</b>"
    return line
