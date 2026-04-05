"""bot.alerts — split into focused submodules.

Re-exports the full public API so existing ``from bot.alerts import X``
statements continue to work unchanged.
"""

# ── pipeline (shared core) ───────────────────────────────────────
from bot.alerts.pipeline import (              # noqa: F401
    AlertSeverity,
    ACK_WINDOW_MINUTES,
    MAX_REALERTS,
    SNOOZE_MINUTES,
    SNOOZE_OPTIONS,
    SYSTEM_USER_ID,
    COOLANT_SPNS,
    _COOLDOWN_HOURS,
    _warmup_done,
    build_alert_keyboard,
    send_alert,
    is_vehicle_suppressed,
)

# ── faults ───────────────────────────────────────────────────────
from bot.alerts.faults import (                # noqa: F401
    check_new_faults,
    initialize_known_faults,
)

# ── health ───────────────────────────────────────────────────────
from bot.alerts.health import (                # noqa: F401
    check_health_alerts,
    _CRITICAL_HEALTH,
    _WARNING_HEALTH,
)

# ── fuel ─────────────────────────────────────────────────────────
from bot.alerts.fuel import (                  # noqa: F401
    check_low_fuel,
    FUEL_CRITICAL_PCT,
)

# ── cameras ──────────────────────────────────────────────────────
from bot.alerts.cameras import (               # noqa: F401
    check_camera_alerts,
)

# ── escalation (ACK, re-alert, snooze) ───────────────────────────
from bot.alerts.escalation import (            # noqa: F401
    handle_alert_ack,
    handle_alert_snooze,
    handle_snooze_pick,
    handle_back_to_alert,
    _restore_alert_keyboard,
    _auto_resolve_vehicle_alerts,
    _is_alert_resolved,
    check_alert_realerts,
)

# ── events ───────────────────────────────────────────────────────
from bot.alerts.events import (                # noqa: F401
    check_events,
    _event_severity,
)

# ── parking ──────────────────────────────────────────────────────
from bot.alerts.parking import (               # noqa: F401
    check_unsafe_parking,
    classify_parking_location,
    get_parking_classification_reason,
    _is_inside_any_geofence,
    _render_parking_map,
    _get_ai_parking_analysis,
    _format_parking_alert,
)

# ── AI + maintenance ─────────────────────────────────────────────
from bot.alerts.ai_maintenance import (        # noqa: F401
    check_api_health,
    auto_create_maintenance_from_faults,
)

# ── DND delivery ─────────────────────────────────────────────────
from bot.alerts.dnd import (                   # noqa: F401
    deliver_dnd_alerts,
)
