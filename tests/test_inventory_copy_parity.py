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
#: The panel's in-place correction form.  A THIRD place the same three
#: fields are named — and the newest, so the one most likely to drift.
ROWS = REPO / "interfaces/browser_extension/src/features/inventory/ItemRows.tsx"
#: Where the panel's two surfaces now GET those names.  They used to type
#: them out, which is how a rename could leave one form calling a column
#: something the other did not.
DATA = REPO / "interfaces/browser_extension/src/features/inventory/data.ts"

SURFACES = (PANEL, DIALOG, ROWS)
PANEL_SURFACES = (PANEL, ROWS)

#: The words a person reads for each stored field, READ OUT OF the panel's
#: own constant rather than repeated here — a list kept by hand in a test
#: is one more copy that can drift from the three it is policing.
FIELD_NAMES = tuple(
    re.findall(r"^\s*\w+: '([^']+)',", 
               DATA.read_text().split("export const FIELD_LABEL = {")[1].split("} as const")[0],
               re.M)
)


def test_the_panel_declares_the_names_this_guard_polices():
    """If FIELD_LABEL is emptied or renamed away, every other test here
    would pass vacuously against an empty tuple."""
    assert len(FIELD_NAMES) == 3, FIELD_NAMES
    assert "Serial or card number" in FIELD_NAMES

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
def test_the_dashboard_says_what_the_panel_says(field):
    """The two interfaces cannot import each other, so this is the only
    place their words can be compared."""
    assert field in _visible_text(DIALOG), f"ItemDialog.tsx does not say {field!r}"


@pytest.mark.parametrize("field", FIELD_NAMES)
def test_the_panel_never_re_types_a_name_it_has_a_constant_for(field):
    """The stronger half, and the newer one.

    Within the panel these three names came from two files that each
    spelled them out; a rename in one left the same column with two names.
    They now come from FIELD_LABEL, and re-typing a literal would quietly
    re-open the gap the constant closed.
    """
    for path in PANEL_SURFACES:
        assert f'"{field}"' not in _visible_text(path), (
            f"{path.name} types {field!r} out instead of using FIELD_LABEL"
        )


@pytest.mark.parametrize("path", PANEL_SURFACES, ids=lambda p: p.name)
def test_each_panel_form_takes_its_names_from_the_constant(path):
    assert "FIELD_LABEL." in _visible_text(path), (
        f"{path.name} names inventory fields without reading FIELD_LABEL"
    )


def test_neither_surface_teaches_with_a_vendor_name():
    for path in SURFACES:
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
    for path in SURFACES:
        text = _visible_text(path)
        for retired in (">Label<", ">Identifier<", '"What it is"', "label=\"What it is\""):
            assert retired not in text, f"{path.name} brought back {retired!r}"
