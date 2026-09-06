"""Unit width — the second Team Management scope question, shared core.

Permissions answer "may this role do this VERB on this FEATURE"; Team
Management answers "which UNITS".  This module is the ONE
implementation of the width answer during the verb/scope bridge, worn
by two thin adapters: ``interfaces.api.deps.member_unit_scope`` for
API surfaces (a JWT user dict) and the bot's handlers (a DB ``User``
row they already hold).  One implementation, because the width rule
duplicated per surface is the rule that drifts — the company wall's
``company_allows`` lesson, applied again.

One answer: the member's three-layer Team Management scope (member
override ⊃ account role width ⊃ built-in role default).

During the bridge this also honoured a second claim — "the effective
grant is vehicle-only" — so an account that had narrowed a role
through the old permission pairs kept its narrowing.  That claim
retired one stage early, and for a reason: once the matrix started
writing verbs, a pair's halves stopped meaning width.  On a manage-
pair, View-without-Manage stores wide=False, narrow=True — and the
grant claim read that as "narrow this member", so revoking WRITES
silently made a wide member see nothing.  The pre-flight had already
proven no stored row was narrow-only, so the claim had nothing left
to protect and one new thing to break.
"""

from __future__ import annotations

import logging
from typing import Optional

from capabilities.permissions.roles import (
    PAIRED_UNIT_FEATURES,
    Role,
)

_log = logging.getLogger(__name__)

#: (account_id, role) → (expires_at, scope-or-None).  Every width check
#: on every unit-scoped list asks the role layer; without this each ask
#: was a round trip.  Same TTL as the permissions cache, so the two
#: layers of one answer go stale together; same multi-worker caveat —
#: invalidation clears THIS process, siblings ride out the TTL.
_role_scope_cache: dict[tuple[int, str], tuple[float, Optional[str]]] = {}


def invalidate_role_scope_cache(account_id: Optional[int] = None) -> None:
    """Drop cached role widths — for one account, or all.  Called by
    the Team Management role-width PUT and the fold script after a
    write, so the writer's own process answers fresh immediately."""
    if account_id is None:
        _role_scope_cache.clear()
        return
    for k in [k for k in _role_scope_cache if k[0] == int(account_id)]:
        _role_scope_cache.pop(k, None)


async def role_scope_layer(
    account_id: int, role, platform_db=None,
) -> Optional[str]:
    """The account's ROLE-level width, or None for "built-in default".

    Its own try/except on purpose: a missing role layer must never
    change anyone's width — before it existed, every account resolved
    member-override-then-built-in, and None reproduces exactly that.
    A failed read is NOT cached: the next ask tries again.
    """
    import time as _time
    from capabilities.permissions.roles import _PERMS_CACHE_TTL_S
    role_str = role.value if hasattr(role, "value") else str(role)
    key = (int(account_id), role_str)
    now = _time.monotonic()
    hit = _role_scope_cache.get(key)
    if hit is not None and hit[0] > now:
        return hit[1]
    try:
        if platform_db is None:
            from infra.platform import get_platform_db
            platform_db = get_platform_db()
        value = await platform_db.get_role_vehicle_scope(int(account_id), role_str)
    except Exception:
        _log.debug("role vehicle scope unavailable", exc_info=True)
        return None
    _role_scope_cache[key] = (now + _PERMS_CACHE_TTL_S, value)
    return value


async def unit_width(
    account_id: int, role, db_user, feature: str, platform_db=None,
) -> str:
    """'all' or 'assigned' for one paired unit feature.

    ``db_user`` is the member's DB row when the caller has it (the bot
    always does), or None when it could not be loaded — which falls
    back to the role's BUILT-IN width: an unloadable row means no
    override is KNOWN, and inventing one would make a wide role's data
    vanish whenever the platform hiccups, while a driver stays
    'assigned' regardless.  The cautious fail-closed answer belongs to
    the OTHER question ("what is this member's scope" —
    deps.get_member_vehicle_scope).
    """
    if feature not in PAIRED_UNIT_FEATURES:
        raise KeyError(f"not a unit-paired feature: {feature!r}")
    role_enum = role if isinstance(role, Role) else Role(role)
    if db_user is None:
        # No member row (bot surface, script, a platform hiccup).  The
        # documented fail-open — "do not invent an override that would
        # narrow a wide caller to nothing" — used to sit BEHIND the grant
        # claim, which had already narrowed drivers.  With that claim
        # retired, blanket 'all' here would widen a driver whose row
        # could not be read.  So the fallback is the role's BUILT-IN
        # width: still 'all' for every wide role, 'assigned' for a
        # driver — the same answer the three layers give with no
        # override and no role row.
        from capabilities.permissions.fold import builtin_width
        return builtin_width(role_enum.value)
    return db_user.scope_with_role_default(
        await role_scope_layer(account_id, role_enum, platform_db))


# ─── Person width ────────────────────────────────────────────────────
#
# The person-subject features ("my paystubs", "my coaching", "my
# documents", "my loads") carry the same disease the ten unit pairs
# had: a `*_own` grant that smuggles WIDTH into a permission.  Their
# width has no customer for a third layer — "self" for anyone without a
# driver row is nonsense, and "a driver who sees everyone's paystubs"
# is a role change, not a width — so it is a pure function of the
# role, with no storage, no role row and no Team Management control.
# Each feature resolves its own self-key (loads → the member's user id;
# pay and coaching → the bound driver id; documents → the member's own
# row) exactly as unit features resolve their own assigned-truck list.

from capabilities.permissions.roles import PAIRED_PERSON_FEATURES  # noqa: E402  (generated)


def person_width(role, feature: str, is_manager: bool = False) -> str:
    """'self' or 'all' for one person-subject feature.

    A driver reads their own rows; every other role reads the
    account's.  ``is_manager`` is accepted so the call sites carry the
    tier they know, but a manager-tier driver is still a driver: the
    tier adds VERBS (MANAGER_GRANTS), never width.
    """
    if feature not in PAIRED_PERSON_FEATURES:
        raise KeyError(f"not a person-paired feature: {feature!r}")
    role_enum = role if isinstance(role, Role) else Role(role)
    del is_manager  # documented no-op — see docstring
    return "self" if role_enum is Role.DRIVER else "all"
