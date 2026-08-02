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
