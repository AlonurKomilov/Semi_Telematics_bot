"""Vehicle telemetry cascade — the first roll-up registered with the
Roll-up hub (``capabilities/rollups``).

Vehicle state arrives as a high-frequency live stream (the 60s ingestor
writes ``vehicle_state``).  We downsample it into a tiered history so each
consumer reads the cheapest tier that answers it: 5-min snapshot (7d) →
hourly (90d) → daily (730d).  The aggregation functions stay in the
warehouse ingestor (``capabilities/telemetry/ingestor``) — this file only
declares the cadences and registers them as a cascade, so the engine +
scheduler run them generically and a second high-frequency stream can
register the same way without touching the scheduler.

Cadences match exactly what the scheduler wired by hand before this was
hub-driven:
  * snapshot — every 5 min (interval, tz-agnostic)
  * hourly   — :05 every hour
  * daily    — 00:05 UTC.  The ``tz`` is REQUIRED: the scheduler runs in the
    server's local zone, so a bare local 00:05 fires BEFORE UTC midnight and
    the aggregator's "yesterday UTC" then resolves two days back, leaving the
    daily tier perpetually ~1 day stale.
"""

from __future__ import annotations

from capabilities.rollups.registry import (
    RollupCascade,
    RollupStage,
    register_cascade,
)
from capabilities.telemetry.ingestor import (
    aggregate_metrics_daily,
    aggregate_telemetry_hourly,
    snapshot_vehicle_state,
)

register_cascade(
    RollupCascade(
        "vehicle",
        (
            RollupStage(
                "warehouse_state_snapshot",
                {"interval_min": 5},
                snapshot_vehicle_state,
                "Capture the 5-min vehicle-state history",
            ),
            RollupStage(
                "warehouse_telemetry_hourly",
                {"cron": "5 * * * *"},
                aggregate_telemetry_hourly,
                "Roll 5-min snapshots into the hourly tier",
            ),
            RollupStage(
                "warehouse_metrics_daily",
                {"cron": "5 0 * * *", "tz": "UTC"},
                aggregate_metrics_daily,
                "Roll the hourly tier into the daily tier",
            ),
        ),
    )
)
