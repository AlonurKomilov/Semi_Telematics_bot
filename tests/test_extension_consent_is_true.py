"""What the panel says it can do must be what it can do.

Three surfaces ask a person to hand a browser extension a token, and all
three used to promise less than the token grants:

* the panel's own connect screen closed with "and nothing else";
* the dashboard's consent list said "It cannot change anything in your
  account" — flatly false for anybody holding ``can_manage_inventory``,
  since the panel POSTs inventory-verify, -status, -add and -edit;
* the profile card said it "sees what it shows and nothing else".

A consent screen that overstates the limit is worse than one that states
a wider limit honestly: the person agreed to something narrower than what
happened.  This guard holds the three surfaces to the SCOPE, so widening
the token without widening the sentence fails here rather than in front
of a customer.

The root suite owns it because no package can see all three files.
"""
from __future__ import annotations

import re

import pytest

from tests._repo import REPO

PANEL = REPO / "interfaces/browser_extension/src/shell/Connect.tsx"
CONSENT = REPO / "interfaces/dashboard/src/pages/ExtensionConnect.tsx"
CARD = REPO / "interfaces/dashboard/src/features/profile/BrowserExtensionCard.tsx"
AUTH = REPO / "interfaces/api/auth.py"

SURFACES = (PANEL, CONSENT, CARD)

#: Sentences that claim the panel cannot write.  Every one of them was in
#: the tree; the last was found by an independent verifier AFTER a guard
#: scoped to three known files had passed.
LIES = (
    "cannot change anything in your account",
    "live, and nothing else",
    "sees what it shows and nothing else",
    "Read-only",
    "positions only",
)

#: Where a promise about this extension can be made.  A LIST of surfaces is
#: what let STORE.md — the PUBLIC, pre-install Chrome Web Store text, the
#: one a person reads before they can even see the panel — keep saying
#: "Read-only" while the first guard passed on the three files it knew.
#: So this sweeps instead of enumerating.
SWEPT = (
    "interfaces/browser_extension",
    "interfaces/dashboard/src/pages",
    "interfaces/dashboard/src/features/profile",
)
SWEPT_SUFFIXES = (".tsx", ".ts", ".md")
SKIP = ("node_modules", "/dist/", "/versions/", "/_archive/", ".test.")


def _swept_files():
    for root in SWEPT:
        for f in sorted((REPO / root).rglob("*")):
            if f.suffix not in SWEPT_SUFFIXES:
                continue
            if any(x in str(f) for x in SKIP):
                continue
            yield f


def test_no_file_anywhere_claims_the_panel_cannot_write():
    """The sweep, not the list.

    A guard that names its surfaces can only be as complete as whoever
    wrote it, and the most important surface here was the one nobody
    listed: the store description, read before installing, which the
    Chrome review compares against the code.
    """
    offenders = []
    for f in _swept_files():
        text = _visible(f)
        for lie in LIES:
            if lie in text:
                offenders.append(f"{f.relative_to(REPO)}: {lie!r}")
    assert offenders == [], (
        "These claim the panel cannot write. It can — can_manage_inventory is "
        "in EXTENSION_SCOPE and the panel POSTs inventory-verify, -status, "
        "-add and -edit:\n  " + "\n  ".join(offenders)
    )


def _visible(path) -> str:
    """The file with comments stripped — a promise a PERSON reads must not
    be satisfied by a comment explaining it."""
    src = path.read_text()
    src = re.sub(r"/\*.*?\*/", " ", src, flags=re.S)
    return re.sub(r"^\s*//.*$", " ", src, flags=re.M)


@pytest.mark.parametrize("path", SURFACES, ids=lambda p: p.name)
def test_no_surface_promises_it_cannot_write(path):
    for lie in ("cannot change anything in your account",
                "live, and nothing else",
                "sees what it shows and nothing else"):
        assert lie not in _visible(path), (
            f"{path.name} still promises the panel cannot write. It can: "
            "can_manage_inventory is in EXTENSION_SCOPE and the panel POSTs "
            "inventory-verify, -status, -add and -edit."
        )


@pytest.mark.parametrize("path", SURFACES, ids=lambda p: p.name)
def test_every_surface_names_the_inventory_it_can_reach(path):
    assert "inventory" in _visible(path).lower(), (
        f"{path.name} does not mention inventory at all, and the token opens it."
    )


def test_the_write_grant_the_copy_describes_is_the_one_in_the_scope():
    """If the scope stops granting writes, this copy becomes an
    understatement rather than a lie — and that is the moment to rewrite
    it, not months later."""
    assert '"can_manage_inventory"' in AUTH.read_text()


def test_the_two_verbs_the_copy_promises_are_still_shut():
    """Both consent surfaces say retiring and moving stay on the dashboard.
    EXTENSION_ROUTES is what makes that true."""
    src = AUTH.read_text()
    for shut in ("/extension/inventory-remove", "/extension/inventory-transfer"):
        assert shut not in src, f"{shut} is reachable now — the consent copy is stale"
