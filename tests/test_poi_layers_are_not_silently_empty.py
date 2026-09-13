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

import inspect
import re

from features.location import pois


def _built_query() -> str:
    """The Overpass query text this build would send, without sending it."""
    src = inspect.getsource(pois._fetch_overpass)
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
    src = inspect.getsource(pois.map_pois)
    assert "_clip_bbox_to_usa" in src, (
        "nothing bounds the query to the US any more — the area filter was "
        "removed because this clip already does it")


def test_a_source_that_did_not_answer_is_not_an_empty_area():
    src = inspect.getsource(pois.map_pois)
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
    assert pois._overpass_gave_up(
        {"remark": 'runtime error: Query timed out in "query" at line 3 after 25 seconds.'})
    assert pois._overpass_gave_up(
        {"remark": 'runtime error: Query run out of memory in "recurse" at line 2.'})
    # A plain empty answer is still a legitimate empty answer.
    assert not pois._overpass_gave_up({"elements": []})
    assert not pois._overpass_gave_up({})
    assert not pois._overpass_gave_up({"remark": None})


def test_a_refusal_is_retried_before_it_is_believed():
    """The mirror is queue-bound, not slow: three consecutive runs of
    the heaviest query took 7.8s, 11.9s and 16.8s, and a fourth never
    returned inside ninety seconds.  A second pass converts most of
    those into the answer that was there all along."""
    assert pois._OVERPASS_ATTEMPTS >= 2, "one try is not a policy for a flaky mirror"
    src = inspect.getsource(pois._fetch_overpass)
    assert "_overpass_gave_up" in src, "a soft refusal must be caught where the reply is read"
    assert "continue" in src, "a refusal must fall through to the next attempt"


def test_the_server_gives_up_before_the_browser_does():
    """A server still trying after its caller has left is burning a
    shared mirror for nobody.  Both clients wait 90 seconds."""
    worst = pois._OVERPASS_ATTEMPT_S * pois._OVERPASS_ATTEMPTS + pois._OVERPASS_RETRY_PAUSE_S
    assert worst < 90, f"worst-case {worst}s outlasts the 90s the browser waits"


def test_a_failure_is_never_cached():
    """Five minutes of a wrong answer outlives most of the outages that
    cause it.

    Scoped to the Overpass branch: the function caches in more than one
    place (the vendor directory reads the database and caches its own
    answer), so the first cache write in the source is not this one.
    """
    src = inspect.getsource(pois.map_pois)
    branch = src[src.index("query_parts = POI_OVERPASS_QUERIES"):]
    raise_at = branch.index("status_code=502")
    cache_at = branch.index("_poi_cache[cache_key] = features")
    assert raise_at < cache_at, (
        "the cache write is reachable from the failure path")
