"""Unsafe parking detection — package split.

Public API (used by ``capabilities/alerting/__init__.py`` and tests):

* ``check_unsafe_parking``           — scheduled job
* ``classify_parking_location``      — keyword/regex classifier
* ``get_parking_classification_reason``
* ``_is_inside_any_geofence``        — geofence check helper
* ``_render_parking_map``            — satellite + road map renderer
* ``_save_parking_map``              — render + persist to object store
* ``_get_ai_parking_analysis``       — AI vision analysis
* ``_format_parking_alert``          — telegram alert formatter
* ``_send_parking_resolved``         — auto-resolve notifier
* ``parse_ai_confidence``            — extract HIGH/MEDIUM/LOW confidence
"""

from features.parking.classifier import (    # noqa: F401
    classify_parking_location,
    get_parking_classification_reason,
    parse_ai_confidence,
    _is_inside_any_geofence,
    _SAFE_KEYWORDS,
    _SAFE_REGEX,
    _UNSAFE_KEYWORDS,
    _UNSAFE_REGEX,
)
from features.parking.maps import (          # noqa: F401
    _render_parking_map,
    _save_parking_map,
)
from features.parking.ai_vision import (     # noqa: F401
    _get_ai_parking_analysis,
)
from features.parking.formatting import (    # noqa: F401
    _format_parking_alert,
    _send_parking_resolved,
)
from features.parking.check import (         # noqa: F401
    check_unsafe_parking,
)
