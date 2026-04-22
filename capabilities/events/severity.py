"""Event severity — Single Source of Truth for safety-event classification.

Both the API safety routes and the bot events handler import from here so
they always produce identical severity labels.
"""

from __future__ import annotations

# Maps Samsara event-type names to a default severity bucket.
EVENT_SEVERITY: dict[str, str] = {
    "crash": "severe",
    "braking": "moderate",
    "harshTurn": "moderate",
    "rollingStop": "mild",
    "followingDistance": "mild",
    "laneDeparture": "mild",
    "acceleration": "mild",
}


def classify_event_severity(event_type: str, g_force: float) -> str:
    """Classify event severity from G-force magnitude or event type.

    G-force thresholds take precedence; the EVENT_SEVERITY lookup is the
    fallback for events that have no meaningful G-force reading (g_force==0).
    """
    if g_force >= 0.8:
        return "severe"
    if g_force >= 0.6:
        return "harsh"
    if g_force >= 0.4:
        return "moderate"
    if g_force > 0:
        return "mild"
    return EVENT_SEVERITY.get(event_type, "mild")
