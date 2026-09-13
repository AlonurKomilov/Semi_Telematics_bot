"""Request metering — Redis counters shared across every API worker.

WHY REDIS: the API runs as N gunicorn workers, each holding its own
in-process metric registry — scraping those gives a random worker's
fragment, never the platform total (verified live: two /metrics reads
30s apart produced a NEGATIVE request delta).  Redis is the one store
all workers already share, so counters here are platform-true.

Cost per request: three best-effort INCRs on a local Redis (~sub-ms,
and ``infra.cache`` silently no-ops when Redis is down — metering must
never add a failure mode to the request path).

Key layout (all transient — durable history lives in Postgres via the
sampler/flush jobs):

  ``sysreq:min:<YYYY-MM-DDTHH:MM>``  total requests that UTC minute
  ``sysreq:surf:<YYYY-MM-DD>``       hash: interface surface → count
  ``sysreq:feat:<YYYY-MM-DD>``       hash: feature (route family) → count
  ``sysreq:acct:<YYYY-MM-DD>``       hash: account_id → count
"""

from __future__ import annotations

import re
from datetime import datetime, timedelta, timezone

import infra.cache as cache

# Minute counters only need to survive until the sampler reads the
# previous minute; hours of slack cost nothing.
_MINUTE_TTL = 2 * 3600
# Daily hashes need to survive until the nightly flush (00:10 UTC next
# day) plus generous slack for a missed run.
_DAY_TTL = 3 * 86400

# Interface surface labels — named after the INTERFACE (a genuinely
# per-surface artifact), not a persona: every dashboard subdomain is
# the same SPA, so they meter as one surface.
_HOST_SURFACES = {
    "api": "api",
    "app": "miniapp",
    "bot": "bot",
    "system": "console",
}


def surface_for_host(host: str) -> str:
    """Map a request Host header to its interface surface."""
    label = (host or "").split(":")[0].split(".")[0].lower()
    return _HOST_SURFACES.get(label, "dashboard")


def minute_key(dt: datetime) -> str:
    return dt.strftime("%Y-%m-%dT%H:%M")


# Route families are a small finite set (the first path segment after
# /api[/v1]), but the meter must never trust that: anything outside
# this shape folds into 'other' so a probing crawler can't explode the
# hash cardinality.
_FEATURE_RE = re.compile(r"^[a-z0-9_-]{1,32}$")


def feature_for_path(path: str) -> str:
    """Map a request path to its feature (route family).

    ``/api/v1/work-orders/12/attachments`` → ``work-orders``;
    ``/api/vehicles/103`` → ``vehicles``.  Unknown shapes → 'other'.
    """
    parts = [p for p in (path or "").split("/") if p]
    if parts and parts[0] == "api":
        parts = parts[1:]
    if parts and parts[0] == "v1":
        parts = parts[1:]
    seg = (parts[0] if parts else "").lower()
    return seg if _FEATURE_RE.match(seg) else "other"


#: A proxied map tile is a request this server handled, but it is not an
#: API CALL and must not be counted as one.
#:
#: The browser panel cannot fetch Google's tiles directly — the platform
#: key is HTTP-referrer restricted and an extension page sends no
#: referer — so they come through ``/map/tile``.  One map view is about
#: nine of them and a drag is hundreds.  Folded into the ordinary
#: counters, a single dispatcher panning for a minute reads on the
#: Capacity page as an account hammering the API, and the per-feature
#: ranking becomes "map" and nothing else.
#:
#: They are still LOAD, so they still count toward the per-minute total
#: and the per-surface split.  What changes is that they get their own
#: feature row instead of drowning the map's real endpoints, and they
#: stay out of the per-account API-call number.
TILE_FEATURE = "map-tiles"
_TILE_PATH_RE = re.compile(r"^/api(?:/v\d+)?/map/tile(?:-copyright)?/?$")


def is_tile_path(path: str) -> bool:
    """Whether this path is a proxied third-party tile fetch."""
    return bool(_TILE_PATH_RE.match(path or ""))


async def count_request(host: str, account_id: int | None, path: str = "") -> None:
    """Meter one completed request.  Best-effort, never raises."""
    now = datetime.now(timezone.utc)
    day = now.strftime("%Y-%m-%d")
    tile = is_tile_path(path)
    await cache.incr(f"sysreq:min:{minute_key(now)}", _MINUTE_TTL)
    await cache.hincrby(f"sysreq:surf:{day}", surface_for_host(host), _DAY_TTL)
    await cache.hincrby(
        f"sysreq:feat:{day}", TILE_FEATURE if tile else feature_for_path(path), _DAY_TTL)
    # Per-account is the API-CALL number.  A tile is a picture we
    # forwarded, priced by Google per request rather than by our database
    # — a different resource, and averaging the two hides both.
    if account_id is not None and not tile:
        await cache.hincrby(f"sysreq:acct:{day}", str(int(account_id)), _DAY_TTL)


async def requests_last_minute(now: datetime | None = None) -> int | None:
    """The PREVIOUS full minute's total (the current one is still
    accumulating).  None = Redis unavailable (distinct from 0)."""
    now = now or datetime.now(timezone.utc)
    prev = now - timedelta(minutes=1)
    return await cache.get_int(f"sysreq:min:{minute_key(prev)}")


async def account_counts(day: str) -> dict[int, int]:
    """Per-account request counts for a UTC day (``YYYY-MM-DD``)."""
    raw = await cache.hgetall_int(f"sysreq:acct:{day}")
    out: dict[int, int] = {}
    for k, v in raw.items():
        try:
            out[int(k)] = v
        except (TypeError, ValueError):
            continue
    return out


async def surface_counts(day: str) -> dict[str, int]:
    """Per-surface request counts for a UTC day."""
    return await cache.hgetall_int(f"sysreq:surf:{day}")


async def feature_counts(day: str) -> dict[str, int]:
    """Per-feature (route family) request counts for a UTC day."""
    return await cache.hgetall_int(f"sysreq:feat:{day}")
