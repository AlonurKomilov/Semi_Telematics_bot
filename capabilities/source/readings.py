"""Whole-reading arbitration — for values that carry their own clock.

``merge_fields`` next door decides FIELD by field, and that is right for
facts about a static object: a VIN from one integration, a plate from
another, each true on its own.  A live reading is a different kind of
thing, in two ways the field merge cannot express.

  THE UNIT IS THE READING.  A position is a latitude, a longitude, a
  speed, a heading and the instant they were true, from one device.
  Taking the latitude from one provider and the timestamp from another
  produces a fix neither device reported; an odometer under a different
  provider's clock is a reading that never happened.  So a reading moves
  whole — values and clock together — or not at all.

  FRESHNESS IS A FACT, NOT A TIE-BREAK.  The configured order says whose
  reading the owner trusts; it does not say a reading from an hour ago
  beats one from a minute ago.  Among readings still inside the
  dataset's tolerance the order decides; once every candidate is past
  it, the newest wins regardless of order — a stale preferred device
  must not hide a live one.  The tolerance is the dataset's one
  staleness number (``sla_minutes``), never a second constant here.

  ZERO IS A VALUE.  ``is_unset`` treats ``0`` as "not provided", which is
  right for a model year and wrong for a speed — a parked truck reports
  0 mph and that IS the reading.  This module never consults
  ``is_unset``: a reading is absent only when it carries no value at
  all, and what "no value" means per group is the caller's rule, stated
  where the group is built (a (0, 0) fix, for instance).

``__newest__`` is a selectable RULE, not the default — the decision the
ELD entity made first: freshest-wins is the obvious default to an
engineer and the wrong one to an owner who has decided which device
they trust.  Manual never appears here; nobody hand-enters a GPS fix.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Mapping, Sequence

from capabilities.data_lifecycle.staleness import data_age_minutes, is_stale

from .engine import source_rank

#: The rule that may head an order: "whichever reported most recently".
NEWEST = "__newest__"


@dataclass(frozen=True)
class Reading:
    """One provider's observation of one group of values at one instant."""
    source: str
    values: Mapping[str, Any]
    time: str | None = None

    @property
    def present(self) -> bool:
        """Carries at least one value.  ``0`` counts; ``None``/``""`` do not."""
        return any(v is not None and v != "" for v in self.values.values())


def _age(r: Reading, now: datetime) -> float:
    """Minutes old; a reading with no usable clock sorts oldest."""
    age = data_age_minutes(r.time, now=now)
    return age if age is not None else float("inf")


def pick_readings(
    candidates: Mapping[str, Sequence[Reading]],
    *,
    precedence: Mapping[str, Sequence[str]],
    sla_min: float,
    now: datetime | None = None,
) -> dict[str, Reading]:
    """For each group, the reading that wins — or nothing, when no
    provider reported one.

    Per group: readings with no value never compete.  Among those
    inside ``sla_min`` the configured order decides (``NEWEST`` as the
    head of the order means newest-among-fresh); with none inside it,
    the newest wins whatever the order says.  Ties fall to the other
    criterion, so the answer is deterministic for a fixed input.
    """
    at = now or datetime.now(timezone.utc)
    chosen: dict[str, Reading] = {}
    for group, readings in candidates.items():
        live = [r for r in readings if r.present]
        if not live:
            continue
        order = tuple(precedence.get(group) or ())
        # ``is_stale`` rather than a hand-rolled compare, so "unknown age
        # IS stale" holds here exactly as it does for every reader.
        fresh = [r for r in live if not is_stale(r.time, sla_min, now=at)]
        if fresh:
            if order and order[0] == NEWEST:
                fresh.sort(key=lambda r: (_age(r, at), source_rank(r.source, order)))
            else:
                fresh.sort(key=lambda r: (source_rank(r.source, order), _age(r, at)))
            chosen[group] = fresh[0]
        else:
            live.sort(key=lambda r: (_age(r, at), source_rank(r.source, order)))
            chosen[group] = live[0]
    return chosen
