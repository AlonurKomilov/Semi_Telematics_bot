"""One field, one name — across two interfaces that cannot import each other.

Inventory is added from two places: the dashboard's dialog
(``interfaces/dashboard/src/features/inventory/ItemDialog.tsx``) and the
browser panel's inline form
(``interfaces/browser_extension/src/features/inventory/InventoryPanel.tsx``).
They are separate builds with separate test runners and no shared module,
so nothing but this guard can see them disagree — and they did:

* the same field was ``Label`` on the dashboard and ``What it is`` in the
  panel (a question among nouns);
* the same field was ``Identifier`` on the dashboard and ``Serial or card
  number`` in the panel;
* both taught the reader with VENDOR names — "Samsara CM32", "EFS card" —
  in a product that also speaks to Motive and Datatruck, which made the
  form read as built for one customer's fleet.

The owner named it: the copy looked hardcoded to his account, when
Inventory has to hold anything a truck can carry.

This lives in the repo-root suite because it is exactly what that suite
is for: a rule neither package owns alone.
"""
from __future__ import annotations

import re

import pytest

from tests._repo import REPO

PANEL = REPO / "interfaces/browser_extension/src/features/inventory/InventoryPanel.tsx"
DIALOG = REPO / "interfaces/dashboard/src/features/inventory/ItemDialog.tsx"

#: The words a person reads for each stored field.  Change one and you
#: change it in both, or this fails — which is the point.
FIELD_NAMES = ("Category", "Name", "Serial or card number")

#: Brands the copy must not teach with.  A placeholder naming one
#: telematics or fuel-card vendor tells every account using another that
#: this screen was not built for them.  Examples belong to the DOMAIN
#: (a dashcam, a fuel card), never to a supplier.
VENDORS = ("Samsara", "Motive", "Datatruck", "EFS", "Comdata", "Geotab")


def _visible_text(path) -> str:
    """The file with its comments stripped — a rule about what a PERSON
    reads must not be satisfied, or broken, by a comment explaining it."""
    src = path.read_text()
    src = re.sub(r"/\*.*?\*/", " ", src, flags=re.S)
    src = re.sub(r"^\s*//.*$", " ", src, flags=re.M)
    return src


@pytest.mark.parametrize("field", FIELD_NAMES)
def test_both_surfaces_call_the_field_the_same_thing(field):
    for path in (PANEL, DIALOG):
        assert field in _visible_text(path), f"{path.name} does not say {field!r}"


def test_neither_surface_teaches_with_a_vendor_name():
    for path in (PANEL, DIALOG):
        text = _visible_text(path)
        for vendor in VENDORS:
            assert vendor not in text, (
                f"{path.name} names {vendor} in copy a customer reads. "
                "Examples belong to the domain, not to one supplier."
            )


def test_the_retired_names_do_not_come_back():
    """``Label`` and ``Identifier`` are our words for the columns, not the
    reader's.  They stay in the wire and in the database; they do not go
    back on screen."""
    for path in (PANEL, DIALOG):
        text = _visible_text(path)
        for retired in (">Label<", ">Identifier<", '"What it is"', "label=\"What it is\""):
            assert retired not in text, f"{path.name} brought back {retired!r}"
