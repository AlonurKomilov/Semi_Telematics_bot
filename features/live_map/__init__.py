"""Live Map — the account's vehicles on a map, and what is drawn under them.

POI layers (fuel, DEF, truck parking, showers, weigh stations, rest areas,
repair shops, plus an account's own) are a SUB-feature and live in ``poi/``.
"""

from features.live_map.service import get_vehicles_for_map  # noqa: F401
