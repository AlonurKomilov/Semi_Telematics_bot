"""Geofencing — the ACQUIRE half of the feature's data lifecycle.

The definitions table is a CACHE of the provider's geofences, and its
rows carry no world-time (an address edited in 2024 is not two-year-old
data), so the watchdog judges it on delivery, not on age.  It earns its
declaration the hard way: this ingest spent months writing zero rows
while hammering a URL that never existed, and nothing said a word.
"""

from __future__ import annotations

from capabilities.data_lifecycle.ingest import IngestDataset, register_dataset


def _run_geofences(account_id: int):
    from capabilities.integrations.samsara.sync import (
        ingest_geofence_definitions,
    )
    return ingest_geofence_definitions(account_id)


register_dataset(IngestDataset(
    key="geofencing.definitions",
    owner="geofencing",
    job_id="warehouse_geofence_definitions",
    capability="geofence_definitions",
    cadence={"interval_min": 60},
    run=_run_geofences,
    tables=("geofence_definitions",),
    freshness_sla_min=1440,
    label="Sync geofence definitions",
))
