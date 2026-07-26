"""Catalog of forum topics for the Option-C routing layout.

This is the single source of truth for *which* topics get created
when ``/setupforum`` runs.  Adding a new alert type means appending
one row here AND extending ``ALERT_TYPE_KEYS`` in storage/models.py.

The icon colours are Telegram's six predefined topic colours (decimal
integers per Bot API spec).  We don't use custom emoji icons because
that would require the bot account to have Telegram Premium — these
defaults cover every case the dashboard ships today.
"""

from __future__ import annotations

from dataclasses import dataclass

# Telegram's six allowed icon colours (hex → decimal).
ICON_BLUE   = 0x6FB9F0
ICON_YELLOW = 0xFFD67E
ICON_PURPLE = 0xCB86DB
ICON_GREEN  = 0x8EEE98
ICON_RED    = 0xFF93B2
ICON_ORANGE = 0xFB6F5F


@dataclass(frozen=True)
class TopicSpec:
    """Specification for one forum topic.

    ``key`` matches ``ALERT_TYPE_KEYS`` so the storage row, the
    pipeline routing lookup, and this catalog all agree on the
    identifier.
    """
    key: str           # canonical alert_type
    name: str          # display name shown in Telegram
    icon_emoji: str    # leading emoji used in messages (informational)
    icon_color: int    # one of the ICON_* constants
    pinned: bool       # bot pins these topics so they sort to the top
    description: str   # one-line "what lands here" copy (for admin UI)


# Ordered so the topic list in Telegram renders in this sequence.
# Pinned topics float to the top regardless of order, so we still
# author them in the doc-canonical order below.
#
# Naming convention: PLAIN TEXT, no emoji in the topic name itself.
# Telegram auto-generates a coloured letter icon from the first
# character ("F" → Faults), which already acts as the visual marker
# next to the topic name.  An additional emoji in the name was just
# noise — admins asked for cleaner formatting matching how teams
# typically name forum topics.  The ``icon_emoji`` field below is
# still used inside the *message body* of alerts posted to the
# topic, so the visual cue is preserved where it actually helps.
FORUM_TOPIC_SPEC: tuple[TopicSpec, ...] = (
    TopicSpec("faults",      "Faults",            "🚨", ICON_RED,    True,
              "Engine DTC codes from the periodic fault check."),
    TopicSpec("health",      "Health",            "❤️", ICON_ORANGE, False,
              "Oil pressure, coolant, DEF and battery anomalies."),
    TopicSpec("fuel",        "Fuel",              "⛽", ICON_YELLOW, False,
              "Low-fuel warnings with hysteresis dedup."),
    TopicSpec("events",      "Safety Events",     "💥", ICON_RED,    True,
              "Crashes, harsh braking, harsh accel, speeding."),
    TopicSpec("camera",      "Cameras",           "🎥", ICON_PURPLE, False,
              "Dashcam obstruction, misalignment, AI vision flags."),
    TopicSpec("parking",     "Parking",           "🅿️", ICON_BLUE,   False,
              "Unsafe-parking (roadside, unknown >2 h)."),
    TopicSpec("geofence",    "Geofences",         "📍", ICON_GREEN,  False,
              "Enter / exit / extended-dwell events."),
    TopicSpec("scorecard",   "Scorecards",        "📊", ICON_BLUE,   False,
              "Daily driver / vehicle safety-score drops."),
    TopicSpec("maintenance", "Maintenance",       "🔧", ICON_ORANGE, True,
              "Overdue tasks plus pre-overdue warnings."),
    TopicSpec("documents",   "Driver Documents",  "🪪", ICON_YELLOW, False,
              "CDL, medical, MVR expiration alerts."),
    TopicSpec("system",      "Sync & System",     "🔄", ICON_GREEN,  False,
              "Samsara identity drift, integration warnings, re-escalation."),
)

# Convenience lookup table — handy from /setupforum + /repairforum.
TOPIC_BY_KEY: dict[str, TopicSpec] = {t.key: t for t in FORUM_TOPIC_SPEC}


def get_topic_spec(alert_type: str) -> TopicSpec | None:
    """Return the spec for one alert key, or None if unknown."""
    return TOPIC_BY_KEY.get(alert_type)
