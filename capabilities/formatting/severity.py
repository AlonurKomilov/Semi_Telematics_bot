"""Shared severity grammar for every alert formatter.

Why this exists
───────────────
Every alert type (fault, health, fuel, parking, camera, geofence …)
used to have its own header style — different emoji, different
dividers, different action wording.  A first-time reader had to learn
a new vocabulary per alert type to gauge urgency.

Option A grammar (one visual language everywhere):

    <BADGE> <SEVERITY> — <Alert Type>

    🚛 <Truck>  ·  📍 <Where>
    🏢 <Carrier>  ·  🕐 <When>

    <marker> <What>
          <plain-language impact>
          <technical detail in parens>

    💡 <action by severity>
    🔖 #<alert id>

This module is the SSoT for the badge, the body marker, and the
default action line per severity.  Each formatter imports
``SEVERITY_BADGE``, ``BODY_MARKER``, and ``default_action`` and uses
them verbatim — no per-formatter strings.
"""

from __future__ import annotations


# Five severity tiers visible to the user.  Internal pipeline severity
# names (``critical`` / ``warning`` / ``info``) map onto the first
# three; ``resolved`` and ``reminder`` are owned by the auto-resolve
# and re-escalation paths respectively and never leak into the regular
# alert dispatch.
SEVERITY_CRITICAL = "critical"
SEVERITY_WARNING  = "warning"
SEVERITY_INFO     = "info"
SEVERITY_RESOLVED = "resolved"
SEVERITY_REMINDER = "reminder"


# Glance-level badge — colored circle + word.  These are the FIRST
# thing a user reads; severity must be obvious before they parse a
# single body word.  Bold is applied at the call site so the colour
# stays neutral inside <b>…</b>.
SEVERITY_BADGE: dict[str, str] = {
    SEVERITY_CRITICAL: "🔴 CRITICAL",
    SEVERITY_WARNING:  "🟠 WARNING",
    SEVERITY_INFO:     "🔵 INFO",
    SEVERITY_RESOLVED: "🟢 RESOLVED",
    SEVERITY_REMINDER: "🟡 REMINDER",
}


# Per-DTC / per-condition marker used inside the body.  Pairs visually
# with the badge so the eye can tie "this body row belongs to the
# CRITICAL header" without re-reading the header.
BODY_MARKER: dict[str, str] = {
    SEVERITY_CRITICAL: "🔴",
    SEVERITY_WARNING:  "🔸",
    SEVERITY_INFO:     "🔵",
    SEVERITY_RESOLVED: "✅",
    SEVERITY_REMINDER: "⏱",
}


# Default action line by severity.  Formatters MAY override with a
# rule-specific recommendation (e.g. low-fuel passes a "Refuel within
# 20 mi · nearest station …" line), but every alert gets *some*
# 💡 Action line — that's the single biggest UX win identified in the
# audit.  Plain language, imperative voice, short.
_DEFAULT_ACTIONS: dict[str, str] = {
    SEVERITY_CRITICAL: "Pull over safely · stop driving · call maintenance",
    SEVERITY_WARNING:  "Schedule shop check · drive normally",
    SEVERITY_INFO:     "Review at next service",
    # Resolved alerts use a neutral closing line, not an imperative.
    SEVERITY_RESOLVED: "Condition cleared — no action needed",
    SEVERITY_REMINDER: "Still active — please follow up",
}


def normalize(severity: object) -> str:
    """Coerce an enum/string severity to a canonical lowercase key.

    Accepts ``AlertSeverity`` enum members (they're ``str`` subclass),
    raw strings (``"critical"``, ``"CRITICAL"``, ``"Warning"``), or
    anything ``str()`` can render.  Unknown values fall back to
    ``warning`` — the safe middle tier — so a stray severity never
    crashes the formatter.
    """
    key = str(severity).lower().strip()
    if key in SEVERITY_BADGE:
        return key
    return SEVERITY_WARNING


def badge(severity: object) -> str:
    return SEVERITY_BADGE[normalize(severity)]


def marker(severity: object) -> str:
    return BODY_MARKER[normalize(severity)]


def default_action(severity: object) -> str:
    return _DEFAULT_ACTIONS[normalize(severity)]
