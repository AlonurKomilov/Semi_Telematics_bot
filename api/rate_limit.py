"""Shared rate limiter instance for the API."""

from slowapi import Limiter
from slowapi.util import get_remote_address

# Keyed by client IP via X-Forwarded-For; default 60 req/min per IP
limiter = Limiter(key_func=get_remote_address, default_limits=["60/minute"])
