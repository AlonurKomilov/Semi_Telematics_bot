"""The boot card, which exists so the owner notices a number MOVED.

Every claim here is one the owner made about the card in its earlier
shapes: that it looked bad, that a count changed twice without
explanation, and that the numbers were hard to read. A boot message is
also the thing most likely to rot silently, because nobody re-reads what
they have learned to skim.
"""
import re

from capabilities.formatting.system_card import (
    PRE_WIDTH_BUDGET,
    format_system_card,
)


def _census(real=4, test=44, users_real=23, users_test=52,
            normal=10, monitored=38, quarantined=0):
    return {
        "accounts": {"total": real + test, "real": real, "test": test},
        "users": {"total": users_real + users_test,
                  "real": users_real, "test": users_test},
        "security": {"normal": normal, "monitored": monitored,
                     "quarantined": quarantined},
    }


def _pre_rows(msg):
    return re.search(r"<pre>([\s\S]*?)</pre>", msg).group(1).split("\n")


def test_the_census_is_a_column_that_can_be_compared():
    """<pre> is the only thing Telegram renders fixed-width. Outside it
    the figures sit at three different x positions and the card stops
    being scannable, which is its whole job."""
    msg = format_system_card(_census())
    rows = _pre_rows(msg)
    assert len(rows) == 2
    # The figures land on the same columns in every row.
    cols = [[m.start() for m in re.finditer(r"\d+", r)] for r in rows]
    ends = [[m.end() for m in re.finditer(r"\d+", r)] for r in rows]
    assert ends[0] == ends[1], f"figures do not align: {rows}"
    assert cols[0] != []


def test_no_pre_row_runs_off_the_edge_of_a_phone():
    """<pre> SCROLLS sideways rather than wrapping, so an over-wide row
    hides its own tail — invisibly, which is the dangerous part."""
    for c in (_census(), _census(real=9999, test=9999,
                                 users_real=99999, users_test=99999)):
        for row in _pre_rows(format_system_card(c)):
            assert len(row) <= PRE_WIDTH_BUDGET, f"{len(row)} chars: {row!r}"


def test_the_security_state_is_not_a_third_census_row():
    """"38 monitored" printed under "75 users" reads as users. These are
    accounts, and a state is a different statement from a count."""
    msg = format_system_card(_census())
    assert len(_pre_rows(msg)) == 2, "the security state got into the census block"
    assert "accounts watched" in msg, "the security line must name its own noun"
    assert "of 48" in msg, "a bare count says nothing about whether that is most of them"


def test_quarantined_is_silent_at_zero_and_shouts_otherwise():
    """Printed as 0 on every boot it becomes something the eye skips,
    and the day it turns into 1 nothing about the line changes."""
    quiet = format_system_card(_census(quarantined=0))
    assert "quarantined" not in quiet

    loud = format_system_card(_census(quarantined=2))
    assert "<b>2 quarantined</b>" in loud
    assert "⛔" in loud, "the line must change shape, not just its digits"


def test_nothing_flagged_says_so_rather_than_showing_zeroes():
    msg = format_system_card(_census(monitored=0, quarantined=0, normal=48))
    assert "nothing flagged" in msg
    assert "0 of" not in msg


def test_normal_is_never_printed():
    """It is the total minus the other two, it is the resting state, and
    with it the security line measured 49 characters."""
    msg = format_system_card(_census(normal=10))
    assert "normal" not in msg
    assert "10 normal" not in msg


def test_the_card_never_says_safe():
    """Nobody examined the unflagged accounts, so the card must not imply
    they were cleared — and `safe` is one letter from the Safety role."""
    for c in (_census(), _census(monitored=0), _census(quarantined=3)):
        assert "safe" not in format_system_card(c).lower()


def test_the_console_host_is_not_hardcoded_twice():
    msg = format_system_card(_census(), console_host="ops.example.com")
    assert "<code>ops.example.com</code>" in msg
    assert "system.4truck.us" not in msg
