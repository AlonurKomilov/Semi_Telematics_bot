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
