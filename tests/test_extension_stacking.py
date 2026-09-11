"""Nothing the map floats may cover the panel's own menus.

The panel is one narrow column: the header's feature switcher and
account menu drop DOWN over the map, and anything the map floats in a
corner is a sibling of the map container, competing in the same
stacking order.

This has broken twice.  The first time it was Leaflet's own panes
(200-1000) leaking into the panel, fixed by giving `.leaflet-container`
`isolation: isolate`.  The second time it was ours: a map control
copied `z-index: 500` from the dashboard — where the map is NOT
isolated and 500 is necessary — and covered the feature switcher, which
then looked like a button that did nothing.  The isolation rule could
not help, because the control sits OUTSIDE the thing being isolated.

The rule: a control floated over the map needs to beat a stacking
context whose own level is `auto`, which takes exactly 1.  It must stay
under every menu the shell opens.
"""
from __future__ import annotations

import re

from tests._repo import REPO

EXT = REPO / "interfaces" / "browser_extension" / "src"


def _menu_z() -> int:
    css = (EXT / "index.css").read_text(encoding="utf-8")
    m = re.search(r"\.menu\s*\{[^}]*z-index:\s*(\d+)", css)
    assert m, "index.css must give .menu a z-index — the shell's menus rely on it"
    return int(m.group(1))


def test_the_map_controls_stay_under_the_shell_menus():
    src = (EXT / "features" / "live-map" / "MapControls.tsx").read_text(encoding="utf-8")
    m = re.search(r"const CTL_Z = (\d+)", src)
    assert m, "MapControls must name its stacking level once, as CTL_Z"
    assert int(m.group(1)) < _menu_z(), (
        f"map controls at z-index {m.group(1)} would cover .menu at {_menu_z()} — "
        "the feature switcher opens over the map"
    )


def test_no_surface_floated_over_the_map_outranks_a_menu():
    """Every inline zIndex in the map feature, held to the same ceiling.

    Comments are stripped first: a guard that reads prose matches its
    own explanation, which has happened in this repo before.
    """
    ceiling = _menu_z()
    offenders: list[str] = []
    for path in (EXT / "features" / "live-map").rglob("*.tsx"):
        text = path.read_text(encoding="utf-8")
        text = re.sub(r"/\*.*?\*/", "", text, flags=re.S)
        text = re.sub(r"//.*$", "", text, flags=re.M)
        for value in re.findall(r"zIndex:\s*(\d+)", text):
            if int(value) >= ceiling:
                offenders.append(f"{path.name}: zIndex {value}")
    assert not offenders, (
        "these would cover the shell's menus (ceiling "
        f"{ceiling}): {offenders}"
    )


def test_the_map_container_keeps_leaflets_ladder_to_itself():
    """The other half of the pair, and the reason 1 is enough."""
    css = (EXT / "index.css").read_text(encoding="utf-8")
    assert re.search(r"\.leaflet-container\s*\{[^}]*isolation:\s*isolate", css), (
        "without `isolation: isolate` Leaflet's 200-1000 pane ladder "
        "competes with the panel's own menus"
    )
