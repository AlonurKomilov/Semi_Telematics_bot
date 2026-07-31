"""Sensitive-field registry — what the trail READER masks.

Values are stored in full (recovery requires them; never hash or strip
at write time).  The reader masks these fields unless the viewer holds
the owning feature's permission — the trail must not become a side
channel around per-feature gates (a user who cannot open Driver
Profiles must not read CDL numbers out of the audit page).

Keyed by ``entity_type``.  Adopting a feature that stores PII =
adding its fields here in the SAME commit.
"""

SENSITIVE_FIELDS: dict[str, frozenset[str]] = {
    # Driver PII (FMCSA/DOT data) — gate: the drivers feature's perms.
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
    hidden = SENSITIVE_FIELDS.get(entity_type)
    if not hidden:
        return changes
    out: dict[str, dict] = {}
    for field, pair in changes.items():
        if field in hidden:
            out[field] = {"from": _MASK, "to": _MASK}
        else:
            out[field] = pair
    return out
