"""Per-subtype alert_history dedup (alert_subkey).

For events the alert_type is just "events" — without a subkey, all
event subtypes (rollingStop, braking, followingDistance, …) on the
same vehicle would collapse into one alert_history row and the
occurrence count would mean "any event on this truck" instead of
"this specific behavior fired N times."  These tests pin the
post-migration behavior: same vehicle + different subkeys = different
rows + independent counts.
"""

from __future__ import annotations

import os
import sys

os.environ.setdefault("ENCRYPTION_KEY", "")

import pytest_asyncio
import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

from adapters.storage import Database


@pytest_asyncio.fixture
async def db(pg_db):
    acct = await pg_db.create_account("Subkey Co")
    yield pg_db, acct.id


class TestAlertHistorySubkey:
    @pytest.mark.asyncio
    async def test_distinct_subkeys_create_distinct_rows(self, db):
        database, acct_id = db
        # Two event subtypes on the same vehicle.
        r1 = await database.upsert_alert_history(
            account_id=acct_id, alert_type="events",
            vehicle_id="V1", vehicle_name="208",
            alert_subkey="followingDistance",
        )
        r2 = await database.upsert_alert_history(
            account_id=acct_id, alert_type="events",
            vehicle_id="V1", vehicle_name="208",
            alert_subkey="braking",
        )
        # Different rows.
        assert r1["id"] != r2["id"]
        assert r1["occurrence_count"] == 1
        assert r2["occurrence_count"] == 1
        assert r1["alert_subkey"] == "followingDistance"
        assert r2["alert_subkey"] == "braking"

    @pytest.mark.asyncio
    async def test_same_subkey_increments_in_place(self, db):
        database, acct_id = db
        for _ in range(3):
            r = await database.upsert_alert_history(
                account_id=acct_id, alert_type="events",
                vehicle_id="V1", vehicle_name="208",
                alert_subkey="followingDistance",
            )
        # Single row, count = 3.
        assert r["occurrence_count"] == 3
        assert r["alert_subkey"] == "followingDistance"

    @pytest.mark.asyncio
    async def test_independent_counts_per_subkey(self, db):
        database, acct_id = db
        # Five tailgates, two harsh brakes — should NOT share a count.
        for _ in range(5):
            await database.upsert_alert_history(
                account_id=acct_id, alert_type="events",
                vehicle_id="V1", vehicle_name="208",
                alert_subkey="followingDistance",
            )
        for _ in range(2):
            await database.upsert_alert_history(
                account_id=acct_id, alert_type="events",
                vehicle_id="V1", vehicle_name="208",
                alert_subkey="braking",
            )
        fd = await database.get_active_alert_history(
            acct_id, "events", "V1", "followingDistance",
        )
        br = await database.get_active_alert_history(
            acct_id, "events", "V1", "braking",
        )
        assert fd["occurrence_count"] == 5
        assert br["occurrence_count"] == 2

    @pytest.mark.asyncio
    async def test_default_subkey_unchanged_for_faults(self, db):
        # Fault alerts don't pass a subkey — default '' must keep
        # the legacy single-bucket behavior so fault dedup is
        # unchanged from before the migration.
        database, acct_id = db
        for _ in range(4):
            r = await database.upsert_alert_history(
                account_id=acct_id, alert_type="fault",
                vehicle_id="V2", vehicle_name="301",
            )
        assert r["occurrence_count"] == 4
        assert r["alert_subkey"] == ""

    @pytest.mark.asyncio
    async def test_subkey_isolates_from_default_bucket(self, db):
        # An events row with subkey='followingDistance' must not
        # collide with one that has subkey='' (e.g. legacy/backfilled).
        database, acct_id = db
        r_legacy = await database.upsert_alert_history(
            account_id=acct_id, alert_type="events",
            vehicle_id="V3", vehicle_name="404",
            alert_subkey="",
        )
        r_typed = await database.upsert_alert_history(
            account_id=acct_id, alert_type="events",
            vehicle_id="V3", vehicle_name="404",
            alert_subkey="followingDistance",
        )
        assert r_legacy["id"] != r_typed["id"]
