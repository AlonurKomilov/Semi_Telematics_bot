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
#
# Re-exported LAZILY (PEP 562 module __getattr__), not with a top-level
# import, because the two packages are mutually recursive:
#
#   features/parking/__init__  ->  check / ai_vision / formatting
#     -> capabilities.alerting.*  ->  capabilities/alerting/__init__
#       -> from features.parking import check_unsafe_parking   ← half-built
#
# Entering from the alerting side happened to work, so the cycle stayed
# invisible for as long as nothing imported the parking package first.
# Then ``features/parking/retention.py`` was added: the retention hub's
# ``discover()`` imports each contributor by name, the parking import blew
# up, and the hub's "missing modules are skipped (logged), never fatal"
# policy swallowed it — parking retention silently never registered. A
# cycle that only costs an import order is tolerable; one that silently
# disables a data-lifecycle job is not.
#
# ``from capabilities.alerting import check_unsafe_parking`` still works
# (PEP 562 covers the from-import form) — interfaces/bot/app.py and
# interfaces/bot/callbacks/parking.py rely on it.
#
# SCOPE OF THE FIX, stated honestly: this removes the ALERTING-side edge,
# which is what the retention hub tripped over and what production boot
# order exercises.  The forward edges from features/parking/* INTO
# capabilities.alerting still exist, so a cold import of a parking
# submodule before capabilities.alerting can still hit a partially
# initialized module — which is why tests/test_parking_scope_
# consolidation.py imports capabilities.alerting first and says so.
# Closing those remaining edges is a separate change; do not read this
# block as "the cycle is gone".
_PARKING_EXPORTS = frozenset({
    "check_unsafe_parking",
    "classify_parking_location",
    "get_parking_classification_reason",
    "_is_inside_any_geofence",
    "_render_parking_map",
    "_get_ai_parking_analysis",
    "_format_parking_alert",
})


def __getattr__(name: str):
    """Resolve the parking re-exports on first access, not at import."""
    if name in _PARKING_EXPORTS:
        import features.parking as _parking
        return getattr(_parking, name)
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")

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
