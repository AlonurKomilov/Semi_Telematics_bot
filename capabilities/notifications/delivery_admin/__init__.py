"""Delivery-destination admin routers (team groups, Sub bots, topics).
HTTP skins only — mounted by interfaces/api/app.py."""
from .routing import router as routing_router  # noqa: F401
from .forum import router as forum_router  # noqa: F401
from .forum import config_router as forum_config_router  # noqa: F401
