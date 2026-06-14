"""Invites domain logic — email validation + multi-bucket send rate limits."""
import logging
from typing import Optional

logger = logging.getLogger(__name__)

_INVITE_EMAIL_RE = __import__("re").compile(
    r"^[A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,}$"
)


async def _invite_email_rate_check(
    account_id: int, actor_sub: str,
    recipient: Optional[str] = None,
) -> bool:
    """Outbound-email rate limit, SEPARATE from invite_mutate.

    Four buckets, ordered so the most-likely-to-fire CHEAPEST check
    runs first.  Recipient-level is the most protective (one bad
    address can't be hammered) — checked early.

      1. PER-RECIPIENT, per-account (3/24h):
         invite_email_recipient:{account_id}:{sha256(recipient)[:16]}
         Stops an operator from re-mailing the same address inside one
         account.  Hashed (no plaintext PII in Redis) + lowercase-
         normalized (case-insensitive bucket).
      2. PER-RECIPIENT, global (8/24h):
         invite_email_recipient_global:{sha256(recipient)[:16]}
         Caps cross-account abuse — a leaked admin token at one account
         can't use 4truck's relay reputation to harass an external
         mailbox once another account already mailed them today.
      3. PER-ACTOR per-minute (5/min):
         invite_email_send:{account_id}:{actor_sub}
         Burst protection for one compromised admin token.
      4. PER-ACCOUNT per-day (50/day):
         invite_email_send_daily:{account_id}
         Damage cap if buckets 1-3 are bypassed somehow.

    Fail CLOSED on Redis outage — the default ``rate_limit_check``
    fails open which is correct for read-mostly endpoints but
    disastrously wrong for outbound mail (a Redis blip would lift
    the cap entirely and let a compromised admin token blast).

    Fixed-window cliff acknowledged: a recipient hit 3 times at 23:59
    can take 3 more at 00:00.  Sliding window would need a sorted-
    set redesign in infra/cache.py — deferred.  The cliff is
    bounded by the per-actor cap (5/min) so the worst-case scenario
    is ~7-8 sends crossing midnight to the same recipient, which is
    still inside the global 8/24h ceiling.
    """
    from adapters.cache.redis import rate_limit_check, is_available as _redis_ok
    if not _redis_ok():
        return False
    if recipient:
        import hashlib as _hashlib
        rcp_hash = _hashlib.sha256(
            recipient.strip().lower().encode("utf-8"),
        ).hexdigest()[:16]
        # Per-(account, recipient) — protects against intra-account
        # spam to one address.
        if not await rate_limit_check(
            f"invite_email_recipient:{account_id}:{rcp_hash}",
            window_secs=24 * 60 * 60, max_requests=3,
        ):
            return False
        # Global per-recipient — cross-account abuse cap.
        if not await rate_limit_check(
            f"invite_email_recipient_global:{rcp_hash}",
            window_secs=24 * 60 * 60, max_requests=8,
        ):
            return False
    # Per-actor burst cap.
    per_actor_ok = await rate_limit_check(
        f"invite_email_send:{account_id}:{actor_sub}",
        window_secs=60, max_requests=5,
    )
    if not per_actor_ok:
        return False
    # Per-account daily cap.
    per_account_ok = await rate_limit_check(
        f"invite_email_send_daily:{account_id}",
        window_secs=24 * 60 * 60, max_requests=50,
    )
    return per_account_ok
