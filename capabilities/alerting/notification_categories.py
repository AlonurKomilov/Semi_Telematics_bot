"""Alerting registers its notification categories with the notification
service — the first source to do so.

This is the one-way seam in action: alerting imports notifications
(never the reverse).  Each alert type becomes a BROADCAST
``alert.<type>`` category whose audience is the existing role-eligibility
rule, so ``dispatch()`` can drop a recipient whose current role isn't
eligible without the notification core ever knowing what an "alert" is.

Importing this module (done from ``capabilities.alerting`` at boot)
performs the registration.
"""

from __future__ import annotations

from functools import partial

from capabilities.alerting.relevance import (
    ALERT_TYPE_REQUIRED_PERM,
    perms_allow_alert_type,
    role_can_receive_alert,
)
from adapters.storage.models import Role
from capabilities.permissions.roles import ROLE_PERMISSIONS


def _extra_type_audience(alert_type: str, role) -> bool:
    """Role → static-default FeatureSet → permission check (ANY-OF)."""
    try:
        perms = ROLE_PERMISSIONS.get(Role(str(role)))
    except ValueError:
        return False
    return perms is not None and perms_allow_alert_type(perms, alert_type)
from capabilities.notifications.categories import (
    BROADCAST,
    TARGETED,
    NotificationCategory,
    register_category,
)

_LABELS = {
    "faults": "Engine faults",
    "health": "Vehicle health",
    "fuel": "Fuel & DEF",
    "geofence": "Geofence entry/exit",
    "events": "Safety events",
    "camera": "Camera issues",
    "parking": "Unsafe parking",
}


def register_alert_categories() -> None:
    for atype in ALERT_TYPE_REQUIRED_PERM:
        register_category(NotificationCategory(
            key=f"alert.{atype}",
            label=_LABELS.get(atype, atype.replace("_", " ").title()),
            kind=BROADCAST,
            # audience(role) -> role_can_receive_alert(role, alert_type=atype)
            audience=partial(role_can_receive_alert, alert_type=atype),
        ))
    # Profile-upgraded lite types (profiles.py — Board rows + personal
    # channels, owner decision 2026-07-26).  Audience is PERMISSION-
    # derived (CATEGORY_EXTRA_PERMS — the same SSOT the Board's type
    # gate reads), never a hard-coded role set: if an account's matrix
    # takes documents access away from a role, its subscription toggle
    # and delivery follow automatically at the static-defaults tier
    # (same known per-account-override limitation as every category —
    # notifications.md §9d).
    for atype, label in (
        ("documents", "Driver documents"),
        ("scorecard", "Scorecard drops"),
    ):
        register_category(NotificationCategory(
            key=f"alert.{atype}",
            label=label,
            kind=BROADCAST,
            audience=partial(_extra_type_audience, atype),
        ))


register_alert_categories()


def register_targeted_alert_notices() -> None:
    """Personal alert-adjacent NOTICES (targeted: one affected person,
    opt-out) — delivered via notify_user, muteable in the
    account-activity preferences section.

    • alert.document_expiry — "your CDL/medical/DQF is expiring".
      Audience: drivers (the only role these target), so the mute
      toggle doesn't clutter other roles' preference pages.
    • alert.shift_report — the morning shift-handoff summary + PDF for
      users with a quiet window configured.  Any role can have one.
    """
    register_category(NotificationCategory(
        key="alert.document_expiry",
        label="Document expiry (your documents)",
        kind=TARGETED,
        audience=lambda role: str(role) == "driver",
    ))
    register_category(NotificationCategory(
        key="alert.shift_report",
        label="Shift handoff report",
        kind=TARGETED,
    ))
    register_category(NotificationCategory(
        key="alert.api_error",
        label="Integration API errors",
        kind=TARGETED,
        audience=lambda role: str(role) in ("owner", "admin"),
    ))
    register_category(NotificationCategory(
        key="alert.device_identity",
        label="Vehicle device changes",
        kind=TARGETED,
        audience=lambda role: str(role) in ("owner", "admin"),
    ))
    register_category(NotificationCategory(
        key="alert.bot_health",
        label="Telegram bot delivery problems",
        kind=TARGETED,
        audience=lambda role: str(role) in ("owner", "admin"),
    ))


register_targeted_alert_notices()
