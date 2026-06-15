"""Friendly text summaries for PTI rows.

Used by:
  * bot notifications (24h reminder, due-now, overdue, fleet digest)
  * dashboard tooltips + audit-log lines
  * AI tool descriptions

Keep all of these in one module so the wording stays consistent
across surfaces — a defect-summary string the driver reads in
Telegram should match what the fleet sees in the dashboard.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Optional


# ── Status label maps ──────────────────────────────────────────────


WORKFLOW_STATUS_LABELS: dict[str, str] = {
    "scheduled":         "Scheduled",
    "in_progress":       "In Progress",
    "submitted":         "Awaiting Review",
    "reviewed":          "Reviewed",
    "revision_required": "Revision Required",
}

REVIEW_STATUS_LABELS: dict[str, str] = {
    "approved":          "Approved",
    "needs_service":     "Needs Service",
    "rejected":          "Rejected",
    "revision_required": "Revision Required",
}

ITEM_STATUS_LABELS: dict[str, str] = {
    "pending": "Pending",
    "ok":      "OK",
    "minor":   "Minor Issue",
    "major":   "Major Issue",
    "oos":     "Out of Service",
    "na":      "N/A",
}

ITEM_STATUS_EMOJI: dict[str, str] = {
    "pending": "○",
    "ok":      "✅",
    "minor":   "🟡",
    "major":   "🟠",
    "oos":     "🛑",
    "na":      "➖",
}


# ── Helpers ────────────────────────────────────────────────────────


def _parse_iso(value: Optional[str]) -> Optional[datetime]:
    if not value:
        return None
    try:
        return datetime.fromisoformat(value)
    except (TypeError, ValueError):
        return None


def relative_due_label(due_by: Optional[str], now: Optional[datetime] = None) -> str:
    """Render ``due_by`` as a friendly relative string — "in 2 days" /
    "today" / "overdue 3d" — for bot notifications and chip badges."""
    target = _parse_iso(due_by)
    if not target:
        return ""
    now_dt = now or datetime.now(timezone.utc)
    delta = target - now_dt
    days = int(delta.total_seconds() // 86_400)
    hours = int(delta.total_seconds() // 3600)
    if delta.total_seconds() < 0:
        if -hours < 24:
            return f"overdue {-hours}h"
        return f"overdue {-days}d"
    if hours < 24:
        if hours <= 1:
            return "due now"
        return f"due in {hours}h"
    if days <= 1:
        return "due tomorrow"
    return f"due in {days}d"


# ── Friendly summaries ─────────────────────────────────────────────


def inspection_summary(
    inspection: dict, *, include_status: bool = True,
) -> str:
    """One-line: ``Truck 107 · 18 items · 2 defects · awaiting review``.

    Used for the bot fleet-digest, the dashboard list-row tooltip,
    and the audit-log entry.
    """
    parts: list[str] = []
    vname = inspection.get("vehicle_name") or "—"
    parts.append(f"Truck {vname}")
    # Defects line (only when there are any — keeps the happy path
    # uncluttered).
    defects = int(inspection.get("defects_count") or 0)
    if defects:
        if inspection.get("has_oos_defect"):
            parts.append(f"{defects} defect{'s' if defects != 1 else ''} · OOS")
        else:
            parts.append(f"{defects} defect{'s' if defects != 1 else ''}")
    # Status — wording differs for workflow vs review status because
    # a "Reviewed → Approved" row reads cleaner than "Reviewed".
    if include_status:
        review = (inspection.get("review_status") or "").lower()
        if review and review in REVIEW_STATUS_LABELS:
            parts.append(REVIEW_STATUS_LABELS[review])
        else:
            wf = (inspection.get("status") or "").lower()
            parts.append(WORKFLOW_STATUS_LABELS.get(wf, wf or "—"))
    return " · ".join(parts)


def reminder_body(inspection: dict, *, now: Optional[datetime] = None) -> str:
    """Driver-facing notification body — used by the 24h-before, due-now,
    and overdue reminders.  Doesn't include the deep-link button —
    that's added by the bot caller."""
    vname = inspection.get("vehicle_name") or "your truck"
    rel = relative_due_label(inspection.get("due_by"), now)
    if rel.startswith("overdue"):
        return f"Your weekly PTI for {vname} is {rel}. Please complete it."
    if rel == "due now":
        return f"Your weekly PTI for {vname} is due now."
    return f"Your weekly PTI for {vname} is {rel}."


def fleet_digest_line(counts: dict) -> str:
    """Single line for the daily fleet digest::

        3 PTIs awaiting review · 1 overdue · 2 marked Needs Service this week

    ``counts`` shape::

        {"awaiting_review": int, "overdue": int, "needs_service_7d": int}
    """
    parts: list[str] = []
    n = int(counts.get("awaiting_review") or 0)
    if n:
        parts.append(f"{n} PTI{'s' if n != 1 else ''} awaiting review")
    n = int(counts.get("overdue") or 0)
    if n:
        parts.append(f"{n} overdue")
    n = int(counts.get("needs_service_7d") or 0)
    if n:
        parts.append(f"{n} marked Needs Service this week")
    return " · ".join(parts) if parts else "No PTI activity"
