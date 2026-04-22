"""Shared constants for the Semi Telematics Bot."""

from zoneinfo import ZoneInfo

# US timezone constants used across the codebase
TZ_ET = ZoneInfo("America/New_York")
TZ_CT = ZoneInfo("America/Chicago")
TZ_MT = ZoneInfo("America/Denver")
TZ_PT = ZoneInfo("America/Los_Angeles")

# Unit conversion — single source of truth for meters ↔ miles
METERS_PER_MILE: float = 1609.344

# Static map tile template — used by map_renderer and alerting/parking
OSM_TILE_URL: str = "https://tile.openstreetmap.org/{z}/{x}/{y}.png"
