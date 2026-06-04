"""Tests for the broadened ``acknowledge_alert`` cascade (Fix B).

Background — the pre-fix bug
─────────────────────────────
Bot-side ack (``handle_alert_ack`` → ``tenant.acknowledge_alert``)
used to cascade only rows sharing the same ``alert_key``:

  * per-recipient DM ack rows had ``alert_key = "{co}:{vid}:{detail}"``
  * group-post sentinel had ``alert_key = "{co}:{vid}:group"``

Different strings → bot ack from a DM never closed the group sentinel
(and vice versa).  The unacked sentinels piled up in the table and
showed on the dashboard as ever-growing "pending" counts even after
the operator acked from Telegram.

Fix B widens the cascade to (account, alert_type, vehicle_id) so a
single ack closes every related row + the alert_history row.

This file pins the new behaviour so the cascade doesn't regress.
"""

from __future__ import annotations

from datetime import datetime, timezone

import pytest
import pytest_asyncio

from adapters.storage import Role


# ── Fixtures ───────────────────────────────────────────────────────


@pytest_asyncio.fixture
async def seeded_account(db):
    account = await db.create_account("Fix B Test Co")
    await db.add_company(
        account_id=account.id, code="CO1",
        samsara_api_key="k", display_name="Co One",
    )
    await db.create_user(
        telegram_id=100, account_id=account.id, role=Role.OWNER,
    )
    return db, account


async def _seed_history(
    db, account_id: int, *,
    alert_type: str = "fault",
    vehicle_id: str = "v1",
    status: str = "active",
) -> int:
    """Insert one alert_history row so the cascade has something to clear."""
    when_iso = datetime.now(timezone.utc).isoformat()
    cur = await db._db.execute(
        "INSERT INTO alert_history "
        "(account_id, alert_type, vehicle_id, vehicle_name, chat_id, "
        " message_id, occurrence_count, first_seen, last_seen, status) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (account_id, alert_type, vehicle_id, vehicle_id, 200, 1000,
         1, when_iso, when_iso, status),
    )
    await db._db.commit()
    return cur.lastrowid


async def _seed_ack_pair(db, account_id: int, *, alert_type: str = "fault",
                        vehicle_id: str = "v1") -> tuple[int, int]:
    """Create the realistic two-row setup:
       (group-sentinel with alert_key=..:group, DM with alert_key=..:detail).
       Returns (group_ack_id, dm_ack_id).
    """
    # Group-post sentinel (sent_to=0).
    group_ack = await db.create_alert_ack(
        account_id=account_id, alert_type=alert_type,
        vehicle_id=vehicle_id, vehicle_name=vehicle_id,
        alert_key=f"CO1:{vehicle_id}:group",
        message_id=10, chat_id=-100, sent_to=0,
    )
    # Per-recipient DM (sent_to=positive telegram id).
    dm_ack = await db.create_alert_ack(
        account_id=account_id, alert_type=alert_type,
        vehicle_id=vehicle_id, vehicle_name=vehicle_id,
        alert_key=f"CO1:{vehicle_id}:spn123:fmi4",
        message_id=11, chat_id=999, sent_to=999,
    )
    return group_ack, dm_ack


# ── The bug we're regression-pinning ───────────────────────────────


class TestCascadeAcrossSurfaces:
    @pytest.mark.asyncio
    async def test_ack_from_dm_also_closes_group_sentinel(self, seeded_account):
        """Ack via the DM row's id closes the group-post sentinel too.

        Before Fix B: the alert_key match would skip the sentinel,
        leaving it forever ``status='active'``.
        """
        db, account = seeded_account
        await _seed_history(db, account.id)
        group_ack, dm_ack = await _seed_ack_pair(db, account.id)

        ok = await db.acknowledge_alert(dm_ack, user_id=999)
        assert ok is True

        # The group sentinel must also be closed.
        cur = await db._db.execute(
            "SELECT status FROM alert_acknowledgments WHERE id = ?",
            (group_ack,),
        )
        row = await cur.fetchone()
        assert row["status"] == "acknowledged", (
            "group-post sentinel should close on DM ack"
        )

        # And the DM row itself.
        cur = await db._db.execute(
            "SELECT status FROM alert_acknowledgments WHERE id = ?",
            (dm_ack,),
        )
        row = await cur.fetchone()
        assert row["status"] == "acknowledged"

    @pytest.mark.asyncio
    async def test_ack_from_group_also_closes_dm(self, seeded_account):
        """Symmetric: acking from the group sentinel closes the DM too."""
        db, account = seeded_account
        await _seed_history(db, account.id)
        group_ack, dm_ack = await _seed_ack_pair(db, account.id)

        ok = await db.acknowledge_alert(group_ack, user_id=0)
        assert ok is True

        cur = await db._db.execute(
            "SELECT status FROM alert_acknowledgments WHERE id = ?",
            (dm_ack,),
        )
        row = await cur.fetchone()
        assert row["status"] == "acknowledged"

    @pytest.mark.asyncio
    async def test_ack_clears_alert_history(self, seeded_account):
        """The cascade also closes the matching alert_history row so the
        resolve flow's "is this in flight?" gate reflects the ack.
        """
        db, account = seeded_account
        hist_id = await _seed_history(db, account.id)
        _, dm_ack = await _seed_ack_pair(db, account.id)

        await db.acknowledge_alert(dm_ack, user_id=999)

        cur = await db._db.execute(
            "SELECT status FROM alert_history WHERE id = ?", (hist_id,),
        )
        row = await cur.fetchone()
        assert row["status"] == "cleared"

    @pytest.mark.asyncio
    async def test_does_not_touch_unrelated_vehicle(self, seeded_account):
        """Cascade is scoped by (account, alert_type, vehicle_id) — a
        different truck's rows must stay active.
        """
        db, account = seeded_account
        await _seed_history(db, account.id, vehicle_id="v1")
        await _seed_history(db, account.id, vehicle_id="v2")
        _, dm_ack_v1 = await _seed_ack_pair(db, account.id, vehicle_id="v1")
        group_ack_v2, dm_ack_v2 = await _seed_ack_pair(
            db, account.id, vehicle_id="v2",
        )

        await db.acknowledge_alert(dm_ack_v1, user_id=999)

        # v2 rows untouched.
        for ack_id in (group_ack_v2, dm_ack_v2):
            cur = await db._db.execute(
                "SELECT status FROM alert_acknowledgments WHERE id = ?",
                (ack_id,),
            )
            row = await cur.fetchone()
            assert row["status"] == "active", (
                f"v2 row {ack_id} should be untouched by v1 ack"
            )

    @pytest.mark.asyncio
    async def test_does_not_touch_different_alert_type(self, seeded_account):
        """Acking a fault on a vehicle must not close the same vehicle's
        unrelated health alert.
        """
        db, account = seeded_account
        await _seed_history(db, account.id, alert_type="fault")
        await _seed_history(db, account.id, alert_type="health")
        _, dm_ack_fault = await _seed_ack_pair(
            db, account.id, alert_type="fault",
        )
        group_ack_health, _ = await _seed_ack_pair(
            db, account.id, alert_type="health",
        )

        await db.acknowledge_alert(dm_ack_fault, user_id=999)

        cur = await db._db.execute(
            "SELECT status FROM alert_acknowledgments WHERE id = ?",
            (group_ack_health,),
        )
        row = await cur.fetchone()
        assert row["status"] == "active"

    @pytest.mark.asyncio
    async def test_unknown_ack_id_returns_false(self, seeded_account):
        """Sanity — non-existent ack_id returns False, no SQL error."""
        db, account = seeded_account
        ok = await db.acknowledge_alert(999_999, user_id=1)
        assert ok is False
