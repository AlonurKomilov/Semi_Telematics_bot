"""Tests for the account-security mixin: lockout, password reset, email verification."""
from __future__ import annotations

import asyncio
import os
import sys

os.environ.setdefault("ENCRYPTION_KEY", "")

import pytest

from adapters.storage import Role


@pytest.fixture
async def db(pg_db):
    yield pg_db


# ── Email verification ──────────────────────────────────────────────


class TestEmailVerification:
    async def test_round_trip_flips_user_to_verified(self, db):
        acct = await db.create_account("Verify Co")
        user = await db.create_user_with_email(
            email="alice@example.com", password_hash="x",
            account_id=acct.id, role=Role.OWNER, display_name="Alice",
        )
        # Fresh email-only signups are NOT auto-verified.
        await db._db.execute(
            "UPDATE users SET email_verified = 0 WHERE id = ?", (user.id,),
        )
        await db._db.commit()
        assert not await db.is_email_verified(user.id)

        token = await db.create_email_verification_token(user.id, user.email)
        result = await db.consume_email_verification_token(token)
        assert result == user.id
        assert await db.is_email_verified(user.id)

    async def test_one_time_use(self, db):
        acct = await db.create_account("Verify Once")
        user = await db.create_user_with_email(
            email="bob@example.com", password_hash="x",
            account_id=acct.id, role=Role.OWNER, display_name="Bob",
        )
        token = await db.create_email_verification_token(user.id, user.email)
        assert await db.consume_email_verification_token(token) == user.id
        # Second redeem returns None.
        assert await db.consume_email_verification_token(token) is None

    async def test_unknown_token_returns_none(self, db):
        assert await db.consume_email_verification_token("not-a-real-token") is None


# ── Failed-login lockout ────────────────────────────────────────────


class TestFailedLoginLockout:
    async def test_increments_counter_and_locks_at_threshold(self, db):
        acct = await db.create_account("Lockout Co")
        user = await db.create_user_with_email(
            email="charlie@example.com", password_hash="x",
            account_id=acct.id, role=Role.OWNER, display_name="Charlie",
        )
        # First 4 failures: counter increments but no lock.
        for i in range(4):
            state = await db.record_failed_login(user.id)
            assert state["attempts"] == i + 1
            assert state["locked_until"] is None
        # 5th failure trips the lock.
        state = await db.record_failed_login(user.id)
        assert state["attempts"] == 5
        assert state["locked_until"] is not None

        # is_user_locked surfaces the lock timestamp.
        locked = await db.is_user_locked(user.id)
        assert locked == state["locked_until"]

    async def test_clear_resets_counter_and_unlocks(self, db):
        acct = await db.create_account("Clear Co")
        user = await db.create_user_with_email(
            email="dana@example.com", password_hash="x",
            account_id=acct.id, role=Role.OWNER, display_name="Dana",
        )
        for _ in range(5):
            await db.record_failed_login(user.id)
        assert await db.is_user_locked(user.id) is not None
        await db.clear_failed_logins(user.id)
        assert await db.is_user_locked(user.id) is None

    async def test_expired_lock_auto_clears(self, db, monkeypatch):
        """Once locked_until is in the past, is_user_locked clears the lock
        on the next call.  Lets a user back in without admin intervention
        once the cooldown window passes."""
        acct = await db.create_account("Expire Co")
        user = await db.create_user_with_email(
            email="erin@example.com", password_hash="x",
            account_id=acct.id, role=Role.OWNER, display_name="Erin",
        )
        # Stamp a lock in the distant past.
        await db._db.execute(
            "UPDATE users SET failed_login_attempts = 5, "
            "locked_until = '2020-01-01T00:00:00+00:00' WHERE id = ?",
            (user.id,),
        )
        await db._db.commit()
        # is_user_locked should see the expired lock and clear it.
        assert await db.is_user_locked(user.id) is None


# ── Password reset ──────────────────────────────────────────────────


class TestPasswordReset:
    async def test_round_trip(self, db):
        acct = await db.create_account("Reset Co")
        user = await db.create_user_with_email(
            email="frank@example.com", password_hash="x",
            account_id=acct.id, role=Role.OWNER, display_name="Frank",
        )
        token = await db.create_password_reset_token(user.id)
        assert await db.consume_password_reset_token(token) == user.id

    async def test_one_time_use(self, db):
        acct = await db.create_account("Reset Once")
        user = await db.create_user_with_email(
            email="gina@example.com", password_hash="x",
            account_id=acct.id, role=Role.OWNER, display_name="Gina",
        )
        token = await db.create_password_reset_token(user.id)
        assert await db.consume_password_reset_token(token) == user.id
        # Second redeem is a no-op.
        assert await db.consume_password_reset_token(token) is None

    async def test_password_changed_timestamp(self, db):
        acct = await db.create_account("PwTime Co")
        user = await db.create_user_with_email(
            email="hank@example.com", password_hash="x",
            account_id=acct.id, role=Role.OWNER, display_name="Hank",
        )
        await db.mark_password_changed(user.id)
        refreshed = await db.get_user(user.id)
        assert refreshed is not None
        # We expect the timestamp column to be populated (raw row access
        # because the dataclass doesn't carry it yet).
        cur = await db._db.execute(
            "SELECT password_changed_at FROM users WHERE id = ?", (user.id,),
        )
        row = await cur.fetchone()
        assert row is not None
        stamp = dict(row).get("password_changed_at")
        assert stamp  # Non-empty ISO timestamp.
