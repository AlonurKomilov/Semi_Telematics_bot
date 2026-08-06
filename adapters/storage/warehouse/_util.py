"""Shared warehouse-storage plumbing — constants, typing stub, tiny helpers."""

from __future__ import annotations

import hashlib
import logging
from datetime import datetime, timezone
from typing import TYPE_CHECKING, Any

logger = logging.getLogger(__name__)



# ── Velocity tuning constants ─────────────────────────────────────
#
# A "drive day" is a day where the vehicle moved meaningfully —
# 5 mi keeps the threshold robust against parking-lot crawl, fuel-
# island repositioning, and GPS jitter that can register as a
# fractional mile.  Below this we treat the day as idle (shop /
# weekend / spare) and exclude it from the median.
VELOCITY_DRIVE_DAY_MIN_MILES = 5.0

# Minimum days of OBSERVED data (regardless of whether they were
# drive or idle) before we'll surface a velocity at all.  Below this
# coverage threshold the projection is noise, and we'd rather show
# nothing than guess.  7 days = at least one full week including
# whatever the truck's weekly cycle is.
VELOCITY_MIN_COVERAGE_DAYS = 7

# Minimum DRIVE days (the median's actual sample size) before we
# trust the result.  3 drive days catches a typical Mon-Wed-Fri
# pattern; below this the median is too dependent on a single point.
VELOCITY_MIN_DRIVE_DAYS = 3

if TYPE_CHECKING:
    class _MixinBase:
        """Typing stub — attributes provided by the concrete DB class at runtime."""
        _db: Any

        def acquire(self) -> Any: ...
        def transaction(self) -> Any: ...
        async def read_all(self, sql: str, params: tuple = ()) -> list: ...
        async def read_one(self, sql: str, params: tuple = ()) -> Any: ...
        @staticmethod
        def _now() -> str: ...
else:
    _MixinBase = object


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _opt_float(value: Any) -> float | None:
    """Coerce a possibly-None / possibly-strings input into ``float | None``.

    Lets snapshot writers pass raw dict-from-JSON values without
    pre-cleaning every column.  Returns None for None, empty strings,
    or unparseable inputs so the column stays NULL rather than turning
    into 0.0 (which would silently corrupt downstream delta math).
    """
    if value is None or value == "":
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _dtc_id(dtc: dict[str, Any]) -> str:
    """Stable ID for a Samsara DTC.  Prefer the API's own id when
    present; otherwise hash on (spn, fmi) which uniquely identifies a
    J1939 trouble code.

    The live payload spells the pair ``spnId`` / ``fmiId`` (and carries
    no ``id`` at all) — reading only the bare spellings returned "" for
    every real DTC, so every one was skipped and the table stayed empty
    for its whole life.  Both spellings are read, like fuel and engine
    state before it: the provider's naming has drifted once per field.
    """
    sid = str(dtc.get("id") or dtc.get("samsara_id") or "").strip()
    if sid:
        return sid
    spn = dtc.get("spn") if dtc.get("spn") is not None else dtc.get("spnId")
    fmi = dtc.get("fmi") if dtc.get("fmi") is not None else dtc.get("fmiId")
    if spn is not None and fmi is not None:
        return f"spn:{spn}-fmi:{fmi}"
    return ""
