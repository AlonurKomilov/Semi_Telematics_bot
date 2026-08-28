"""The Synced-data "last sync" must read an INGEST-time column, not a
source/event/period time.

Otherwise a feed looks stale when its *source* is quiet even though the sync
is healthy — e.g. Vehicle health showed "15h ago" overnight because its
``captured_at`` is the parked truck's GPS time, not when we last pulled.
This guards against a feed being repointed back to a source/event column.
"""

from __future__ import annotations

import os

os.environ.setdefault("ENCRYPTION_KEY", "")

from adapters.telematics.catalog import PROVIDER_CATALOG

# Columns stamped at INGEST time: updated_at (overwrite upserts), ingested_at
# (roll-up / append), and the 5-min snapshot's captured_at (set to now()).
INGEST_TIME_COLS = {"updated_at", "ingested_at", "captured_at"}
# Source/event/period columns that must NOT back "last sync".
SOURCE_TIME_COLS = {"hour_utc", "day_utc", "day", "occurred_at"}


def test_samsara_feeds_read_ingest_time_freshness():
    feeds = {f.capability: f for f in PROVIDER_CATALOG["samsara"].feeds}

    for cap, f in feeds.items():
        assert f.ts_col not in SOURCE_TIME_COLS, (
            f"{cap}: freshness column {f.ts_col!r} is a source/event time"
        )
        assert f.ts_col in INGEST_TIME_COLS, (
            f"{cap}: freshness column {f.ts_col!r} is not an ingest-time column"
        )

    # The feeds that were stale-looking are pinned to their ingest column.
    assert feeds["vehicle_health"].ts_col == "updated_at"
    assert feeds["vehicle_faults"].ts_col == "updated_at"
    assert feeds["safety_events"].ts_col == "ingested_at"
    # "driver_efficiency" is the capability WIRE key; the table behind it
    # is driver_efficiency_day (adapters/telematics/catalog.py:206).
    assert feeds["driver_efficiency"].ts_col == "ingested_at"

    # The roll-up tiers are NOT customer feeds — they're warehouse plumbing
    # (operator console), so they must not appear on the integration card.
    assert "state_snapshot_history" not in feeds
    assert "telemetry_hourly" not in feeds
    assert "metrics_daily" not in feeds
