"""The time-grid rule — one flooring function for every sample writer.

A sample row answers two different time questions with two different
columns: its LABEL (``captured_at``) says *which slot of the grid is
this*, and ``source_ts`` (staleness.py) says *when the provider's
sensor actually saw it*.  Writers therefore floor the label to the
grain's slot boundary — 07:26:00, never 07:26:13 — and lose nothing,
because the honest moment rides in ``source_ts``.

A label taken from the raw fire time instead writes the scheduler's
boot history into the data: an interval trigger fires N seconds from
whenever the process started, so every restart mints a new second
offset (the minute tier accumulated ``:06 :36 :38 :16 :52 :31 :13`` —
one per deploy) and the grid stops being joinable across vehicles or
comparable across days.

Aggregate tiers don't use this helper — their labels are bucket starts
constructed by the builders (hour ``:00:00``, day ``YYYY-MM-DD``, week
its ISO Monday), which is the same rule at a coarser grain.
"""

from __future__ import annotations

from datetime import datetime, timezone


def floor_to_slot(ts: datetime, slot_seconds: int = 60) -> str:
    """Snap a timestamp down to its UTC slot boundary, returned as the
    canonical ISO string sample tiers use for ``captured_at``."""
    slot_min = max(1, slot_seconds // 60)
    aligned = ts.replace(
        minute=(ts.minute // slot_min) * slot_min, second=0, microsecond=0,
    )
    return aligned.astimezone(timezone.utc).isoformat(timespec="seconds")
