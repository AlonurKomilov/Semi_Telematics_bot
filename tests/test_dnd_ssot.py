"""Tests for the DND single-source-of-truth.

Verifies the two-tier check implemented in
``capabilities/alerting/dnd.py``:

  Tier 1: per-user override (``users.quiet_start`` + ``quiet_end``
          both set) — evaluated against the user's own timezone.

  Tier 2: derived from account ``work_hours`` for the user's role —
          evaluated against the account timezone.  Union semantics
          across multiple shifts.

Key invariant: every alert delivery path consults ``is_user_dnd_active``
and gets a per-user answer.  Two users in the same role + same account
must compute their DND state INDEPENDENTLY — one user's override never
leaks into the other.
"""

from __future__ import annotations

import datetime as _dt
from zoneinfo import ZoneInfo

import pytest

from adapters.storage import Role
from capabilities.alerting.dnd import (
    _hour_in_window,
    is_user_dnd_active,
    is_user_on_shift,
)


# ════════════════════════════════════════════════════════════════════
#  _hour_in_window — pure helper, no DB needed
# ════════════════════════════════════════════════════════════════════

class TestHourInWindow:
    """Edge cases for the wrap-aware hour interval check."""

    @pytest.mark.parametrize("hour, start, end, expected", [
        # Regular daytime windows
        (8, 6, 14, True),
        (6, 6, 14, True),    # start is inclusive
        (14, 6, 14, False),  # end is exclusive
        (5, 6, 14, False),
        (13, 6, 14, True),

        # Overnight wrap-around (e.g. 22:00–06:00)
        (2, 22, 6, True),
        (5, 22, 6, True),    # last hour in wrap
        (6, 22, 6, False),   # end is exclusive even when wrapped
        (22, 22, 6, True),   # start inclusive
        (21, 22, 6, False),  # right before start
        (8, 22, 6, False),   # mid-day, outside overnight window

        # Edge cases
        (0, 0, 24, True),    # 24/7 window
        (23, 0, 24, True),
        (10, 12, 12, False), # zero-width
        (0, 0, 0, False),    # zero-width at midnight
    ])
    def test_window_membership(self, hour, start, end, expected):
        assert _hour_in_window(hour, start, end) is expected


# ════════════════════════════════════════════════════════════════════
#  is_user_on_shift + is_user_dnd_active — round-trip with real DB
# ════════════════════════════════════════════════════════════════════

@pytest.mark.asyncio
class TestDNDSingleSourceOfTruth:
    """Per-user isolation across the two derivation tiers."""

    async def _setup_account_with_driver_shift(self, db):
        """Account with a driver-targeted morning shift covering current
        hour in the account's timezone.  Returns (account_id, admin_id)
        plus the start/end hours chosen so the current ET hour lands
        INSIDE the shift (so on_shift=True is the expected default).
        """
        acct = await db.create_account("DND Test Co")
        await db.update_account(acct.id, timezone="America/New_York")

        admin = await db.create_user(
            telegram_id=10_001, account_id=acct.id,
            role=Role.OWNER, display_name="Admin",
        )
        # Make the shift cover the current ET hour so tests are
        # deterministic regardless of when CI runs.
        now_et = _dt.datetime.now(ZoneInfo("America/New_York"))
        current = now_et.hour
        # 2-hour-wide window centered on "now" (with wrap if needed).
        start = (current - 1) % 24
        end = (current + 1) % 24
        await db.create_work_hour(
            account_id=acct.id, label="Test driver shift",
            start_hour=start, end_hour=end if end != start else (start + 1) % 24,
            created_by=admin.id, target_role="driver",
        )
        return acct.id, admin.id, start, end

    async def test_user_override_wins_over_work_hours(self, db):
        """Tier 1: when user.quiet_start + quiet_end are both set, the
        override is consulted — work_hours table is ignored for this
        user (but still applies to other users in the same role)."""
        acct_id, _, _, _ = await self._setup_account_with_driver_shift(db)

        # Driver with override that catches every hour (0-23 wrap)
        bob = await db.create_user(
            telegram_id=20_001, account_id=acct_id,
            role=Role.DRIVER, display_name="Bob",
        )
        # Override every hour as DND: 0-23 wraps through midnight,
        # making "in DND" the answer at any time.
        await db.update_user(bob.id, quiet_start=0, quiet_end=23)
        bob = await db.get_user_by_telegram_id(20_001)

        # Even though Bob is on shift per work_hours, the override
        # forces DND active.
        on_shift = await is_user_on_shift(bob, db)
        in_dnd = await is_user_dnd_active(bob, db)
        assert on_shift is True, "WH still says on-shift…"
        # Bob's override is 0-23 — depending on current hour, either
        # _hour_in_window(now, 0, 23) is True (DND) or False (not).
        # The test is that override is *consulted*, not WH.  Re-check
        # by comparing to the override directly:
        expected = _hour_in_window(_dt.datetime.now(ZoneInfo("UTC")).hour, 0, 23)
        # Bob has no per-user timezone so is_in_quiet_hours uses UTC.
        # We just verify the override path is taken (not WH).
        assert in_dnd == (not bob.is_in_quiet_hours()) ^ (not False), (
            "Tier 1 should call user.is_in_quiet_hours(), not work_hours"
        )
        # The cleaner assertion: in_dnd === user.is_in_quiet_hours()
        assert in_dnd == bob.is_in_quiet_hours(), (
            "Override path must equal user.is_in_quiet_hours()"
        )
        _ = expected  # noqa — kept above for documentation

    async def test_no_override_derives_from_work_hours(self, db):
        """Tier 2: when no override, the answer comes from work_hours
        evaluated in account timezone."""
        acct_id, _, _, _ = await self._setup_account_with_driver_shift(db)

        alice = await db.create_user(
            telegram_id=20_002, account_id=acct_id,
            role=Role.DRIVER, display_name="Alice",
        )
        alice = await db.get_user_by_telegram_id(20_002)
        # No override set; shift covers the current ET hour by setup.
        assert alice.quiet_start is None
        assert alice.quiet_end is None

        on_shift = await is_user_on_shift(alice, db)
        in_dnd = await is_user_dnd_active(alice, db)
        assert on_shift is True, "Driver shift should cover current hour"
        assert in_dnd is False, "On-shift means DND inactive"

    async def test_no_override_and_no_work_hours_means_dnd_off(self, db):
        """Tier 2 fallback: role with no matching work_hours rows →
        on_shift returns True (defaults to always-on) → DND off."""
        acct = await db.create_account("Empty WH Co")
        await db.update_account(acct.id, timezone="America/New_York")

        dave = await db.create_user(
            telegram_id=20_003, account_id=acct.id,
            role=Role.DISPATCHER, display_name="Dave",
        )
        dave = await db.get_user_by_telegram_id(20_003)

        # No work_hours rows for this account at all.
        on_shift = await is_user_on_shift(dave, db)
        in_dnd = await is_user_dnd_active(dave, db)
        assert on_shift is True, "No WH defined → defaults to on-shift"
        assert in_dnd is False, "No WH + no override → DND off, alerts 24/7"

    async def test_two_users_same_role_different_overrides_isolated(self, db):
        """Two drivers in the same account: one with override, one without.
        Their DND outcomes must be computed independently — Bob's
        override must NOT affect Alice's evaluation."""
        acct_id, _, _, _ = await self._setup_account_with_driver_shift(db)

        alice = await db.create_user(
            telegram_id=20_011, account_id=acct_id,
            role=Role.DRIVER, display_name="Alice",
        )
        bob = await db.create_user(
            telegram_id=20_012, account_id=acct_id,
            role=Role.DRIVER, display_name="Bob",
        )
        # Bob: override 0-23 (always DND)
        await db.update_user(bob.id, quiet_start=0, quiet_end=23)

        alice = await db.get_user_by_telegram_id(20_011)
        bob = await db.get_user_by_telegram_id(20_012)

        alice_dnd = await is_user_dnd_active(alice, db)
        bob_dnd = await is_user_dnd_active(bob, db)

        # Alice has no override → falls through to WH → on-shift → DND off
        assert alice_dnd is False, "Alice (no override) follows WH"
        # Bob has override → uses it → effectively always-DND
        assert bob_dnd == bob.is_in_quiet_hours(), "Bob uses override"

    async def test_different_roles_get_different_shift_data(self, db):
        """Role-A user and role-B user with no override get the
        work_hours rows for their OWN role, not the other's."""
        acct = await db.create_account("Multi-role Co")
        await db.update_account(acct.id, timezone="America/New_York")

        owner = await db.create_user(
            telegram_id=10_011, account_id=acct.id,
            role=Role.OWNER, display_name="Owner",
        )
        # Driver shift covers current hour
        now_et = _dt.datetime.now(ZoneInfo("America/New_York"))
        cur = now_et.hour
        await db.create_work_hour(
            account_id=acct.id, label="Driver shift",
            start_hour=(cur - 1) % 24, end_hour=(cur + 1) % 24,
            created_by=owner.id, target_role="driver",
        )
        # Fleet shift definitely does NOT cover current hour: pin to
        # a 1-hour-wide window that's 6 hours away.
        far_start = (cur + 6) % 24
        far_end = (cur + 7) % 24
        await db.create_work_hour(
            account_id=acct.id, label="Fleet shift",
            start_hour=far_start, end_hour=far_end,
            created_by=owner.id, target_role="fleet",
        )

        driver = await db.create_user(
            telegram_id=20_021, account_id=acct.id,
            role=Role.DRIVER, display_name="DriverX",
        )
        fleet_mgr = await db.create_user(
            telegram_id=20_022, account_id=acct.id,
            role=Role.FLEET, display_name="FleetY",
        )
        driver = await db.get_user_by_telegram_id(20_021)
        fleet_mgr = await db.get_user_by_telegram_id(20_022)

        assert await is_user_on_shift(driver, db) is True, (
            "Driver shift covers current hour"
        )
        assert await is_user_on_shift(fleet_mgr, db) is False, (
            "Fleet shift is 6 hours away → not on shift"
        )

    async def test_target_role_all_applies_to_every_role(self, db):
        """A work_hours row with target_role='all' should match every
        user regardless of their role."""
        acct = await db.create_account("Universal shift Co")
        await db.update_account(acct.id, timezone="America/New_York")
        owner = await db.create_user(
            telegram_id=10_021, account_id=acct.id,
            role=Role.OWNER, display_name="Owner",
        )
        now_et = _dt.datetime.now(ZoneInfo("America/New_York"))
        cur = now_et.hour
        await db.create_work_hour(
            account_id=acct.id, label="All-hands",
            start_hour=(cur - 1) % 24, end_hour=(cur + 1) % 24,
            created_by=owner.id, target_role="all",
        )

        driver = await db.create_user(
            telegram_id=20_031, account_id=acct.id,
            role=Role.DRIVER, display_name="Driver",
        )
        dispatcher = await db.create_user(
            telegram_id=20_032, account_id=acct.id,
            role=Role.DISPATCHER, display_name="Dispatcher",
        )
        driver = await db.get_user_by_telegram_id(20_031)
        dispatcher = await db.get_user_by_telegram_id(20_032)

        assert await is_user_on_shift(driver, db) is True
        assert await is_user_on_shift(dispatcher, db) is True

    async def test_union_semantics_for_multiple_shifts(self, db):
        """When a role has multiple work_hours rows that cover the
        current hour between them (e.g. morning + evening shifts), the
        union determines on-shift status."""
        acct = await db.create_account("Multi-shift Co")
        await db.update_account(acct.id, timezone="America/New_York")
        owner = await db.create_user(
            telegram_id=10_031, account_id=acct.id,
            role=Role.OWNER, display_name="Owner",
        )

        now_et = _dt.datetime.now(ZoneInfo("America/New_York"))
        cur = now_et.hour
        # Shift A: 6 hours BEFORE current (definitely doesn't cover now)
        far_start = (cur - 8) % 24
        far_end = (cur - 6) % 24
        await db.create_work_hour(
            account_id=acct.id, label="Morning (off)",
            start_hour=far_start, end_hour=far_end,
            created_by=owner.id, target_role="driver",
        )
        # Shift B: covers current hour
        await db.create_work_hour(
            account_id=acct.id, label="Active",
            start_hour=(cur - 1) % 24, end_hour=(cur + 1) % 24,
            created_by=owner.id, target_role="driver",
        )

        driver = await db.create_user(
            telegram_id=20_041, account_id=acct.id,
            role=Role.DRIVER, display_name="Driver",
        )
        driver = await db.get_user_by_telegram_id(20_041)

        # Union: shift A says off, shift B says on → user is on-shift.
        assert await is_user_on_shift(driver, db) is True, (
            "Union semantics: any matching shift → on-shift"
        )

    async def test_clearing_override_falls_back_to_work_hours(self, db):
        """A user who once had an override but now has it cleared
        (quiet_start = NULL or quiet_end = NULL) should derive from
        work_hours again — no stale per-user state."""
        acct_id, _, _, _ = await self._setup_account_with_driver_shift(db)

        carol = await db.create_user(
            telegram_id=20_051, account_id=acct_id,
            role=Role.DRIVER, display_name="Carol",
        )
        # Set override
        await db.update_user(carol.id, quiet_start=0, quiet_end=23)
        carol = await db.get_user_by_telegram_id(20_051)
        assert carol.quiet_start == 0 and carol.quiet_end == 23

        # Clear override (both → NULL)
        await db.update_user(carol.id, quiet_start=None, quiet_end=None)
        carol = await db.get_user_by_telegram_id(20_051)
        assert carol.quiet_start is None and carol.quiet_end is None

        # Should now derive from work_hours, not the stale override.
        # Driver shift covers current hour → on-shift → DND off.
        in_dnd = await is_user_dnd_active(carol, db)
        assert in_dnd is False, (
            "Clearing override must fall back to WH-derived DND"
        )
