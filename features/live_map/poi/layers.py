"""The built-in POI layer catalogue: which layers exist, and what each
one asks OpenStreetMap for.

Data only — no I/O, no FastAPI, nothing that can fail.  A layer added
here appears in the API the moment the dashboard and panel registries
name it too (tests/test_poi_layers_agree.py holds those three together).
"""
from __future__ import annotations

import re


def point_to_feature(point: dict) -> dict:
    """One POI point as the GeoJSON both frontends already draw.

    THE ONE PLACE THAT SAYS WHAT A POI LOOKS LIKE ON THE WIRE.  There are
    two sources now — the OSM mirror, and our own table once a layer has
    been imported — and a map that drew one shape for a fresh layer and
    another for a stored one would be a bug nobody could see until a
    popup came up empty.  Pure dict-in, dict-out: no I/O, nothing that
    can fail, which is why it belongs beside the layer definitions.
    """
    return {
        "type": "Feature",
        "geometry": {"type": "Point", "coordinates": [point["lng"], point["lat"]]},
        "properties": {
            "name": point.get("name") or "",
            "osm_id": point.get("osm_id"),
            **(point.get("props") or {}),
        },
    }


# ── POI Overlay Layers ────────────────────────────────────────────────────────
#
# To ADD a new POI layer:
#   1. Add an entry to POI_OVERPASS_QUERIES below — or, for a DB-backed
#      layer, a source branch in map_pois() (see vendor_directory).
#   2. Add the matching entry to BOTH frontends' layers.ts.
#   Nothing else changes — the endpoint and the hook handle the rest, and
#   tests/test_poi_layers_agree.py holds all three lists together.
#
# To SPLIT one fetch into two layers (see the weigh-station note below):
#   1. Write the classifier — point dict in, layer id out.
#   2. Name it in POI_SPLITS.  SERVED_LAYERS picks the new layer up, and
#      with it /map/poi-versions, /map/poi-set and the importer.
#   3. Add the new layer to both frontends as above.
#   The mirror is asked exactly as often as before: the split reads tags
#   that are already in the reply.
#
# Each entry is a LIST of Overpass filter expressions — all are unioned into
# one request.  Single-element lists are the common case; multi-element lists
# let a layer combine several OSM tags (e.g. truck_stop).

POI_OVERPASS_QUERIES: dict[str, list[str]] = {
    # ── Fuel Stations (diesel-capable) ────────────────────────────────────────
    # A BRAND IS EVIDENCE ONLY FOR A CHAIN WHERE EVERY SITE QUALIFIES.
    #
    # Measured on this layer 2026-09-14, by share of a brand's points
    # that carry `fuel:diesel`, `hgv` or `fuel:HGV_diesel`:
    #
    #     TA 81%   Petro 51%   Flying J 47%   Sapp Bros 46%
    #     Pilot 37%   Road Ranger 37%   Love's 36%
    #     ...        Kwik Trip 11%      Kwik Star 7%
    #
    # Those shares measure TAGGING, not capability — OSM is sparse, which
    # is why the allowlist exists at all.  But the band is 36-81% for
    # chains that are truck stops by definition, and Kwik Trip/Kwik Star
    # sit three to five times below its floor.  They are a convenience
    # chain: some sites have truck lanes, most do not, so the NAME says
    # nothing about the pumps.  Both are gone from the list.
    #
    # It costs nothing: a Kwik Trip that OSM says has diesel still
    # arrives through the tag clauses above.  What stops is claiming it
    # for the 212 that say nothing either way.
    # Tag-based filtering misses ~70% of diesel-capable stations in the US, so
    # we supplement with a brand allowlist for major chains. Brand allowlist
    # uses `node` only (not `nwr`) — the area variant times out on CONUS bbox.
    #
    # The brand allowlist names `Petro` EXACTLY, plus its real suffixes —
    # not as a prefix.  A bare `Petro` in a prefix-anchored alternation
    # matched PETRO-CANADA, a different company in a different country,
    # and brought 539 of its stations into this layer and 662 into DEF.
    # The bbox cannot catch them either: "clipped to the USA" is a
    # RECTANGLE, and the CONUS box reaches from 24.4N (northern Mexico)
    # to 49.5N, taking in southern Ontario and Quebec whole.
    #
    # The first fix guarded only the hyphen and still let three through —
    # measured on the layer itself: Petro Canada (the spelling WITHOUT a
    # hyphen), Petro Seven, Petro Bras.  A guard written from IMAGINED
    # spellings tests imagination; the one in
    # test_poi_layers_are_not_silently_empty.py now iterates the values
    # the table actually holds.  Naming the two real American forms is
    # what closes it: a suffix cannot be enumerated, but a brand can.
    "fuel_station": [
        'node["amenity"="fuel"]["fuel:diesel"="yes"]',
        'node["amenity"="fuel"]["hgv"="yes"]',
        'nwr["amenity"="truck_stop"]',
        'node["amenity"="fuel"]["brand"~"^(Pilot|Flying J|Pilot Flying J|Love.s|TA|Petro( (Stopping|Travel).*)?$|TravelCenters|Sapp Bros|Road Ranger|Bosselman|Ambest)",i]',
    ],
    # ── DEF / AdBlue Stations ─────────────────────────────────────────────────
    # fuel:adblue=yes has ~15-25% coverage; brand allowlist catches the rest.
    #
    # MEASURED, and the real number is worse: 21 of 1,318 imported points
    # carry the tag — 1%.  The layer is a chain inference and its LABEL
    # now says so ("DEF / AdBlue (by chain)"), which was the owner's call
    # over serving 21 honest points and calling it a map.
    "def_station": [
        'node["amenity"="fuel"]["fuel:adblue"="yes"]',
        'nwr["amenity"="truck_stop"]["fuel:adblue"="yes"]',
        'nwr["amenity"="truck_stop"]["brand"~"^(Pilot|Flying J|Pilot Flying J|Love.s|TA|Petro( (Stopping|Travel).*)?$|TravelCenters|Sapp Bros|Road Ranger)",i]',
        'node["amenity"="fuel"]["brand"~"^(Pilot|Flying J|Pilot Flying J|Love.s|TA|Petro( (Stopping|Travel).*)?$|TravelCenters|Sapp Bros|Road Ranger)",i]',
    ],
    # ── Truck parking ─────────────────────────────────────────────────────────
    # Node-only — way/relation queries silently timeout on large bboxes.
    "truck_parking": [
        'node["amenity"="parking"]["truck"="yes"]',
        'node["amenity"="parking"]["hgv"="yes"]',
        'node["amenity"="parking"]["access:hgv"~"yes|designated"]',
    ],
    # ── Showers ───────────────────────────────────────────────────────────────
    #
    # NOT `node["amenity"="shower"]`, which is what this asked for until
    # 2026-09-14 and which took every shower in the box.  Measured on the
    # 3,532 points it imported: 24 of them — 0.7% — were within 300m of a
    # fuel station.  The rest were state parks, campgrounds, marinas and
    # beaches: BC Parks, sepaq, Tobyhanna State Park, Ontario Parks, a
    # Scout reservation, a `Lave-vélo`.  A driver who switched Showers on
    # got a map of park bathhouses.
    #
    # NARROWING IT WAS THE OBVIOUS FIX AND THE WRONG ONE.  Keeping only
    # the two truck-stop clauses leaves THREE points nationwide, because
    # `amenity=truck_stop` barely exists in US OpenStreetMap — one node in
    # the whole 5,394-point fuel layer.  A US truck stop is tagged
    # `amenity=fuel` plus a brand, so the clause that looks honest finds
    # nothing.
    #
    # So this is a CHAIN INFERENCE, like DEF beside it and labelled the
    # same way: a Love's has showers, and that is a real fact about the
    # chain even when no mapper has tagged the individual site.  597
    # points against 3,532 — and all 597 answer the question that was
    # asked.
    "shower": [
        'node["amenity"="fuel"]["shower"="yes"]',
        'nwr["amenity"="truck_stop"]["shower"="yes"]',
        'node["amenity"="fuel"]["brand"~"^(Pilot|Flying J|Pilot Flying J|Love.s|TA|Petro( (Stopping|Travel).*)?$|TravelCenters|Sapp Bros|Road Ranger)",i]',
    ],
    # ── Rest areas ────────────────────────────────────────────────────────────
    "rest_area": [
        'node["highway"="rest_area"]["amenity"!="truck_stop"]',
        'nwr["amenity"="rest_area"]["amenity"!="truck_stop"]',
    ],
    # ── Weigh stations ────────────────────────────────────────────────────────
    # amenity=weighbridge is the primary tag (~3800 nationwide).
    "weigh_station": [
        'nwr["amenity"="weighbridge"]',
        'nwr["highway"="weigh_station"]',
        'nwr["amenity"="vehicle_inspection"]',
        'node["highway"="motorway_junction"]["name"~"Weigh Station|Weigh Sta|Port of Entry|Inspection Station",i]',
    ],
}



# ── What a layer's points actually MEAN ───────────────────────────────
#
# A standing caveat, shown on the row whenever the layer is on.  Not a
# fault and not per-request: a fact about how the layer is BUILT, which
# the label alone has no room to carry.
#
# No numbers in these sentences.  A count is true until the next import
# and then it is a lie nobody notices — the freshness line taught this
# the hard way (9600d8e5).
POI_LAYER_NOTES: dict[str, str] = {
    # Measured 2026-09-14: 21 of 1,318 points carry `fuel:adblue=yes`.
    # The other 1,297 are here because their BRAND is on an allowlist —
    # which is useful (a Love's usually does sell DEF) and is not the
    # same claim as the label was making.  The owner's call was to keep
    # the points and fix the promise.
    "def_station":
        "Mostly inferred from the chain, not a confirmed DEF tag",
    # Measured 2026-09-14: the old query took every `amenity=shower` in
    # the box — 3,532 points, 24 of them within 300m of a fuel station.
    # The honest clauses alone return three, so this layer is a chain
    # inference too and says so.
    "shower":
        "Inferred from the chain — these stops usually have showers",
}

# ── One fetch, two layers ─────────────────────────────────────────────
#
# A WEIGHBRIDGE IS NOT A WEIGH STATION, and OpenStreetMap does not say
# which is which.  `amenity=weighbridge` means only "there is a scale
# here"; it is used for a state DOT enforcement station and for the CAT
# Scale behind a Pilot alike.  Measured on our own 4,500 imported points
# (2026-09-14): 2,160 of them — 48% — carry a truck-stop chain as their
# operator or brand.  Love's 513, Pilot 370, Flying J 197, TA 173, CAT
# Scale 157.
#
# The two answer different questions and one of them is not optional:
#
#   Weigh Stations (DOT)  where a truck MUST stop.  Free, enforced.
#   Truck Scales          where a driver MAY weigh a load.  Pay per weigh.
#
# Serving them as one layer meant a driver asking "where must I stop"
# got a map half made of places that cannot answer, which is how a map
# stops being trusted.
#
# Split at IMPORT and not at fetch: one Overpass query still goes out, so
# the volunteer mirrors are asked exactly as often as before.  Every tag
# the split needs is already in the reply.

#: Operators and brands whose scales are commercial.  Word-boundary
#: matched, which `TA` makes mandatory — a substring test files every
#: MONTANA and INTERSTATE scale under the wrong layer.
_TRUCK_SCALE_CHAIN = re.compile(
    r"\b(cat\s*scale|pilot|flying\s*j|love'?s|travelcenters?\s+of\s+america|ta"
    r"|petro|road\s+ranger|kwik\s+(?:trip|star)|one9|circle\s+k|casey'?s"
    r"|speedco|sapp\s+bros|ambest|bosselman|maverik|sheetz|wawa|quiktrip"
    r"|racetrac|travel\s+(?:center|stop|plaza)|truck\s*stop)\b",
    re.IGNORECASE,
)


#: A name that says "enforcement" outright.  It BEATS a chain word in
#: the same name, and it has to: the fourth clause of the weigh-station
#: query matches `highway=motorway_junction` nodes on NAME ALONE, and
#: those carry no amenity, operator or brand to check instead.  US place
#: names do the rest of the damage — Pilot Mountain, Pilot Knob, Pilot
#: Rock and Pilot Point are all real, so "Pilot Mountain Weigh Station"
#: is a shape this query returns and a chain test would file under
#: Truck Scales.  That is the direction that costs a driver a violation.
#: NO TRAILING \b on the prefix alternatives.  The first cut of this
#: pattern ended in one and so could not match the very case it was
#: written for: "stat" followed by "ion" is not a word boundary, so
#: "Weigh Station" fell through to the chain test.  A regex that looks
#: right is not one that has been run.
_ENFORCEMENT_NAME = re.compile(
    r"\b(?:weigh\s*sta\w*|port\s+of\s+entry|inspection\s+stat\w*"
    r"|scale\s*house|dot\b|d\.o\.t|highway\s+patrol|state\s+patrol"
    r"|department\s+of\s+transportation)",
    re.IGNORECASE,
)


def weigh_station_layer(point: dict) -> str:
    """Which of the two weigh-station layers this point belongs to.

    UNKNOWN GOES TO DOT, deliberately.  2,142 of the imported points
    carry no operator at all, and the two mistakes are not equal: an
    extra pin on the DOT layer costs a driver one look, while a
    mandatory stop filed under Truck Scales costs them a violation.  The
    safe default is the one that cannot be missed.

    EVIDENCE IS RANKED, not just tested in any order.  A tag was put
    there by somebody who surveyed the place; a name is whatever the
    place is called, and the query deliberately matches some nodes on
    nothing else.  So tags decide first, and where only a name is
    available the reading that cannot hurt wins.
    """
    props = point.get("props") or {}
    name = point.get("name") or ""

    # ── tag evidence: strong, someone surveyed it ────────────────────
    #
    # An inspection station is enforcement by definition — 515 of them
    # against 2 that carry a chain.
    if (props.get("amenity") or "").lower() == "vehicle_inspection":
        return "weigh_station"
    who = f"{props.get('operator') or ''} | {props.get('brand') or ''}"
    if _TRUCK_SCALE_CHAIN.search(who):
        return "truck_scale"

    # ── name evidence: weak, and only one direction of it is safe ────
    if _ENFORCEMENT_NAME.search(name):
        return "weigh_station"
    if _TRUCK_SCALE_CHAIN.search(name):
        return "truck_scale"
    return "weigh_station"


#: Fetched layer → the layers its points are filed under, and the
#: function that decides.  A layer absent here is its own single layer.
POI_SPLITS: dict[str, tuple[tuple[str, ...], object]] = {
    "weigh_station": (("weigh_station", "truck_scale"), weigh_station_layer),
}


def split_of(layer: str):
    """The classifier for a fetched layer, or None if it does not split."""
    entry = POI_SPLITS.get(layer)
    return entry[1] if entry else None


#: EVERY layer a client may ask for.
#:
#: Not the same set as POI_OVERPASS_QUERIES any more, and the difference
#: is the point: that dict says what we ASK THE MIRROR, this says what we
#: SERVE.  One fetch can produce two layers, so a reader who uses the
#: query dict to answer "what layers are there" now gets the wrong list.
SERVED_LAYERS: tuple[str, ...] = tuple(
    name
    for fetched in POI_OVERPASS_QUERIES
    for name in (POI_SPLITS[fetched][0] if fetched in POI_SPLITS else (fetched,))
)


def fetch_layer_for(served: str) -> str | None:
    """Which fetch produces this served layer — itself, unless it is one
    half of a split.  None if we do not serve it at all."""
    if served in POI_OVERPASS_QUERIES:
        return served
    for fetched, (names, _fn) in POI_SPLITS.items():
        if served in names:
            return fetched
    return None
