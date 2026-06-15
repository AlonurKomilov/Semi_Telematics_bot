"""Maintenance signal — reads tenant DB ``maintenance_tasks`` and scopes
PTI / other-task counts to the trucks each driver actually drove in
the window.

Driver↔truck mapping comes from the efficiency record's
``_vehicle_summaries`` (already loaded for the efficiency signal), so
no extra DB join is needed for .
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone


# Lower-cased substrings that classify a maintenance_tasks.task_type as
# a pre-trip inspection.  Adjust if tenants use different labels.
_PTI_TASK_KEYWORDS = ("pti", "pre_trip", "pre-trip", "pretrip")


def _is_pti(task_type: str) -> bool:
    t = (task_type or "").lower()
    return any(k in t for k in _PTI_TASK_KEYWORDS)


def _is_overdue(task: dict, now: datetime) -> bool:
    if task.get("status") == "overdue":
        return True
    due = task.get("due_date")
    if not due:
        return False
    try:
        return datetime.fromisoformat(due) < now and task.get("status") != "done"
    except Exception:
        return False


def _completed_in_window(task: dict, since: datetime) -> bool:
    if task.get("status") != "done":
        return False
    completed = task.get("completed_at")
    if not completed:
        return False
    try:
        return datetime.fromisoformat(completed) >= since
    except Exception:
        return False


def aggregate(tasks: list[dict], driver_vehicle_names: list[str], window_days: int) -> dict:
    """Count PTI / other tasks for the window restricted to *driver_vehicle_names*.

    Empty truck list ⇒ zero counts (driver had no Samsara-reported
    activity, so we don't penalise/credit them with someone else's
    truck's maintenance state).
    """
    if not driver_vehicle_names:
        return {"pti_completed": 0, "pti_overdue": 0, "other_overdue": 0}

    needles = {n.lower() for n in driver_vehicle_names if n}
    now    = datetime.now(timezone.utc)
    since  = now - timedelta(days=max(window_days, 1))

    pti_completed = pti_overdue = other_overdue = 0

    for t in tasks:
        truck = (t.get("vehicle_name") or "").strip().lower()
        if truck not in needles:
            continue
        pti = _is_pti(t.get("task_type", ""))
        if pti and _completed_in_window(t, since):
            pti_completed += 1
        if _is_overdue(t, now):
            if pti:
                pti_overdue += 1
            else:
                other_overdue += 1

    return {
        "pti_completed":  pti_completed,
        "pti_overdue":    pti_overdue,
        "other_overdue":  other_overdue,
    }
