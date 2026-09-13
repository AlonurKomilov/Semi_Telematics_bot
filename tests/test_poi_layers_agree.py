"""The map's overlay layers are defined twice.  They must agree.

The dashboard and the browser panel each carry a layer registry, and
the copies are not cosmetic:

  * an ``id`` IS the ``type`` parameter of ``GET /map/pois`` — a typo is
    a 422, not a styling bug;
  * a ``color`` is what a marker MEANS.  Blue is truck parking on the
    dashboard, so it has to be truck parking in the panel, or the two
    screens describe the same road in two languages;
  * a brand chip a driver uses on one surface and cannot find on the
    other is the panel quietly being the lesser tool.

The brand list drifted once already: four chains — Ambest, Bosselman,
Maverick, Speedway — were trimmed from the panel on the theory that
sixteen chips would wrap across a 320px column.  That was reasoning
about the wrong number; chips render only for brands PRESENT IN THE
VIEW, which is three to five.  This test is why it cannot drift
silently again.
"""
from __future__ import annotations

import re

from tests._repo import REPO

DASH = (REPO / "interfaces" / "dashboard" / "src" / "features"
        / "live-map" / "poi" / "layers.ts")
EXT = (REPO / "interfaces" / "browser_extension" / "src" / "features"
       / "live-map" / "poiLayers.ts")


def _strip_comments(src: str) -> str:
    """A guard that reads prose matches its own explanation."""
    src = re.sub(r"/\*.*?\*/", "", src, flags=re.S)
    return "\n".join(re.sub(r"//.*$", "", line) for line in src.splitlines())


def _layers(path) -> dict[str, str]:
    """id → colour, for every layer in a registry."""
    src = _strip_comments(path.read_text(encoding="utf-8"))
    out: dict[str, str] = {}
    for m in re.finditer(
        r"id:\s*'([a-z_]+)',\s*label:\s*'[^']*',\s*color:\s*'(#[0-9a-fA-F]{6})'",
        re.sub(r"\s*\n\s*", " ", src),
    ):
        out[m.group(1)] = m.group(2).lower()
    return out


def _brands(path) -> set[str]:
    return set(re.findall(r"value:\s*'([^']+)'", _strip_comments(path.read_text(encoding="utf-8"))))


def test_both_surfaces_offer_the_same_layers():
    dash, ext = _layers(DASH), _layers(EXT)
    assert dash, "no layers parsed from the dashboard registry — the shape moved"
    assert set(ext) == set(dash), (
        "one surface offers a layer the other does not: "
        f"only dashboard={sorted(set(dash) - set(ext))}, "
        f"only panel={sorted(set(ext) - set(dash))}")


def test_a_colour_means_the_same_thing_on_both():
    dash, ext = _layers(DASH), _layers(EXT)
    differ = {k: (dash[k], ext[k]) for k in dash if k in ext and dash[k] != ext[k]}
    assert not differ, f"same layer, different marker colour: {differ}"


def test_every_brand_chip_exists_on_both():
    dash, ext = _brands(DASH), _brands(EXT)
    assert dash, "no brand chips parsed from the dashboard registry"
    assert ext == dash, (
        "a chain a driver can filter by on one surface and not the other: "
        f"only dashboard={sorted(dash - ext)}, only panel={sorted(ext - dash)}")
