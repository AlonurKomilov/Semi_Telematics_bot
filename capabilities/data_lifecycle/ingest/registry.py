"""Ingest registry — datasets declare themselves, or they can die silently.

The ACQUIRE engine of the data-lifecycle family (rollups is BUILD,
retention is KEEP).  Five datasets were found dead with nobody noticing —
an engine-state column empty for the life of its table, a fault store at
zero rows platform-wide, a geofence cache 404ing hourly for months, a
registry frozen 47 days, a fuel average dropped at persist — and every
one died the same way: a hand-wired job whose failure had no generic
observer.  A dataset that is DECLARED can be watched by machinery that
knows nothing about it; a dataset that is only wired can only be watched
by someone re-reading the wiring.

Each owning feature declares its datasets in its own ``lifecycle.py``
(the same file that already declares its roll-up cascade and retention),
and the engine runs them, records every run, and gives the watchdog one
uniform surface to judge.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Awaitable, Callable

# (account_id) -> rows written.  The run function fetches its own tenant
# DB and does one provider fetch + one upsert; the engine has already
# decided WHOM to run it for and records what happened.
IngestRun = Callable[[int], Awaitable[int]]


@dataclass(frozen=True)
class IngestDataset:
    """One provider-fed dataset, declared by its owning feature.

    ``job_id`` keeps the legacy scheduler id verbatim — the operator
    console and the scheduler snapshot key on those ids, and renaming
    wire identifiers costs migrations for zero benefit.

    ``freshness_sla_min`` is the watchdog's contract: how old the
    dataset's ``source_ts`` may get before silence stops being normal.
    ``expect_rows`` is False for sparse feeds (faults exist only when
    trucks are broken) so an honest zero is not an alarm.
    """

    key: str                    # "vehicles.state" — domain noun, never a role word
    owner: str                  # feature id per docs/FEATURES.md
    job_id: str                 # legacy scheduler id, verbatim
    capability: str             # adapters.telematics.protocol.Capability value
    cadence: dict               # same spec as RollupStage ({"interval_min": 1} / {"cron": ...})
    run: IngestRun
    tables: tuple[str, ...]     # physical tables written (watchdog + RLS audit read this)
    freshness_sla_min: int
    expect_rows: bool = True
    label: str = ""


_DATASETS: dict[str, IngestDataset] = {}


def register_dataset(dataset: IngestDataset) -> IngestDataset:
    existing = _DATASETS.get(dataset.key)
    if existing is not None and existing is not dataset:
        raise ValueError(f"ingest dataset {dataset.key!r} registered twice")
    _DATASETS[dataset.key] = dataset
    return dataset


def all_datasets() -> tuple[IngestDataset, ...]:
    return tuple(_DATASETS[k] for k in sorted(_DATASETS))


def get_dataset(key: str) -> IngestDataset | None:
    return _DATASETS.get(key)
