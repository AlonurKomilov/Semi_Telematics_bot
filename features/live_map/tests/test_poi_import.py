"""The import job that replaced asking a mirror on every map pan.

One rule carries it, and it is this codebase's oldest one in a new
place: A RUN THAT DID NOT FINISH MUST NOT LEAVE THE LAYER EMPTIER THAN
IT FOUND IT.  The regions are fetched separately, so it is entirely
normal for two to answer and one to refuse — and sweeping on that would
delete every point the third region was going to supply.  Last week's
truck stops beat none, with a week's reach rather than the five minutes
the /pois cache used to hold a wrong answer for.
"""
from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest
import pytest_asyncio

from adapters.storage.poi_directory import PoiDirectoryMixin
from features.live_map.poi import importer

pytestmark = pytest.mark.asyncio


@pytest_asyncio.fixture
async def store(db):
    """The POI mixin over the test database, composed by hand — it is not
    on ``Database`` yet (that file is held by another session)."""
    m = PoiDirectoryMixin()
    m._db = db._db          # type: ignore[attr-defined]
    return m


@pytest.fixture(autouse=True)
def _no_waiting(monkeypatch):
    """The job waits a minute between attempts and half a minute between
    layers, which is right for a mirror and wrong for a test."""
    monkeypatch.setattr(importer, "_PAUSE_S", 0)
    monkeypatch.setattr(importer, "_BETWEEN_LAYERS_S", 0)


def _osm(node_id: int, lat: float, lng: float, **tags):
    return {"type": "node", "id": node_id, "lat": lat, "lon": lng,
            "tags": {"amenity": "fuel", **tags}}


def _reply(*elements):
    return {"elements": list(elements)}


async def test_a_finished_import_stores_the_points_and_the_date(store):
    # Three regions are asked; each answers with its own node.
    replies = [_reply(_osm(1, 41.8, -87.6, name="Pilot")),
               _reply(_osm(2, 61.2, -149.9, name="Anchorage")),
               _reply(_osm(3, 21.3, -157.8, name="Honolulu"))]
    with patch("features.live_map.poi.overpass._overpass_post",
               new=AsyncMock(side_effect=replies)):
        result = await importer.import_layer(store, "fuel_station",
                                             stamp="2026-09-13T10:00:00Z")

    assert result["ok"] is True
    assert result["points"] == 3
    assert await store.poi_layer_imported_at("fuel_station") == "2026-09-13T10:00:00Z"
    rows = await store.poi_points_in_bbox("fuel_station", 41.0, -88.0, 42.0, -87.0)
    assert [r["name"] for r in rows] == ["Pilot"]
    # The tags the map draws with came across, through the one reader.
    assert rows[0]["props"]["amenity"] == "fuel"


async def test_one_region_short_fails_the_whole_layer_and_sweeps_nothing(store):
    """The rule this file exists for."""
    good = _reply(_osm(1, 41.8, -87.6, name="Pilot"),
                  _osm(2, 41.9, -87.7, name="Love's"))
    with patch("features.live_map.poi.overpass._overpass_post",
               new=AsyncMock(side_effect=[good, _reply(), _reply()])):
        first = await importer.import_layer(store, "fuel_station",
                                            stamp="2026-09-13T10:00:00Z")
    assert first["ok"] is True and first["points"] == 2

    # A week later: the first region answers with ONE node, the second
    # refuses every attempt.
    later = _reply(_osm(1, 41.8, -87.6, name="Pilot"))
    calls = [later] + [RuntimeError("Overpass 504")] * 9
    with patch("features.live_map.poi.overpass._overpass_post",
               new=AsyncMock(side_effect=calls)):
        second = await importer.import_layer(store, "fuel_station",
                                             stamp="2026-09-20T10:00:00Z")

    assert second["ok"] is False
    assert "504" in second["note"]
    # NOTHING was swept: the node the failing regions never got the
    # chance to re-supply is still on the map.
    rows = await store.poi_points_in_bbox("fuel_station", 41.0, -88.0, 42.0, -87.0)
    assert {r["osm_id"] for r in rows} == {1, 2}
    # And the date still says the last run we can vouch for.
    assert await store.poi_layer_imported_at("fuel_station") == "2026-09-13T10:00:00Z"


async def test_a_region_that_answers_on_the_second_try_is_not_a_failure(store):
    """What the retries are for: the mirror is queue-bound, not broken."""
    calls = [RuntimeError("busy"), _reply(_osm(5, 41.8, -87.6)), _reply(), _reply()]
    with patch("features.live_map.poi.overpass._overpass_post",
               new=AsyncMock(side_effect=calls)) as post:
        result = await importer.import_layer(store, "shower",
                                             stamp="2026-09-13T10:00:00Z")
    assert result["ok"] is True, result["note"]
    assert post.await_count == 4      # one retry, then the other two regions


async def test_the_same_place_twice_in_one_union_is_stored_once(store):
    """The clauses overlap on purpose — a Pilot tagged fuel:diesel=yes
    also matches the brand allowlist — so the same node arrives twice."""
    dup = _reply(_osm(9, 41.8, -87.6, name="Pilot"),
                 _osm(9, 41.8, -87.6, name="Pilot", brand="Pilot"))
    with patch("features.live_map.poi.overpass._overpass_post",
               new=AsyncMock(side_effect=[dup, _reply(), _reply()])):
        result = await importer.import_layer(store, "fuel_station",
                                             stamp="2026-09-13T10:00:00Z")
    assert result["points"] == 1
    rows = await store.poi_points_in_bbox("fuel_station", 41.0, -88.0, 42.0, -87.0)
    assert len(rows) == 1


async def test_an_unknown_layer_is_answered_not_raised(store):
    """The scheduler calls this; a typo must not be a dead job."""
    result = await importer.import_layer(store, "not_a_layer")
    assert result["ok"] is False
    assert "unknown" in result["note"]


async def test_the_query_is_built_from_the_layer_registry(store):
    """So a layer added to POI_OVERPASS_QUERIES is imported without a
    second edit here — the registry stays the one place that says what a
    layer is."""
    from features.live_map.poi.layers import POI_OVERPASS_QUERIES

    q = importer._region_query("weigh_station", "24.0,-125.0,49.5,-66.5")
    for clause in POI_OVERPASS_QUERIES["weigh_station"]:
        assert clause in q
    assert "(24.0,-125.0,49.5,-66.5)" in q
    assert "out center" in q
    # No area: the same fault the map path removed, and for the same
    # reason — a mirror without an area index answers nothing, at 200.
    assert "area." not in q
