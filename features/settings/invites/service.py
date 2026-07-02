"""Invites domain logic — email validation + multi-bucket send rate limits."""
import logging
from typing import Optional

logger = logging.getLogger(__name__)

_INVITE_EMAIL_RE = __import__("re").compile(
    r"^[A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,}$"
)


# ── Invite-targeting policy (owned by this feature) ───────────────
# Two authority paths, both resolved by :func:`invite_authorized`:
#
#   • MANAGER path — a MANAGER (``is_manager``) of a listed role may invite
#     ONLY that role's sub-team, and this authority is RANK-INDEPENDENT.  A
#     recruiting manager IS a ``recruiter`` (rank 2), so it can't out-rank
#     another recruiter (rank 2); its authority to build a recruiter team
#     comes from the manager tier, not from rank.  It can never invite
#     fleet/hr/dispatcher/etc — only what's whitelisted here.
#   • RANK path — everyone else (non-managers, or managers of un-listed
#     roles) may invite anyone strictly below their rank, via the RBAC
#     ``validate_invite_role`` (which also blocks owner-via-invite).
#
# This lives with the invites feature (not the RBAC layer) because "who a
# role may invite" is invite policy.  Keyed on the BASE role + is_manager —
# there is no ``recruiter_manager`` role anymore.  Pure data + predicate (no
# FastAPI) so the bot can import it too.
MANAGER_INVITE_ONLY: dict[str, set[str]] = {
    # A team-lead manager invites ONLY their own role — never other
    # departments.  Invited users always arrive as plain employees (invites
    # carry a role, never the manager tier).  Whether a manager can invite AT
    # ALL stays in the Owner's hands: can_invite is a per-tier matrix flag —
    # revoke it on the tier and these whitelists never even get consulted.
    "recruiter": {"recruiter"},
    "fleet": {"fleet"},
    "safety": {"safety"},
    "dispatcher": {"dispatcher"},
    # HR's BASE role already invites drivers (rank path); the manager
    # whitelist REPLACES the rank path, so it must keep driver or promoting
    # an HR user to manager would silently take driver-inviting away.
    "hr": {"hr", "driver"},
    "accounting": {"accounting"},
}


def invite_authorized(
    actor_role: str, actor_is_manager: bool, target_role: str,
) -> tuple[bool, str]:
    """Full invite-target authorization.

    Returns ``(True, "")`` when allowed, else ``(False, reason)`` where
    *reason* is a stable key (``manager_invite_restricted:<csv>`` carries the
    allowed set; otherwise a ``validate_invite_role`` reason key).
    """
    if actor_is_manager and actor_role in MANAGER_INVITE_ONLY:
        allowed = MANAGER_INVITE_ONLY[actor_role]
        if target_role in allowed:
            return True, ""   # manager authority (rank-independent)
        return False, "manager_invite_restricted:" + ",".join(sorted(allowed))
    # Everyone else: standard rank gate (also blocks owner-via-invite).
    from capabilities.permissions.roles import validate_invite_role
    return validate_invite_role(actor_role, target_role)


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
