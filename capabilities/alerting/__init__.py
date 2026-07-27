"""Alerting capability — split into focused submodules.

Re-exports the full public API via
``from capabilities.alerting import X``.
"""

# Register alert.* notification categories at boot (alerting -> notifications).
from capabilities.alerting import notification_categories  # noqa: F401,E402
from capabilities.alerting import spine_actions  # noqa: F401,E402
from capabilities.alerting import spine_quiet  # noqa: F401,E402

# ── pipeline (shared core) ───────────────────────────────────────
from capabilities.alerting.pipeline import (              # noqa: F401
    AlertSeverity,
    SYSTEM_USER_ID,
    COOLANT_SPNS,
    _COOLDOWN_HOURS,
    _warmup_done,
    build_alert_button_specs,
    send_alert,
    is_vehicle_suppressed,
)

# ── faults ───────────────────────────────────────────────────────
from features.vehicles.faults.alert import (       # noqa: F401
    check_new_faults,
    initialize_known_faults,
)

# ── health ───────────────────────────────────────────────────────
from features.vehicles.health.alert import (       # noqa: F401
    check_health_alerts,
    _CRITICAL_HEALTH,
    _WARNING_HEALTH,
)

# ── fuel ─────────────────────────────────────────────────────────
from features.vehicles.fuel.alert import (         # noqa: F401
    check_low_fuel,
    FUEL_CRITICAL_PCT,
)

# ── cameras ──────────────────────────────────────────────────────
from features.cameras.alert import (      # noqa: F401
    check_camera_alerts,
)

# ── escalation (ACK, auto-resolve) ───────────────────────────────
from capabilities.alerting.escalation import (            # noqa: F401
    _auto_resolve_vehicle_alerts,
    re_escalate_critical_alerts,
)

# ── events ───────────────────────────────────────────────────────
from features.events.alert import (                # noqa: F401
    check_events,
    _event_severity,
)

# ── parking (now its own top-level capability) ───────────────────
from features.parking import (                        # noqa: F401
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
from features.maintenance.service import (            # noqa: F401
    auto_create_maintenance_from_faults,
)

# ── DND delivery ─────────────────────────────────────────────────
from capabilities.alerting.dnd import (                   # noqa: F401
    deliver_dnd_alerts,
)
