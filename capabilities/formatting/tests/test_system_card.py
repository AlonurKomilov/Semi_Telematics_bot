"""The boot card, which exists so the owner notices a number MOVED.

Every claim here is one the owner made about the card in its earlier
shapes: that it looked bad, that a count changed twice without
explanation, and that the numbers were hard to read. A boot message is
also the thing most likely to rot silently, because nobody re-reads what
they have learned to skim.
"""
import re

import pytest

from capabilities.formatting.system_card import format_system_card


def _census(real=4, test=44, users_real=23, users_test=52,
            normal=10, monitored=38, quarantined=0):
    return {
        "accounts": {"total": real + test, "real": real, "test": test},
        "users": {"total": users_real + users_test,
                  "real": users_real, "test": users_test},
        "security": {"normal": normal, "monitored": monitored,
                     "quarantined": quarantined},
    }


def test_the_card_is_never_a_code_block():
    """<pre> makes Telegram render a CODE block, with a "copy" bar above
    it — a status card arrived looking like a source listing, and the
    grey box dominated the message. The alignment it bought did not do
    its job either: two boot cards are never adjacent in a chat, so
    lining their columns up helps nobody compare them."""
    msg = format_system_card(_census())
    assert "<pre>" not in msg
    assert "```" not in msg


def test_a_change_since_the_last_card_is_shown_on_the_number_itself():
    """The delta is what re-reading yesterday's card could not do."""
    was = _census(real=5, test=39, users_real=24, users_test=48, monitored=34)
    msg = format_system_card(_census(real=4, test=44), was)
    assert "(−1)" in msg, "a lost customer must be visible"
    assert "(+5)" in msg
    # ...and a minus SIGN, not a hyphen: beside a digit a hyphen reads as
    # punctuation in the sentence rather than as the number's sign.
    assert "(-1)" not in msg


def test_a_first_boot_invents_no_deltas():
    msg = format_system_card(_census())
    assert "(+" not in msg and "(−" not in msg


def test_an_unchanged_number_carries_no_delta():
    """A card full of (+0) is a card nobody reads."""
    msg = format_system_card(_census(), _census())
    assert "(+0)" not in msg and "(−0)" not in msg
    # Nothing moved, so the body carries no parenthesis at all — an
    # earlier version of this assertion ended in `or True` and proved
    # nothing.
    body = msg.split("Operator console")[0]
    assert "(" not in body, body


def test_the_security_state_is_not_a_third_census_row():
    """"38 monitored" printed under "75 users" reads as users. These are
    accounts, and a state is a different statement from a count."""
    msg = format_system_card(_census())
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


# ── the ordering trap ────────────────────────────────────────────────

class _FakeSettings:
    """Just the two settings methods the card uses."""

    def __init__(self, stored=""):
        self.stored = stored

    async def get_platform_setting(self, key, default=""):
        return self.stored or default

    async def set_platform_setting(self, key, value):
        self.stored = value


@pytest.mark.asyncio
async def test_the_previous_census_survives_a_round_trip():
    from capabilities.formatting.system_card import read_last_census, store_census
    db = _FakeSettings()
    assert await read_last_census(db) is None, "a first boot has nothing to compare with"
    await store_census(db, _census(real=5))
    assert (await read_last_census(db))["accounts"]["real"] == 5


@pytest.mark.asyncio
async def test_reading_must_happen_before_storing():
    """Store first and every card compares itself with itself, so no
    delta ever appears and the failure is silent."""
    from capabilities.formatting.system_card import read_last_census, store_census
    db = _FakeSettings()
    await store_census(db, _census(real=5))

    now = _census(real=4)
    right = format_system_card(now, await read_last_census(db))
    await store_census(db, now)
    wrong = format_system_card(now, await read_last_census(db))

    assert "(−1)" in right
    assert "(−1)" not in wrong, "the wrong order hides the very change the card exists for"


@pytest.mark.asyncio
async def test_a_broken_settings_store_costs_the_delta_and_nothing_else():
    """A boot must not fail because a nicety could not be read."""
    from capabilities.formatting.system_card import read_last_census, store_census

    class _Broken:
        async def get_platform_setting(self, *a, **k):
            raise RuntimeError("db down")

        async def set_platform_setting(self, *a, **k):
            raise RuntimeError("db down")

    db = _Broken()
    assert await read_last_census(db) is None
    await store_census(db, _census())          # must not raise
    assert format_system_card(_census(), None)  # the card still renders


@pytest.mark.asyncio
async def test_a_corrupt_stored_census_is_ignored_not_crashed_on():
    from capabilities.formatting.system_card import read_last_census
    db = _FakeSettings(stored="{not json")
    assert await read_last_census(db) is None
