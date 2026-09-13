"""A map-data source that did not answer is not an empty map.

The owner opened the panel over Chicago, switched on Fuel stations, and
read "None in this view".  Nothing was broken in the panel; the server
had been answering HTTP 200 with zero features for every POI layer,
everywhere, for as long as the condition had held.

Two faults, and the second is the one that hid the first:

1. THE QUERY DEPENDED ON AN OVERPASS *AREA*.  Every filter carried
   ``(area.us)`` so a border-spanning bbox would not return Canadian
   results.  Areas are not part of an Overpass database — they are
   built by a separate periodic job that public mirrors often do not
   run.  ``overpass-api.de`` stopped accepting connections from this
   host entirely (every address, v4 and v6, refused on 443, the same
   week OpenStreetMap blocked our tiles), leaving only a mirror whose
   area resolves to nothing.  Measured on one Chicago-metro bbox:
   34 results without the area clause, 0 with it.

   The bound is already applied before the query: ``_clip_bbox_to_usa``
   intersects the request with US bounding boxes.

2. A FAILURE WAS CACHED AS AN EMPTY ANSWER.  ``except Exception:
   features = []`` followed by a five-minute cache write, served at
   HTTP 200 — so a refusal looked exactly like "there is nothing here",
   and went on looking like it after the refusal had passed.  The same
   shape as the tile block, which went unnoticed for a day for the same
   reason.
"""
from __future__ import annotations

import ast
import inspect
import re

from features.live_map.poi import custom, overpass
from features.live_map.poi import router as poi_routes


def _built_query() -> str:
    """The Overpass query text this build would send, without sending it."""
    src = inspect.getsource(overpass._fetch_overpass)
    # The two lines that assemble it, read rather than executed — the
    # function is async and talks to the network.
    body = src.split('"""', 2)[-1]
    return re.sub(r"\s+", " ", body)


def test_the_bbox_is_the_only_geographic_bound():
    q = _built_query()
    assert "area.us" not in q, (
        "the POI query depends on an Overpass area again — that resolves to "
        "nothing on every mirror without an area-building job, and empties "
        "every layer at HTTP 200")
    assert "ISO3166" not in q, "same fault, spelled differently"


def test_the_bbox_is_still_clipped_to_the_us_before_the_query():
    """The area filter's PURPOSE has to survive its removal."""
    src = inspect.getsource(poi_routes.map_pois)
    assert "_clip_bbox_to_usa" in src, (
        "nothing bounds the query to the US any more — the area filter was "
        "removed because this clip already does it")


def test_a_source_that_did_not_answer_is_not_an_empty_area():
    src = inspect.getsource(poi_routes.map_pois)
    # The old shape, which must not come back.
    assert not re.search(r"except Exception:\s*\n\s*features = \[\]", src), (
        "a failed fetch is being turned into an empty result again")
    assert "502" in src, "a fetch failure must reach the caller as a status"


def test_a_200_that_admits_defeat_is_not_an_answer():
    """Overpass gives up INSIDE a 200.

    Exceed the `[timeout:25]` the query carries, or its memory, and the
    reply is a successful HTTP response with no elements and a `remark`
    saying why.  Read as a feature list — which is all the old code read
    — that is "there is nothing here".

    This guard is DEFENSIVE, and says so.  A 0-feature 200 was seen
    once (31.3s, no exception, a retry then returning 85), but six
    deliberate attempts to capture the remark itself all came back with
    85 features and no remark.  The mechanism is Overpass's documented
    one; that this particular zero came from it is inference.  The
    check costs one string comparison, so it is worth having either
    way — but nobody should read it as a measurement.
    """
    assert overpass._overpass_gave_up(
        {"remark": 'runtime error: Query timed out in "query" at line 3 after 25 seconds.'})
    assert overpass._overpass_gave_up(
        {"remark": 'runtime error: Query run out of memory in "recurse" at line 2.'})
    # A plain empty answer is still a legitimate empty answer.
    assert not overpass._overpass_gave_up({"elements": []})
    assert not overpass._overpass_gave_up({})
    assert not overpass._overpass_gave_up({"remark": None})


def test_a_refusal_is_retried_before_it_is_believed():
    """The mirror is queue-bound, not slow: three consecutive runs of
    the heaviest query took 7.8s, 11.9s and 16.8s, and a fourth never
    returned inside ninety seconds.  A second pass converts most of
    those into the answer that was there all along."""
    assert overpass._OVERPASS_ATTEMPTS >= 2, "one try is not a policy for a flaky mirror"
    src = inspect.getsource(overpass._fetch_overpass)
    assert "_overpass_gave_up" in src, "a soft refusal must be caught where the reply is read"
    assert "continue" in src, "a refusal must fall through to the next attempt"


def test_the_server_gives_up_before_nginx_does():
    """The app has to answer before the gateway answers for it.

    nginx gives /api/ sixty seconds (nginx/4truck.conf), and past that it
    sends its OWN 504 — an HTML gateway page, which the dashboard reads as
    the platform being down and RELOADS THE PAGE for.  The app's own JSON
    502 is handled by the caller and reloads nothing, so the app has to
    get there first.

    THE ARITHMETIC THIS REPLACES WAS WRONG, and quietly: it multiplied the
    passes but not the MIRRORS — `35 * 2 + 1.5 = 71.5`, "under 90", while
    the real worst case over two mirrors was 141.5 seconds against a limit
    that was 60, not 90.  A guard is not protection when its formula is
    missing a term, so the ceiling is a budget the code actually enforces
    rather than a product of constants a reader has to re-derive.
    """
    assert overpass._OVERPASS_TOTAL_S < 60, (
        f"a {overpass._OVERPASS_TOTAL_S}s budget lets nginx answer first")
    for fn in (overpass._fetch_overpass, overpass._overpass_post):
        src = inspect.getsource(fn)
        assert "_OVERPASS_TOTAL_S" in src, (
            f"{fn.__name__} does not hold itself to the budget, so adding a "
            f"third mirror silently extends it past nginx")
        assert "deadline" in src, f"{fn.__name__} has no wall clock"


def test_a_failure_is_never_cached():
    """Five minutes of a wrong answer outlives most of the outages that
    cause it.

    Scoped to the Overpass branch: the function caches in more than one
    place (the vendor directory reads the database and caches its own
    answer), so the first cache write in the source is not this one.
    """
    src = inspect.getsource(poi_routes.map_pois)
    branch = src[src.index("query_parts = POI_OVERPASS_QUERIES"):]
    raise_at = branch.index("status_code=502")
    cache_at = branch.index("_poi_cache[cache_key] = features")
    assert raise_at < cache_at, (
        "the cache write is reachable from the failure path")


# ── The same fault, three branches the first fix did not reach ───────────
#
# The built-in layers stopped turning a refusal into an empty answer.
# Three neighbours kept doing it, and the measurements on 2026-09-13 are
# why they matter: overpass-api.de refuses this host on all four of its
# addresses, and the one mirror that still answers returned HTTP 504
# after 178 seconds to the brand-count query below — which the code then
# read as "this chain has no locations in the United States".


def _handlers(fn) -> list[ast.ExceptHandler]:
    """Every `except` branch of a function, as a tree.

    The first version of the two checks below matched SOURCE TEXT, and
    one of them could not fire: it allowed comment lines between the
    `except` line and the assignment, and the real bug has a
    `logger.warning(...)` there.  It passed with the fault fully
    restored, and only the assertion beside it turned the test red — so
    the test worked and its reasoning did not.  A regex cannot see a
    statement nobody thought to allow for; the tree does not have to.
    """
    tree = ast.parse(inspect.getsource(fn))
    return [h for n in ast.walk(tree) if isinstance(n, ast.Try) for h in n.handlers]


def _assigns_in_handlers(fn, name: str) -> bool:
    """Does any except branch assign to `name`?"""
    for h in _handlers(fn):
        for stmt in h.body:
            for node in ast.walk(stmt):
                if isinstance(node, ast.Assign) and any(
                    isinstance(t, ast.Name) and t.id == name for t in node.targets
                ):
                    return True
    return False


def _returns_from_handlers(fn) -> list[object]:
    """Every literal an except branch returns — the values a failure is
    being reported AS."""
    out: list[object] = []
    for h in _handlers(fn):
        for stmt in h.body:
            for node in ast.walk(stmt):
                if isinstance(node, ast.Return):
                    if isinstance(node.value, ast.Constant):
                        out.append(node.value.value)
                    elif isinstance(node.value, (ast.List, ast.Tuple, ast.Dict)):
                        out.append(ast.dump(node.value))
    return out


def test_a_custom_layer_that_did_not_answer_is_not_an_empty_layer():
    """A custom layer is not a lesser layer.

    `except Exception: features = []` followed by a five-minute cache
    write is the exact shape that was removed from map_pois; it survived
    one branch over, where an account's OWN layer is served.
    """
    assert not _assigns_in_handlers(custom._serve_custom_layer, "features"), (
        "a failed custom layer is being turned into an empty one again")
    src = inspect.getsource(custom._serve_custom_layer)
    assert "502" in src, "a fetch failure must reach the caller as a status"
    raise_at = src.index("status_code=502")
    cache_at = src.index("_poi_cache[cache_key] = features")
    assert raise_at < cache_at, "the cache write is reachable from the failure path"


def test_a_brand_count_that_could_not_be_asked_is_not_zero():
    """None and 0 are different answers.

    `return 0` on a refusal put "0 in USA" on the screen that offers to
    save a layer covering all of them — for a truck stop the owner was
    at that moment looking at.
    """
    src = inspect.getsource(overpass._count_brand_in_usa)
    assert "-> int | None" in src, "the signature has to admit it can fail"
    assert 0 not in _returns_from_handlers(overpass._count_brand_in_usa), (
        "a count that could not be taken is being reported as zero again")
    assert "List(elts=[]" not in " ".join(
        str(v) for v in _returns_from_handlers(overpass._sample_brand_in_usa)), (
        "a sample that could not be taken is being reported as none found")


def test_the_brand_path_also_reads_a_200_that_admits_defeat():
    """_fetch_overpass checked for the soft refusal; _overpass_post — the
    one the pin-drop, the preview and the type-ahead all go through — did
    not, so a `remark` reply reached those screens as real data."""
    src = inspect.getsource(overpass._overpass_post)
    assert "_overpass_gave_up" in src, (
        "the interactive Overpass path believes a 200 that says it gave up")


def test_the_type_ahead_asks_the_mirror_once():
    """It used to sample, then re-count each of the top ten chains
    nationwide: eleven area-wide queries per settled keystroke, 30
    seconds budgeted apiece, aimed at a volunteer server.  The exact
    total belongs to the confirm step, which asks for it there."""
    src = inspect.getsource(poi_routes.brand_search)
    assert "_count_brand_in_usa" not in src, (
        "brand-search is re-counting every hit nationwide again")
    assert src.count("_overpass_post") == 1, "one query, not a loop of them"
    assert '"exact"' in src, (
        "a tally taken from a capped sample must say it is a floor, or the "
        "client prints it as a total")
