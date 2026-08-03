"""Pure address / geofence classifiers for parking events.

No I/O, no telegram — safe to import from any layer.
"""

from __future__ import annotations

import re as _re


# Keywords that indicate a SAFE parking location (case-insensitive match)
_SAFE_KEYWORDS = [
    "truck stop", "truckstop", "rest area", "rest stop",
    "pilot", "flying j", "love's", "loves", "petro",
    "warehouse", "terminal", "depot", "yard", "dock",
    "distribution", "logistics", "parking lot", "parking area",
    "travel center", "travel plaza", "service plaza",
    "weigh station", "scales",
    "walmart", "costco", "home depot",  # common overnight lots
    "industrial", "commerce",
]

# Regex patterns for safe keywords that need word-boundary matching
_SAFE_REGEX = [
    _re.compile(r"\bta\b", _re.IGNORECASE),        # TA travel centers
    _re.compile(r"\bta-", _re.IGNORECASE),          # TA-Petro
]

# Keywords that indicate an UNSAFE parking location
_UNSAFE_KEYWORDS = [
    "highway", "interstate", "freeway", "beltway", "turnpike",
    "expressway", "parkway", "bypass",
    "shoulder", "ramp", "exit ramp", "on-ramp", "off-ramp",
    "overpass", "underpass", "bridge", "tunnel",
    "median", "roadside", "roadway",
    "interchange", "junction",
]

# Regex patterns for unsafe keywords that need word-boundary matching
_UNSAFE_REGEX = [
    _re.compile(r"\bI[\s-]\d", _re.IGNORECASE),     # I-95, I 70 (interstate)
    _re.compile(r"\bUS[\s-]\d", _re.IGNORECASE),     # US-40, US 54
    _re.compile(r"\bSR[\s-]\d", _re.IGNORECASE),     # SR-99, SR 392
    _re.compile(r"\b[A-Z]{2}\s\d{2,3}\b"),           # NM 392, CA 99, TX 45 (state routes)
]


def classify_parking_location(address: str) -> str:
    """Classify an address as 'safe', 'unsafe', or 'unknown'.

    Uses keyword scoring on the Samsara reverse-geocoded address.
    Both safe and unsafe keywords are checked, and the side with more
    matches wins.  This avoids false positives like
    "Pilot Travel Center, I-95, Exit 42" being classified as unsafe
    just because "I-95" appears in the address alongside "Pilot".
    """
    if not address:
        return "unknown"
    addr_lower = address.lower()

    safe_score = 0
    unsafe_score = 0

    for keyword in _SAFE_KEYWORDS:
        if _re.search(r"\b" + _re.escape(keyword) + r"\b", addr_lower):
            safe_score += 1
    for pattern in _SAFE_REGEX:
        if pattern.search(address):
            safe_score += 1

    for keyword in _UNSAFE_KEYWORDS:
        if _re.search(r"\b" + _re.escape(keyword) + r"\b", addr_lower):
            unsafe_score += 1
    for pattern in _UNSAFE_REGEX:
        if pattern.search(address):
            unsafe_score += 1

    if safe_score == 0 and unsafe_score == 0:
        return "unknown"
    if safe_score > 0 and unsafe_score == 0:
        return "safe"
    if unsafe_score > 0 and safe_score == 0:
        return "unsafe"
    # Both matched — safe POI names (truck stop, pilot, etc.) outweigh
    # generic road names that often appear in the same address line.
    if safe_score >= unsafe_score:
        return "safe"
    return "unsafe"


def get_parking_classification_reason(
    address: str, loc_class: str, ai_analysis: str = "",
) -> str:
    """Return a short explanation of why a parking event was classified."""
    addr_lower = (address or "").lower()
    if loc_class == "geofence":
        return "Inside a designated geofence"
    if loc_class == "safe":
        for kw in _SAFE_KEYWORDS:
            if kw in addr_lower:
                return f"Safe area — matched \"{kw}\""
        return "Classified as safe parking area"
    if loc_class == "unsafe":
        for kw in _UNSAFE_KEYWORDS:
            if kw in addr_lower:
                return f"Hazard keyword — matched \"{kw}\""
        for pat in _UNSAFE_REGEX:
            m = pat.search(address or "")
            if m:
                return f"Hazard pattern — matched \"{m.group()}\""
        if ai_analysis and "unsafe" in ai_analysis.lower():
            return "AI vision analysis — confirmed unsafe"
        return "Roadside / highway location"
    # unknown
    if ai_analysis:
        return "AI analysis inconclusive — manual review advised"
    return "Location unverified — AI review pending"


def _is_inside_any_geofence(
    lat: float, lng: float, geofences: list[dict],
) -> bool:
    """Check if coordinates fall inside any geofence."""
    from features.geofencing.geometry import is_inside_geofence
    for gf in geofences:
        if is_inside_geofence(lat, lng, gf):
            return True
    return False


def parse_ai_confidence(ai_text: str) -> str:
    """Extract the CONFIDENCE level from a structured AI response.

    The AI is instructed to reply with:
      CLASSIFICATION: SAFE or UNSAFE
      CONFIDENCE: HIGH, MEDIUM, or LOW
      REASON: ...

    Returns 'HIGH', 'MEDIUM', 'LOW', or '' if not found.
    """
    for line in ai_text.splitlines():
        if line.strip().upper().startswith("CONFIDENCE"):
            parts = line.split(":", 1)
            if len(parts) == 2:
                val = parts[1].strip().upper()
                for level in ("HIGH", "MEDIUM", "LOW"):
                    if level in val:
                        return level
    return ""


def classify_from_ai(ai_text: str) -> tuple[str, str]:
    """``(location_class, confidence)`` derived from a stored AI analysis.

    Extracted from ``features/parking/check.py``, where it lived inline
    inside the "first detection" branch — and that placement was the bug.
    The check loop runs every 30 minutes; on every pass after the first,
    ``ai_analysis`` already exists, so the branch was skipped, the class
    fell back to address keywords, and the upsert OVERWROTE the AI's
    verdict.  Measured before the fix: 1,973 rows stored ``unknown`` while
    carrying ``CLASSIFICATION: UNSAFE / CONFIDENCE: HIGH`` in the same
    row.  Because ``alert_level`` derives from the class, those stops were
    correctly identified as unsafe and then silently downgraded half an
    hour later, so they never escalated on duration.

    Being a pure function of the stored text is what makes the fix
    self-healing: a re-check re-derives the same verdict the AI already
    gave, with no backfill.

    Returns ``("", confidence)`` when the text carries no usable verdict —
    the caller decides whether that means keyword fallback (no analysis at
    all) or ``unknown`` (analysis present but inconclusive).  Those are
    different situations and only the caller knows which one it is in.
    """
    if not ai_text:
        return "", ""
    confidence = parse_ai_confidence(ai_text)
    low = ai_text.lower()

    # ORDER MATTERS: "unsafe" CONTAINS "safe", so the safe test must
    # exclude it explicitly.  Reversing these two branches silently
    # classifies every unsafe stop as safe.
    if "unsafe" not in low and "safe" in low:
        # Confident SAFE is the caller's cue to resolve and stop tracking;
        # a LOW-confidence safe stays under observation as unknown.
        return ("safe" if confidence in ("HIGH", "MEDIUM") else "unknown"), confidence
    if "unsafe" in low:
        return "unsafe", confidence
    return "", confidence
