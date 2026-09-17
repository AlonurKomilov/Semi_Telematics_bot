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
import textwrap

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


# ── the same fault, wearing the other face ─────────────────────────────
#
# Every guard above catches a FAILURE dressed as an answer.  This one
# catches its sibling: something STALE dressed as fresh.
#
# Both panels render `source_as_of` as "OpenStreetMap · N old".  When the
# layers moved into our own Postgres the DB branch started filling that
# field with `imported_at` — when WE ran the import, which is always a
# few hours ago.  So the line said the data was fresh while the OSM
# extract behind it was stamped 2026-06-01: the exact sentence that line
# exists to prevent, answered in the voice of the question it was asked.
#
# The two dates are both real and both needed.  `imported_at` is a
# VERSION — compared by a client to decide whether to re-download, never
# shown.  `osm_base` is the DATA'S AGE.  Swapping them is silent, which
# is why it is a test and not a comment.


def _returned_source_as_of(fn) -> list[ast.AST]:
    """Every value this function puts under a "source_as_of" key."""
    tree = ast.parse(textwrap.dedent(inspect.getsource(fn)))
    out: list[ast.AST] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Dict):
            continue
        for k, v in zip(node.keys, node.values):
            if isinstance(k, ast.Constant) and k.value == "source_as_of":
                out.append(v)
    return out


def _mentions(node: ast.AST, name: str) -> bool:
    return any(isinstance(n, ast.Name) and n.id == name for n in ast.walk(node))


def test_the_freshness_line_is_never_told_our_import_time():
    """`source_as_of` carries the EXTRACT's date, never the run's."""
    for fn in (poi_routes.map_pois, poi_routes.poi_set):
        values = _returned_source_as_of(fn)
        assert values, (
            f"{fn.__name__} returns no source_as_of at all — a layer served "
            "with no date lets a months-old extract read as current")
        for v in values:
            for ours in ("imported_at", "version", "stamp"):
                assert not _mentions(v, ours), (
                    f"{fn.__name__} serves {ours!r} as source_as_of.  Both "
                    "panels render that field as 'OpenStreetMap · N old', so "
                    "our own import time makes a June extract read as hours "
                    "old.  Read the extract date instead.")
            # And POSITIVELY: it reads an extract date.  Absence of three
            # spellings is a tripwire against the obvious revert and not
            # a proof — the same regression through a differently named
            # local would walk straight past it.
            #
            # Two legitimate sources, one per branch.  Served from our
            # table: the date the import recorded.  Answered live by a
            # mirror: the date that mirror reported, which is the module
            # global in overpass.  Both are the EXTRACT's age; neither is
            # ours.
            called = {n.attr for n in ast.walk(v) if isinstance(n, ast.Attribute)}
            assert called & {"poi_layer_source_as_of", "source_as_of"}, (
                f"{fn.__name__}'s source_as_of no longer comes from an "
                "extract date — whatever it is now, it is not the one fact "
                f"that field is supposed to carry (reads: {sorted(called)})")


def test_the_version_a_client_compares_is_still_the_import_time():
    """The other half of the pair, so the fix cannot be made by deleting
    one of them: /poi-set must keep handing over a version, and it must
    be the import time — that is what changes when the points change."""
    tree = ast.parse(textwrap.dedent(inspect.getsource(poi_routes.poi_set)))
    for node in ast.walk(tree):
        if not isinstance(node, ast.Dict):
            continue
        for k, v in zip(node.keys, node.values):
            if isinstance(k, ast.Constant) and k.value == "version":
                assert _mentions(v, "version"), (
                    "/poi-set's version is no longer the import stamp — a "
                    "client compares it to decide whether its held copy is "
                    "current, so anything that does not change on re-import "
                    "leaves every client holding last week's points")
                return
    raise AssertionError(
        "/poi-set no longer returns a version — without it a client cannot "
        "tell a fresh copy from a stale one and must download every time")


# ── a prefix is not a brand ────────────────────────────────────────────
#
# The brand allowlists are PREFIX-anchored (`^(...)`), which is right for
# "Petro Stopping Center" and wrong for PETRO-CANADA — a different
# company, in a different country, that happens to start with the same
# five letters.  Measured on the imported data 2026-09-14 it had brought
# 539 stations into Fuel Stations and 662 into DEF, where DEF holds 1,318
# points in total: half the layer was Canadian.
#
# The bbox could not have saved us.  "Clipped to the USA" is a RECTANGLE,
# and the CONUS box runs 24.4N to 49.5N — northern Mexico at one end,
# southern Ontario and Quebec whole at the other.
#
# Checked with Python's `re` rather than by asking a mirror: the pattern
# is POSIX ERE either way for constructs this simple, and a guard that
# needs the network is a guard that gets skipped.


def _brand_patterns_by_layer() -> dict[str, list[str]]:
    """layer → every brand pattern its query sends.

    BY LAYER and not a flat list, because the patterns are no longer
    interchangeable: `Petro` has its own clause now, so no single one of
    them sorts every brand and a per-pattern assertion would demand that
    each does.  What has to be true is that the LAYER's union does.
    """
    from features.live_map.poi.layers import POI_OVERPASS_QUERIES
    out: dict[str, list[str]] = {}
    for layer, clauses in POI_OVERPASS_QUERIES.items():
        pats = [m.group(1) for c in clauses
                if (m := re.search(r'"brand"~"([^"]+)"', c))]
        if pats:
            out[layer] = pats
    return out


def _admits(pats: list[str], brand: str) -> bool:
    return any(re.match(p, brand, re.IGNORECASE) for p in pats)


#: EVERY brand value OSM actually holds for these chains, measured
#: 2026-09-17 in one Overpass query over North America:
#: brand → (fuel:diesel=yes, fuel:diesel=no, points in total).
#:
#: READ OFF THE SOURCE, NOT REMEMBERED.  The guard this replaced asserted
#: that "Pilot Travel Center", "Flying J Travel Center" and "Love's
#: Travel Stop" were admitted.  None of those three strings exists in
#: OSM — the brand values are "Pilot", "Flying J" and "Love's" — so the
#: test was checking a vocabulary nobody uses, and passed the day the
#: pattern stopped matching anything real.  This table is the whole
#: vocabulary instead, and both directions are asserted from it.
MEASURED_BRANDS: dict[str, tuple[int, int, int]] = {
    "Petro-Canada": (147, 26, 866),  "Love's":        (30, 0, 261),
    "Kwik Trip":     (25,  0, 241),  "Pilot":         (19, 0, 157),
    "Maverik":       (17,  0, 119),  "Flying J":      (14, 0,  82),
    "Kwik Fill":     (11,  0,  72),  "Kwik Shop":      (1, 0,  51),
    "Petro":          (8,  1,  44),  "Petro-T":        (2, 0,  41),
    "Kwik Star":      (2,  0,  38),  "TA":            (10, 0,  34),
    "Sapp Bros.":     (1,  0,  13),  "Road Ranger":    (1, 0,   8),
    "Petro Seven":    (0,  0,   7),  "Petro Canada":   (0, 0,   3),
    "Petroplus":      (0,  0,   2),  "Kwik Stop":      (0, 0,   2),
    "Kwik-Trip":      (0,  0,   2),  "Petro Bras":     (1, 0,   1),
    "Kwik Serv":      (0,  0,   1),  "PetroUS":        (1, 0,   1),
    "PETROSINA":      (0,  0,   1),  "Petro Mart":     (0, 0,   1),
    "Petro South":    (0,  0,   1),  "Kwik Sak":       (0, 0,   1),
    "AmBest":         (1,  0,   1),  "Sapp Bros":      (1, 0,   1),
    "TA Express":     (1,  0,   1),  "PetroSun":       (0, 0,   1),
    "Love's Alternative Energy": (0, 0, 1),
    "Petro-Pass":     (1,  0,   1),  "Petro-Card 24":  (1, 0,   1),
    "Petro-Pass Card Lock": (0, 0, 1),
}

#: Ground 1 — the business IS the qualification, so no sample applies.
TRUCK_STOP_CHAINS = frozenset({
    "Pilot", "Flying J", "Love's", "TA", "TA Express", "Petro",
    "Sapp Bros", "Sapp Bros.", "Road Ranger", "AmBest", "Bosselman",
    "Speedco",
})

#: Ground 2 — a retail chain earns its name-inference by measurement.
DIESEL_INFERENCE_FLOOR = 0.80
#: …on a real sample.  WITHOUT THIS, `Petro Bras` scores 100% on one
#: tagged point and walks onto a US truck map, which is the exact leak
#: this file was opened for.
MIN_TAGGED_SAMPLE = 10

#: Ground 3 — spellings of a company admitted under ground 1 or 2.
#: A STATED FACT ABOUT OWNERSHIP, not a measurement: Kwik Trip trades as
#: Kwik Star in Iowa, and OSM carries `Kwik-Trip` and `Petro Canada` as
#: punctuation variants.  Written down so it can be argued with.
SAME_COMPANY_VARIANTS = {
    "Kwik-Trip": "Kwik Trip", "Kwik Star": "Kwik Trip",
    "Petro Canada": "Petro-Canada",
}


def _earns_its_name(brand: str) -> bool:
    """The admission rule, stated once so the test cannot drift from it."""
    if brand in TRUCK_STOP_CHAINS:
        return True
    if brand in SAME_COMPANY_VARIANTS:
        return _earns_its_name(SAME_COMPANY_VARIANTS[brand])
    yes, no, _total = MEASURED_BRANDS.get(brand, (0, 0, 0))
    tagged = yes + no
    return tagged >= MIN_TAGGED_SAMPLE and yes / tagged >= DIESEL_INFERENCE_FLOOR


def test_the_brand_allowlists_admit_the_chains_they_name():
    """The brand values OSM really carries for the chains we mean."""
    by_layer = _brand_patterns_by_layer()
    assert by_layer, "no brand allowlist found — the query shape moved"
    for layer, pats in by_layer.items():
        for good in ("Pilot", "Flying J", "Love's", "TA"):
            assert _admits(pats, good), f"{layer} no longer admits {good!r}"


def test_the_fuel_allowlist_is_exactly_what_the_rule_admits():
    """Both directions over the whole measured vocabulary.

    Every brand OSM holds for these chains is judged by the rule and
    compared with the pattern, so the list cannot drift from its evidence
    in either direction: a chain quietly dropped goes red, and a brand
    that sneaks in on a sample of one goes red too.
    """
    pats = _brand_patterns_by_layer()["fuel_station"]
    wrong = []
    for brand, (yes, no, total) in sorted(MEASURED_BRANDS.items()):
        want, got = _earns_its_name(brand), _admits(pats, brand)
        if want != got:
            tagged = yes + no
            share = f"{yes}/{tagged}" if tagged else "no tagged points"
            wrong.append(
                f"{brand!r} ({total} points, {share}): rule says "
                f"{'admit' if want else 'refuse'}, pattern "
                f"{'admits' if got else 'refuses'}")
    assert not wrong, (
        "the fuel allowlist and its rule disagree:\n  " + "\n  ".join(wrong))


def test_a_sample_of_one_is_not_evidence():
    """The floor needs a sample or it is not a floor.

    `Petro Bras` and `PetroUS` each carry one tagged point and it says
    diesel — 100%, on a sample that means nothing.  Both are other
    companies; both would ride a bare percentage onto the map.
    """
    pats = _brand_patterns_by_layer()["fuel_station"]
    for brand in ("Petro Bras", "PetroUS", "Petro-Pass", "Petro-Card 24"):
        yes, no, _ = MEASURED_BRANDS[brand]
        assert yes / max(yes + no, 1) >= DIESEL_INFERENCE_FLOOR, (
            f"{brand} was chosen for this test because it scores 100% — "
            "if that changed, pick another one")
        assert yes + no < MIN_TAGGED_SAMPLE
        assert not _admits(pats, brand), (
            f"fuel_station admits {brand!r} on {yes + no} tagged point(s) — "
            "a percentage without a sample is how a Brazilian and a "
            "cardlock brand reach a US truck map")


def test_no_layer_admits_a_petro_that_is_a_different_company():
    """The leak this section exists for, and it still bites.

    A bare `Petro` prefix once swept 539 PETRO-CANADA stations into Fuel
    Stations and 662 into DEF, where DEF held 1,318 points in total.
    Petro-Canada is now admitted TO FUEL on purpose and by measurement —
    these are the ones that are simply other companies.
    """
    others = ("Petro Seven", "Petroplus", "Petro Bras", "PetroUS",
              "PETROSINA", "Petro Mart", "Petro South", "PetroSun")
    for layer, pats in _brand_patterns_by_layer().items():
        for brand in others:
            assert not _admits(pats, brand), (
                f"{layer} matches {brand!r} — a prefix is not a brand, and "
                "this class of leak put 1,201 foreign stations on a US "
                "truck map")
        # "Petro" and nothing longer: `Petro Stopping Center` was in this
        # assertion until the vocabulary was measured, and OSM has never
        # carried that string — only the bare brand, on 44 nodes.
        assert _admits(pats, "Petro"), (
            f"{layer} refuses 'Petro' — the American chain the entry "
            "exists for")


def test_no_pattern_hides_a_dollar_in_the_middle():
    """A `$` is an anchor at the END of a POSIX ERE and undefined
    elsewhere — and these patterns are executed by a THIRD PARTY, not by
    the `re` module this file checks them with.

    One of them carried `Petro($| )` inside an alternation for a day.  It
    behaved in Python; what Overpass would have made of it was never
    established, and a brand clause that silently matches nothing empties
    a layer on the next weekly import — the quietest way this codebase
    has yet found to lose 5,000 points.

    GENERIC ON PURPOSE.  This guard was briefly lost when the fuel
    allowlist was rebuilt around its measurement: today's clause is fine,
    so nothing was red, so nothing said anything.  A guard that only
    covers the mistake already made is not a guard.
    """
    for layer, pats in _brand_patterns_by_layer().items():
        for pat in pats:
            body = pat[:-1] if pat.endswith("$") else pat
            assert "$" not in body, (
                f"{layer}'s pattern {pat!r} carries a `$` that is not the "
                "final anchor — portable only by accident.  Give the brand "
                "its own `^…$` clause instead.")


def test_the_diesel_measurement_does_not_carry_to_adblue():
    """The deliberate asymmetry, and why it is deliberate.

    Petro-Canada is on the FUEL allowlist because it was measured: 147 of
    its 173 tagged stations sell diesel, and the owner's rule is that a
    real place gets filtered by the user, never deleted by us.

    That measurement is about DIESEL.  It says nothing about AdBlue, and
    DEF is already a chain inference standing on 21 confirmed points out
    of 1,318 — widening it on evidence gathered for a different tag is
    how an inference quietly becomes a fabrication.
    """
    by_layer = _brand_patterns_by_layer()
    assert _admits(by_layer["fuel_station"], "Petro-Canada"), (
        "fuel_station refuses Petro-Canada — measured 147/173 diesel")
    assert not _admits(by_layer["def_station"], "Petro-Canada"), (
        "def_station admits Petro-Canada — the diesel measurement does not "
        "carry to AdBlue, and DEF has 21 confirmed points to its name")


# ── a layer's name is a promise ────────────────────────────────────────
#
# Two layers are CHAIN INFERENCES and could not honestly be anything
# else, so their labels and their standing notes have to say so.
#
#   DEF     21 of 1,318 imported points carried `fuel:adblue=yes`.
#   Showers the old query took every `amenity=shower` in the box — 3,532
#           points, 24 of them within 300m of a fuel station, the rest
#           state parks and campgrounds.  Narrowing it leaves THREE,
#           because `amenity=truck_stop` barely exists in US OSM.
#
# Keeping the points and fixing the promise was the owner's call.  The
# promise is the part a test can hold.


def test_the_inferred_layers_carry_a_standing_note():
    from features.live_map.poi.layers import POI_LAYER_NOTES
    for layer in ("def_station", "shower"):
        note = POI_LAYER_NOTES.get(layer)
        assert note, (
            f"{layer} is built from a brand allowlist and says nothing "
            "about it — the row reads as a confirmed fact")
        assert re.search(r"\b(chain|inferred)\b", note, re.I), note
        # No counts: a number is true until the next import and then it
        # is a lie nobody notices.
        assert not re.search(r"\d", note), (
            f"{layer}'s note carries a number that the next import "
            f"invalidates: {note!r}")


def test_the_shower_layer_no_longer_asks_for_every_shower_in_the_box():
    from features.live_map.poi.layers import POI_OVERPASS_QUERIES
    clauses = POI_OVERPASS_QUERIES["shower"]
    assert not any(c.strip() == 'node["amenity"="shower"]' for c in clauses), (
        "the catch-everything clause is back — 99.3% of what it returns "
        "is park, beach and campground showers")
    assert any('"brand"~' in c for c in clauses), (
        "without the chain clause this layer returns three points "
        "nationwide, because US truck stops are not tagged truck_stop")
