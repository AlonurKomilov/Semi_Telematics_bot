"""Holding a subject — the enforcement half of the security vocabulary.

``monitored`` records and changes nothing.  ``quarantined`` is the word
that promises the subject is HELD, and until this module existed it
promised nothing: the console let an operator set it and the request
path never read it, so a person under quarantine behaved exactly like a
person under observation.  The account page said so in small grey text,
which is not a substitute for the word being true.

**Two subjects, and the difference is the point.**  Holding a PERSON
refuses one login.  Holding an ACCOUNT refuses everyone in it — at the
largest customer that is twenty-three people, most of whom nobody
accused of anything — so it is the heavier instrument by far and the
console should make an operator reach further for it.

The account hold also closes a door the person hold cannot reach: the
public application links.  A recruiter link carries no JWT, belongs to
the company rather than to any one employee, and collects a stranger's
full FMCSA application — licence number, employment history, consent
signatures.  That is usually the real reason to hold a company, and it
is why the hold is applied at the two token resolvers rather than at
the routes: eight call sites, one place, and a held link answers with
the same uniform 404 an unknown token already gets, so nobody outside
learns that a company is under review.

What a held person keeps, and why each one:

* **Signing in.**  A person who cannot sign in cannot be TOLD anything,
  and cannot be observed either.  They reach a page that says they are
  under review and who to contact.  We do not pretend the platform is
  broken; they may turn out to be innocent, and lying to them costs more
  than the hold gains.
* **Their own identity** (``/user/me``) — so that page can name them.
* **Billing.**  A held account is still a billed account.  Taking the
  payment while blocking the page that takes the payment is
  indefensible.
* **Logging out**, so they can leave.

Everything else is refused with 403 and a reason.

Two things this module deliberately does NOT do:

* It never touches the inbound webhook paths.  Those are Stripe and
  Resend talking to US, not the subject talking to us; blocking them
  corrupts our own billing and bounce state rather than holding anyone.
* It never decides alone.  ``QUARANTINE_ENFORCEMENT_ENABLED`` gates the
  whole thing, so it lands dark and an operator turns it on — the way
  billing enforcement did, and for the same reason: this can lock out a
  paying customer.
"""

from __future__ import annotations

import logging
import os
import time

logger = logging.getLogger(__name__)

# The standing that holds.  Kept as its own name rather than the literal
# so a reader of the request path sees WHY the check exists.
HELD = "quarantined"

ERROR_CODE = "user_quarantined"

#: What the held person is told — by the API, the login page and the
#: bot, in the same words.  It states the fact, names the way back, and
#: accuses them of nothing: an operator opens a review on a suspicion,
#: and a suspicion is not a finding.
MESSAGE = (
    "Your access is under review and is paused for now. "
    "Your company's account is unaffected. "
    "Contact your account owner or support to have it looked at."
)


def enabled() -> bool:
    """Whether the hold is enforced at all.

    Read from the environment on every call rather than captured at
    import: a constant frozen at import cannot be flipped by a test's
    ``monkeypatch.setenv``, and this is exactly the switch a test needs
    to flip.  The cost is one dict lookup.
    """
    return os.getenv("QUARANTINE_ENFORCEMENT_ENABLED", "0") == "1"


# Path suffixes the hold leaves open, matched AFTER the API prefix so
# ``/api`` and ``/api/v1`` both hit.  Each entry is one of the four
# reasons in this module's docstring; nothing is here for convenience.
OPEN_SUFFIXES: tuple[str, ...] = (
    # They must be able to arrive, leave, and keep a live page working
    # long enough to read why they are held.
    "/auth/login", "/auth/logout", "/auth/refresh",
    # ...and the page that tells them has to be able to name them.
    "/user/me",
    # A held account is still a billed account.
    "/billing/summary", "/billing/usage", "/billing/invoices",
    "/billing/portal",
    # Never the subject talking: these are Stripe and Resend talking to
    # us.  Refusing them holds nobody and corrupts our own records.
    "/billing/webhook", "/billing/stripe/webhook", "/webhooks/resend",
    # Monitors.
    "/health", "/version",
)


# Which API prefixes a suffix may hang off.  Both are live: the
# dashboard calls /api, older integrations call /api/v1.
_PREFIXES: tuple[str, ...] = ("/api", "/api/v1")

#: Every open path, spelled out.  Built from the two lists rather than
#: matched with ``endswith``, because a suffix match is open to anything
#: that merely ENDS the same way: ``/api/system/health`` was already
#: exempted by the ``/health`` meant for the platform probe, and a future
#: ``/vehicles/{id}/health`` would quietly join it with nothing to catch
#: the drift.
OPEN_PATHS: frozenset[str] = frozenset(
    f"{prefix}{suffix}" for prefix in _PREFIXES for suffix in OPEN_SUFFIXES
)


def is_open_path(path: str) -> bool:
    """True when the hold must let this request through regardless.

    An exact match against the whole path, never a suffix: the list says
    which endpoints are open, not which endings are.

    The trailing slash is normalised because a client that sends
    ``/api/user/me/`` means the same route, and refusing a held person
    their own profile over a slash would leave them at a page that
    cannot explain itself.
    """
    return path.rstrip("/") in OPEN_PATHS or path in OPEN_PATHS


# user_id -> (held, expires_at).  The hold is asked on every request, so
# it cannot be a database read every time; sixty seconds is short enough
# that holding someone takes effect within the minute and long enough to
# cost nothing.  The operator's own click clears the entry immediately
# (``forget``), so in practice the delay applies to nobody.
_TTL_S = 60.0
_cache: dict[int, tuple[bool, float]] = {}


def forget(user_id: int | None = None) -> None:
    """Drop the cached answer — one person, or everyone.

    Called the moment an operator changes a standing, so the decision is
    live on the next request instead of up to a minute later.
    """
    if user_id is None:
        _cache.clear()
    else:
        _cache.pop(int(user_id), None)


async def is_held(user_id: int | None) -> bool:
    """Whether this person is currently held.

    Fails OPEN.  A database hiccup must not lock out every customer at
    once, and the failure is logged loudly rather than silently denying:
    a hold that is not in force is a visible gap an operator can act on,
    while a platform that refuses everyone because Postgres blinked is
    an outage we caused ourselves.
    """
    if user_id is None:
        return False
    key = int(user_id)
    now = time.monotonic()
    hit = _cache.get(key)
    if hit and hit[1] > now:
        return hit[0]

    held = False
    try:
        from infra.platform import get_platform_db
        row = await get_platform_db().get_user_by_id(key)
        held = bool(row is not None and getattr(row, "security", None) == HELD)
    except Exception:
        # Deliberately not cached: a transient failure must not pin
        # "not held" for the next minute.
        logger.warning(
            "quarantine: standing lookup failed for user %s — "
            "letting the request through", key, exc_info=True)
        return False

    _cache[key] = (held, now + _TTL_S)
    return held


# Its own cache, keyed by account.  Deliberately NOT merged with the
# person cache under a composite key: the two are asked at different
# rates (every request asks both) and a shared dict would make the
# eviction of one evict the other.
_account_cache: dict[int, tuple[bool, float]] = {}


def forget_account(account_id: int | None = None) -> None:
    """Drop the cached answer for one account, or all of them."""
    if account_id is None:
        _account_cache.clear()
    else:
        _account_cache.pop(int(account_id), None)


async def is_account_held(account_id: int | None) -> bool:
    """Whether this whole company is held.

    Fails OPEN, for the same reason the person check does and with more
    at stake: a database hiccup that latched this closed would refuse
    every employee of every customer at once.
    """
    if account_id is None:
        return False
    key = int(account_id)
    now = time.monotonic()
    hit = _account_cache.get(key)
    if hit and hit[1] > now:
        return hit[0]

    held = False
    try:
        from infra.platform import get_platform_db
        row = await get_platform_db().get_account(key)
        held = bool(row is not None and getattr(row, "security", None) == HELD)
    except Exception:
        logger.warning(
            "quarantine: account standing lookup failed for %s — "
            "letting the request through", key, exc_info=True)
        return False

    _account_cache[key] = (held, now + _TTL_S)
    return held


async def is_request_held(user_id: int | None, account_id: int | None) -> bool:
    """Whether this request is held, for either reason.

    The person is asked FIRST: it is the narrower fact, it is the one
    that is true more often, and asking it first means the common case
    costs one cache read rather than two.
    """
    if await is_held(user_id):
        return True
    return await is_account_held(account_id)


async def delivery_blocked(account_id: int | None) -> bool:
    """Whether this company may still be sent anything.

    The notification core gates itself at ``dispatch``, ``notify_user``
    and ``deliver``.  This exists for the senders that do NOT go through
    it: four bot modules reach a company's people by calling the
    Telegram and email transports directly — maintenance overdue
    notices, PTI reminders and digests, scheduled report PDFs, the
    Samsara sync digest.  Each uses the alerting pipeline only as a
    first attempt and falls back to a direct send when no group route is
    configured, which is the ordinary case for a smaller fleet.

    So "the hold covers every delivery path" was not true of the three
    core functions alone, and the gap was exactly the deliveries a held
    company would most notice still arriving.

    Fails open, like every reader of this standing.
    """
    if account_id is None:
        return False
    try:
        if not enabled():
            return False
        return await is_account_held(int(account_id))
    except Exception:
        logger.warning("quarantine: delivery check failed for account %s — "
                       "sending", account_id, exc_info=True)
        return False


async def hold_account_sessions(platform_db, account_id: int) -> int:
    """End every live session in the account, now.

    The same reasoning as the per-person sweep, multiplied: holding a
    company whose twenty-three people all keep working for another
    eight hours is not holding it.
    """
    try:
        revoked = await platform_db.revoke_account_sessions(account_id)
    except Exception:
        logger.warning("quarantine: account session sweep failed for %s",
                       account_id, exc_info=True)
        return 0
    if not revoked:
        return 0
    from interfaces.api.auth import mark_jti_revoked
    ended = 0
    for row in revoked:
        try:
            await mark_jti_revoked(row.get("jti", ""), row.get("expires_at"))
            ended += 1
        except Exception:
            logger.warning("quarantine: denylist push failed for jti %s",
                           row.get("jti"), exc_info=True)
    return ended


async def hold_sessions(platform_db, user_id: int) -> int:
    """End every live session this person holds, now.

    Without this the hold would begin at their next sign-in, which is up
    to eight hours away — thirty days if they ticked "remember me".  The
    account-suspension gate that predates this module has exactly that
    shape and exactly that hole: it lives in ``mint_session_token``, so
    suspending an account today does not stop anyone already signed in.

    Returns how many sessions were ended, so the operator's audit row
    can say it.  Never raises: a hold whose session sweep failed is
    still a hold, and the standing is already written.
    """
    try:
        # Empty current_jti means "no session is exempt" — the operator
        # is not one of this person's browsers.
        revoked = await platform_db.revoke_other_user_sessions(user_id, "")
    except Exception:
        logger.warning("quarantine: session sweep failed for user %s",
                       user_id, exc_info=True)
        return 0
    if not revoked:
        return 0
    from interfaces.api.auth import mark_jti_revoked
    ended = 0
    for row in revoked:
        try:
            await mark_jti_revoked(row.get("jti", ""), row.get("expires_at"))
            ended += 1
        except Exception:
            # The database row is already marked revoked; a denylist
            # push that failed means that one token survives until it
            # expires.  Worth a line, not worth abandoning the rest.
            logger.warning("quarantine: denylist push failed for jti %s",
                           row.get("jti"), exc_info=True)
    return ended


_CATEGORY = "security.access_review"


def _register_category() -> None:
    """Register the owner notice's category, once, on first use.

    Registered lazily rather than at import so this module can be read
    by the request path (``is_held`` on every request) without dragging
    the notification stack in behind it.

    TARGETED, because it is addressed to one person — the owner — not
    broadcast to whoever subscribed.  MANDATORY, because a decision
    about one of their people is not something they may have muted their
    way out of hearing.
    """
    from capabilities.notifications.categories import (
        NotificationCategory, TARGETED, get_category, register_category,
    )
    if get_category(_CATEGORY) is not None:
        return
    register_category(NotificationCategory(
        key=_CATEGORY,
        label="A team member's access is under review",
        kind=TARGETED,
        mandatory=True,
    ))


async def tell_the_owner(platform_db, *, account_id: int, person, ended: int) -> bool:
    """Tell the account owner, once, that one of their people is held.

    The owner starts receiving that person's alerts from this moment
    (see ``_reroute_quarantined``), and an alert that arrives with no
    explanation is a mystery, not a hand-over.  So the reason is said
    ONCE, by name, here — rather than pasted onto every alert forever,
    which would turn an explanation into noise.

    What it deliberately does NOT say: why.  The owner is told that a
    review is open and that the work is covered.  An operator opens a
    review on a suspicion, the owner may well repeat what they read to
    the person, and a suspicion repeated as a finding is how an innocent
    employee gets fired over our wording.

    Returns whether the notice went out.  Never raises: a hold whose
    notice failed is still a hold.
    """
    try:
        owner = await platform_db.get_primary_owner(account_id)
        if owner is None or int(owner.id) == int(person.id):
            # Holding the owner themselves: there is nobody above them
            # to hand the work to, and telling them their own access is
            # held is the login banner's job, not this one.
            return False
        who = (getattr(person, "display_name", "") or getattr(person, "email", "")
               or f"user {person.id}")
        from capabilities.notifications.channels import NotificationContent
        from capabilities.notifications.service import notify_user
        _register_category()
        content = NotificationContent(
            category=_CATEGORY,
            severity="warning",
            title="A team member's access is under review",
            body=(
                f"{who}'s access to the platform is paused while it is "
                f"reviewed. Their alerts, digests and scheduled reports "
                f"now come to you so nothing goes unread.\n\n"
                f"Your company's account is unaffected. Contact support "
                f"if you need this looked at."
            ),
        )
        await notify_user(platform_db, account_id, int(owner.id), content)
        return True
    except Exception:
        logger.warning(
            "quarantine: could not tell the owner of account %s that user %s "
            "is held — their alerts will arrive unexplained",
            account_id, getattr(person, "id", None), exc_info=True)
        return False
