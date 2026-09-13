"""The built-in POI layer catalogue: which layers exist, and what each
one asks OpenStreetMap for.

Data only — no I/O, no FastAPI, nothing that can fail.  A layer added
here appears in the API the moment the dashboard and panel registries
name it too (tests/test_poi_layers_agree.py holds those three together).
"""
from __future__ import annotations


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
#   2. Add the matching entry to poiLayers.ts in the dashboard config.
#   Nothing else changes — the endpoint and the hook handle the rest.
#
# Each entry is a LIST of Overpass filter expressions — all are unioned into
# one request.  Single-element lists are the common case; multi-element lists
# let a layer combine several OSM tags (e.g. truck_stop).

POI_OVERPASS_QUERIES: dict[str, list[str]] = {
    # ── Fuel Stations (diesel-capable) ────────────────────────────────────────
    # Tag-based filtering misses ~70% of diesel-capable stations in the US, so
    # we supplement with a brand allowlist for major chains. Brand allowlist
    # uses `node` only (not `nwr`) — the area variant times out on CONUS bbox.
    "fuel_station": [
        'node["amenity"="fuel"]["fuel:diesel"="yes"]',
        'node["amenity"="fuel"]["hgv"="yes"]',
        'nwr["amenity"="truck_stop"]',
        'node["amenity"="fuel"]["brand"~"^(Pilot|Flying J|Pilot Flying J|Love.s|TA|Petro|TravelCenters|Sapp Bros|Road Ranger|Kwik Trip|Kwik Star|Bosselman|Ambest)",i]',
    ],
    # ── DEF / AdBlue Stations ─────────────────────────────────────────────────
    # fuel:adblue=yes has ~15-25% coverage; brand allowlist catches the rest.
    "def_station": [
        'node["amenity"="fuel"]["fuel:adblue"="yes"]',
        'nwr["amenity"="truck_stop"]["fuel:adblue"="yes"]',
        'nwr["amenity"="truck_stop"]["brand"~"^(Pilot|Flying J|Pilot Flying J|Love.s|TA|Petro|TravelCenters|Sapp Bros|Road Ranger)",i]',
        'node["amenity"="fuel"]["brand"~"^(Pilot|Flying J|Pilot Flying J|Love.s|TA|Petro|TravelCenters|Sapp Bros|Road Ranger)",i]',
    ],
    # ── Truck parking ─────────────────────────────────────────────────────────
    # Node-only — way/relation queries silently timeout on large bboxes.
    "truck_parking": [
        'node["amenity"="parking"]["truck"="yes"]',
        'node["amenity"="parking"]["hgv"="yes"]',
        'node["amenity"="parking"]["access:hgv"~"yes|designated"]',
    ],
    # ── Showers ───────────────────────────────────────────────────────────────
    "shower": [
        'node["amenity"="shower"]',
        'nwr["amenity"="truck_stop"]["shower"="yes"]',
        'node["amenity"="fuel"]["shower"="yes"]',
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
