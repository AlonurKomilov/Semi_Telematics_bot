"""Tests for chronic-pattern suppression (Fix C).

Covers the ``chronic_alert_suppressions`` table + the four mixin
helpers (``mute_chronic_pattern``, ``is_chronic_pattern_muted``,
``unmute_chronic_pattern``, ``list_active_chronic_mutes``).

The ``send_alert`` pipeline integration (gate before delivery) is
covered by integration-level tests in ``test_alerts.py`` so this
file stays focused on the storage layer.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest
import pytest_asyncio

from adapters.storage import Role


# ── Fixtures ───────────────────────────────────────────────────────


@pytest_asyncio.fixture
async def seeded_account(db):
    account = await db.create_account("Chronic Test Co")
    await db.add_company(
        account_id=account.id, code="CO1",
        samsara_api_key="k", display_name="Co One",
    )
    await db.create_user(
        telegram_id=100, account_id=account.id, role=Role.OWNER,
    )
    return db, account


# ── Mute helpers ───────────────────────────────────────────────────


class TestMuteChronicPattern:
    @pytest.mark.asyncio
    async def test_mute_creates_active_row(self, seeded_account):
        db, account = seeded_account
        result = await db.mute_chronic_pattern(
            account.id, "fault", "v_237", hours=24, muted_by=100,
        )
        assert result["alert_type"] == "fault"
        assert result["vehicle_id"] == "v_237"
        assert result["hours"] == 24

        # Verify it's active.
        muted = await db.is_chronic_pattern_muted(
            account.id, "fault", "v_237",
        )
        assert muted is True

    @pytest.mark.asyncio
    async def test_mute_is_pattern_scoped(self, seeded_account):
        """Muting (fault, v_237) does NOT mute (health, v_237) or
        (fault, v_999) — scope is the full triple."""
        db, account = seeded_account
        await db.mute_chronic_pattern(
            account.id, "fault", "v_237", hours=24,
        )

        # Same vehicle, different alert_type
        assert await db.is_chronic_pattern_muted(
            account.id, "health", "v_237",
        ) is False

        # Same alert_type, different vehicle
        assert await db.is_chronic_pattern_muted(
            account.id, "fault", "v_999",
        ) is False

    @pytest.mark.asyncio
    async def test_mute_is_account_scoped(self, db):
        """Tenant isolation — muting in account A doesn't leak to B."""
        a = await db.create_account("A")
        await db.add_company(account_id=a.id, code="CA",
                             samsara_api_key="k", display_name="A")
        b = await db.create_account("B")
        await db.add_company(account_id=b.id, code="CB",
                             samsara_api_key="k", display_name="B")

        await db.mute_chronic_pattern(a.id, "fault", "v_237", hours=24)

        assert await db.is_chronic_pattern_muted(b.id, "fault", "v_237") is False

    @pytest.mark.asyncio
    async def test_remute_updates_existing_row(self, seeded_account):
        """The UNIQUE constraint + ON CONFLICT means re-muting the same
        triple replaces ``muted_until`` rather than failing."""
        db, account = seeded_account

        first = await db.mute_chronic_pattern(
            account.id, "fault", "v_237", hours=1,
        )
        second = await db.mute_chronic_pattern(
            account.id, "fault", "v_237", hours=168,
        )

        # Both succeeded (no IntegrityError).
        assert first["muted_until"] != second["muted_until"]

        # Only one row exists.
        rows = await db.list_active_chronic_mutes(account.id)
        assert len(rows) == 1


# ── Expiry ─────────────────────────────────────────────────────────


class TestMuteExpiry:
    @pytest.mark.asyncio
    async def test_expired_mute_returns_false(self, seeded_account):
        db, account = seeded_account
        # Insert an expired mute directly so we don't need to wait.
        past = (datetime.now(timezone.utc) - timedelta(hours=1)).isoformat()
        now = (datetime.now(timezone.utc) - timedelta(hours=2)).isoformat()
        await db._db.execute(
            "INSERT INTO chronic_alert_suppressions "
            "(account_id, alert_type, vehicle_id, muted_until, created_at) "
            "VALUES (?, ?, ?, ?, ?)",
            (account.id, "fault", "v_old", past, now),
        )
        await db._db.commit()

        assert await db.is_chronic_pattern_muted(
            account.id, "fault", "v_old",
        ) is False


# ── Unmute ─────────────────────────────────────────────────────────


class TestUnmute:
    @pytest.mark.asyncio
    async def test_unmute_lifts_the_suppression(self, seeded_account):
        db, account = seeded_account
        await db.mute_chronic_pattern(
            account.id, "fault", "v_237", hours=168,
        )
        assert await db.is_chronic_pattern_muted(account.id, "fault", "v_237") is True

        deleted = await db.unmute_chronic_pattern(account.id, "fault", "v_237")
        assert deleted == 1

        assert await db.is_chronic_pattern_muted(account.id, "fault", "v_237") is False

    @pytest.mark.asyncio
    async def test_unmute_unknown_pattern_is_noop(self, seeded_account):
        db, account = seeded_account
        deleted = await db.unmute_chronic_pattern(account.id, "fault", "nope")
        assert deleted == 0


# ── Listing ────────────────────────────────────────────────────────


class TestList:
    @pytest.mark.asyncio
    async def test_list_returns_only_active_mutes(self, seeded_account):
        db, account = seeded_account
        await db.mute_chronic_pattern(account.id, "fault", "v1", hours=168)
        await db.mute_chronic_pattern(account.id, "health", "v2", hours=168)

        # Add an expired mute via raw SQL.
        past = (datetime.now(timezone.utc) - timedelta(hours=1)).isoformat()
        await db._db.execute(
            "INSERT INTO chronic_alert_suppressions "
            "(account_id, alert_type, vehicle_id, muted_until, created_at) "
            "VALUES (?, ?, ?, ?, ?)",
            (account.id, "fuel", "v_old", past,
             datetime.now(timezone.utc).isoformat()),
        )
        await db._db.commit()

        active = await db.list_active_chronic_mutes(account.id)
        active_pairs = {(r["alert_type"], r["vehicle_id"]) for r in active}
        assert active_pairs == {("fault", "v1"), ("health", "v2")}
