"""Alerting capability — split into focused submodules.

Re-exports the full public API via
``from capabilities.alerting import X``.
"""

# ── pipeline (shared core) ───────────────────────────────────────
from capabilities.alerting.pipeline import (              # noqa: F401
    AlertSeverity,
    SYSTEM_USER_ID,
    COOLANT_SPNS,
    _COOLDOWN_HOURS,
    _warmup_done,
    build_alert_keyboard,
    send_alert,
    is_vehicle_suppressed,
)

# ── faults ───────────────────────────────────────────────────────
from capabilities.alerting.faults import (                # noqa: F401
    check_new_faults,
    initialize_known_faults,
)

# ── health ───────────────────────────────────────────────────────
from capabilities.alerting.health import (                # noqa: F401
    check_health_alerts,
    _CRITICAL_HEALTH,
    _WARNING_HEALTH,
)

# ── fuel ─────────────────────────────────────────────────────────
from capabilities.alerting.fuel import (                  # noqa: F401
    check_low_fuel,
    FUEL_CRITICAL_PCT,
)

# ── cameras ──────────────────────────────────────────────────────
from capabilities.alerting.cameras import (               # noqa: F401
    check_camera_alerts,
)

# ── escalation (ACK, auto-resolve) ───────────────────────────────
from capabilities.alerting.escalation import (            # noqa: F401
    handle_alert_ack,
    handle_back_to_alert,
    _auto_resolve_vehicle_alerts,
    re_escalate_critical_alerts,
)

# ── events ───────────────────────────────────────────────────────
from capabilities.alerting.events import (                # noqa: F401
    check_events,
    _event_severity,
)

# ── parking (now its own top-level capability) ───────────────────
from capabilities.parking import (                        # noqa: F401
    check_unsafe_parking,
    classify_parking_location,
    get_parking_classification_reason,
    _is_inside_any_geofence,
    _render_parking_map,
    _get_ai_parking_analysis,
    _format_parking_alert,
)

# ── AI + maintenance ─────────────────────────────────────────────
from capabilities.alerting.ai_maintenance import (        # noqa: F401
    check_api_health,
)
from capabilities.maintenance.service import (            # noqa: F401
    auto_create_maintenance_from_faults,
)

# ── DND delivery ─────────────────────────────────────────────────
from capabilities.alerting.dnd import (                   # noqa: F401
    deliver_dnd_alerts,
)
