"""Which requests the security ledger keeps, and how they are kept safe.

Two populations, two reasons:

- **Every refusal, from anyone.**  401/403/429 are the one signal the
  2026-09-08 probe left ONLY in a log file: seventeen ``/system/*``
  refusals, twenty-five ``PUT /api/admin/*`` refusals, six
  ``promote-owner`` attempts.  The detector's strongest rules are
  "someone keeps being told no", and it needs them in a table.
- **Everything, from a monitored account.**  ``monitored`` is the trust
  class for someone we let keep going and watch; the whole point is the
  timeline.

Nothing else.  404s are deliberately excluded: fifty-five thousand
scanner hits for ``/etc/passwd`` would swamp the ledger with noise about
people who never got in.  A 2xx from a customer is their own business.

Observation only.  This module cannot refuse a request — it runs after
the response exists and its failures are swallowed.  That is also what
lets ``monitored`` be applied automatically later: recording someone by
mistake costs disk, not access.

What is never stored: a request body (no parameter for one exists), and
the query string on ``/api/auth/*`` — reset tokens, verification codes
and emails travel there.
"""

from __future__ import annotations

import logging
import time

logger = logging.getLogger(__name__)

# Statuses that mean "the platform said no".  404 is not in the set on
# purpose — see the module docstring.
DENIAL_STATUSES: frozenset[int] = frozenset({401, 403, 429})

# The account kind whose every request is kept.
RECORD_ALL_KIND = "monitored"

# Paths whose query string is never stored.
NO_QUERY_PREFIXES: tuple[str, ...] = ("/api/auth/", "/api/v1/auth/")

QUERY_MAX = 300
UA_MAX = 200
IP_MAX = 64

# account_id -> (kind, expires_at).  The metering middleware runs on every
# request; a DB read per request to learn the kind would be a tax on
# customers to watch a handful of accounts.  Sixty seconds is short
# enough that flipping an account to monitored takes effect within the
# minute, and long enough to cost nothing.
_KIND_TTL_S = 60.0
_kind_cache: dict[int, tuple[str | None, float]] = {}


def should_record(status: int, kind: str | None) -> bool:
    """The whole policy, in one line each."""
    if status in DENIAL_STATUSES:
        return True
    return kind == RECORD_ALL_KIND


def safe_query(path: str, query: str | None) -> str | None:
    """The query string as stored: absent on auth paths, else bounded."""
    if not query:
        return None
    if path.startswith(NO_QUERY_PREFIXES):
        return None
    return query[:QUERY_MAX]


def _clip(value: str | None, n: int) -> str | None:
    if not value:
        return None
    return value[:n]


async def kind_for_account(account_id: int | None) -> str | None:
    """The account's trust class, cached for a minute; None when unknown.

    Fail-quiet: a DB hiccup here must not turn into an exception on the
    request path, and "unknown" already means "not monitored".
    """
    if account_id is None:
        return None
    now = time.monotonic()
    hit = _kind_cache.get(account_id)
    if hit and hit[1] > now:
        return hit[0]
    kind: str | None = None
    try:
        from infra.platform import get_platform_db
        acct = await get_platform_db().get_account(account_id)
        kind = getattr(acct, "kind", None) if acct else None
    except Exception as e:  # noqa: BLE001 — observation must never raise
        logger.debug("kind lookup skipped for account %s: %s", account_id, e)
    _kind_cache[account_id] = (kind, now + _KIND_TTL_S)
    return kind


def forget_kind(account_id: int | None = None) -> None:
    """Drop the cache (one account, or all) — for tests and kind changes."""
    if account_id is None:
        _kind_cache.clear()
    else:
        _kind_cache.pop(account_id, None)


async def record_request(
    *,
    method: str,
    path: str,
    status: int,
    query: str | None = None,
    account_id: int | None = None,
    user_id: int | None = None,
    role: str | None = None,
    duration_ms: int | None = None,
    ip: str | None = None,
    ua: str | None = None,
    request_id: str | None = None,
) -> bool:
    """Decide, then write.  Returns whether a row was kept.  Never raises."""
    try:
        kind = await kind_for_account(account_id)
        if not should_record(int(status), kind):
            return False
        from infra.platform import get_platform_db
        await get_platform_db().record_security_request(
            method=method,
            path=path,
            status=int(status),
            account_id=account_id,
            user_id=user_id,
            role=role,
            kind=kind,
            query=safe_query(path, query),
            duration_ms=duration_ms,
            ip=_clip(ip, IP_MAX),
            ua=_clip(ua, UA_MAX),
            request_id=request_id,
        )
        return True
    except Exception as e:  # noqa: BLE001 — observation must never raise
        logger.debug("security record skipped (%s %s -> %s): %s", method, path, status, e)
        return False
