"""Location service — live map data + vehicle status detection."""

from __future__ import annotations

import asyncio
import logging
import time

import infra.cache as rcache
from features.vehicles.service import get_vehicles_overview
from infra.services import get_client

logger = logging.getLogger(__name__)


# Speed threshold for distinguishing moving vs idle (mph)
_MOVING_THRESHOLD = 2


def classify_vehicle_status(vehicle: dict) -> str:
    """Determine vehicle status from speed + (preferably) real engine state.

    Returns one of: 'moving', 'idle', 'stopped'.
    Used by both bot/maps.py (PNG rendering) and api/routes/maps.py (GeoJSON).

    Prefers the authoritative CAN-bus ``engineState`` value when present
    (put on the row by ``get_vehicles_for_map`` — from the warehouse row
    itself, or from /fleet/vehicles/stats?types=engineStates when the row
    arrived without one).  Falls back to a speed-only heuristic for
    vehicles whose Samsara plan doesn't expose engineStates or for paths
    that don't merge that data (e.g. ``features.location`` consumers
    other than the map).
    """
    loc = vehicle.get("location", {})
    speed = loc.get("speedMilesPerHour", vehicle.get("speed_mph", 0)) or 0
    engine = vehicle.get("engineState") or loc.get("engineState")

    # Authoritative path: real engineState value present
    if engine in ("On", "Idle", "Off"):
        if engine == "On" and speed > _MOVING_THRESHOLD:
            return "moving"
        if engine in ("On", "Idle"):
            return "idle"
        return "stopped"

    # Heuristic fallback (engineState unavailable)
    if speed > _MOVING_THRESHOLD:
        return "moving"
    if speed > 0:
        return "idle"
    return "stopped"


# Status → marker color for map rendering
STATUS_COLORS = {
    "moving": "#22c55e",   # green
    "idle": "#eab308",     # yellow
    "stopped": "#ef4444",  # red
}


# ── engineStates cache ───────────────────────────────────────────────────────
# Samsara enforces ~100 req/min per token.  /map/vehicles is hit by the
# dashboard every 30s.  This call is the FALLBACK path only — a row the
# warehouse served already carries its engine word (see
# ``_engine_state_on_row``) and never reaches it; a row from the live
# Samsara overview does not, and for those caching engineStates for 15s
# gives an upper bound of 4 calls/min while keeping marker accuracy
# within one polling cycle of reality.
_ENGINE_STATES_TTL = 15.0  # seconds

#: The only three words the classifier trusts, in the provider's own
#: vocabulary — what the wire's ``engine_state`` has always carried.
_ENGINE_WORDS = ("On", "Idle", "Off")

#: The warehouse speaks a different vocabulary: the ingest resolves the
#: provider's word against road speed and stores ``moving`` / ``idle``
#: / ``off`` (capabilities/integrations/shared/engine_state.py), and the
#: reader hands that word back under the provider's key.  Translated
#: here, at the one consumer that must answer in provider words, so the
#: wire contract does not change.  An empty string is an ingest that had
#: nothing to resolve; anything unlisted means "not known here", and the
#: row goes to the provider like one that carried nothing.
_WAREHOUSE_WORDS = {"moving": "On", "idle": "Idle", "off": "Off"}
_engine_states_cache: dict[int, tuple[float, dict[str, str]]] = {}


async def _get_engine_states_by_id(account_id: int) -> dict[str, str]:
    """Return {vehicle_id: engineState} for the given account.

    Cached for _ENGINE_STATES_TTL seconds per account.  Returns {} on any
    error (including Samsara plans that don't support engineStates) so the
    caller can degrade gracefully to the speed-based heuristic.
    """
    now = time.monotonic()
    cached = _engine_states_cache.get(account_id)
    if cached and (now - cached[0]) < _ENGINE_STATES_TTL:
        return cached[1]

    try:
        client = await get_client(account_id)
        rows = await client.get_engine_states()
        by_id: dict[str, str] = {}
        for r in rows:
            vid = r.get("id")
            es = r.get("engineStates") or {}
            value = es.get("value") if isinstance(es, dict) else None
            if vid and value in _ENGINE_WORDS:
                by_id[str(vid)] = value
    except Exception:
        by_id = {}

    _engine_states_cache[account_id] = (now, by_id)
    return by_id


def _engine_state_on_row(v: dict) -> str | None:
    """The engine word a row already carries, or None.

    A warehouse-served row keeps the ingest's resolved word under
    ``location.engineStates.value`` (the reshape in
    features/vehicles/warehouse/readers.py); a live Samsara overview row
    carries none — the provider sends that word on a separate stats
    call, never on the overview.  So "is the word on the row" is exactly
    "did the warehouse serve this row", and it decides whether the
    provider must be asked at all.
    """
    es = (v.get("location") or {}).get("engineStates")
    value = es.get("value") if isinstance(es, dict) else None
    if value in _ENGINE_WORDS:
        return value
    return _WAREHOUSE_WORDS.get(str(value or "").strip().lower())


async def get_vehicles_for_map(
    account_id: int,
    company: str | None = None,
) -> list[dict]:
    """Fetch the vehicle overview with a real engine state on every row
    that can have one, for the live map.

    The word comes from the row itself when the warehouse served it —
    the ingest resolved it a minute ago at most, against the same speed
    the row carries.  Only rows that arrived WITHOUT one (the live
    Samsara fallback, whose overview payload never has it) cost a
    provider round-trip, and that one is cached.  Before this the map
    asked Samsara for engine states on every refresh, for every
    account, while holding rows that already said the answer.

    When engineStates are unavailable (older Samsara plans, transient
    API errors) the row stays wordless and ``classify_vehicle_status``
    falls back to speed-only.
    """
    overview = await get_vehicles_overview(account_id, company=company)
    wordless: list[dict] = []
    for v in overview:
        own = _engine_state_on_row(v)
        if own:
            v["engineState"] = own
        else:
            wordless.append(v)
    if wordless:
        engine_by_id = await _get_engine_states_by_id(account_id)
        for v in wordless:
            es = engine_by_id.get(str(v.get("id")))
            if es:
                v["engineState"] = es
    return overview


# ── live positions: one fan-out per account, shared by every poller ──────────
#
# Three surfaces poll ``/map/vehicles/live`` every five seconds — the
# dashboard's Live Map, the browser extension's panel, and its overlay on
# google.com/maps — and until this section each poll ran its own provider
# fan-out, one call per company.  So the provider load scaled with the
# number of open tabs: three viewers on a five-company account were 180
# calls a minute, against an ingest that costs eleven.
#
# Now the fan-out happens at most once per TTL per account, and every
# poller reads the same snapshot.  The snapshot has two homes: this
# worker's memory, and Redis for the other API workers — gunicorn runs
# three, and a poller lands on any of them, so a per-worker cache alone
# would still be three fan-outs.  Redis is best-effort: when it is down,
# infra.cache's get/set are no-ops and this degrades to the per-worker
# cache, never to an error.
#
# What is cached is the RAW provider rows, per company, before any
# per-user narrowing.  Company restriction and the assigned-truck scope
# are applied per request by the router, on top of the snapshot — a
# snapshot keyed only by account can never carry one member's view to
# another.

#: How long a snapshot is served before the provider is asked again.
#: The pollers run at five seconds, so this is one fan-out per poll
#: cycle for the whole account, however many tabs are open.
LIVE_TTL_S = 5.0
#: A fan-out that fails keeps serving the last snapshot this long.  A
#: provider hiccup is no reason for every marker to vanish; a snapshot
#: older than this is.  Also the Redis TTL, so a neighbouring worker
#: can serve the same stale snapshot — freshness is judged by the
#: fetch time inside the value, never by the key's expiry.
LIVE_STALE_OK_S = 30.0
_LIVE_KEY = "map:live:{account_id}"

_live_local: dict[int, dict] = {}
_live_locks: dict[int, asyncio.Lock] = {}


def _live_lock(account_id: int) -> asyncio.Lock:
    lock = _live_locks.get(account_id)
    if lock is None:
        lock = _live_locks[account_id] = asyncio.Lock()
    return lock


def _snapshot_age(snap, now: float) -> float | None:
    """Seconds since the snapshot was fetched, or None when it is not a
    snapshot at all (a Redis value some other version wrote, a decode
    that came back as something else)."""
    if not isinstance(snap, dict) or not isinstance(snap.get("companies"), dict):
        return None
    try:
        fetched = float(snap.get("fetched_at"))
    except (TypeError, ValueError):
        return None
    return max(0.0, now - fetched)


def _fresh(snap, now: float) -> bool:
    age = _snapshot_age(snap, now)
    return age is not None and age < LIVE_TTL_S


async def _fetch_positions(account_id: int) -> dict[str, list[dict]]:
    """One provider fan-out: every company's raw location rows, keyed by
    company code.  Kept separate so tests replace it.  A company whose
    call failed is simply absent — the multi-company client logs and
    skips it, as it always has."""
    client = await get_client(account_id)

    async def _get_locs(c):
        return await c.get_locations()

    per_co = await client._run_per_company(_get_locs)
    return {code: rows for code, rows in per_co.items() if isinstance(rows, list)}


async def live_snapshot(account_id: int) -> dict[str, list[dict]]:
    """The account's current raw positions, per company.

    Read order: this worker's copy, then the shared one, then one
    fan-out that every concurrent caller waits for rather than
    repeating.  When the fan-out fails, a snapshot younger than
    ``LIVE_STALE_OK_S`` is served instead; older than that, the error
    is the caller's, as it was before any of this existed.
    """
    now = time.time()
    local = _live_local.get(account_id)
    if _fresh(local, now):
        return local["companies"]
    key = _LIVE_KEY.format(account_id=account_id)
    shared = await rcache.get(key)
    if _fresh(shared, now):
        _live_local[account_id] = shared
        return shared["companies"]
    async with _live_lock(account_id):
        # Re-check both homes: the caller that held this lock a moment
        # ago, or a worker next door, may already have refreshed.
        now = time.time()
        local = _live_local.get(account_id)
        if _fresh(local, now):
            return local["companies"]
        shared = await rcache.get(key)
        if _fresh(shared, now):
            _live_local[account_id] = shared
            return shared["companies"]
        try:
            companies = await _fetch_positions(account_id)
        except Exception:
            # The fetch that just failed may have spent the provider's
            # whole timeout; the bound is measured from NOW, not from
            # before it started.
            now = time.time()
            for stale in (local, shared):
                age = _snapshot_age(stale, now)
                if age is not None and age < LIVE_STALE_OK_S:
                    logger.warning(
                        "live positions: fan-out failed for acct=%d; serving "
                        "a %.0fs-old snapshot", account_id, age)
                    return stale["companies"]
            raise
        snap = {"fetched_at": time.time(), "companies": companies}
        _live_local[account_id] = snap
        # A snapshot nobody could be served any more is not worth a
        # worker's memory: the copy holds the accounts watched lately.
        for other in [a for a, c in _live_local.items()
                      if a != account_id and not _snapshot_age(c, snap["fetched_at"]) < LIVE_STALE_OK_S]:
            _live_local.pop(other, None)
        await rcache.cache_set(key, snap, ttl=max(1, int(LIVE_STALE_OK_S)))
        return companies


def reset_live_snapshot_for_tests() -> None:
    _live_local.clear()
    _live_locks.clear()
