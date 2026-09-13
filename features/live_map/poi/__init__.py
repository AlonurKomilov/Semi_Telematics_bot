"""POI overlay layers — Live Map's sub-feature.

A POI layer is public map furniture (fuel, DEF, truck parking, showers,
weigh stations, rest areas, repair shops) plus whatever the account has
drawn for itself.  It is a SUB-feature of Live Map, not a feature of its
own: it has no navigation entry of its own, it is only ever seen ON the
map, and its two grants sit under the map's row in the permission matrix
(``can_view_poi`` to see the layers, ``can_manage_poi_layers`` to author
the account's own).

URL structure (mounted under /map):
    GET    /map/pois                              built-in or custom POI fetch
    GET    /map/custom-layers                     list custom layers
    POST   /map/custom-layers                     create custom (overpass/csv)
    PATCH  /map/custom-layers/{id}                edit custom layer
    DELETE /map/custom-layers/{id}                soft-delete custom layer
    POST   /map/custom-layers/from-pin            pin-drop UX shortcut
    POST   /map/custom-layers/preview-pin         non-persistent preview
    GET    /map/custom-layers/brand-search        type-ahead brand picker
    POST   /map/custom-layers/from-brand          persist a previewed brand
    POST   /map/custom-layers/{id}/csv            replace CSV-source points

The modules, and the line between them:

    layers.py    WHICH built-in layers exist and what each asks OSM for
    viewport.py  WHERE a request may look (the US clip, bbox maths) and
                 what is remembered of the answer — the cache holds
                 features from OSM, from the vendor directory and from an
                 account's own CSV alike, so it belongs to no one source
    overpass.py  the OSM source: session, mirrors, retries, the check that
                 tells a refusal from an empty area, and the validator for
                 an admin-supplied query
    custom.py    per-tenant layers: the DTOs, and serving one
    router.py    the HTTP surface — the only file here that may import
                 interfaces.api.deps

This package deliberately does NOT re-export the APIRouter under the name
``router``, so ``from features.live_map.poi import router`` always means
the MODULE and ``poi.router.router`` is always the APIRouter.  Reach the
Overpass client through ``overpass.<name>`` rather than importing its
functions by name: a name bound at import time cannot be patched by a
test that patches the module.
"""
