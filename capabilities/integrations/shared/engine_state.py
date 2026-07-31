"""The engine-state vocabulary, shared by every writer of that column.

Providers report the engine, not the truck: Samsara's ``On`` says the
engine is running and nothing more.  Whether the truck is *moving*
depends on road speed, and that distinction is the entire point of the
metric — an engine running while the truck sits still is idle time,
which burns fuel for no miles.  Collapsing ``On`` to "moving" would
book that time as driving and leave idle looking like zero.

Two paths write ``engine_state``: the live ingest and the history
backfill's resampler.  They resolve it here so a backfilled day and a
live day mean the same thing, and so the roll-ups — which count
``moving`` samples as drive minutes and ``idle`` samples as idle
minutes — get one consistent vocabulary underneath them.
"""

from __future__ import annotations

from typing import Any

# The stored vocabulary.  Roll-ups match on these exact strings.
MOVING = "moving"
IDLE = "idle"
OFF = "off"
UNKNOWN = ""

# What providers call it, lowercased, before speed is considered.
_PROVIDER_STATES = {"on": MOVING, "idle": IDLE, "off": OFF}

# Below this the truck is stationary as far as duty time is concerned;
# GPS jitter on a parked truck reports a fraction of a mile per hour.
_MOVING_SPEED_MPH = 1.0


def resolve_engine_state(raw: Any, speed_mph: Any = None) -> str:
    """Our engine-state value for one provider reading.

    ``raw`` is the provider's own state (Samsara: On / Idle / Off) and
    may be missing — some plans don't carry engine states at all, in
    which case speed alone decides between moving and unknown.  We
    never guess ``off``: a truck with no engine feed and no speed is
    unknown, and saying "off" would let the roll-ups count silence as
    a parked truck.
    """
    speed: float | None
    try:
        speed = float(speed_mph) if speed_mph is not None else None
    except (TypeError, ValueError):
        speed = None

    state = _PROVIDER_STATES.get(str(raw).strip().lower()) if raw else None

    if state == MOVING:
        # Engine running: moving only if it is actually going somewhere.
        # With no speed reading, trust the provider rather than invent
        # idle time.
        if speed is None:
            return MOVING
        return MOVING if speed >= _MOVING_SPEED_MPH else IDLE
    if state is not None:
        return state
    # No usable provider state — speed is all we have.
    if speed is not None and speed >= _MOVING_SPEED_MPH:
        return MOVING
    return UNKNOWN
