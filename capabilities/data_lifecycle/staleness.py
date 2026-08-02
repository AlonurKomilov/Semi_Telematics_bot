"""The staleness vocabulary — one definition of "how old is this data".

``source_ts`` is the provider's own world-time for a row: when the
sensor sampled, when the event occurred.  It is the only honest basis
for freshness — our write times advance every tick whether or not the
world does, which is how a truck parked since May stayed
indistinguishable from one reporting this minute.

Readers ask two questions and nothing else:

  * :func:`data_age_minutes` — how old is this, or ``None`` for
    "age unknown" (a NULL ``source_ts``: caches, pre-contract rows).
  * :func:`is_stale` — is it older than the caller's SLA?  Unknown age
    is STALE by definition: the one thing an unknown age cannot be is
    provably fresh, and "unknown treated as fresh" is the exact
    confusion of silence with fact this contract ends.

Shared here (the data-lifecycle family) because every warehouse, every
reader facade and the future ingest watchdog need the same answer —
three private definitions of "fresh" is how the last three freshness
bugs stayed invisible.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any


def _parse(ts: Any) -> datetime | None:
    if not ts:
        return None
    text = str(ts).strip()
    if not text:
        return None
    try:
        if text.endswith("Z"):
            text = text[:-1] + "+00:00"
        parsed = datetime.fromisoformat(text)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed


def data_age_minutes(source_ts: Any, *, now: datetime | None = None) -> float | None:
    """Minutes since the provider last saw the world move, or ``None``
    when the row cannot say (NULL / unparseable ``source_ts``)."""
    parsed = _parse(source_ts)
    if parsed is None:
        return None
    current = now or datetime.now(timezone.utc)
    return max(0.0, (current - parsed).total_seconds() / 60.0)


def is_stale(source_ts: Any, sla_minutes: float,
             *, now: datetime | None = None) -> bool:
    """Whether the row is older than the caller's tolerance.

    Unknown age is stale: it cannot be proven fresh, and the callers of
    this function are deciding whether to trust a number or go get a
    live one.
    """
    age = data_age_minutes(source_ts, now=now)
    return True if age is None else age > sla_minutes


def freshest(*timestamps: Any) -> str | None:
    """The newest of several provider timestamps, for writers composing
    ``source_ts`` from multiple markers (location time, odometer time,
    engine-hours time).  Compares on the sortable ISO prefix so ``Z``
    and ``+00:00`` suffix styles mix safely; returns the ORIGINAL string
    so nothing is ever rewritten in passing."""
    best: str | None = None
    best_key = ""
    for ts in timestamps:
        if not ts:
            continue
        text = str(ts).strip()
        key = text[:19]
        if key > best_key:
            best_key, best = key, text
    return best
