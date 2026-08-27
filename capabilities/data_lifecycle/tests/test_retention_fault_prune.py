"""The cleared-only fault prune must never delete an ACTIVE fault.

``prune_vehicle_fault_log`` is the one retention executor with a
conditional (``cleared_at IS NOT NULL``) rather than a flat age cut, so it
gets its own integration test against real Postgres.
"""

from __future__ import annotations

import os

os.environ.setdefault("ENCRYPTION_KEY", "")

from datetime import datetime, timedelta, timezone

import pytest


def _iso(dt: datetime) -> str:
    return dt.isoformat()


@pytest.mark.asyncio
async def test_prune_fault_log_keeps_active_and_recent_cleared(pg_db):
    db = pg_db
    acct = 1
    now = datetime.now(timezone.utc)

    async def _insert(dtc_id: str, cleared_at):
        await db._db.execute(
            "INSERT INTO vehicle_fault_log "
            "(account_id, vehicle_id, dtc_id, spn, fmi, description, severity, "
            " observed_at, cleared_at, raw_json) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (acct, "v1", dtc_id, 100, 3, "test fault", "warning",
             _iso(now - timedelta(days=500)), cleared_at, "{}"),
        )

    await _insert("active",      None)                               # active → always kept
    await _insert("cleared_old", _iso(now - timedelta(days=400)))    # cleared > 365d → pruned
    await _insert("cleared_new", _iso(now - timedelta(days=10)))     # cleared < 365d → kept
    await db._db.commit()

    deleted = await db.prune_vehicle_fault_log(acct, days_keep=365)
    assert deleted == 1  # only the old cleared fault

    cur = await db._db.execute(
        "SELECT dtc_id FROM vehicle_fault_log WHERE account_id = ? ORDER BY dtc_id",
        (acct,),
    )
    remaining = {dict(r)["dtc_id"] for r in await cur.fetchall()}
    assert remaining == {"active", "cleared_new"}
