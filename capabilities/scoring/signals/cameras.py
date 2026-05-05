"""Cameras signal — reads ``camera_check_history`` rows and aggregates
pass / fail counts to the trucks each driver actually drove in the
window.

Driver↔truck mapping comes from the efficiency record's
``_vehicle_names`` (same approach as :mod:`signals.maintenance`).
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone


# Camera-check field values we treat as "issues" — case-insensitive.
# Anything outside these sets is considered passing.
_BAD_OBSTRUCTIONS = {"partial", "blocked", "obstructed", "dirty", "covered"}
_BAD_ALIGNMENTS   = {"misaligned", "off", "tilted", "skewed"}
_BAD_QUALITY      = {"poor", "bad"}
_BAD_STATUSES     = {"fail", "failed", "error", "warning"}


def _is_failing(check: dict) -> bool:
    status      = (check.get("status") or "").lower()
    obstruction = (check.get("obstruction") or "").lower()
    alignment   = (check.get("alignment") or "").lower()
    quality     = (check.get("quality") or "").lower()
    if status in _BAD_STATUSES:
        return True
    if obstruction and obstruction != "none" and obstruction in _BAD_OBSTRUCTIONS:
        return True
    if alignment and alignment != "centered" and alignment in _BAD_ALIGNMENTS:
        return True
    if quality in _BAD_QUALITY:
        return True
    return False


def _checked_in_window(check: dict, since: datetime) -> bool:
    ts = check.get("checked_at")
    if not ts:
        return False
    try:
        return datetime.fromisoformat(ts) >= since
    except Exception:
        return False


def aggregate(checks: list[dict], driver_vehicle_names: list[str], window_days: int) -> dict:
    """Return per-driver camera signal slice.

    Empty truck list ⇒ zero counts (driver had no Samsara-reported
    activity, so we don't credit/penalise them with someone else's
    truck).
    """
    if not driver_vehicle_names:
        return {
            "checks_total":   0,
            "checks_passing": 0,
            "checks_failing": 0,
            "vehicles_with_failing_camera": 0,
        }

    needles = {n.lower() for n in driver_vehicle_names if n}
    since   = datetime.now(timezone.utc) - timedelta(days=max(window_days, 1))

    total = passing = failing = 0
    failing_trucks: set[str] = set()

    for c in checks:
        truck = (c.get("vehicle_name") or "").strip().lower()
        if truck not in needles:
            continue
        if not _checked_in_window(c, since):
            continue
        total += 1
        if _is_failing(c):
            failing += 1
            failing_trucks.add(truck)
        else:
            passing += 1

    return {
        "checks_total":              total,
        "checks_passing":            passing,
        "checks_failing":            failing,
        "vehicles_with_failing_camera": len(failing_trucks),
    }
