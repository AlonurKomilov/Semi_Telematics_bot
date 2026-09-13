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

import asyncio
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


async def test_a_dead_source_stops_the_run_instead_of_grinding_through_it(store):
    """Six layers x three regions x three attempts is most of a working
    day spent learning one fact, and the first two layers have already
    established it.

    Measured while the owner watched the first real run: HTTP 504 after
    eighty seconds, on every attempt.  Stopping is also the honest
    outcome — a failed layer changes nothing, so an abandoned run leaves
    exactly what a completed failing one would have, hours earlier.
    """
    with patch("features.live_map.poi.overpass._overpass_post",
               new=AsyncMock(side_effect=RuntimeError("Overpass 504"))) as post:
        results = await importer.import_all(store, stamp="2026-09-13T14:00:00Z")

    # Every layer is accounted for — the ones not attempted say so
    # rather than going missing from the report.
    from features.live_map.poi.layers import POI_OVERPASS_QUERIES
    assert sorted(r["layer"] for r in results) == sorted(POI_OVERPASS_QUERIES)
    assert all(r["ok"] is False for r in results)
    assert any("not attempted" in r["note"] for r in results), results

    # It gave up after two dead layers — the rest say so rather than
    # being silently missing.  Asserting an exact request count here was
    # the first version and it was wrong to: the number moved the moment
    # a refused box started being split, and it was never what mattered.
    # What matters is that the run STOPS and changes nothing.
    attempted = [r for r in results if "not attempted" not in r["note"]]
    assert len(attempted) == importer._GIVE_UP_AFTER_DEAD_LAYERS, attempted
    assert post.await_count > 0

    # And nothing was written or dated for any of them.
    assert await store.count_poi_points() == {}
    for layer in POI_OVERPASS_QUERIES:
        assert await store.poi_layer_imported_at(layer) is None


async def test_a_source_that_comes_back_is_not_cut_off(store):
    """The breaker counts CONSECUTIVE dead layers, so one awkward layer
    between two good ones must not end the run."""
    from features.live_map.poi.layers import POI_OVERPASS_QUERIES
    dead_layer = sorted(POI_OVERPASS_QUERIES, key=importer._query_cost)[1]

    async def _one_bad_layer(query, **_kw):
        # Keyed on the layer's own clause rather than a call count: with
        # boxes splitting, how MANY calls a layer makes is not fixed.
        if any(c in query for c in POI_OVERPASS_QUERIES[dead_layer]):
            raise RuntimeError("Overpass 504")
        return _reply(_osm(1, 41.8, -87.6))

    with patch("features.live_map.poi.overpass._overpass_post",
               new=AsyncMock(side_effect=_one_bad_layer)):
        results = await importer.import_all(store, stamp="2026-09-13T14:00:00Z")

    assert [r["ok"] for r in results] == [True, False, True, True, True, True]
    assert not any("not attempted" in r["note"] for r in results)


async def test_a_refused_box_is_asked_again_in_quarters(store):
    """A mirror refuses on ESTIMATED COST, so the answer to "too
    expensive" is a cheaper question, not a more patient one.

    Watched live on 2026-09-13: the CONUS box for fuel_station was
    refused on every attempt while the Alaska and Hawaii boxes beside it
    answered at once.  Size was the whole difference.
    """
    seen: list[str] = []

    async def _refuse_the_big_one(query, **_kw):
        # The bbox is inside the query text the importer built.
        import re as _re
        box = _re.search(r"\(([-\d.,]+)\);", query).group(1)
        seen.append(box)
        south, west, north, east = (float(x) for x in box.split(","))
        if (north - south) * (east - west) > 400:      # CONUS whole
            raise RuntimeError("Overpass 504")
        return _reply(_osm(len(seen), (south + north) / 2, (west + east) / 2))

    with patch("features.live_map.poi.overpass._overpass_post",
               new=AsyncMock(side_effect=_refuse_the_big_one)):
        result = await importer.import_layer(store, "fuel_station",
                                             stamp="2026-09-13T14:30:00Z")

    assert result["ok"] is True, result["note"]
    # The whole box twice, then its four quarters — each of which fits.
    assert seen[0] == seen[1], "the first box was not retried before splitting"
    assert len(seen) >= 6, seen
    # And the points from every quarter are kept: a missing quarter would
    # be missing points, and the sweep would then delete them.
    assert result["points"] >= 4


async def test_a_box_that_refuses_even_quartered_still_fails_the_layer(store):
    """Splitting is not a way to turn a dead source into a good answer —
    if a piece never arrives, the layer must still fail and sweep
    nothing."""
    with patch("features.live_map.poi.overpass._overpass_post",
               new=AsyncMock(side_effect=RuntimeError("Overpass 504"))):
        result = await importer.import_layer(store, "shower",
                                             stamp="2026-09-13T14:30:00Z")
    assert result["ok"] is False
    assert await store.poi_layer_imported_at("shower") is None


async def test_one_expensive_region_does_not_starve_the_cheap_ones(store):
    """Watched on a real run, 2026-09-13.

    The CONUS box spent the layer's entire fifteen minutes quartering
    itself and never reached Alaska or Hawaii — two boxes that had
    answered in seconds four minutes earlier.  The layer fails either
    way; what was lost was the LOG saying which regions are reachable,
    which is the only thing that run could still have taught anyone.
    """
    from features.live_map.poi import viewport

    asked: list[str] = []

    async def _conus_hangs(query, **_kw):
        import re as _re
        box = _re.search(r"\(([-\d.,]+)\);", query).group(1)
        asked.append(box)
        south, west, north, east = (float(x) for x in box.split(","))
        # CONUS *OR ANY PIECE OF IT* — the first version of this matched
        # only the whole box, so every quarter answered instantly, the
        # budget was never spent and the test passed with the fault
        # restored.  A guard that cannot fail is not a guard.
        in_conus = south >= 24 and north <= 50 and west >= -125 and east <= -66
        if in_conus:
            await asyncio.sleep(0.05)
            raise RuntimeError("Overpass 504")
        return _reply(_osm(len(asked), (south + north) / 2, (west + east) / 2))

    # A budget small enough that a POOLED one would be spent on CONUS.
    monkey = importer._LAYER_BUDGET_S
    importer._LAYER_BUDGET_S = 0.3
    try:
        with patch("features.live_map.poi.overpass._overpass_post",
                   new=AsyncMock(side_effect=_conus_hangs)):
            await importer.import_layer(store, "fuel_station",
                                        stamp="2026-09-13T15:30:00Z")
    finally:
        importer._LAYER_BUDGET_S = monkey

    # Alaska and Hawaii were reached — the two regions that are not CONUS.
    reached = {b for b in asked}
    for region in viewport._USA_REGIONS[1:]:
        want = viewport._bbox_to_str(*region)
        assert want in reached, f"{want} never asked — CONUS ate the clock"


async def test_the_cheapest_queries_are_tried_first(store):
    """Which layers get tried AT ALL, when a mirror is refusing.

    The breaker stops after two dead layers, so the order decides what
    is reached.  The registry lists the two most expensive first —
    fuel_station and def_station, four clauses each with brand regexes —
    so on 2026-09-13 a struggling mirror refused both and the run ended
    before rest_area, two clauses and no regex, was ever asked.
    """
    from features.live_map.poi.layers import POI_OVERPASS_QUERIES

    order: list[str] = []

    async def _note_the_layer(query, **_kw):
        for layer, clauses in POI_OVERPASS_QUERIES.items():
            if all(c in query for c in clauses):
                if layer not in order:
                    order.append(layer)
                break
        raise RuntimeError("Overpass 504")

    with patch("features.live_map.poi.overpass._overpass_post",
               new=AsyncMock(side_effect=_note_the_layer)):
        await importer.import_all(store, stamp="2026-09-13T16:00:00Z")

    # Only the two the breaker allows — and they are the two CHEAPEST.
    assert order == ["rest_area", "shower"], order
    cheapest = sorted(POI_OVERPASS_QUERIES, key=importer._query_cost)[:2]
    assert order == cheapest
