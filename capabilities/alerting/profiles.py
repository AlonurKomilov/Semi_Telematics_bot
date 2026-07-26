"""Alert delivery profiles — every type DECLARES its lifecycle.

Ends the accidental two-tier split (owner decision 2026-07-26): the
send_alert types always had history + ack + personal channels while the
lite group-post types had none of it.  Now each type states what it
gets; the delivery helpers consult this instead of the entry point
deciding by accident.

  board    → an ``alert_history`` row: visible on the dashboard Alerts
             Board with occurrence counting (dismissable there via the
             normal Board ack).
  ackable  → the ✅ group/DM Acknowledge action + unacked reminders
             (severity still gates: only CRITICAL/WARNING ever ack).
  personal → subscribable in the notification matrix: Telegram DM /
             email / web-push toggles for the category's audience
             (opt-in — nobody receives until they enable it).

samsara_sync/system stay group-only housekeeping.  The heavy types'
profiles document the status quo; the lite trio gains board + personal
per the owner's choice ("Recommendations + Board rows" — not ackable:
a due task is done, not acked).
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class AlertProfile:
    board: bool
    ackable: bool
    personal: bool


_FULL = AlertProfile(board=True, ackable=True, personal=True)
_BOARD_FYI = AlertProfile(board=True, ackable=False, personal=True)
_GROUP_ONLY = AlertProfile(board=False, ackable=False, personal=False)

PROFILES: dict[str, AlertProfile] = {
    # send_alert family — unchanged status quo.
    "fault": _FULL, "faults": _FULL,
    "health": _FULL,
    "fuel": _FULL,
    "events": _FULL, "event": _FULL,
    "parking": _FULL,
    "camera": _FULL,
    "geofence": AlertProfile(board=True, ackable=False, personal=True),
    # lite trio — Board rows + personal channels, not ackable.
    "maintenance": _BOARD_FYI,
    "documents": _BOARD_FYI, "doc_expiry": _BOARD_FYI,
    "scorecard": _BOARD_FYI,
    # housekeeping — team group only.
    "samsara_sync": _GROUP_ONLY,
    "system": _GROUP_ONLY,
}


def get_profile(alert_type: str) -> AlertProfile:
    """Unknown types fail safe: group-only (no Board noise, no fanout)."""
    return PROFILES.get(alert_type, _GROUP_ONLY)
