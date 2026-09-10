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
import re
import time
from dataclasses import replace
from typing import Optional

from capabilities.permissions.registry import CROSS_FEATURE_FLAGS, ENTRIES

logger = logging.getLogger(__name__)

EVERYTHING = "*"
_TTL_S = 60.0

#: what an owner must always reach, whatever the plan — never for sale
NOT_FOR_SALE: frozenset[str] = frozenset({"overview", "billing"})


def _own_flags(e) -> frozenset[str]:
    return frozenset(f for f in e.flags if f not in CROSS_FEATURE_FLAGS)


#: ids a plan may leave out — the sellable set: what an owner need not
#: always reach AND what the mask can enforce.  An entry that rides
#: another's verb (``flags=[]`` — Scheduled Reports under Reports, DOT
#: Binder, My payouts under KPI) is governed by the entry it rides and
#: is not a plan line of its own.
EXCLUDABLE: tuple[str, ...] = tuple(
    e.id for e in ENTRIES
    if e.tier != "administration" and e.id not in NOT_FOR_SALE and _own_flags(e)
)
_EXCLUDABLE_SET = frozenset(EXCLUDABLE)
_FLAGS_OF: dict[str, frozenset[str]] = {
    e.id: _own_flags(e) for e in ENTRIES if e.id in _EXCLUDABLE_SET
}
_ALL_SELLABLE_FLAGS: frozenset[str] = frozenset(f for fl in _FLAGS_OF.values() for f in fl)

#: a plan's key — accounts.tier — as the operator may create one
PLAN_KEY_RE = re.compile(r"^[a-z][a-z0-9_]{1,31}$")
#: the quotas a plan row may set; each is enforced by one reader
#: (interfaces/api/deps.py) with the config table as its default
QUOTA_KEYS: tuple[str, ...] = ("max_users", "max_companies")

_ACRONYMS = {"ai": "AI", "kpi": "KPI", "dot": "DOT"}


def label_of(feature_id: str) -> str:
    """A readable name for the operator console, from the id alone —
    the customer dashboard's names are translated per locale and live
    with it; the console is English and reads the registry."""
    words = re.split(r"[_\-]", feature_id)
    return " ".join(_ACRONYMS.get(w, w.capitalize()) for w in words if w)


def catalog() -> list[dict]:
    """The sellable set as the console draws it: registry order, each
    with its kind, tier, parent and a label."""
    return [
        {"id": e.id, "kind": e.kind, "tier": e.tier, "parent": e.parent,
         "label": label_of(e.id), "flags": sorted(_FLAGS_OF[e.id])}
        for e in ENTRIES if e.id in _EXCLUDABLE_SET
    ]


def quota_defaults(tier: str) -> dict[str, int]:
    """The config table's numbers for a tier — what applies when the
    plan row sets none (0 = unlimited)."""
    from infra.config import QUOTA_MAX_COMPANIES, QUOTA_MAX_USERS
    return {
        "max_users": QUOTA_MAX_USERS.get(tier, QUOTA_MAX_USERS.get("free", 0)),
        "max_companies": QUOTA_MAX_COMPANIES.get(tier, QUOTA_MAX_COMPANIES.get("free", 1)),
    }


def normalize_included(ids) -> tuple[list[str], list[str]]:
    """``(included, unknown)`` — ``["*"]`` whole when everything is
    named; else the sellable ids in registry order, deduplicated; ids
    outside the sellable set come back as *unknown* for the caller to
    refuse."""
    ids = list(ids or [])
    if EVERYTHING in ids:
        return [EVERYTHING], [i for i in ids if i != EVERYTHING and i not in _EXCLUDABLE_SET]
    wanted = set(ids)
    unknown = [i for i in ids if i not in _EXCLUDABLE_SET]
    return [i for i in EXCLUDABLE if i in wanted], unknown

# the last-known table: tier → included ids (a frozenset, or EVERYTHING)
_PLANS: dict[str, frozenset[str] | str] = {}
_LOADED_AT: float | None = None      # monotonic; None = never loaded
# the last-known quotas per tier (a plan's numbers, beside its ids)
_QUOTAS: dict[str, dict] = {}
# the last-known label per tier (what the customer's page calls it)
_LABELS: dict[str, str] = {}
#: flag → the sellable entry that owns it (the 403's "which feature")
_OWNER_OF_FLAG: dict[str, str] = {f: fid for fid, fl in _FLAGS_OF.items() for f in fl}
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
    labels: dict[str, str] = {}
    for r in rows:
        inc = r.get("included") or []
        fresh[r["tier"]] = EVERYTHING if EVERYTHING in inc else frozenset(inc)
        quotas[r["tier"]] = dict(r.get("quotas") or {})
        labels[r["tier"]] = str(r.get("label") or "").strip() or str(r["tier"]).replace("_", " ").title()
    _PLANS.clear(); _PLANS.update(fresh)
    _QUOTAS.clear(); _QUOTAS.update(quotas)
    _LABELS.clear(); _LABELS.update(labels)
    _LOADED_AT = time.monotonic()


def forget() -> None:
    """Back to never-loaded: no table, no remembered tiers (closed)."""
    global _LOADED_AT
    _PLANS.clear(); _QUOTAS.clear(); _LABELS.clear(); _TIER_OF.clear()
    _LOADED_AT = None


def plan_label(tier: Optional[str]) -> str:
    """What the plan is called on the customer's page — the row's label,
    else the key itself, readable."""
    if not tier:
        return ""
    return _LABELS.get(tier) or tier.replace("_", " ").title()


def excluded_for(tier: Optional[str]) -> list[str]:
    """The sellable ids the plan leaves out, in registry order — what
    the customer's surfaces draw as "not in your plan".  Empty for a
    plan that includes everything; the whole sellable set for a plan
    that is unknown (closed)."""
    inc = included_for(tier)
    if inc == EVERYTHING:
        return []
    if inc is None:
        return list(EXCLUDABLE)
    return [i for i in EXCLUDABLE if i not in inc]


def excluded_flags_for(tier: Optional[str]) -> list[str]:
    """Every flag the plan mask forces off for this tier, sorted — the
    exact answer the resolver gives, for a surface that locks by flag
    (the matrix, a route guard)."""
    return sorted(plan_flags_off(included_for(tier)))


def excludes_flag(tier: Optional[str], flag: str) -> Optional[str]:
    """The sellable entry through which the plan withholds *flag*, or
    ``None`` when the plan does not — the 403's reason.  A flag no
    plan line owns is never the plan's doing."""
    fid = _OWNER_OF_FLAG.get(flag)
    if fid is None:
        return None
    return fid if flag in plan_flags_off(included_for(tier)) else None


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


def account_plan_changed(account_id: int) -> None:
    """The billing contract: whoever moves an account to another plan
    (checkout, a cancelled subscription, a trial, the operator) calls
    this, and the resolver answers for the new plan on the next request
    instead of after its cache TTL."""
    from capabilities.permissions.roles import invalidate_permissions_cache
    _TIER_OF.pop(int(account_id), None)
    invalidate_permissions_cache(int(account_id))


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
