"""Single source of truth for "what timezone should I render in / fire
at?" across the whole codebase.

Resolution order
----------------
    effective_tz = users.timezone  ← per-user override (optional)
                 ∨ accounts.timezone   ← account default (admin sets)
                 ∨ "America/New_York"  ← hard fallback for fresh installs

Both string columns hold an IANA tz name (``"America/Chicago"`` etc.).

The helpers below are async because they read from the platform DB.
Results are cached per-process for short windows so an alert pipeline
that touches the same user 500× a minute doesn't re-hit SQLite each
time — invalidation is hooked off the same paths that mutate the
column (``update_user(timezone=…)`` / ``update_account(timezone=…)``).
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Optional

from cachetools import TTLCache

logger = logging.getLogger(__name__)

# Default when no override is set anywhere — kept aligned with the
# DDL default in both ``platform_schema.py`` and ``schema.py``.
DEFAULT_TIMEZONE = "America/New_York"

# IANA names the admin Settings dropdown advertises.  Deliberately
# limited to the four contiguous-US zones because that's where every
# customer fleet operates today — keeping the list short stops admins
# from picking ``UTC`` "because it sounds technical" and then getting
# alerts in the middle of the night.  Phoenix (MST no-DST), Alaska,
# and Hawaii were dropped for the same simplification — re-add here
# (and in ``interfaces/dashboard/src/utils/timezones.ts``) when a
# customer in those regions onboards.
IANA_OPTIONS: tuple[str, ...] = (
    "America/New_York",     # Eastern
    "America/Chicago",      # Central
    "America/Denver",       # Mountain
    "America/Los_Angeles",  # Pacific
)

# Per-process caches — small TTL so a tz change doesn't take longer
# than a minute to propagate across workers.
_user_tz_cache: TTLCache[int, str] = TTLCache(maxsize=4096, ttl=60)
_account_tz_cache: TTLCache[int, str] = TTLCache(maxsize=512, ttl=60)


def _valid_iana(tz: Optional[str]) -> Optional[str]:
    """Return ``tz`` if it parses as a known IANA name; ``None`` otherwise.

    Accepts only names from our supported ``IANA_OPTIONS`` list.  This
    is stricter than ``ZoneInfo``'s built-in parser (which accepts
    deprecated POSIX aliases like ``"EST"`` and ``"PST"`` on some
    platforms) — using the whitelist here guarantees that the
    resolution chain only returns names the API + UI agreed on.
    """
    if not tz:
        return None
    tz = tz.strip()
    if tz in IANA_OPTIONS:
        return tz
    return None


async def effective_tz_for_account(account_id: int) -> str:
    """Return the IANA name the given account is configured to use.

    Falls back to ``DEFAULT_TIMEZONE`` when the column is missing
    (cold-boot before migration ran) or holds an invalid value.
    """
    cached = _account_tz_cache.get(account_id)
    if cached:
        return cached
    try:
        from infra.platform import get_platform_db
        acct = await get_platform_db().get_account(account_id)
        tz = _valid_iana(getattr(acct, "timezone", None)) or DEFAULT_TIMEZONE
    except Exception as e:
        logger.debug("effective_tz_for_account(%s) lookup failed: %s", account_id, e)
        tz = DEFAULT_TIMEZONE
    _account_tz_cache[account_id] = tz
    return tz


async def effective_tz_for_user(user_id: int) -> str:
    """Return the IANA name to use for this specific user.

    ``users.timezone`` wins when set to a valid IANA name (per-user
    override).  Otherwise, falls through to the account default.
    The per-user cache holds the *resolved* answer, so a user who
    relies on the account default still picks up account-tz changes
    within one cache TTL — the account-tz cache is invalidated
    alongside theirs.
    """
    cached = _user_tz_cache.get(user_id)
    if cached:
        return cached
    try:
        from infra.platform import get_platform_db
        pdb = get_platform_db()
        u = await pdb.get_user_by_id(user_id)
        if u is None:
            tz = DEFAULT_TIMEZONE
        else:
            user_tz = _valid_iana(getattr(u, "timezone", None))
            if user_tz:
                tz = user_tz
            else:
                tz = await effective_tz_for_account(u.account_id)
    except Exception as e:
        logger.debug("effective_tz_for_user(%s) lookup failed: %s", user_id, e)
        tz = DEFAULT_TIMEZONE
    _user_tz_cache[user_id] = tz
    return tz


def invalidate_user(user_id: int) -> None:
    """Drop the cached resolution for one user — call from
    ``update_user(timezone=…)``."""
    _user_tz_cache.pop(user_id, None)


def invalidate_account(account_id: int) -> None:
    """Drop the cached resolution for an account AND every user we've
    seen lookup for them — safest to drop the whole user cache since
    we don't track the reverse index per-tenant."""
    _account_tz_cache.pop(account_id, None)
    _user_tz_cache.clear()


def invalidate_all() -> None:
    """Used by tests and from admin diagnostic endpoints."""
    _account_tz_cache.clear()
    _user_tz_cache.clear()


# ── Convenience: should this hourly tick fire for this account? ───


async def is_local_hour(account_id: int, target_hour: int) -> bool:
    """Return True if it's currently ``target_hour`` (0-23) in the
    account's effective timezone.  Powers the per-account local-hour
    gate inside the rewritten hourly cron jobs:

        scheduler.add_job(check_X, "cron", minute=5, ...)  # tick hourly
        async def check_X(...):
            for acct in accounts:
                if not await is_local_hour(acct.id, 9):
                    continue          # skip — not 09:00 here
                ...run the per-account body...
    """
    tz_name = await effective_tz_for_account(account_id)
    try:
        from zoneinfo import ZoneInfo
        now_local = datetime.now(timezone.utc).astimezone(ZoneInfo(tz_name))
    except Exception:
        # Fall back to UTC — bad data shouldn't silently skip every account.
        now_local = datetime.now(timezone.utc)
    return now_local.hour == target_hour
