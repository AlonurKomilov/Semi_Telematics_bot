"""The built-in POI layers, stored instead of asked for.

They describe things that do not move, and they were being fetched from
a volunteer Overpass mirror on every map pan — so the map went blank
whenever a mirror was busy.  Measured 2026-09-13: all three reachable
mirrors refused a one-node query inside the same hour.

Two rules carry the whole design, and both are here:

  * a re-import UPDATES what moved, it does not grow a second copy;
  * a run that DID NOT FINISH removes nothing and moves no date, so the
    map shows last week's truck stops rather than none.  That second one
    is this codebase's oldest lesson in a new place: a source that did
    not answer is not an area with nothing in it.
"""
from __future__ import annotations

import pytest
import pytest_asyncio

from adapters.storage.poi_directory import PoiDirectoryMixin

pytestmark = pytest.mark.asyncio


@pytest_asyncio.fixture
async def poi(db):
    """The mixin over the test database.

    Composed by hand rather than through ``Database``: the mixin is not
    on it yet (adapters/storage/__init__.py is held by another session
    mid-edit), and the storage contract is testable without it.
    """
    m = PoiDirectoryMixin()
    m._db = db._db          # type: ignore[attr-defined]
    return m


def _point(osm_id: int, lat: float, lng: float, name: str = "", **props):
    return {"osm_type": "node", "osm_id": osm_id, "lat": lat, "lng": lng,
            "name": name, "props": props}


async def test_a_viewport_gets_only_what_is_in_it(poi):
    await poi.upsert_poi_points("fuel_station", [
        _point(1, 41.85, -87.65, "Pilot Chicago", brand="Pilot"),
        _point(2, 34.05, -118.24, "Love's LA", brand="Love's"),
    ], "2026-09-13T10:00:00Z")

    chicago = await poi.poi_points_in_bbox("fuel_station", 41.0, -88.0, 42.0, -87.0)
    assert [p["osm_id"] for p in chicago] == [1]
    # The OSM tags come back as a mapping, not the JSON they are stored as.
    assert chicago[0]["props"] == {"brand": "Pilot"}

    # And a layer is a layer: the same box, a different layer, is empty.
    assert await poi.poi_points_in_bbox("shower", 41.0, -88.0, 42.0, -87.0) == []


async def test_a_reimport_moves_a_point_instead_of_duplicating_it(poi):
    await poi.upsert_poi_points(
        "shower", [_point(7, 41.5, -93.6, "Old name")], "2026-09-13T10:00:00Z")
    # Same OSM identity, new position and name — the shop was re-surveyed.
    await poi.upsert_poi_points(
        "shower", [_point(7, 41.6, -93.7, "New name")], "2026-09-13T11:00:00Z")

    rows = await poi.poi_points_in_bbox("shower", 40.0, -95.0, 43.0, -92.0)
    assert len(rows) == 1, "the same OSM node was stored twice"
    assert rows[0]["name"] == "New name"
    assert rows[0]["lat"] == pytest.approx(41.6)


async def test_a_finished_run_removes_what_left_openstreetmap(poi):
    await poi.upsert_poi_points("rest_area", [
        _point(10, 41.0, -93.0, "Still there"),
        _point(11, 41.1, -93.1, "Closed since"),
    ], "2026-09-13T10:00:00Z")
    await poi.finish_poi_import("rest_area", "2026-09-13T10:00:00Z", 2, ok=True)

    # The next run sees only one of them.
    await poi.upsert_poi_points(
        "rest_area", [_point(10, 41.0, -93.0, "Still there")], "2026-09-14T10:00:00Z")
    await poi.finish_poi_import("rest_area", "2026-09-14T10:00:00Z", 1, ok=True)

    rows = await poi.poi_points_in_bbox("rest_area", 40.0, -94.0, 42.0, -92.0)
    assert [r["osm_id"] for r in rows] == [10]
    assert await poi.poi_layer_imported_at("rest_area") == "2026-09-14T10:00:00Z"


async def test_a_run_that_did_not_finish_empties_nothing(poi):
    """THE RULE THIS TABLE EXISTS FOR.

    A half-finished import that swept anyway would blank a layer for
    everyone — the same wrong answer, with a week's reach instead of
    five minutes', that the /pois endpoint used to cache.
    """
    await poi.upsert_poi_points("weigh_station", [
        _point(20, 41.0, -93.0, "One"),
        _point(21, 41.1, -93.1, "Two"),
    ], "2026-09-13T10:00:00Z")
    await poi.finish_poi_import("weigh_station", "2026-09-13T10:00:00Z", 2, ok=True)

    # A run that got one point in and then the mirror stopped answering.
    await poi.upsert_poi_points(
        "weigh_station", [_point(20, 41.0, -93.0, "One")], "2026-09-20T10:00:00Z")
    await poi.finish_poi_import(
        "weigh_station", "2026-09-20T10:00:00Z", 1, ok=False, note="Overpass 504")

    rows = await poi.poi_points_in_bbox("weigh_station", 40.0, -94.0, 42.0, -92.0)
    assert {r["osm_id"] for r in rows} == {20, 21}, (
        "a failed run swept points it never had the chance to re-fetch")
    # And the map keeps showing the date it can actually vouch for.
    assert await poi.poi_layer_imported_at("weigh_station") == "2026-09-13T10:00:00Z"
    # while the operator can see the run failed, and why.
    runs = await poi.poi_layer_imports()
    assert runs["weigh_station"]["ok"] == 0
    assert "504" in runs["weigh_station"]["note"]


async def test_a_layer_never_imported_has_no_date_rather_than_a_wrong_one(poi):
    assert await poi.poi_layer_imported_at("truck_parking") is None
    assert await poi.poi_points_in_bbox("truck_parking", 40.0, -94.0, 42.0, -92.0) == []
    # None is what lets the caller say "not loaded yet" instead of
    # drawing an empty layer and calling it "none in this view".
    assert await poi.count_poi_points("truck_parking") == {}


# ── two dates, and they are not the same fact ──────────────────────────


async def test_when_we_ran_and_how_old_the_data_is_are_kept_apart(poi):
    """The freshness line asks the SECOND question.

    `imported_at` is a version: it changes on every successful run and a
    client compares it to decide whether to re-download.  `osm_base` is
    what the mirror said its extract was stamped, and the mirrors run
    months behind — measured 2026-09-13, the two this host could reach
    were stamped 2026-06-01 and 2026-07-28.

    Serving the first where the second belongs made the map say its data
    was hours old while it was a season behind.  They differ by a season
    here on purpose, so a swap cannot pass.
    """
    await poi.upsert_poi_points(
        "shower", [_point(30, 41.0, -93.0, "Truck stop")], "2026-09-14T03:00:00Z")
    await poi.finish_poi_import(
        "shower", "2026-09-14T03:00:00Z", 1, ok=True,
        osm_base="2026-06-01T00:00:00Z")

    assert await poi.poi_layer_imported_at("shower") == "2026-09-14T03:00:00Z"
    assert await poi.poi_layer_source_as_of("shower") == "2026-06-01T00:00:00Z"


async def test_a_failed_run_moves_neither_date(poi):
    """The same rule that protects the points protects both stamps: the
    points still on the table are last week's, so last week's dates are
    the ones still true of them."""
    await poi.finish_poi_import(
        "def_station", "2026-09-07T03:00:00Z", 5, ok=True,
        osm_base="2026-06-01T00:00:00Z")
    await poi.finish_poi_import(
        "def_station", "2026-09-14T03:00:00Z", 0, ok=False, note="every mirror 504",
        osm_base="2026-07-28T00:00:00Z")

    assert await poi.poi_layer_imported_at("def_station") == "2026-09-07T03:00:00Z"
    assert await poi.poi_layer_source_as_of("def_station") == "2026-06-01T00:00:00Z"


async def test_a_first_run_that_failed_carries_neither_date(poi):
    """The CASE arms only fire on conflict, so the very first run needs
    its own guard — a layer that has never finished must not look like
    one holding data from the run that brought back nothing."""
    await poi.finish_poi_import(
        "rest_area", "2026-09-14T03:00:00Z", 0, ok=False, note="no mirror answered",
        osm_base="2026-07-28T00:00:00Z")

    assert await poi.poi_layer_imported_at("rest_area") is None
    assert await poi.poi_layer_source_as_of("rest_area") is None


async def test_an_extract_date_the_mirror_never_gave_is_none_not_our_own(poi):
    """Omitted, never guessed.  An unknown extract date draws no line at
    all; substituting the import time would draw a confident one that is
    wrong by however far behind the mirror was."""
    await poi.finish_poi_import("fuel_station", "2026-09-14T03:00:00Z", 9, ok=True)

    assert await poi.poi_layer_imported_at("fuel_station") == "2026-09-14T03:00:00Z"
    assert await poi.poi_layer_source_as_of("fuel_station") is None
    runs = await poi.poi_layer_imports()
    assert runs["fuel_station"]["osm_base"] is None


async def test_a_layer_never_imported_has_no_extract_date_either(poi):
    assert await poi.poi_layer_source_as_of("truck_parking") is None


# ── re-filing a split decided after the import ────────────────────────


def _classify_by_operator(point):
    """Stand-in for the real weigh-station classifier — the storage
    layer takes any callable, and pinning the real one here would make
    this a test of that rule instead of of this one."""
    op = (point["props"].get("operator") or "").lower()
    return "truck_scale" if "cat scale" in op else "weigh_station"


async def test_a_split_decided_later_is_applied_without_asking_the_source(poi):
    """THE POINT: every tag the classifier reads is already stored.

    The weigh-station split was designed after 4,500 points had been
    imported, and re-fetching them would have meant an hour against
    volunteer mirrors to learn nothing new.  The rows carry their own
    tags; re-filing them is a local decision.
    """
    await poi.upsert_poi_points("weigh_station", [
        _point(1, 41.0, -93.0, "CAT Scale", operator="CAT Scale"),
        _point(2, 41.1, -93.1, "", operator="Illinois Department of Transportation"),
        _point(3, 41.2, -93.2, "", operator="CAT Scale"),
    ], "2026-09-14T10:00:00Z")
    await poi.finish_poi_import(
        "weigh_station", "2026-09-14T10:00:00Z", 3, ok=True,
        osm_base="2026-06-01T00:00:00Z")

    counts = await poi.reclassify_poi_points(
        ("weigh_station", "truck_scale"), _classify_by_operator,
        "2026-09-14T10:00:00Z", "2026-06-01T00:00:00Z")

    assert counts == {"weigh_station": 1, "truck_scale": 2}
    box = (40.0, -94.0, 42.0, -92.0)
    assert {r["osm_id"] for r in await poi.poi_points_in_bbox("weigh_station", *box)} == {2}
    assert {r["osm_id"] for r in await poi.poi_points_in_bbox("truck_scale", *box)} == {1, 3}


async def test_the_new_half_gets_a_version_or_the_map_never_stops_asking(poi):
    """A layer with no row in poi_imports is one the client fetches per
    viewport forever.  Re-filing has to close BOTH halves, and carry the
    source's dates — the points did not change, so neither did their
    age."""
    await poi.upsert_poi_points("weigh_station", [
        _point(1, 41.0, -93.0, "", operator="CAT Scale"),
    ], "2026-09-14T10:00:00Z")
    await poi.finish_poi_import(
        "weigh_station", "2026-09-14T10:00:00Z", 1, ok=True,
        osm_base="2026-06-01T00:00:00Z")

    await poi.reclassify_poi_points(
        ("weigh_station", "truck_scale"), _classify_by_operator,
        "2026-09-14T10:00:00Z", "2026-06-01T00:00:00Z")

    assert await poi.poi_layer_imported_at("truck_scale") == "2026-09-14T10:00:00Z"
    assert await poi.poi_layer_source_as_of("truck_scale") == "2026-06-01T00:00:00Z"
    # And the half that gave everything away still has a date, not a
    # zero-point layer that looks like a failed import.
    assert await poi.poi_layer_imported_at("weigh_station") == "2026-09-14T10:00:00Z"


async def test_re_filing_twice_changes_nothing_the_second_time(poi):
    """The rows are keyed (layer, osm_type, osm_id), so a point that is
    already in the right layer must not be UPDATEd onto itself — that is
    a primary-key collision waiting for the second run."""
    await poi.upsert_poi_points("weigh_station", [
        _point(1, 41.0, -93.0, "", operator="CAT Scale"),
        _point(2, 41.1, -93.1, "", operator="FDOT"),
    ], "2026-09-14T10:00:00Z")
    await poi.finish_poi_import("weigh_station", "2026-09-14T10:00:00Z", 2, ok=True)

    args = (("weigh_station", "truck_scale"), _classify_by_operator,
            "2026-09-14T10:00:00Z", None)
    first = await poi.reclassify_poi_points(*args)
    second = await poi.reclassify_poi_points(*args)
    assert first == second == {"weigh_station": 1, "truck_scale": 1}


async def test_re_filing_never_sweeps_whatever_stamp_it_is_given(poi):
    """THE FOOT-GUN, REMOVED RATHER THAN DOCUMENTED.

    `finish_poi_import` deletes every row older than its stamp, which is
    right after a fetch and catastrophic after a re-file: nothing was
    fetched, so nothing left OpenStreetMap.  Re-filing used to be safe
    only because its one caller happened to pass the existing import's
    stamp; a caller passing "now" — the natural-looking argument — would
    have emptied both layers silently.
    """
    await poi.upsert_poi_points("weigh_station", [
        _point(1, 41.0, -93.0, "", operator="CAT Scale"),
        _point(2, 41.1, -93.1, "", operator="FDOT"),
    ], "2026-09-14T10:00:00Z")
    await poi.finish_poi_import("weigh_station", "2026-09-14T10:00:00Z", 2, ok=True)

    # A stamp from the FUTURE — every stored row is older than it.
    counts = await poi.reclassify_poi_points(
        ("weigh_station", "truck_scale"), _classify_by_operator,
        "2099-01-01T00:00:00Z", None)

    assert counts == {"weigh_station": 1, "truck_scale": 1}
    box = (40.0, -94.0, 42.0, -92.0)
    assert len(await poi.poi_points_in_bbox("weigh_station", *box)) == 1, (
        "re-filing swept the layer it was only meant to re-label")
    assert len(await poi.poi_points_in_bbox("truck_scale", *box)) == 1
