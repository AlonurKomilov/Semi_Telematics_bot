"""Default PTI checklist templates.

The actual templates live in the database (``pti_checklist_templates``
+ ``pti_checklist_template_items``) so fleets can edit them.  This
module is the **source of truth for new-account seeding** + the
"Reset to Standard DOT" button on the dashboard editor — both call
back into these lists to repopulate.

Item shape::

    {
        "item_key":       str,   # stable identifier, never localised
        "label":          str,   # display label (English default)
        "category":       str,   # groups items in the UI
        "requires_media": bool,  # at least one photo/video required
        "required":       bool,  # must be completed before submit
        "sort_order":     int,   # display order, 1-based
    }

The values below were chosen to match the FMCSA § 396 walkaround
inspection guidance + common fleet practice.  Photo requirements are
deliberately conservative — only the items where photographic
evidence has clear audit value (tires, fifth wheel coupling, body
damage, trailer condition).  Fleets can flip any item to
``requires_media=True`` from the dashboard template editor without
code changes.
"""

from __future__ import annotations


STANDARD_DOT_TRUCK_ITEMS: list[dict] = [
    {"item_key": "brakes_service",    "label": "Service brake response",                 "category": "brakes",     "requires_media": False, "required": True,  "sort_order":  1},
    {"item_key": "brakes_parking",    "label": "Parking brake hold",                     "category": "brakes",     "requires_media": False, "required": True,  "sort_order":  2},
    {"item_key": "tires_walkaround",  "label": "All tires — tread depth + sidewall",    "category": "tires",      "requires_media": True,  "required": True,  "sort_order":  3},
    {"item_key": "lights_all",        "label": "Headlights / brake / turn / hazard",     "category": "lights",     "requires_media": False, "required": True,  "sort_order":  4},
    {"item_key": "mirrors",           "label": "Mirrors — alignment + condition",       "category": "mirrors",    "requires_media": False, "required": True,  "sort_order":  5},
    {"item_key": "wipers",            "label": "Wipers + washer fluid",                  "category": "cab",        "requires_media": False, "required": True,  "sort_order":  6},
    {"item_key": "horn",              "label": "Horn",                                   "category": "cab",        "requires_media": False, "required": True,  "sort_order":  7},
    {"item_key": "fluids",            "label": "Oil / coolant / power steering",         "category": "fluids",     "requires_media": False, "required": True,  "sort_order":  8},
    {"item_key": "belts_hoses",       "label": "Belts + hoses",                          "category": "engine",     "requires_media": False, "required": True,  "sort_order":  9},
    {"item_key": "battery",           "label": "Battery + cable condition",              "category": "engine",     "requires_media": False, "required": True,  "sort_order": 10},
    {"item_key": "seatbelt",          "label": "Seatbelt + cab integrity",               "category": "cab",        "requires_media": False, "required": True,  "sort_order": 11},
    {"item_key": "emergency_kit",     "label": "Triangles / extinguisher / spare fuses", "category": "safety",     "requires_media": False, "required": True,  "sort_order": 12},
    {"item_key": "fifth_wheel",       "label": "Fifth wheel + coupling",                 "category": "coupling",   "requires_media": True,  "required": True,  "sort_order": 13},
    {"item_key": "suspension",        "label": "Suspension + air bags",                  "category": "chassis",    "requires_media": False, "required": True,  "sort_order": 14},
    {"item_key": "exhaust",           "label": "Exhaust + air intake",                   "category": "engine",     "requires_media": False, "required": True,  "sort_order": 15},
    {"item_key": "fuel_def",          "label": "Fuel + DEF level",                       "category": "fluids",     "requires_media": False, "required": True,  "sort_order": 16},
    {"item_key": "plate_visible",     "label": "License plate + DOT number visible",     "category": "exterior",   "requires_media": False, "required": True,  "sort_order": 17},
    {"item_key": "walkaround_damage", "label": "Walkaround damage — 4 angles",          "category": "walkaround", "requires_media": True,  "required": True,  "sort_order": 18},
]


STANDARD_DOT_TRAILER_ITEMS: list[dict] = [
    {"item_key": "tr_tires",        "label": "Trailer tires — tread + sidewall",      "category": "tires",      "requires_media": True,  "required": True,  "sort_order":  1},
    {"item_key": "tr_lights",       "label": "Trailer lights — brake / marker / turn", "category": "lights",    "requires_media": False, "required": True,  "sort_order":  2},
    {"item_key": "tr_doors",        "label": "Doors + latches + seal",                 "category": "exterior",   "requires_media": False, "required": True,  "sort_order":  3},
    {"item_key": "tr_floor",        "label": "Cargo floor + walls",                    "category": "interior",   "requires_media": False, "required": True,  "sort_order":  4},
    {"item_key": "tr_landing_gear", "label": "Landing gear",                           "category": "chassis",    "requires_media": False, "required": True,  "sort_order":  5},
    {"item_key": "tr_brakes",       "label": "Trailer brakes + air lines",             "category": "brakes",     "requires_media": False, "required": True,  "sort_order":  6},
    {"item_key": "tr_suspension",   "label": "Trailer suspension",                     "category": "chassis",    "requires_media": False, "required": True,  "sort_order":  7},
    {"item_key": "tr_reflectors",   "label": "Reflectors + conspicuity tape",          "category": "exterior",   "requires_media": False, "required": True,  "sort_order":  8},
    {"item_key": "tr_plate",        "label": "License plate visible",                  "category": "exterior",   "requires_media": False, "required": True,  "sort_order":  9},
    {"item_key": "tr_damage",       "label": "Walkaround damage — 4 angles",         "category": "walkaround", "requires_media": True,  "required": True,  "sort_order": 10},
]


VALID_INSPECTION_STATUSES: frozenset[str] = frozenset({
    "scheduled", "in_progress", "submitted", "reviewed", "revision_required",
})

VALID_ITEM_STATUSES: frozenset[str] = frozenset({
    "pending", "ok", "minor", "major", "oos", "na",
})

VALID_REVIEW_STATUSES: frozenset[str] = frozenset({
    "approved", "needs_service", "rejected", "revision_required",
})

# Items in these statuses count as a defect for ``defects_count``.
DEFECT_ITEM_STATUSES: frozenset[str] = frozenset({"minor", "major", "oos"})

# Item statuses that flip ``has_oos_defect`` to true.
OOS_ITEM_STATUSES: frozenset[str] = frozenset({"oos"})
