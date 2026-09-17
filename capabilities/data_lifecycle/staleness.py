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

THE NUMBER IS DECLARED ONCE, TOO
--------------------------------
"How old is too old" is answered by the dataset that produces the
rows — ``IngestDataset.freshness_sla_min`` in the ingest registry —
and by nothing else.  :func:`sla_minutes` is how a reader, a feature or
a surface asks for it.

Before it existed, one dataset carried FOUR answers.  Vehicle state:
the registry said 15, the reader facade said 30 (with a comment
claiming that matched the operator console — which reads the
registry, so it did not), the ELD feature said 15 for hours of service
where the registry said 30, and the dashboard's freshness dot fired at
60 for everything.  So the watchdog paged an operator at 15 minutes
while readers kept serving the same rows as current until 30, and a
40-minute-old engine state — 25 minutes past its own SLA — drew no
cue on screen.  Four numbers is not a policy; it is four places for
the policy to drift, and it did.
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


def sla_minutes(dataset_key: str) -> float:
    """The one tolerance for rows a dataset writes, in minutes.

    Read from the ingest registry — the dataset declares it, next to
    the cadence that makes it meaningful — so a reader falling back to
    the live provider, the watchdog paging an operator, and a freshness
    cue on a screen all fire on the SAME age.

    An unknown key RAISES.  Returning a default here would turn a typo
    into "this data is never stale", which is the silent failure this
    whole module exists to end; a KeyError at first use is the loud one.

    The import is deliberately inside the function: the ingest package
    imports this module at load time (its watchdog and its router both
    do), so importing it back at module level would be a cycle.
    """
    from capabilities.data_lifecycle.ingest import discover, get_dataset

    discover()
    ds = get_dataset(dataset_key)
    if ds is None:
        raise KeyError(
            f"no ingest dataset {dataset_key!r} — the staleness SLA is "
            "declared on the dataset that writes the rows, and this key "
            "names none"
        )
    return float(ds.freshness_sla_min)


def age_label(source_ts: Any, *, now: datetime | None = None) -> str:
    """"21d ago" — the same words the dashboard's ``formatAgoShort`` puts
    next to a value, for surfaces that render TEXT rather than a dot: a
    Telegram message cannot carry a warn dot, so it carries the age.

    Thresholds mirror the frontend exactly (seconds, minutes, hours,
    days under 30, then a calendar date — past a month WHEN reads
    better than how-long-ago), so a bot line and a screen never
    disagree about the same reading.  Unknown age is ``""``: a suffix
    that says nothing is honest; "0s ago" for a NULL is not.
    """
    parsed = _parse(source_ts)
    if parsed is None:
        return ""
    current = now or datetime.now(timezone.utc)
    sec = max(0, int((current - parsed).total_seconds()))
    if sec < 60:
        return f"{sec}s ago"
    minutes = sec // 60
    if minutes < 60:
        return f"{minutes}m ago"
    hours = minutes // 60
    if hours < 24:
        return f"{hours}h ago"
    days = hours // 24
    if days < 30:
        return f"{days}d ago"
    if parsed.year == current.year:
        return f"{parsed:%b} {parsed.day}"
    return f"{parsed:%b} {parsed.day}, {parsed.year}"


def age_suffix(source_ts: Any, sla_minutes_: float, *, now: datetime | None = None) -> str:
    """`` · 21d ago`` when the reading is past its dataset's tolerance,
    ``""`` when it is not — the text form of the freshness dot's rule:
    quiet while fresh, one small honest suffix once the number would
    mislead.  Unknown age counts as stale (Contract 2) but carries no
    words, since there is no age to state."""
    if not is_stale(source_ts, sla_minutes_, now=now):
        return ""
    label = age_label(source_ts, now=now)
    return f" · {label}" if label else ""


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
