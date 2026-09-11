"""What a vehicle is SUPPOSED to be carrying.

Until this existed, Inventory could only answer "what has somebody
recorded on this truck?".  That makes two useful questions unanswerable:

* **Completeness** — a truck with three items looks identical whether it
  is carrying everything it should or has lost half of it.
* **A missing item that was never recorded** — the strongest case for the
  feature, and the one it could not make.  A dashcam nobody ever typed in
  cannot go `missing`; it simply is not there, and nothing notices.

The template answers both by DECLARING the expectation, so the absence of
a row becomes a fact rather than a silence.

Matching is by CATEGORY AND COUNT, never by label.  Categories are an
open vocabulary but a stable key; labels are free text somebody types
standing at a truck ("front dashcam", "dash cam", "DashCam 2"), and a
template keyed on those would drift the day it shipped.  So the row says
"this account expects two of `camera`", and the label it carries is for
the reader, not the matcher.

This module is the source of truth for SEEDING a new account and for a
"Reset to standard" action.  The live values are rows in
``inventory_expected_items``, which the account edits.
"""
from __future__ import annotations


# A trailer's template is deliberately EMPTY rather than absent: an empty
# template means "nothing is expected here", which is a real answer and
# keeps trailers out of every completeness count until somebody says
# otherwise.  Inventing a default for equipment we do not know the shape
# of would flag a whole fleet on our guess.
STANDARD_TRAILER_EXPECTED: list[dict] = []

# Required vs not is the dial that keeps this honest.  A toll transponder
# is normal to carry and normal not to — flagging every truck without one
# teaches people to ignore the flag, which costs more than the flag is
# worth.  It is declared, counted, and not enforced.
STANDARD_TRUCK_EXPECTED: list[dict] = [
    {"category": "camera",           "label": "Dashcam",          "quantity": 1, "required": True,  "sort_order": 1},
    {"category": "eld",              "label": "ELD",              "quantity": 1, "required": True,  "sort_order": 2},
    {"category": "fuel_card",        "label": "Fuel card",        "quantity": 1, "required": True,  "sort_order": 3},
    {"category": "toll_transponder", "label": "Toll transponder", "quantity": 1, "required": False, "sort_order": 4},
]

STANDARD_EXPECTED: dict[str, list[dict]] = {
    "truck": STANDARD_TRUCK_EXPECTED,
    "trailer": STANDARD_TRAILER_EXPECTED,
}

#: The vehicle types a template can be written for — the same two PTI
#: checklists are written for, because it is the same split of the same
#: registry.
EXPECTED_VEHICLE_TYPES = tuple(STANDARD_EXPECTED)


def standard_for(vehicle_type: str) -> list[dict]:
    """The shipped default for a vehicle type — a copy, never the list.

    Callers seed with it and the dashboard's reset button re-reads it; a
    caller that mutated the module-level list would change what every
    later account gets seeded.
    """
    return [dict(row) for row in STANDARD_EXPECTED.get(vehicle_type, [])]
