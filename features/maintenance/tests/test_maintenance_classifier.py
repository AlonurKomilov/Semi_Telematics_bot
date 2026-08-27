"""Smoke tests for ``classify_task_urgency``.

This classifier feeds the "Maintenance due N" badge on the shell
header + the Fleet Overview card.  Mismatch with the dashboard's
``dueSoonClassify`` in Tasks.tsx would show two different counts for
the same concept.  Tests cover each axis (date / mileage / engine
hours) and the closed-status short-circuit.
"""

from __future__ import annotations

from datetime import date, timedelta

from features.maintenance.service import classify_task_urgency


# ── Date axis ─────────────────────────────────────────────────────


def test_overdue_when_due_date_in_past():
    yesterday = (date.today() - timedelta(days=1)).isoformat()
    assert classify_task_urgency({
        "status": "pending", "due_date": yesterday,
    }) == "overdue"


def test_due_soon_when_due_date_within_7_days():
    in_5_days = (date.today() + timedelta(days=5)).isoformat()
    assert classify_task_urgency({
        "status": "pending", "due_date": in_5_days,
    }) == "due_soon"


def test_not_due_when_due_date_far_future():
    in_30_days = (date.today() + timedelta(days=30)).isoformat()
    assert classify_task_urgency({
        "status": "pending", "due_date": in_30_days,
    }) is None


def test_due_date_exactly_at_threshold_is_due_soon():
    in_7_days = (date.today() + timedelta(days=7)).isoformat()
    assert classify_task_urgency({
        "status": "pending", "due_date": in_7_days,
    }) == "due_soon"


# ── Mileage axis ──────────────────────────────────────────────────


def test_overdue_when_odometer_past_due_miles():
    assert classify_task_urgency({
        "status": "pending",
        "due_miles": 100_000,
        "last_odometer": 105_000,
    }) == "overdue"


def test_due_soon_when_under_5000_miles_remaining():
    assert classify_task_urgency({
        "status": "pending",
        "due_miles": 100_000,
        "last_odometer": 96_000,
    }) == "due_soon"


def test_not_due_when_50000_miles_remaining():
    assert classify_task_urgency({
        "status": "pending",
        "due_miles": 100_000,
        "last_odometer": 50_000,
    }) is None


# ── Engine-hours axis ────────────────────────────────────────────


def test_overdue_when_engine_hours_past_due():
    assert classify_task_urgency({
        "status": "pending",
        "due_engine_hours": 1_000,
        "last_engine_hours": 1_050,
    }) == "overdue"


def test_due_soon_when_under_100_engine_hours_remaining():
    assert classify_task_urgency({
        "status": "pending",
        "due_engine_hours": 1_000,
        "last_engine_hours": 950,
    }) == "due_soon"


# ── Status short-circuits ────────────────────────────────────────


def test_completed_status_returns_none_even_if_overdue_by_date():
    yesterday = (date.today() - timedelta(days=1)).isoformat()
    assert classify_task_urgency({
        "status": "completed", "due_date": yesterday,
    }) is None


def test_cancelled_status_returns_none():
    assert classify_task_urgency({
        "status": "cancelled",
        "due_miles": 100_000, "last_odometer": 200_000,
    }) is None


def test_explicit_overdue_status_wins():
    """A task already flipped to overdue by the scheduler counts as
    overdue regardless of axis math (the axes may not have data
    yet — e.g. snoozed but still over the line)."""
    assert classify_task_urgency({
        "status": "overdue",
    }) == "overdue"


# ── Multi-axis precedence ────────────────────────────────────────


def test_date_overdue_wins_even_if_miles_safe():
    yesterday = (date.today() - timedelta(days=1)).isoformat()
    assert classify_task_urgency({
        "status": "pending",
        "due_date": yesterday,
        "due_miles": 100_000, "last_odometer": 50_000,  # plenty of room
    }) == "overdue"


def test_miles_overdue_wins_even_if_date_safe():
    in_30_days = (date.today() + timedelta(days=30)).isoformat()
    assert classify_task_urgency({
        "status": "pending",
        "due_date": in_30_days,  # well within future
        "due_miles": 100_000, "last_odometer": 105_000,
    }) == "overdue"


def test_none_when_no_axis_has_data():
    """A pending task with no thresholds set is just scheduled,
    not due."""
    assert classify_task_urgency({"status": "pending"}) is None
