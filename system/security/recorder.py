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
# The security axis, not the kind axis: `kind` says whether an
# account is a customer, which has nothing to do with whether we
# are recording it. A watched CUSTOMER is real + monitored.
#
# `quarantined` is the stronger standing, so it records at least as
# much: a subject we have decided to hold cannot be watched LESS
# closely than one we are merely observing.
RECORD_ALL_SECURITY: tuple[str, ...] = ("monitored", "quarantined")

# Paths whose query string is never stored.
NO_QUERY_PREFIXES: tuple[str, ...] = ("/api/auth/", "/api/v1/auth/")

QUERY_MAX = 300
UA_MAX = 200
IP_MAX = 64

# (kind, subject_id) -> (standing, expires_at), where kind is "account"
# or "user".  The metering middleware runs on every request; a DB read
# per request to learn a standing would be a tax on customers to watch
# a handful of subjects.  Sixty seconds is short enough that flipping
# someone to monitored takes effect within the minute, and long enough
# to cost nothing.
_SECURITY_TTL_S = 60.0
_security_cache: dict[tuple[str, int], tuple[str | None, float]] = {}


def should_record(status: int, security: str | None) -> bool:
    """The whole policy, in one line each."""
    if status in DENIAL_STATUSES:
        return True
    return security in RECORD_ALL_SECURITY


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


async def _standing(kind: str, subject_id: int | None) -> str | None:
    """One subject's security standing, cached for a minute.

    Fail-quiet: a DB hiccup here must not turn into an exception on the
    request path, and "unknown" already means "not monitored".
    """
    if subject_id is None:
        return None
    now = time.monotonic()
    key = (kind, subject_id)
    hit = _security_cache.get(key)
    if hit and hit[1] > now:
        return hit[0]
    standing: str | None = None
    try:
        from infra.platform import get_platform_db
        db = get_platform_db()
        row = (await db.get_account(subject_id) if kind == "account"
               else await db.get_user_by_id(subject_id))
        standing = getattr(row, "security", None) if row else None
    except Exception as e:  # noqa: BLE001 — observation must never raise
        logger.debug("security lookup skipped for %s %s: %s", kind, subject_id, e)
    _security_cache[key] = (standing, now + _SECURITY_TTL_S)
    return standing


async def security_for_account(account_id: int | None) -> str | None:
    """The ACCOUNT's standing."""
    return await _standing("account", account_id)


async def security_for_user(user_id: int | None) -> str | None:
    """The PERSON's standing.

    Separate from the account's because an account is often fine while
    one person inside it is not, and marking the account to watch them
    records everyone in it — twenty-three people at the largest customer
    to observe one.
    """
    return await _standing("user", user_id)


def strongest(*standings: str | None) -> str | None:
    """The most serious of several standings.

    A request is kept if EITHER the account or the person is watched,
    and the ledger records which reason applied — so the row says why it
    was kept, not merely that something was.
    """
    order = {"quarantined": 3, "monitored": 2, "normal": 1}
    best, rank = None, 0
    for s in standings:
        r = order.get(s or "", 0)
        if r > rank:
            best, rank = s, r
    return best


def forget_security(subject_id: int | None = None, kind: str | None = None) -> None:
    """Drop the cache — for tests, and for the moment an operator changes
    a standing.

    With no arguments it clears everything, which is what a test wants.
    With ``subject_id`` it clears that id under every kind unless ``kind``
    narrows it: an operator changing an account and a user that happen to
    share an id is not worth a bug.
    """
    if subject_id is None:
        _security_cache.clear()
        return
    kinds = (kind,) if kind else ("account", "user")
    for k in kinds:
        _security_cache.pop((k, subject_id), None)


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
        # BOTH subjects: the account, and the person acting inside it.
        # Either being watched keeps the row, which is the whole point of
        # a per-user standing — otherwise watching one dispatcher still
        # means marking their employer.
        security = strongest(
            await security_for_account(account_id),
            await security_for_user(user_id),
        )
        if not should_record(int(status), security):
            return False
        from infra.platform import get_platform_db
        await get_platform_db().record_security_request(
            method=method,
            path=path,
            status=int(status),
            account_id=account_id,
            user_id=user_id,
            role=role,
            security=security,
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
