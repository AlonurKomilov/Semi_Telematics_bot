"""Core service functions — platform-agnostic business helpers.

Provides telematics provider management, database access helpers, and
rate limiting.  These are the canonical definitions — ``bot.state``
and ``bot.config`` re-export them for backward compatibility.

Telematics provider resolution
------------------------------
``get_telematics_client(account_id, provider_id)`` is the canonical
vendor-neutral entry point.  It reads the account's integration row
to determine which provider class to use, builds the underlying
client, and wraps it in the protocol-shaped provider instance the
caller works against.

``get_client(account_id)`` is preserved as a back-compat shim that
returns the raw ``MultiCompanyClient`` for the Samsara provider —
keeps the dozens of existing imports working unchanged while new
code migrates to the protocol-shaped surface.
"""

import asyncio
import logging
import time
from typing import TYPE_CHECKING

from cachetools import LRUCache

from adapters.telematics.samsara.client import MultiCompanyClient, build_multi_company_client
from infra.config import SAMSARA_BASE_URL, RATE_LIMIT_SECONDS

# Re-export platform DB accessors so non-bot code has a single import.
from infra.platform import get_platform_db, get_tenant_db, get_db  # noqa: F401

logger = logging.getLogger(__name__)

if TYPE_CHECKING:
    from adapters.telematics.protocol import TelematicsProvider

# ── Client cache ─────────────────────────────────────────────────

# Two caches, one canonical state.  ``_client_cache`` keeps the raw
# Samsara ``MultiCompanyClient`` instances for the legacy ``get_client``
# entry point; ``_provider_cache`` keeps the protocol-shaped wrappers
# for the new ``get_telematics_client`` entry point.  The provider
# wrapper holds a reference to the same underlying client, so closing
# either drops both — see ``invalidate_client``.
_client_cache: dict[int, MultiCompanyClient] = {}
_provider_cache: dict[tuple[int, str], "TelematicsProvider"] = {}
_client_lock = asyncio.Lock()


async def get_client(
    account_id: int,
    *,
    prefetch: bool = True,
) -> MultiCompanyClient:
    """Get or build a ``MultiCompanyClient`` for an account.

    Back-compat shim — preserved so dozens of existing imports
    continue to resolve.  New code should call
    ``get_telematics_client(account_id)`` instead, which returns a
    protocol-shaped wrapper that survives a future provider swap.

    Per-company API keys are sourced from the Integration's
    ``credentials["companies"]`` map (canonical, set via the
    Integrations dashboard).  ``build_multi_company_client`` falls
    back to the legacy ``companies.samsara_api_key`` column when
    no integration entry exists yet — that keeps the four legacy
    read sites working during the cutover.

    ``prefetch`` controls whether ``prefetch_org_ids`` runs on
    first-build.  Default ``True`` preserves the legacy behaviour
    for callers that need org IDs immediately (vehicle URL builders,
    audit log writers).  ``test_connection`` passes ``False`` because
    that path does its own direct /me probe and prefetch's retry
    chain (5s × 3 retries × N companies) was eating its 12s budget
    before the probe even got to run.
    """
    if account_id in _client_cache:
        return _client_cache[account_id]
    async with _client_lock:
        if account_id in _client_cache:
            return _client_cache[account_id]
        db = get_db()
        companies = await db.get_account_companies(account_id)
        # Pull the per-company creds map from the integration row.
        creds_map: dict[str, str] = {}
        fallback_token = ""
        try:
            from infra.platform import get_platform_db
            platform_db = get_platform_db()
            integ = await platform_db.get_account_integration(
                account_id, "samsara",
            )
            if integ and integ.credentials:
                creds_map = integ.credentials.get("companies") or {}
                fallback_token = integ.credentials.get("api_token") or ""
        except Exception:
            # Integration row not yet seeded (fresh install, mid-
            # migration) — the per-company column fallback in
            # build_multi_company_client keeps things working.
            logger.exception(
                "get_client: failed to load integration creds acct=%d",
                account_id,
            )
        client = build_multi_company_client(
            companies, SAMSARA_BASE_URL, account_id=account_id,
            creds_map=creds_map, fallback_token=fallback_token,
        )
        if prefetch:
            await client.prefetch_org_ids()
        _client_cache[account_id] = client
        return client


async def get_telematics_client(
    account_id: int,
    provider_id: str = "samsara",
    *,
    prefetch: bool = True,
) -> "TelematicsProvider":
    """Resolve the protocol-shaped telematics client for an account.

    Reads the registered provider class from
    ``adapters.telematics.registry`` and constructs an instance over
    whatever underlying client that provider needs.  For Samsara the
    underlying client is the existing ``MultiCompanyClient``; for
    future providers (Motive / Geotab / Datatruck) it will be
    whatever each vendor's adapter requires.

    ``prefetch=False`` skips the org-id prefetch step on first build
    — for use by the test-connection probe which has its own direct
    /me path and can't afford to wait on the legacy retry chain.

    The wrapper is cached per ``(account_id, provider_id)`` pair so
    repeated calls return the same instance.  Cache invalidation
    happens through ``invalidate_client(account_id)`` — the provider
    wrapper holds a reference to the same underlying ``MultiCompanyClient``
    that ``get_client`` returns, so closing one drops both.
    """
    key = (account_id, provider_id)
    cached = _provider_cache.get(key)
    if cached is not None:
        return cached

    from adapters.telematics.registry import get_provider as _get_provider_cls
    provider_cls = _get_provider_cls(provider_id)

    # Build the underlying client OUTSIDE any wrapper-side lock.
    # ``get_client`` takes ``_client_lock`` itself when it has work
    # to do; holding the same lock here while awaiting it self-
    # deadlocks on a cold cache (asyncio.Lock is not reentrant —
    # one task acquiring its own held lock waits forever).  The
    # symptom is a route timing out at exactly the wait_for budget
    # (8s/12s) with an "upstream slow" banner that's actually our
    # own timeout, not a real upstream condition.
    if provider_id == "samsara":
        client = await get_client(account_id, prefetch=prefetch)
    else:
        # Defensive: a registered-but-unhandled provider should
        # surface as a clear error rather than crash with
        # ``TypeError`` somewhere deeper.  Future provider work
        # will add explicit construction branches above.
        raise NotImplementedError(
            f"telematics provider {provider_id!r} is registered but "
            "has no construction path in get_telematics_client; add a "
            "branch here when its adapter lands."
        )

    # Wrapper construction is a single sync call — no need to lock.
    # If two concurrent tasks race here they both build trivial
    # wrappers over the same underlying client; the second dict
    # assignment wins and the loser's wrapper is GC'd.  Both
    # reference the same MultiCompanyClient so no resource leak.
    cached = _provider_cache.get(key)
    if cached is not None:
        return cached
    instance = provider_cls(client)
    _provider_cache[key] = instance
    return instance


async def invalidate_client(account_id: int):
    """Drop the cached clients for an account.

    Called after adding / removing companies, after a credential
    rotation, after disconnecting an integration.  Closes the
    underlying HTTP session and drops both the legacy
    ``MultiCompanyClient`` and any protocol-shaped wrappers built on
    top of it.
    """
    old = _client_cache.pop(account_id, None)
    if old:
        await old.close()
    # Drop every provider wrapper for this account.  Provider
    # ``close`` delegates to the underlying client, which is already
    # closed above — calling close on the wrapper is a no-op cleanup.
    for key in [k for k in _provider_cache if k[0] == account_id]:
        _provider_cache.pop(key, None)


async def get_user_company_codes(account_id: int) -> list[str]:
    """Get sorted company codes for an account."""
    db = get_db()
    companies = await db.get_account_companies(account_id)
    return [o.code for o in companies]


# ── Rate limiting ────────────────────────────────────────────────

_rate_limits = LRUCache(maxsize=10_000)

# ── Shared UI state (used by both bot/ and capabilities/alerting/) ───

_active_messages: dict = LRUCache(maxsize=5_000)  # (account_id, chat_id, user_id) → [msg_ids]


def check_rate_limit(user_id: int, command: str) -> bool:
    """Return True if the command is allowed (not rate-limited).

    Returns False if the user should be throttled.
    In-memory fallback — use check_rate_limit_async() in async contexts
    to also enforce via Redis (cross-process, survives restarts).
    """
    key = (user_id, command)
    now = time.time()
    last = _rate_limits.get(key, 0)
    if now - last < RATE_LIMIT_SECONDS:
        return False
    _rate_limits[key] = now
    return True


async def check_rate_limit_async(user_id: int, command: str) -> bool:
    """Async rate limit check: Redis first, in-memory LRUCache fallback.

    Redis key: ``rl:{user_id}:{command}`` (global, not tenant-scoped —
    use TenantContext.check_rate_limit_async for per-tenant keys).
    """
    import infra.cache as _redis_cache
    if _redis_cache.is_available():
        rl_key = f"rl:{user_id}:{command}"
        allowed = await _redis_cache.rate_limit_check(rl_key, RATE_LIMIT_SECONDS, 1)
        if not allowed:
            return False
        _rate_limits[(user_id, command)] = time.time()
        return True

    # In-memory fallback
    return check_rate_limit(user_id, command)
