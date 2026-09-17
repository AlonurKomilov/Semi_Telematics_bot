"""Drivers — the ACQUIRE half of the feature's data lifecycle.

Per-driver daily efficiency is driver-grain data owned by this feature
(``retention.py`` holds the KEEP half).  Declared here so the hub runs
it and the watchdog can see it stop.

The identity fill is declared here too, and it is a different SHAPE of
job: not telemetry arriving, but a slow second opinion on people who
are already on the roster.  See ``spec_ingest`` for why it can only
fill and never create.
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
    #
    # Two days, because the table is DAY-grain: its newest row is
    # yesterday's total on the best day the feed ever has, so anything
    # under 24h would call a healthy feed stale on every read.  The
    # reader facade used to carry this as a private 2-day literal while
    # this line said 240 — one number here, read by ``sla_minutes``.
    freshness_sla_min=2 * 24 * 60,
    label="Ingest per-driver daily efficiency",
))


def _run_driver_spec(account_id: int):
    from features.drivers.spec_ingest import ingest_driver_spec
    return ingest_driver_spec(account_id)


register_dataset(IngestDataset(
    key="drivers.spec_fill",
    owner="drivers",
    job_id="driver_spec_fill",
    capability="driver_spec",
    # Resolve, do not assume — this feed exists BECAUSE more than one
    # provider can answer, and the point is that it survives any one of
    # them going dark.
    provider_id=None,
    # Six hours.  A licence number does not change; this fills gaps and
    # takes over when another integration stops, rather than watching
    # anything.  MINUTES — the scheduler reads ``cadence["interval_min"]``
    # and nothing else, so an ``interval_hour`` here is a KeyError at
    # boot that takes every scheduled job down with it.
    cadence={"interval_min": 360},
    run=_run_driver_spec,
    tables=("users",),
    freshness_sla_min=1440,
    # Silence is normal and GOOD here: a merge that changes nothing
    # means the roster and the provider already agree.  It is also what
    # an account with no links yet looks like, which is why zero rows
    # must never read as a stopped feed.
    expect_rows=False,
    label="Fill driver identity gaps from a linked integration",
))
