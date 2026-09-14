"""A weighbridge is not a weigh station.

The owner opened the map over Edwardsville, switched Weigh Stations on,
and found a CAT Scale at a Pilot Travel Center.  That is not a weigh
station in any sense a driver uses the word: a DOT station is a stop you
MUST make, free and enforced; a CAT Scale is a service you MAY buy.

The cause is OpenStreetMap's, not ours.  ``amenity=weighbridge`` means
only "there is a scale here" and is used for both.  Measured on our own
4,500 imported points (2026-09-14), 2,160 of them — 48% — carried a
truck-stop chain as operator or brand: Love's 513, Pilot 370, Flying J
197, TA 173, CAT Scale 157.  Half the layer answered a question nobody
had asked, which is how a map stops being trusted.

The tags needed to tell them apart were already in the reply, so the
split costs the mirrors nothing: ONE fetch, filed under TWO layers.
"""
from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest
import pytest_asyncio

from adapters.storage.poi_directory import PoiDirectoryMixin
from features.live_map.poi import importer
from features.live_map.poi.layers import (
    POI_OVERPASS_QUERIES,
    SERVED_LAYERS,
    fetch_layer_for,
    weigh_station_layer,
)

pytestmark = pytest.mark.asyncio


@pytest_asyncio.fixture
async def store(db):
    m = PoiDirectoryMixin()
    m._db = db._db          # type: ignore[attr-defined]
    return m


def _pt(osm_id: int, lat: float, lng: float, name: str = "", **props):
    return {"osm_type": "node", "osm_id": osm_id, "lat": lat, "lng": lng,
            "name": name, "props": {"amenity": "weighbridge", **props}}


# ── which layer a point belongs to ────────────────────────────────────


def test_a_chain_operated_scale_is_not_a_weigh_station():
    for operator in ("CAT Scale", "Love's Travel Stop", "Pilot Travel Center",
                     "Flying J Travel Center", "TA", "Petro Stopping Center",
                     "Road Ranger", "Kwik Trip", "ONE9 Travel Center"):
        got = weigh_station_layer(_pt(1, 0, 0, operator=operator))
        assert got == "truck_scale", f"{operator!r} filed as {got}"


def test_a_government_operator_is_a_weigh_station():
    for operator in ("FDOT", "California Highway Patrol",
                     "Illinois Department of Transportation",
                     "New Jersey Motor Vehicle Commission",
                     "Colorado Department of Transportation"):
        got = weigh_station_layer(_pt(1, 0, 0, operator=operator))
        assert got == "weigh_station", f"{operator!r} filed as {got}"


def test_the_chain_match_respects_word_boundaries():
    """`TA` is a real chain and a substring of a great many place names.

    A plain `in` test files every MONTANA and INTERSTATE scale under
    Truck Scales — which is the wrong half, and the half a driver may
    not skip.
    """
    for operator in ("Montana DOT", "Montana Highway Patrol",
                     "Interstate Weigh Station", "Atalanta Inspection"):
        got = weigh_station_layer(_pt(1, 0, 0, operator=operator))
        assert got == "weigh_station", f"{operator!r} filed as {got}"


def test_an_inspection_station_is_enforcement_whatever_the_brand_says():
    """`amenity=vehicle_inspection` is enforcement by definition — 515 of
    them in the data against 2 carrying a chain, so the tag decides
    before the operator does."""
    p = _pt(1, 0, 0, amenity="vehicle_inspection", operator="Pilot")
    assert weigh_station_layer(p) == "weigh_station"


def test_no_evidence_means_the_layer_that_cannot_be_missed():
    """2,142 of the imported points carry no operator at all, and the two
    mistakes are not equal: an extra pin on the DOT layer costs one look,
    a mandatory stop filed under Truck Scales costs a violation."""
    assert weigh_station_layer(_pt(1, 0, 0)) == "weigh_station"
    assert weigh_station_layer({"name": "", "props": {}}) == "weigh_station"


# ── the name-only branch, which the query invites ─────────────────────
#
# The fourth clause of the weigh-station query matches
# `highway=motorway_junction` nodes on NAME ALONE — "Weigh Station",
# "Port of Entry", "Inspection Station".  Those nodes carry no amenity,
# no operator and no brand, so the tag tests above them decide nothing
# and the name is all there is.
#
# US place names then do the damage: Pilot Mountain, Pilot Knob, Pilot
# Rock and Pilot Point are all real.  A chain test alone files "Pilot
# Mountain Weigh Station" under Truck Scales, which is the direction
# that costs a driver a violation, and it bypasses the safe default
# entirely.  Caught in review; no point in today's 4,500 happens to
# trip it, which is exactly why it needed a test rather than a look.


def _named(name: str) -> dict:
    """A node as the motorway_junction clause returns one: a name, and
    nothing else to go on."""
    return {"name": name, "props": {}}


def test_a_name_that_says_enforcement_beats_a_chain_word_in_the_same_name():
    for name in ("Pilot Mountain Weigh Station",
                 "Pilot Knob Port of Entry",
                 "Pilot Rock Inspection Station",
                 "Love's Scale House"):
        got = weigh_station_layer(_named(name))
        assert got == "weigh_station", f"{name!r} filed as {got}"


def test_the_enforcement_words_the_query_itself_asks_for_are_recognised():
    """Whatever the query matches on, the classifier has to understand —
    or the clause returns points the split cannot place.

    The first cut of this pattern ended in a word boundary and so could
    not match "Weigh Station": `stat` followed by `ion` is not a
    boundary.  It looked right and had never been run.
    """
    for name in ("Weigh Station", "Weigh Sta", "Weighstation",
                 "Port of Entry", "Inspection Station"):
        assert weigh_station_layer(_named(name)) == "weigh_station", name


def test_a_chain_name_with_nothing_contradicting_it_is_still_a_scale():
    """The safe direction must not swallow the signal it was added
    beside — a node actually named after a truck stop still goes to
    Truck Scales."""
    for name in ("CAT Scale", "Pilot Travel Center", "Love's Travel Stop"):
        got = weigh_station_layer(_named(name))
        assert got == "truck_scale", f"{name!r} filed as {got}"


def test_a_surveyed_tag_outranks_any_name():
    """Tag evidence is strong — somebody put it there — and name
    evidence is weak.  An operator tag decides before either name rule
    is consulted."""
    p = {"name": "Weigh Station", "props": {"operator": "Pilot Travel Center"}}
    assert weigh_station_layer(p) == "truck_scale"


# ── the catalogue ─────────────────────────────────────────────────────


def test_what_we_serve_is_no_longer_what_we_ask():
    """The two lists have diverged, and a reader who uses the query dict
    to answer "what layers are there" now gets the wrong answer."""
    assert "truck_scale" in SERVED_LAYERS
    assert "truck_scale" not in POI_OVERPASS_QUERIES
    assert set(SERVED_LAYERS) > set(POI_OVERPASS_QUERIES)
    # And every served layer traces back to exactly one fetch.
    for served in SERVED_LAYERS:
        assert fetch_layer_for(served) in POI_OVERPASS_QUERIES, served
    assert fetch_layer_for("truck_scale") == "weigh_station"
    assert fetch_layer_for("not a layer") is None


# ── one fetch, two layers ─────────────────────────────────────────────


def _reply(*elements):
    return {"elements": list(elements)}


def _osm(node_id, lat, lng, **tags):
    return {"type": "node", "id": node_id, "lat": lat, "lon": lng,
            "tags": {"amenity": "weighbridge", **tags}}


async def test_one_fetch_fills_both_layers(store):
    """THE POINT OF SPLITTING HERE rather than at the query: the mirrors
    are volunteer-run and are asked exactly as often as before."""
    replies = [
        _reply(_osm(1, 41.8, -87.6, operator="CAT Scale", name="CAT Scale"),
               _osm(2, 41.9, -87.7, operator="Illinois Department of Transportation")),
        _reply(_osm(3, 61.2, -149.9, operator="State of Alaska DOT")),
        _reply(_osm(4, 21.3, -157.8, operator="Love's Travel Stop")),
    ]
    post = AsyncMock(side_effect=replies)
    with patch("features.live_map.poi.overpass._overpass_post", new=post):
        result = await importer.import_layer(store, "weigh_station",
                                             stamp="2026-09-14T10:00:00Z")

    assert result["ok"] is True, result["note"]
    # Three regions, three requests — the same number as before the split.
    assert post.await_count == 3
    assert result["stored"] == {"weigh_station": 2, "truck_scale": 2}

    # Wide enough to hold all three regions — Alaska and Hawaii sit far
    # west of the lower 48, and a box that clipped them would let this
    # assertion pass while a whole region went unstored.
    whole = (0.0, -180.0, 90.0, 0.0)
    dot = await store.poi_points_in_bbox("weigh_station", *whole)
    scales = await store.poi_points_in_bbox("truck_scale", *whole)
    assert {r["osm_id"] for r in dot} == {2, 3}
    assert {r["osm_id"] for r in scales} == {1, 4}


async def test_both_layers_get_a_version_even_when_one_is_empty(store):
    """A half that found nothing still RAN.  Leaving it unclosed would
    make it look never-imported, and a layer with no version is one the
    client asks the mirror for on every single pan — forever."""
    replies = [_reply(_osm(1, 41.8, -87.6, operator="Kansas Highway Patrol")),
               _reply(), _reply()]
    with patch("features.live_map.poi.overpass._overpass_post",
               new=AsyncMock(side_effect=replies)):
        result = await importer.import_layer(store, "weigh_station",
                                             stamp="2026-09-14T10:00:00Z")

    assert result["stored"]["truck_scale"] == 0
    assert await store.poi_layer_imported_at("weigh_station") == "2026-09-14T10:00:00Z"
    assert await store.poi_layer_imported_at("truck_scale") == "2026-09-14T10:00:00Z", (
        "the empty half was never closed — the map will ask the mirror "
        "for it on every pan, forever")


async def test_a_failed_run_closes_neither_half(store):
    """The sweep rule, across a split: one region short is the whole
    fetch short, and neither layer may move its date."""
    with patch("features.live_map.poi.overpass._overpass_post",
               new=AsyncMock(side_effect=RuntimeError("Overpass 504"))):
        result = await importer.import_layer(store, "weigh_station",
                                             stamp="2026-09-14T10:00:00Z")
    assert result["ok"] is False
    assert await store.poi_layer_imported_at("weigh_station") is None
    assert await store.poi_layer_imported_at("truck_scale") is None
