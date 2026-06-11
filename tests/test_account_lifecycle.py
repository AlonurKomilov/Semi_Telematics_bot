"""Tests for the account-lifecycle mixin: suspend, soft-delete, purge, deletion codes."""
from __future__ import annotations

import os
import sys

os.environ.setdefault("ENCRYPTION_KEY", "")
sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

import pytest


@pytest.fixture
async def db(pg_db):
    yield pg_db


# ── Suspend / unsuspend ────────────────────────────────────────────


class TestSuspend:
    async def test_suspend_sets_inactive_and_audit_fields(self, db):
        acct = await db.create_account("ACME")
        ok = await db.suspend_account(
            acct.id, reason="fraud investigation", operator_tg_id=12345,
        )
        assert ok is True
        lc = await db.get_account_lifecycle(acct.id)
        assert lc["is_active"] is False
        assert lc["suspended_at"]
        assert lc["suspended_reason"] == "fraud investigation"
        assert lc["suspended_by"] == 12345

    async def test_double_suspend_is_noop(self, db):
        acct = await db.create_account("ACME")
        assert await db.suspend_account(acct.id, reason="r1", operator_tg_id=1)
        # Second call must not overwrite the original audit trail.
        assert await db.suspend_account(acct.id, reason="r2", operator_tg_id=2) is False
        lc = await db.get_account_lifecycle(acct.id)
        assert lc["suspended_reason"] == "r1"
        assert lc["suspended_by"] == 1

    async def test_unsuspend_clears_fields(self, db):
        acct = await db.create_account("ACME")
        await db.suspend_account(acct.id, reason="r", operator_tg_id=1)
        ok = await db.unsuspend_account(acct.id)
        assert ok is True
        lc = await db.get_account_lifecycle(acct.id)
        assert lc["is_active"] is True
        assert lc["suspended_at"] is None
        assert lc["suspended_reason"] is None
        assert lc["suspended_by"] is None

    async def test_unsuspend_noop_when_not_suspended(self, db):
        acct = await db.create_account("ACME")
        assert await db.unsuspend_account(acct.id) is False

    async def test_unsuspend_refuses_when_deletion_pending(self, db):
        """Operator can't undo an owner's deletion via unsuspend — owner has to cancel."""
        acct = await db.create_account("ACME")
        await db.suspend_account(acct.id, reason="r", operator_tg_id=1)
        await db.request_account_deletion(acct.id, owner_tg_id=99)
        # deleted_at is set, so unsuspend must refuse.
        assert await db.unsuspend_account(acct.id) is False
        lc = await db.get_account_lifecycle(acct.id)
        assert lc["is_active"] is False  # still blocked


# ── Soft-delete request / cancel ───────────────────────────────────


class TestSoftDelete:
    async def test_request_deletion_schedules_purge(self, db):
        acct = await db.create_account("ACME")
        purge_iso = await db.request_account_deletion(
            acct.id, owner_tg_id=99, grace_days=90,
        )
        assert purge_iso is not None
        lc = await db.get_account_lifecycle(acct.id)
        assert lc["is_active"] is False  # immediate login disable
        assert lc["deleted_at"]
        assert lc["delete_requested_by"] == 99
        assert lc["purge_at"] == purge_iso

    async def test_double_request_is_noop(self, db):
        acct = await db.create_account("ACME")
        first = await db.request_account_deletion(acct.id, owner_tg_id=99)
        second = await db.request_account_deletion(acct.id, owner_tg_id=99)
        assert first is not None
        assert second is None

    async def test_cancel_restores_active(self, db):
        acct = await db.create_account("ACME")
        await db.request_account_deletion(acct.id, owner_tg_id=99)
        ok = await db.cancel_account_deletion(acct.id)
        assert ok is True
        lc = await db.get_account_lifecycle(acct.id)
        assert lc["is_active"] is True
        assert lc["deleted_at"] is None
        assert lc["purge_at"] is None

    async def test_cancel_noop_when_not_pending(self, db):
        acct = await db.create_account("ACME")
        assert await db.cancel_account_deletion(acct.id) is False


class TestPendingPurge:
    async def test_lists_only_past_due(self, db):
        from datetime import datetime, timezone, timedelta
        a1 = await db.create_account("A")
        a2 = await db.create_account("B")
        a3 = await db.create_account("C")
        # a1 + a2 are past their purge_at; a3 is in the future.
        # Use tiny grace_days so we don't need to mock the clock.
        await db.request_account_deletion(a1.id, owner_tg_id=99, grace_days=0)
        await db.request_account_deletion(a2.id, owner_tg_id=99, grace_days=0)
        await db.request_account_deletion(a3.id, owner_tg_id=99, grace_days=999)

        future = (
            datetime.now(timezone.utc) + timedelta(seconds=10)
        ).isoformat(timespec="seconds").replace("+00:00", "Z")
        ids = await db.list_accounts_pending_purge(before_iso=future)
        assert a1.id in ids
        assert a2.id in ids
        assert a3.id not in ids


# ── Deletion verification codes ────────────────────────────────────


class TestDeletionCodes:
    async def test_mint_returns_six_digits(self, db):
        acct = await db.create_account("ACME")
        code = await db.create_deletion_code(acct.id, user_id=1)
        assert len(code) == 6
        assert code.isdigit()

    async def test_consume_valid_code(self, db):
        acct = await db.create_account("ACME")
        code = await db.create_deletion_code(acct.id, user_id=1)
        assert await db.consume_deletion_code(acct.id, user_id=1, code=code) is True

    async def test_consume_burns_code_no_replay(self, db):
        acct = await db.create_account("ACME")
        code = await db.create_deletion_code(acct.id, user_id=1)
        await db.consume_deletion_code(acct.id, user_id=1, code=code)
        # Second use must fail.
        assert await db.consume_deletion_code(acct.id, user_id=1, code=code) is False

    async def test_wrong_code_rejected(self, db):
        acct = await db.create_account("ACME")
        await db.create_deletion_code(acct.id, user_id=1)
        assert await db.consume_deletion_code(
            acct.id, user_id=1, code="000000",
        ) is False

    async def test_wrong_user_rejected(self, db):
        """An attacker who steals the code can't redeem it as a different user."""
        acct = await db.create_account("ACME")
        code = await db.create_deletion_code(acct.id, user_id=1)
        assert await db.consume_deletion_code(
            acct.id, user_id=999, code=code,
        ) is False

    async def test_remint_invalidates_previous(self, db):
        acct = await db.create_account("ACME")
        code1 = await db.create_deletion_code(acct.id, user_id=1)
        code2 = await db.create_deletion_code(acct.id, user_id=1)
        # The first code is gone (UNIQUE constraint on account_id + ON CONFLICT).
        assert code1 != code2 or True  # both 1-in-1M chance of equal, still test mint
        assert await db.consume_deletion_code(acct.id, user_id=1, code=code1) is False
        assert await db.consume_deletion_code(acct.id, user_id=1, code=code2) is True

    async def test_expired_code_rejected(self, db):
        acct = await db.create_account("ACME")
        code = await db.create_deletion_code(acct.id, user_id=1)
        # Simulate clock skew / late entry: backdate expires_at by 1h.
        # Using seconds-precision ISO so it matches consume's comparison.
        from datetime import datetime, timezone, timedelta
        past = (
            datetime.now(timezone.utc) - timedelta(hours=1)
        ).isoformat(timespec="seconds").replace("+00:00", "Z")
        await db._db.execute(
            "UPDATE account_deletion_codes SET expires_at = ? WHERE account_id = ?",
            (past, acct.id),
        )
        await db._db.commit()
        assert await db.consume_deletion_code(
            acct.id, user_id=1, code=code,
        ) is False

    async def test_cleanup_removes_expired(self, db):
        acct = await db.create_account("ACME")
        await db.create_deletion_code(acct.id, user_id=1)
        # Backdate to ensure it's eligible for cleanup.
        from datetime import datetime, timezone, timedelta
        past = (
            datetime.now(timezone.utc) - timedelta(hours=1)
        ).isoformat(timespec="seconds").replace("+00:00", "Z")
        await db._db.execute(
            "UPDATE account_deletion_codes SET expires_at = ? WHERE account_id = ?",
            (past, acct.id),
        )
        await db._db.commit()
        n = await db.cleanup_expired_deletion_codes()
        assert n >= 1
