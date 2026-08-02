"""Safety Events — the ACQUIRE half of the feature's data lifecycle.

The event log is this feature's own store (see ``retention.py`` for the
KEEP half and why its window is three years).  Declaring the ingest
here rather than leaving it hand-wired in the scheduler is what puts it
under the watchdog: a feed that stops delivering events now says so
instead of looking like a quiet week.
"""

from __future__ import annotations

from capabilities.data_lifecycle.ingest import IngestDataset, register_dataset


def _run_safety_events(account_id: int):
    from capabilities.integrations.samsara.sync import ingest_safety_events
    return ingest_safety_events(account_id)


register_dataset(IngestDataset(
    key="events.safety",
    owner="events",
    job_id="warehouse_safety_events",
    capability="safety_events",
    cadence={"interval_min": 5},
    run=_run_safety_events,
    tables=("safety_event_log",),
    freshness_sla_min=120,
    # A well-driven fleet generates no harsh-braking events for hours.
    # Silence here is good news, not a broken feed.
    expect_rows=False,
    label="Ingest safety / harsh-driving events",
))
