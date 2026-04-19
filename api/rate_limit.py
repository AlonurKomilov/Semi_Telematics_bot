"""Shared rate limiter instance for the API."""

from starlette.requests import Request
from slowapi import Limiter


def _get_real_ip(request: Request) -> str:
    """Extract client IP from X-Forwarded-For (behind reverse proxy) or fall back to
    the direct connection address.  Takes only the first (leftmost) IP in the chain."""
    xff = request.headers.get("x-forwarded-for", "")
    if xff:
        # First entry is the original client IP
        return xff.split(",")[0].strip()
    return request.client.host if request.client else "unknown"


# Keyed by real client IP via X-Forwarded-For; default 60 req/min per IP
limiter = Limiter(key_func=_get_real_ip, default_limits=["60/minute"])
