"""The ingest registry — declared datasets cannot die silently.

Five datasets died the same quiet death: a hand-wired job whose
failure had no generic observer.  The registry makes acquisition a
DECLARATION — owner, cadence, tables, freshness SLA — so one engine
runs them, one ledger records them, and one watchdog can judge them
without knowing what any of them mean.
"""

from __future__ import annotations

import pytest

from capabilities.data_lifecycle.ingest import (
    IngestDataset,
    all_datasets,
    discover,
    get_dataset,
    register_dataset,
)


def test_discovery_yields_the_vehicles_state_dataset():
    discover()
    ds = get_dataset("vehicles.state")
    assert ds is not None
    # The wire invariants: legacy job id verbatim, domain-noun key.
    assert ds.job_id == "warehouse_vehicle_state"
    assert ds.capability == "vehicle_state"
    assert ds.freshness_sla_min > 0
    assert "vehicle_state" in ds.tables


def test_double_registration_of_a_key_is_refused():
    async def _noop(account_id):
        return 0

    probe = IngestDataset(
        key="test.probe", owner="vehicles", job_id="test_probe",
        capability="vehicle_state", cadence={"interval_min": 5},
        run=_noop, tables=("vehicle_state",), freshness_sla_min=60,
    )
    register_dataset(probe)
    try:
        assert get_dataset("test.probe") is probe
        clone = IngestDataset(
            key="test.probe", owner="vehicles", job_id="test_probe_2",
            capability="vehicle_state", cadence={"interval_min": 5},
            run=_noop, tables=("vehicle_state",), freshness_sla_min=60,
        )
        with pytest.raises(ValueError):
            register_dataset(clone)
    finally:
        from capabilities.data_lifecycle.ingest import registry as _reg
        _reg._DATASETS.pop("test.probe", None)


def test_datasets_sort_stably_for_the_scheduler():
    discover()
    keys = [d.key for d in all_datasets()]
    assert keys == sorted(keys)


@pytest.mark.asyncio
async def test_ledger_folds_runs_into_day_rows(pg_db):
    acct = 48
    await pg_db.record_ingest_run(acct, "vehicles.state", 95)
    await pg_db.record_ingest_run(acct, "vehicles.state", 0)
    await pg_db.record_ingest_run(acct, "vehicles.state", 96)

    cur = await pg_db._db.execute(
        "SELECT runs, rows_sum, last_rows FROM ingest_runs "
        "WHERE account_id = ? AND dataset_key = ?",
        (acct, "vehicles.state"))
    rows = await cur.fetchall()
    assert len(rows) == 1, "one row per dataset per day, however many runs"
    runs, rows_sum, last_rows = rows[0]
    assert (runs, rows_sum, last_rows) == (3, 191, 96)


# The scheduler ids the operator console and the scheduler-jobs snapshot
# key on.  Renaming one silently orphans its history and its row in the
# console, so the mapping is pinned rather than trusted.
_EXPECTED_JOB_IDS = {
    "vehicles.state":         "warehouse_vehicle_state",
    "vehicles.health":        "warehouse_vehicle_health",
    "vehicles.faults":        "warehouse_vehicle_faults",
    "vehicles.weather":       "warehouse_fleet_weather",
    "vehicles.efficiency":    "warehouse_fleet_efficiency",
    "events.safety":          "warehouse_safety_events",
    "drivers.efficiency":     "warehouse_driver_efficiency",
    "geofencing.definitions": "warehouse_geofence_definitions",
}


def test_every_provider_ingest_is_declared_with_its_legacy_job_id():
    discover()
    got = {d.key: d.job_id for d in all_datasets()}
    assert got == _EXPECTED_JOB_IDS


def test_job_ids_are_unique():
    discover()
    ids = [d.job_id for d in all_datasets()]
    assert len(ids) == len(set(ids))


def test_sparse_feeds_declare_themselves_sparse():
    """A fleet with nothing broken, or driving well, reports nothing —
    those datasets must not be judged on row counts."""
    discover()
    sparse = {d.key for d in all_datasets() if not d.expect_rows}
    assert sparse == {"vehicles.faults", "events.safety"}


def test_no_hand_wired_ingest_jobs_remain_in_the_scheduler():
    """The registry is the single source of what ingests run."""
    from pathlib import Path

    text = Path("interfaces/bot/scheduler.py").read_text()
    # The watchdog's own job function shares the prefix; everything else
    # named job_ingest_* would be a hand-wired ingest.
    stray = [
        line.strip() for line in text.splitlines()
        if "job_ingest_" in line and "watchdog" not in line
    ]
    assert not stray, f"hand-wired ingest jobs still registered: {stray}"
