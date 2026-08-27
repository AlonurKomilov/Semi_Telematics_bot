"""A parking snapshot must never be captioned with another stop's data.

The object-store key for a parking map used to be ``{vehicle_id}.png``,
and ``ObjectStorage.put`` overwrites unconditionally — so every stop a truck
made wrote over the previous stop's image while each row kept its own
``map_image_path`` pointing at the now-shared file.  On live data one
vehicle's 116 events resolved to 4 files.

That was invisible while only a vehicle's CURRENT stop was viewable: the
newest event is the one whose bytes are on disk, so it always looked
right.  The per-vehicle history panel made every stale pairing reachable —
opening a three-month-old stop showed its real address and real AI
reasoning beside a photo of where the truck parked last night.  A wrong
map under a right caption is worse than no map, because nothing signals
the mismatch.

The key is now per event.  These tests pin the two halves of the fix:

  * NEW rows get unique paths and are never reported stale.
  * LEGACY rows that share a path with a newer event ARE reported stale,
    so the route can answer 410 instead of serving the wrong picture —
    without a backfill or migration over existing data.
"""

from __future__ import annotations

import os
import sys

os.environ.setdefault("ENCRYPTION_KEY", "")

import pytest_asyncio

# See test_parking_scope_consolidation.py — alerting must precede
# features.parking while the forward edges remain.
import capabilities.alerting  # noqa: F401

from adapters.storage import Database  # noqa: E402


@pytest_asyncio.fixture
async def map_acct(pg_db):
    """One account, one vehicle, three stops sharing a legacy map path."""
    database: Database = pg_db
    acct = await database.create_account("Snapshot Co")

    made = []
    for i, cls in enumerate(("unsafe", "unknown", "unsafe")):
        ev = await database.upsert_parking_event(
            account_id=acct.id,
            vehicle_id="v777",
            vehicle_name="777",
            company_code="AAA",
            latitude=40.0 + i,
            longitude=-74.0 - i,
            address=f"Shoulder {i}",
            first_stopped=f"2026-04-0{i + 1}T10:00:00",
            duration_hours=3.0 + i,
            location_class=cls,
        )
        # upsert keeps ONE unresolved row per vehicle, so resolve between
        # stops to get three distinct rows the way real history accrues.
        await database.resolve_parking_event(acct.id, "v777")
        made.append(ev["id"])

    return {"db": database, "account_id": acct.id, "ids": made}


class TestLegacySharedPath:
    """Rows written before the per-event key share one file."""

    async def test_older_events_report_stale(self, map_acct):
        db, acct, ids = map_acct["db"], map_acct["account_id"], map_acct["ids"]
        shared = "storage/AAA/parking-maps/v777.png"
        for ev_id in ids:
            await db.update_parking_alert_level(
                ev_id, "warning", account_id=acct, map_image_path=shared,
            )

        # Every event except the newest points at bytes a later stop wrote.
        for ev_id in ids[:-1]:
            assert await db.map_image_overwritten_by_newer(acct, ev_id) is True, (
                f"event {ev_id} shares its map file with a newer stop and "
                f"must be reported stale — serving it captions another "
                f"location with this event's address"
            )

    async def test_newest_event_is_not_stale(self, map_acct):
        """The newest holder of a shared path DOES own the bytes."""
        db, acct, ids = map_acct["db"], map_acct["account_id"], map_acct["ids"]
        shared = "storage/AAA/parking-maps/v777.png"
        for ev_id in ids:
            await db.update_parking_alert_level(
                ev_id, "warning", account_id=acct, map_image_path=shared,
            )

        assert await db.map_image_overwritten_by_newer(acct, ids[-1]) is False


class TestPerEventKey:
    """Rows written after the fix have unique paths."""

    async def test_unique_paths_are_never_stale(self, map_acct):
        db, acct, ids = map_acct["db"], map_acct["account_id"], map_acct["ids"]
        for ev_id in ids:
            await db.update_parking_alert_level(
                ev_id, "warning", account_id=acct,
                map_image_path=f"storage/AAA/parking-maps/v777-{ev_id}.png",
            )

        for ev_id in ids:
            assert await db.map_image_overwritten_by_newer(acct, ev_id) is False, (
                f"event {ev_id} has its own file; nothing overwrote it"
            )

    async def test_empty_path_is_not_stale(self, map_acct):
        """No snapshot at all is a 404 case, not a 410 one."""
        db, acct, ids = map_acct["db"], map_acct["account_id"], map_acct["ids"]
        assert await db.map_image_overwritten_by_newer(acct, ids[0]) is False


class TestTenantIsolation:
    """Staleness must not leak across accounts."""

    async def test_other_accounts_event_does_not_mark_stale(self, map_acct, pg_db):
        db, acct, ids = map_acct["db"], map_acct["account_id"], map_acct["ids"]
        shared = "storage/AAA/parking-maps/v777.png"
        await db.update_parking_alert_level(
            ids[0], "warning", account_id=acct, map_image_path=shared,
        )

        # A DIFFERENT account with a colliding path (same vehicle id and
        # company folder is entirely possible across tenants) must not make
        # this account's row look overwritten.
        other = await db.create_account("Other Co")
        other_ev = await db.upsert_parking_event(
            account_id=other.id, vehicle_id="v777", vehicle_name="777",
            company_code="AAA", latitude=41.0, longitude=-75.0,
            address="Elsewhere", first_stopped="2026-05-01T10:00:00",
            duration_hours=2.0, location_class="unsafe",
        )
        await db.update_parking_alert_level(
            other_ev["id"], "warning", account_id=other.id, map_image_path=shared,
        )

        assert await db.map_image_overwritten_by_newer(acct, ids[0]) is False
