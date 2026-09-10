"""The plan mask — layer 1 of the resolver: what the account's PLAN includes.

The plan is data (``plans`` rows the system console edits), never code:
no route asks whether the plan is Pro; the resolver forces off the
flags of every registry entry the plan leaves out, and nav, API, bot
and AI hear one answer through the permission.

What a plan may leave out is the sellable set — every feature and
service except what an owner must always reach: the administration
tier (the matrix, Team Management, Settings), Billing (the way back to
a wider plan), and Overview (an aggregator gated by what it shows).
The config family's flags are never masked here: they are
cross-feature, and belong to no one plan line.

FAIL-CLOSED, by the owner's rule: when the plan cannot be read, the
last-known table answers; when there is none, the sellable set is
closed.  ``["*"]`` in a plan means everything — today's entries and
tomorrow's — which is how every tier ships before the operator narrows
one.
"""

from __future__ import annotations

import logging
import time
from dataclasses import replace
from typing import Optional

from capabilities.permissions.registry import CROSS_FEATURE_FLAGS, ENTRIES

logger = logging.getLogger(__name__)

EVERYTHING = "*"
_TTL_S = 60.0

#: what an owner must always reach, whatever the plan — never for sale
NOT_FOR_SALE: frozenset[str] = frozenset({"overview", "billing"})
#: ids a plan may leave out — the sellable set
EXCLUDABLE: tuple[str, ...] = tuple(
    e.id for e in ENTRIES if e.tier != "administration" and e.id not in NOT_FOR_SALE
)
_EXCLUDABLE_SET = frozenset(EXCLUDABLE)
_FLAGS_OF: dict[str, frozenset[str]] = {
    e.id: frozenset(f for f in e.flags if f not in CROSS_FEATURE_FLAGS)
    for e in ENTRIES if e.id in _EXCLUDABLE_SET
}
_ALL_SELLABLE_FLAGS: frozenset[str] = frozenset(f for fl in _FLAGS_OF.values() for f in fl)

# the last-known table: tier → included ids (a frozenset, or EVERYTHING)
_PLANS: dict[str, frozenset[str] | str] = {}
_LOADED_AT: float | None = None      # monotonic; None = never loaded
# the last-known quotas per tier (a plan's numbers, beside its ids)
_QUOTAS: dict[str, dict] = {}
# the last-known tier per account, for the resolver when the account
# row itself cannot be read
_TIER_OF: dict[int, str] = {}


def plan_flags_off(included) -> set[str]:
    """The flags a plan with these ids forces off.  ``None`` = unknown
    plan → everything sellable (closed)."""
    if included is None:
        return set(_ALL_SELLABLE_FLAGS)
    if included == EVERYTHING or EVERYTHING in included:
        return set()
    inc = set(included)
    off: set[str] = set()
    for fid, flags in _FLAGS_OF.items():
        if fid not in inc:
            off |= flags
    return off


def included_for(tier: Optional[str]):
    """The last-known included set for a tier — ``EVERYTHING``, a
    frozenset, or ``None`` when the table was never loaded or the tier
    is not in it (both closed)."""
    if not tier:
        return None
    return _PLANS.get(tier)


def plan_includes(tier: Optional[str], feature_id: str) -> bool:
    """The sync question a service or a job asks — from the last-known
    table.  An id outside the sellable set is always included."""
    if feature_id not in _EXCLUDABLE_SET:
        return True
    inc = included_for(tier)
    if inc is None:
        return False
    return inc == EVERYTHING or feature_id in inc


def quota_for(tier: Optional[str], key: str, default: int) -> int:
    """A plan's quota by key, or the caller's default when the plan does
    not set one."""
    q = _QUOTAS.get(tier or "", {})
    v = q.get(key)
    return int(v) if isinstance(v, (int, float)) and not isinstance(v, bool) else default


def _stale() -> bool:
    return _LOADED_AT is None or (time.monotonic() - _LOADED_AT) > _TTL_S


def load_rows(rows) -> None:
    """Make *rows* (``plans`` rows, as the mixin returns them) the
    last-known table, whole — a dropped row is gone."""
    global _LOADED_AT
    fresh: dict[str, frozenset[str] | str] = {}
    quotas: dict[str, dict] = {}
    for r in rows:
        inc = r.get("included") or []
        fresh[r["tier"]] = EVERYTHING if EVERYTHING in inc else frozenset(inc)
        quotas[r["tier"]] = dict(r.get("quotas") or {})
    _PLANS.clear(); _PLANS.update(fresh)
    _QUOTAS.clear(); _QUOTAS.update(quotas)
    _LOADED_AT = time.monotonic()


def forget() -> None:
    """Back to never-loaded: no table, no remembered tiers (closed)."""
    global _LOADED_AT
    _PLANS.clear(); _QUOTAS.clear(); _TIER_OF.clear()
    _LOADED_AT = None


def tier_of(acct) -> str:
    """An account row's plan.  The column defaults to ``free`` and an
    empty value is the same account on the narrowest plan there is — the
    same reading the quotas make."""
    return getattr(acct, "tier", None) or "free"


async def refresh_plans(pdb=None, *, force: bool = False) -> bool:
    """Reload the table into the last-known cache when it is stale (or
    *force*).  A read that fails leaves the last-known table in place and
    returns False."""
    if not force and not _stale():
        return True
    try:
        if pdb is None:
            from infra.platform import get_platform_db
            pdb = get_platform_db()
        rows = await pdb.list_plans()
    except Exception as e:
        logger.warning("plans: table unreadable — keeping the last-known (%s)", e)
        return False
    load_rows(rows)
    return True


def invalidate_plans() -> None:
    """After the operator writes a plan: in THIS process the table reloads
    on the next resolve and every cached permission set is dropped, so
    the change reaches every account on that tier at once.  Sibling
    workers (the API's other workers, the bot, the queue) catch up by
    their own TTLs — the same bound `invalidate_permissions_cache`
    documents — within about two minutes."""
    global _LOADED_AT
    _LOADED_AT = None
    from capabilities.permissions.roles import invalidate_permissions_cache
    invalidate_permissions_cache()


def remember_tier(account_id: int, tier: Optional[str]) -> None:
    if tier:
        _TIER_OF[int(account_id)] = tier


def last_known_tier(account_id: int) -> Optional[str]:
    return _TIER_OF.get(int(account_id))


def apply_plan_mask(fs, tier: Optional[str]):
    """Force off what the account's plan leaves out.  Duck-typed on the
    FeatureSet dataclass, like the account mask."""
    off = plan_flags_off(included_for(tier))
    if not off:
        return fs
    return replace(fs, **{f: False for f in off if hasattr(fs, f)})
