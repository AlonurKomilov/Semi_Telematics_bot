"""Sensitive-field masking — what the trail READER hides.

Values are stored in full (recovery requires them; never hash or strip
at write time).  The reader masks these fields unless the viewer holds
the owning feature's permission — the trail must not become a side
channel around per-feature gates (a user who cannot open Driver
Profiles must not read CDL numbers out of the audit page).

Which fields are sensitive is DECLARED by the owning feature in its
``activity.py`` (see registry.py) — this module only applies the mask.
``SENSITIVE_FIELDS`` remains as the legacy fallback map so masking
still works even if a declaration module failed to load.
"""

SENSITIVE_FIELDS: dict[str, frozenset[str]] = {
    # Fallbacks only — the SSOT lives in features/*/activity.py.
    "driver": frozenset({
        "cdl_number", "cdl_state", "cdl_class", "cdl_expires",
        "med_card_expires", "dob", "phone", "home_address",
        "driver_notes",
    }),
    "user": frozenset({
        "phone", "email", "password_hash", "home_address", "dob",
    }),
}

_MASK = "•••"


def _hidden_fields(entity_type: str) -> frozenset[str]:
    from .registry import sensitive_for
    declared = sensitive_for(entity_type)
    return declared or SENSITIVE_FIELDS.get(entity_type, frozenset())


def mask_changes(
    entity_type: str,
    changes: dict[str, dict],
    *,
    viewer_can_see: bool,
) -> dict[str, dict]:
    """Reader-side masking.  Field NAMES stay visible (the viewer may
    know THAT a value changed); the values hide behind the mask."""
    if viewer_can_see:
        return changes
    hidden = _hidden_fields(entity_type)
    if not hidden:
        return changes
    out: dict[str, dict] = {}
    for field, pair in changes.items():
        if field in hidden:
            out[field] = {"from": _MASK, "to": _MASK}
        else:
            out[field] = pair
    return out
