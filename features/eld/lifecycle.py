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
    # Resolve, do not assume.  The eight datasets that predate the
    # resolver are Samsara-fed and keep the default; this one is the
    # whole point of the resolver, and leaving it on the default would
    # have gated a non-Samsara ELD on the Samsara integration row —
    # skipping the account before its own provider could be found.
    provider_id=None,
    # Five minutes is a compromise, and worth naming as one.  A duty
    # clock changes by the second, so any cadence is a staleness floor;
    # every surface therefore shows source_ts rather than implying the
    # reading is current.  Faster costs a provider call per account per
    # tick for a number that is only ever used to answer a human's
    # question.
    cadence={"interval_min": 5},
    run=_run_driver_hos,
    tables=("driver_hos_live",),
    # Fifteen minutes — two missed ticks plus a margin.  One missed poll
    # is a hiccup; three is a feed that stopped, and on this surface the
    # difference is the case where a dispatcher must go and look at the
    # ELD itself.  The feature's own service reasoned its way to 15 and
    # carried it as a private constant while this line said 30, so the
    # watchdog kept quiet for twice as long as the page had already
    # declared its readings stale.  One number, here, read everywhere.
    freshness_sla_min=15,
    # Unlike safety events, silence here is NOT good news.  A connected
    # ELD reports every driver on every poll, including the off-duty
    # ones, so zero rows means the feed stopped rather than that the
    # fleet is resting.
    expect_rows=True,
    label="Ingest driver hours of service",
))
