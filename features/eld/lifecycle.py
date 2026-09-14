"""ELD — the ACQUIRE half of the feature's data lifecycle.

Declaring the ingest here rather than hand-wiring it into the scheduler
is what puts the feed under the watchdog.  That matters more for this
dataset than for most: a stalled HOS feed and a fleet that is entirely
off duty look identical from the outside, and only one of them is news.
"""

from __future__ import annotations

from adapters.telematics.protocol import Capability
from capabilities.data_lifecycle.ingest import IngestDataset, register_dataset


def _run_driver_hos(account_id: int):
    from features.eld.ingest import ingest_driver_hos
    return ingest_driver_hos(account_id)


register_dataset(IngestDataset(
    key="eld.driver_hos",
    owner="eld",
    job_id="eld_driver_hos",
    capability=Capability.DRIVER_HOS,
    # Five minutes is a compromise, and worth naming as one.  A duty
    # clock changes by the second, so any cadence is a staleness floor;
    # every surface therefore shows source_ts rather than implying the
    # reading is current.  Faster costs a provider call per account per
    # tick for a number that is only ever used to answer a human's
    # question.
    cadence={"interval_min": 5},
    run=_run_driver_hos,
    tables=("driver_hos_live",),
    # Half an hour — six missed ticks — before silence is abnormal.
    freshness_sla_min=30,
    # Unlike safety events, silence here is NOT good news.  A connected
    # ELD reports every driver on every poll, including the off-duty
    # ones, so zero rows means the feed stopped rather than that the
    # fleet is resting.
    expect_rows=True,
    label="Ingest driver hours of service",
))
