"""Drivers — the ACQUIRE half of the feature's data lifecycle.

Per-driver daily efficiency is driver-grain data owned by this feature
(``retention.py`` holds the KEEP half).  Declared here so the hub runs
it and the watchdog can see it stop.
"""

from __future__ import annotations

from capabilities.data_lifecycle.ingest import IngestDataset, register_dataset


def _run_driver_efficiency(account_id: int):
    from capabilities.integrations.samsara.sync import (
        ingest_driver_efficiency_day,
    )
    return ingest_driver_efficiency_day(account_id)


register_dataset(IngestDataset(
    key="drivers.efficiency",
    owner="drivers",
    job_id="warehouse_driver_efficiency",
    capability="driver_efficiency_day",
    cadence={"interval_min": 60},
    run=_run_driver_efficiency,
    tables=("driver_efficiency_day",),
    # Windowed provider totals with no per-record world-time: the rows
    # are ageless by nature, so the watchdog judges this one on whether
    # it writes at all, not on how old it looks.
    freshness_sla_min=240,
    label="Ingest per-driver daily efficiency",
))
