"""Alerting's quiet-hours + digest registrations with the spine.

Part of the alert-DM migration (docs/architecture/alert-dm-migration.md):
the spine owns the deferral MECHANICS (queue under the 'quiet' cadence,
hourly flush); THIS module hands it the two domain pieces it must stay
blind to:

  1. the quiet-hours RULE — a thin wrapper over ``dnd.is_user_dnd_active``
     (THE SSOT: personal opt-out tier, assigned schedule, legacy
     quiet_start/end override, role work-hours), keyed by user id;
  2. the ``alert`` digest renderer — a flushed batch of deferred alerts
     reads like the shift-report's off-hours section (type icons +
     counts + the critical lines), not a generic bullet list.

Importing this module (from ``capabilities.alerting`` at boot, like
``notification_categories`` / ``spine_actions``) performs both
registrations.  Nothing defers until spine-delivered alert DMs ship
(the fanout flip); the legacy DND path (``dnd.py`` + ``dnd_alert_queue``)
keeps serving the live pipeline until the legacy-path cleanup retires
it.  The full
shift-handoff REPORT (summary + PDF from ``get_shift_handoff_data``)
stays a domain job — it aggregates far more than the deferred queue and
is a report, not a flush.
"""

from __future__ import annotations

import logging
from collections import Counter

from capabilities.notifications.channels import NotificationContent
from capabilities.notifications.quiet_hours import (
    register_quiet_hours_provider,
)
from capabilities.notifications.service import register_digest_renderer

logger = logging.getLogger(__name__)

# Same icon vocabulary as the shift-report summary (dnd._build_summary_text).
_TYPE_ICONS = {
    "faults": "⚠️", "fault": "⚠️", "health": "🏥", "fuel": "⛽",
    "events": "🚨", "geofence": "📍", "camera": "📷", "parking": "🅿️",
    "maintenance": "🔧",
}


async def _alert_quiet_rule(account_id: int, user_id: int) -> bool:
    """Spine-shaped adapter over the DND SSOT: fetch the user, delegate
    to ``is_user_dnd_active``.  Unknown user → not quiet (fail-open,
    deliver)."""
    from infra.platform import get_platform_db, get_tenant_db

    from capabilities.alerting.dnd import is_user_dnd_active
    user = await get_platform_db().get_user(user_id)
    if user is None or getattr(user, "account_id", None) != account_id:
        return False
    tenant = await get_tenant_db(account_id)
    return await is_user_dnd_active(user, tenant)


def _render_alert_digest(items: list[dict], cadence: str) -> NotificationContent:
    """Deferred-alert batch → one 'while you were off shift' summary.

    ``items`` are digest-queue rows (category ``alert.<type>``, one-line
    summary, severity).  Counts per type up top; the CRITICAL lines are
    spelled out (they only sit here when a recipient's quiet window
    outlived them — rare, but never bury a critical in a count)."""
    n = len(items)
    by_type = Counter(
        str(i.get("category", "")).split(".", 1)[-1] for i in items)
    parts = [
        f"{_TYPE_ICONS.get(t, '🔔')} {t.replace('_', ' ')}: {c}"
        for t, c in by_type.most_common()
    ]
    body_lines = ["  •  ".join(parts)] if parts else []
    crit = [i for i in items if i.get("severity") == "critical"]
    if crit:
        body_lines.append("")
        for c in crit[:10]:
            body_lines.append(f"🔴 {c.get('summary', '')}")
        if len(crit) > 10:
            body_lines.append(f"…and {len(crit) - 10} more critical")
    sev = "critical" if crit \
        else ("warning" if any(i.get("severity") == "warning" for i in items)
              else "info")
    return NotificationContent(
        title=f"While you were off shift — {n} alert{'s' if n != 1 else ''}",
        body="\n".join(body_lines),
        category="digest",
        severity=sev,
    )


def register_alert_quiet_hooks() -> None:
    register_quiet_hours_provider(_alert_quiet_rule)
    register_digest_renderer("alert", _render_alert_digest)


register_alert_quiet_hooks()
