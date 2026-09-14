"""What "pending" means: LOGICAL alerts, and never age-limited.

Two owner decisions pinned here, because both were silently wrong before
and both are the kind of thing a refactor would "simplify" back:

1. A pending alert is ONE PROBLEM, not one delivery.  ``alert_history``
   holds a row per (truck, problem); ``alert_acknowledgments`` holds a row
   per RECIPIENT.  The Overview card and bell badge used to count the
   latter, so three people DM'd about one fault read as three pending
   alerts and never matched the Alerts board.

2. The open queue is UNWINDOWED.  ``first_seen`` is stamped once and never
   bumped on a re-fire, so windowing active rows hid the longest-running
   unresolved problems — exactly backwards for a safety queue.  The window
   bounds RESOLVED history instead, per row rather than per view: an open
   alert stays visible in 'all' too, so that tab is exactly the union of
   the other two and their counts add up.
"""

from __future__ import annotations

import os
from datetime import datetime, timedelta, timezone

os.environ.setdefault("ENCRYPTION_KEY", "")

import pytest
import pytest_asyncio


@pytest_asyncio.fixture
async def db(pg_db):
    acct = await pg_db.create_account("Pending Semantics Co")
    yield pg_db, acct.id


async def _fire(database, acct, vehicle_id, name, *, atype="fault",
                sev="critical"):
    row = await database.upsert_alert_history(
        acct, atype, vehicle_id, name, last_detail="x", severity=sev)
    return row["id"]


async def _age(database, alert_id, days):
    """Back-date first_seen (what the window filters on) without touching
    last_seen — the chronic-alert shape: opened long ago, still firing."""
    old = (datetime.now(timezone.utc) - timedelta(days=days)).isoformat()
    await database._db.execute(
        "UPDATE alert_history SET first_seen = ? WHERE id = ?", (old, alert_id))
    await database._db.commit()


class TestOneProblemOneAlert:
    @pytest.mark.asyncio
    async def test_delivery_rows_do_not_inflate_the_logical_count(self, db):
        database, acct = db
        await _fire(database, acct, "V1", "T1")
        # Same single problem, delivered to three subscribers.
        for chat in (101, 102, 103):
            await database.create_alert_ack(
                account_id=acct, alert_type="fault", vehicle_id="V1",
                vehicle_name="T1", alert_key=f"K:{chat}", message_id=chat,
                chat_id=chat, sent_to=chat, severity="critical")

        deliveries = await database.get_pending_alerts(acct)
        logical = await database.get_active_alert_history_for_account_paged(
            acct, ack_state="active")
        assert len(deliveries) == 3          # one row per recipient
        assert len(logical) == 1             # ONE problem — what we now count


class TestOpenQueueIgnoresAge:
    @pytest.mark.asyncio
    async def test_old_active_alert_stays_in_the_open_queue(self, db):
        """A 40-day-old unacknowledged alert must appear on a 30-day board."""
        database, acct = db
        old_id = await _fire(database, acct, "V1", "T1")
        await _age(database, old_id, 40)

        rows = await database.get_active_alert_history_for_account_paged(
            acct, ack_state="active", days=30)
        assert {r["id"] for r in rows} == {old_id}

        count = await database.count_active_alert_history_for_account_filtered(
            acct, ack_state="active", days=30)
        assert count == 1                    # count agrees with the list

    @pytest.mark.asyncio
    async def test_window_still_bounds_the_acknowledged_view(self, db):
        """History stays windowed — that's where "last 30 days" is meant."""
        database, acct = db
        old_id = await _fire(database, acct, "V1", "T1")
        recent_id = await _fire(database, acct, "V2", "T2")
        await _age(database, old_id, 40)
        # Clear both so they land in the acknowledged view.
        await database.clear_alert_history(acct, "fault", "V1")
        await database.clear_alert_history(acct, "fault", "V2")

        rows = await database.get_active_alert_history_for_account_paged(
            acct, ack_state="acknowledged", days=30)
        ids = {r["id"] for r in rows}
        assert recent_id in ids              # inside the window
        assert old_id not in ids             # outside it — correctly hidden

    @pytest.mark.asyncio
    async def test_all_keeps_an_old_OPEN_alert(self, db):
        """'all' is the union of the other two tabs, so anything the open
        queue shows must appear here as well.

        This once asserted the opposite — that the window bounds 'all'
        wholesale.  Each rule was defensible alone, but together they let
        the board print "Not acknowledged 3,984 · All 3,295": a total
        smaller than one of its own parts.  The window bounds resolved
        history; an unacknowledged alert is open regardless of age, in
        every view that contains it."""
        database, acct = db
        old_id = await _fire(database, acct, "V1", "T1")
        await _age(database, old_id, 40)
        rows = await database.get_active_alert_history_for_account_paged(
            acct, ack_state="all", days=30)
        assert old_id in {r["id"] for r in rows}

    @pytest.mark.asyncio
    async def test_all_still_windows_out_old_RESOLVED_history(self, db):
        """The window has to keep doing its job on the other half, or
        'all' grows without bound and the date control means nothing."""
        database, acct = db
        old_id = await _fire(database, acct, "V2", "T2")
        await database.acknowledge_alert_history(old_id, 1, account_id=acct)
        await _age(database, old_id, 40)
        rows = await database.get_active_alert_history_for_account_paged(
            acct, ack_state="all", days=30)
        assert old_id not in {r["id"] for r in rows}
