"""Single-source-of-truth timezone resolution tests.

Verifies the resolution chain:

    users.timezone  (override, if a valid IANA name)
        ↓ fallback
    accounts.timezone   (admin-set default)
        ↓ fallback
    "America/New_York"  (DEFAULT_TIMEZONE)

…plus the ``is_local_hour`` helper that gates the rewritten daily
crons, and cache invalidation on column updates.
"""

from __future__ import annotations

import os
import sys
from datetime import datetime, timezone
from unittest.mock import patch

os.environ.setdefault("ENCRYPTION_KEY", "")

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

from adapters.storage import Database, Role
from capabilities.localization import tz as _tz


@pytest.fixture
async def db(pg_db):
    # Wire infra.platform so the tz helper's lazy get_platform_db works.
    import infra.platform as _cp
    _old_db = _cp._db
    _cp._db = pg_db
    # Drop any cached resolutions from prior tests.
    _tz.invalidate_all()
    yield pg_db
    _cp._db = _old_db
    _tz.invalidate_all()


# ── Resolution chain ──────────────────────────────────────────────


class TestResolutionChain:
    async def test_fresh_account_uses_iana_default(self, db):
        acct = await db.create_account("X")
        tz = await _tz.effective_tz_for_account(acct.id)
        # Schema default kicks in — even without an explicit set.
        assert tz == "America/New_York"

    async def test_account_value_takes_effect(self, db):
        acct = await db.create_account("X")
        await db.update_account(acct.id, timezone="America/Chicago")
        # Cache may have stale 'New_York' from any prior lookup — the
        # mixin invalidates on update, so this should return the fresh
        # value within the same tick.
        tz = await _tz.effective_tz_for_account(acct.id)
        assert tz == "America/Chicago"

    async def test_user_override_wins_over_account(self, db):
        acct = await db.create_account("X")
        await db.update_account(acct.id, timezone="America/Chicago")
        user = await db.create_user(101, acct.id, role=Role.DRIVER)
        await db.update_user(user.id, timezone="America/Los_Angeles")
        tz = await _tz.effective_tz_for_user(user.id)
        assert tz == "America/Los_Angeles"

    async def test_user_with_blank_override_falls_through(self, db):
        acct = await db.create_account("X")
        await db.update_account(acct.id, timezone="America/Denver")
        user = await db.create_user(102, acct.id, role=Role.DRIVER)
        # Clearing the override — same path the dashboard uses when the
        # "Use account default" select option is picked.
        await db.update_user(user.id, timezone="")
        tz = await _tz.effective_tz_for_user(user.id)
        assert tz == "America/Denver"

    async def test_invalid_user_value_falls_through(self, db):
        """Bad data in the column shouldn't crash callers — fall
        through to the account default instead."""
        acct = await db.create_account("X")
        await db.update_account(acct.id, timezone="America/Chicago")
        user = await db.create_user(103, acct.id, role=Role.DRIVER)
        # Bypass the API validator and write a stale alias directly.
        await db._db.execute(
            "UPDATE users SET timezone = ? WHERE id = ?",
            ("EST", user.id),
        )
        await db._db.commit()
        _tz.invalidate_user(user.id)
        tz = await _tz.effective_tz_for_user(user.id)
        assert tz == "America/Chicago"


# ── Cache behavior ─────────────────────────────────────────────────


class TestCacheInvalidation:
    async def test_user_update_clears_cached_resolution(self, db):
        acct = await db.create_account("X")
        await db.update_account(acct.id, timezone="America/Chicago")
        user = await db.create_user(201, acct.id, role=Role.DRIVER)
        # New rows default to the legacy 'America/New_York' value, so
        # explicitly clear the override to test the fall-through path.
        await db.update_user(user.id, timezone="")
        first = await _tz.effective_tz_for_user(user.id)
        assert first == "America/Chicago"
        # Setting a new override should propagate without waiting for
        # the 60-second TTL — the update path clears the cache key.
        await db.update_user(user.id, timezone="America/Los_Angeles")
        second = await _tz.effective_tz_for_user(user.id)
        assert second == "America/Los_Angeles"

    async def test_account_update_clears_user_cache_too(self, db):
        acct = await db.create_account("X")
        await db.update_account(acct.id, timezone="America/Chicago")
        user = await db.create_user(202, acct.id, role=Role.DRIVER)
        # Clear the per-user override so the user truly inherits.
        await db.update_user(user.id, timezone="")
        # User inherits — cache populated with 'Chicago'.
        assert await _tz.effective_tz_for_user(user.id) == "America/Chicago"
        # Admin changes the account default; inheriting users see the
        # new value on the next lookup.
        await db.update_account(acct.id, timezone="America/Los_Angeles")
        assert await _tz.effective_tz_for_user(user.id) == "America/Los_Angeles"


# ── is_local_hour cron gate ────────────────────────────────────────


class TestIsLocalHour:
    async def test_eastern_account_at_9_utc_is_not_local_9(self, db):
        """09:00 UTC ≠ 09:00 ET (it's 04:00 / 05:00 ET depending on DST).
        The gate should return False so the old UTC schedule no longer
        leaks through the new local-hour-based logic."""
        acct = await db.create_account("X")
        await db.update_account(acct.id, timezone="America/New_York")
        with patch(
            "capabilities.localization.tz.datetime",
            wraps=datetime,
        ) as mocked:
            mocked.now.return_value = datetime(2026, 6, 15, 9, 0, tzinfo=timezone.utc)
            assert await _tz.is_local_hour(acct.id, 9) is False

    async def test_eastern_account_at_13_utc_is_local_9_dst(self, db):
        """13:00 UTC = 09:00 ET during EDT (summer)."""
        acct = await db.create_account("X")
        await db.update_account(acct.id, timezone="America/New_York")
        with patch(
            "capabilities.localization.tz.datetime",
            wraps=datetime,
        ) as mocked:
            mocked.now.return_value = datetime(2026, 6, 15, 13, 0, tzinfo=timezone.utc)
            assert await _tz.is_local_hour(acct.id, 9) is True

    async def test_pacific_account_at_17_utc_is_local_9_dst(self, db):
        """17:00 UTC = 10:00 PDT in summer — not 09:00.  This guards
        against an off-by-one: PDT is UTC-7, so 09:00 PDT is 16:00 UTC."""
        acct = await db.create_account("X")
        await db.update_account(acct.id, timezone="America/Los_Angeles")
        with patch(
            "capabilities.localization.tz.datetime",
            wraps=datetime,
        ) as mocked:
            mocked.now.return_value = datetime(2026, 6, 15, 16, 0, tzinfo=timezone.utc)
            assert await _tz.is_local_hour(acct.id, 9) is True
            mocked.now.return_value = datetime(2026, 6, 15, 17, 0, tzinfo=timezone.utc)
            assert await _tz.is_local_hour(acct.id, 9) is False
