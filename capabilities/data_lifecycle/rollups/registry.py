"""Time-series roll-up registry — the Roll-up hub.

Some data arrives as a high-frequency live stream that we keep at several
resolutions so each consumer reads the cheapest tier that answers it:
live → 5-min → hourly → daily.  Building those tiers is *downsampling* — a
generic mechanism (cadence, per-account fan-out, idempotent upsert) that is
the same regardless of WHAT is rolled up.  The *aggregation* (which columns,
summed/maxed/averaged how) is irreducibly feature-specific.

So this hub owns only the generic mechanism: a feature registers a
``RollupCascade`` (its ordered stages, each with a cadence and a per-account
run function) in its own ``rollups.py``, and the engine fans each stage
across active accounts on its schedule — the same "collect feature
contributions" pattern the Retention/Alerting hubs use.  The aggregation
stays in the owning feature; this hub knows nothing about vehicles.

Vehicle telemetry is the first (today, only) registered cascade; a second
high-frequency stream later (e.g. per-minute driver location) registers its
own cascade in one file and the scheduler picks it up — no plumbing changes.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Awaitable, Callable

# (account_id) -> rows written.  The run function fetches its own tenant DB,
# computes its window, and upserts its tier; the engine has already
# established the per-account scope via the shared active-account fan-out.
RollupRun = Callable[[int], Awaitable[int]]


@dataclass(frozen=True)
class RollupStage:
    """One tier of a cascade — e.g. "roll 5-min snapshots into the hourly
    tier" — with the cadence it runs on and the function that does it.

    ``cadence`` is a small spec the scheduler translates to a trigger:
      * ``{"interval_min": 5}``                 — every 5 minutes (tz-agnostic)
      * ``{"cron": "5 * * * *"}``               — crontab string
      * ``{"cron": "5 0 * * *", "tz": "UTC"}``  — crontab + timezone

    ``job_id`` is the scheduler id, kept stable so the Scheduler page and the
    operator console (which key on it) don't move when a cascade is re-homed
    onto this hub.
    """

    job_id: str
    cadence: dict
    run: RollupRun
    label: str = ""
    # WHAT this stage builds and at WHICH resolution — the two words
    # kept separate on purpose.  "vehicle.timeline" names the stream;
    # the grain vocabulary is live / minute / hour / day / week.  The
    # physical table an implementation writes to is a detail; these two
    # fields are how the watchdog, the operator console and the docs
    # speak about a stage without knowing the room layout.
    stream: str = ""
    grain: str = ""


@dataclass(frozen=True)
class RollupCascade:
    """An ordered set of stages downsampling one stream (e.g. vehicle state
    → 5-min → hourly → daily).  ``name`` is the stream/owner id."""

    name: str
    stages: tuple[RollupStage, ...]


_CASCADES: dict[str, RollupCascade] = {}


def register_cascade(cascade: RollupCascade) -> None:
    _CASCADES[cascade.name] = cascade


def all_cascades() -> tuple[RollupCascade, ...]:
    return tuple(_CASCADES.values())


def all_stages() -> tuple[RollupStage, ...]:
    return tuple(s for c in _CASCADES.values() for s in c.stages)
