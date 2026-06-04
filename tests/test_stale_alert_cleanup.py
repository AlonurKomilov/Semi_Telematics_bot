"""Tests for the TTL enforcer (Fix D).

Covers ``capabilities.alerting.cleanup.stale_close_for_account`` —
closes ack rows whose ``expires_at`` has passed AND alert_history
rows whose (severity + last_seen) puts them past the equivalent TTL.

The TTL ladder is applied at row creation by
``AlertsMixin.create_alert_ack`` and at history creation via
``upsert_alert_history``; this helper just enforces the contract.

What's verified
───────────────
* dry-run produces correct candidate counts without modifying any rows
* apply mode closes ack rows with passed ``expires_at`` and the matching
  ``alert_history`` rows under the equivalent TTL ladder (severity +
  last_seen)
* critical alerts (``expires_at IS NULL``) never get touched
* ``events`` + ``maintenance`` alert types are never touched
* an ``audit_log`` row is written with the touched IDs + JSON summary
* per-account isolation
* zero-candidate accounts return clean stats without errors
"""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone

import pytest
import pytest_asyncio

from adapters.storage import Role
from capabilities.alerting.cleanup import (
    SKIP_ALERT_TYPES,
    stale_close_for_account,
)


# ── Fixtures ───────────────────────────────────────────────────────


@pytest_asyncio.fixture
async def seeded_account(db):
    """Single account with the ack-row setup used by every test below."""
    account = await db.create_account("Cleanup Test Co")
    await db.add_company(
        account_id=account.id, code="CO1",
        samsara_api_key="k", display_name="Co One",
    )
    await db.create_user(
        telegram_id=100, account_id=account.id, role=Role.OWNER,
    )
    return db, account


async def _seed_ack(
    db, account_id: int, *,
    alert_type: str = "fault",
    vehicle_id: str = "v1",
    severity: str = "warning",
    expired: bool = True,
    status: str = "active",
) -> int:
    """Insert an ``alert_acknowledgments`` row whose ``expires_at`` is
    in the past (``expired=True``) or in the future.

    Critical alerts have ``expires_at IS NULL`` — never expire.
    """
    now = datetime.now(timezone.utc)
    created_at = (now - timedelta(days=30)).isoformat()
    if severity == "critical":
        expires_at = None
    elif expired:
        expires_at = (now - timedelta(hours=1)).isoformat()
    else:
        expires_at = (now + timedelta(days=7)).isoformat()
    cur = await db._db.execute(
        "INSERT INTO alert_acknowledgments "
        "(account_id, alert_type, vehicle_id, vehicle_name, alert_key, "
        " message_id, chat_id, sent_to, status, created_at, expires_at) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (account_id, alert_type, vehicle_id, vehicle_id, f"k:{vehicle_id}",
         1000, 200, 300, status, created_at, expires_at),
    )
    await db._db.commit()
    return cur.lastrowid


async def _seed_history(
    db, account_id: int, *,
    alert_type: str = "fault",
    vehicle_id: str = "v1",
    severity: str = "warning",
    age_days: int = 30,
    status: str = "active",
) -> int:
    """Insert an ``alert_history`` row whose age + severity puts it
    past the TTL ladder (or not, when caller picks a recent age)."""
    when_iso = (
        datetime.now(timezone.utc) - timedelta(days=age_days)
    ).isoformat()
    cur = await db._db.execute(
        "INSERT INTO alert_history "
        "(account_id, alert_type, vehicle_id, vehicle_name, chat_id, "
        " message_id, occurrence_count, first_seen, last_seen, status, "
        " severity) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (account_id, alert_type, vehicle_id, vehicle_id, 200, 1000,
         1, when_iso, when_iso, status, severity),
    )
    await db._db.commit()
    return cur.lastrowid


# ── Dry-run ─────────────────────────────────────────────────────────


class TestDryRun:
    @pytest.mark.asyncio
    async def test_dry_run_reports_candidates_without_mutating(self, seeded_account):
        db, account = seeded_account
        ack_id = await _seed_ack(db, account.id)  # default: expired warning

        stats = await stale_close_for_account(
            db, account.id, apply_changes=False,
        )

        assert stats["candidate_total"] == 1
        assert stats["candidates_by_type"] == {"fault": 1}
        assert stats["applied"] is False
        assert stats["closed_ack_rows"] == 0

        # Confirm the row is still active in the DB.
        cur = await db._db.execute(
            "SELECT status FROM alert_acknowledgments WHERE id = ?", (ack_id,),
        )
        row = await cur.fetchone()
        assert row["status"] == "active"

    @pytest.mark.asyncio
    async def test_dry_run_zero_candidates_is_clean(self, seeded_account):
        db, account = seeded_account
        stats = await stale_close_for_account(
            db, account.id, apply_changes=False,
        )
        assert stats["candidate_total"] == 0
        assert stats["candidates_by_type"] == {}
        assert stats["closed_ack_rows"] == 0


# ── Apply mode ──────────────────────────────────────────────────────


class TestApply:
    @pytest.mark.asyncio
    async def test_apply_closes_stale_rows(self, seeded_account):
        db, account = seeded_account
        old_ack = await _seed_ack(db, account.id)  # expired warning
        recent_ack = await _seed_ack(db, account.id, expired=False)

        stats = await stale_close_for_account(
            db, account.id, apply_changes=True,
        )

        assert stats["closed_ack_rows"] == 1
        assert stats["applied"] is True

        # Old row is now acknowledged with the system sentinel.
        cur = await db._db.execute(
            "SELECT status, acknowledged_by FROM alert_acknowledgments WHERE id = ?",
            (old_ack,),
        )
        row = await cur.fetchone()
        assert row["status"] == "acknowledged"
        assert row["acknowledged_by"] == 0

        # Recent row is untouched.
        cur = await db._db.execute(
            "SELECT status FROM alert_acknowledgments WHERE id = ?", (recent_ack,),
        )
        row = await cur.fetchone()
        assert row["status"] == "active"

    @pytest.mark.asyncio
    async def test_apply_clears_alert_history_rows(self, seeded_account):
        db, account = seeded_account
        await _seed_ack(db, account.id)  # expired warning
        # alert_history uses severity+last_seen TTL — 60 days old warning
        # is past the 14-day TTL.
        hist_id = await _seed_history(db, account.id, age_days=60)

        stats = await stale_close_for_account(
            db, account.id, apply_changes=True,
        )

        assert stats["closed_history_rows"] == 1
        cur = await db._db.execute(
            "SELECT status FROM alert_history WHERE id = ?", (hist_id,),
        )
        row = await cur.fetchone()
        assert row["status"] == "cleared"


# ── Safety: critical alerts never expire ───────────────────────────


class TestCriticalNeverExpires:
    @pytest.mark.asyncio
    async def test_critical_alert_with_null_expires_at_stays_active(self, seeded_account):
        """Critical alerts have ``expires_at IS NULL`` from creation
        and the TTL enforcer must never close them — the SQL filter
        ``WHERE expires_at IS NOT NULL`` enforces this at the row level.
        """
        db, account = seeded_account
        crit_ack = await _seed_ack(
            db, account.id, severity="critical",
        )  # expires_at is None by construction

        stats = await stale_close_for_account(
            db, account.id, apply_changes=True,
        )

        assert stats["closed_ack_rows"] == 0
        cur = await db._db.execute(
            "SELECT status, expires_at FROM alert_acknowledgments WHERE id = ?",
            (crit_ack,),
        )
        row = await cur.fetchone()
        assert row["status"] == "active"
        assert row["expires_at"] is None


# ── Safety: skipped alert types ─────────────────────────────────────


class TestSkippedTypes:
    @pytest.mark.asyncio
    @pytest.mark.parametrize("alert_type", sorted(SKIP_ALERT_TYPES))
    async def test_protected_types_never_close(self, seeded_account, alert_type):
        db, account = seeded_account
        ack_id = await _seed_ack(
            db, account.id, alert_type=alert_type,
        )  # expired warning, but protected type

        stats = await stale_close_for_account(
            db, account.id, apply_changes=True,
        )

        assert stats["closed_ack_rows"] == 0
        assert alert_type not in stats["candidates_by_type"]

        # Confirm the protected row is still active.
        cur = await db._db.execute(
            "SELECT status FROM alert_acknowledgments WHERE id = ?", (ack_id,),
        )
        row = await cur.fetchone()
        assert row["status"] == "active"


# ── Audit log ───────────────────────────────────────────────────────


class TestAuditLog:
    @pytest.mark.asyncio
    async def test_audit_log_records_batch(self, seeded_account):
        db, account = seeded_account
        ids = [await _seed_ack(db, account.id) for _ in range(3)]

        await stale_close_for_account(
            db, account.id, apply_changes=True,
        )

        cur = await db._db.execute(
            "SELECT * FROM audit_log "
            "WHERE account_id = ? AND action = 'alerts_ttl_close' "
            "ORDER BY id DESC LIMIT 1",
            (account.id,),
        )
        row = await cur.fetchone()
        assert row is not None
        assert row["target_type"] == "alert_acknowledgments"

        # target_id stores up to 500 IDs comma-separated.
        recorded_ids = {int(x) for x in row["target_id"].split(",")}
        assert set(ids) <= recorded_ids

        details = json.loads(row["details"])
        assert details["ack_closed_count"] == 3
        assert "events" in details["skipped_types"]


# ── Per-account isolation ───────────────────────────────────────────


class TestPerAccountIsolation:
    @pytest.mark.asyncio
    async def test_closing_one_account_doesnt_touch_another(self, db):
        a = await db.create_account("A")
        await db.add_company(account_id=a.id, code="CA",
                             samsara_api_key="k", display_name="A")
        b = await db.create_account("B")
        await db.add_company(account_id=b.id, code="CB",
                             samsara_api_key="k", display_name="B")
        await _seed_ack(db, a.id)
        b_ack = await _seed_ack(db, b.id)

        stats = await stale_close_for_account(
            db, a.id, apply_changes=True,
        )
        assert stats["closed_ack_rows"] == 1

        # B's row must still be active.
        cur = await db._db.execute(
            "SELECT status FROM alert_acknowledgments WHERE id = ?", (b_ack,),
        )
        row = await cur.fetchone()
        assert row["status"] == "active"
