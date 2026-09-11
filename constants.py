"""Shared constants for the 4truck Bot."""

from zoneinfo import ZoneInfo

# US timezone constants used across the codebase
TZ_ET = ZoneInfo("America/New_York")
TZ_CT = ZoneInfo("America/Chicago")
TZ_MT = ZoneInfo("America/Denver")
TZ_PT = ZoneInfo("America/Los_Angeles")

# Unit conversion — single source of truth for meters ↔ miles
METERS_PER_MILE: float = 1609.344

# Static map tile template — used by map_renderer and alerting/parking
# NOT OpenStreetMap's servers any more, and the name is kept only so the
# two readers below do not have to change in the same commit.
#
# This constant is the heaviest user of tiles in the product: the server
# RENDERS 800x600 static maps by downloading a dozen or more tiles per
# report, from one IP, with a library's default User-Agent.  OSM's usage
# policy forbids exactly that — bulk downloading and heavy use without
# prior permission — and on 2026-09-11 the product was blocked: tiles
# came back as a PICTURE saying "403 Access blocked".
#
# The browser panel was where the owner SAW it, and the panel was fixed
# first, but this is the likelier cause of the block and would have kept
# earning it.  Esri's keyless endpoint answers the same template.
STATIC_TILE_URL: str = (
    "https://server.arcgisonline.com/ArcGIS/rest/services/"
    "World_Street_Map/MapServer/tile/{z}/{y}/{x}"
)
#: Deprecated alias — the old name, so a caller written before the move
#: still resolves.  Same object, so the two can never disagree.
OSM_TILE_URL: str = STATIC_TILE_URL
