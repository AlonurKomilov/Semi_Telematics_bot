"""
Account module (department) enablement.

A "module" is a toggleable department an account turns on or off — Fleet,
Dispatch, Safety, HR, Accounting.  Core (universal features) and Account
admin are always on and are NOT listed here.

Storage contract: ``accounts.disabled_modules`` is a CSV of *disabled*
module ids.  Empty string = nothing disabled = everything on.  Storing
the disabled set (rather than the enabled set) means existing accounts —
whose column defaults to ``''`` — are all-on automatically, with no
backfill.  This is the "all free, default all-on" rollout.

The department list and the flag → module mask come from the feature
registry (capabilities/permissions/registry.py); the frontend catalog
mirrors both, and tests/test_feature_registry_drift.py holds them together.
"""
from __future__ import annotations

from capabilities.permissions.registry import REGISTRY, TOGGLEABLE_MODULES, derive_flag_modules


def parse_disabled(csv: str | None) -> set[str]:
    """CSV of disabled module ids → a clean set (ignores unknown ids)."""
    if not csv:
        return set()
    return {m.strip() for m in csv.split(",") if m.strip() in TOGGLEABLE_MODULES}


def enabled_modules(disabled_csv: str | None) -> list[str]:
    """The list of currently-enabled toggleable modules for an account."""
    disabled = parse_disabled(disabled_csv)
    return [m for m in TOGGLEABLE_MODULES if m not in disabled]


def to_disabled_csv(enabled: list[str] | tuple[str, ...]) -> str:
    """Inverse: an enabled list → the CSV of disabled ids to persist."""
    enabled_set = {m for m in enabled if m in TOGGLEABLE_MODULES}
    return ",".join(m for m in TOGGLEABLE_MODULES if m not in enabled_set)


# ── Module → permission mask ────────────────────────────
# Which department module(s) "own" each permission flag — DERIVED from
# the feature registry (capabilities/permissions/registry.py): a flag
# takes its feature's departments, or its own narrower set (Onboarding
# is HR's although Drivers is HR + Fleet + Safety).  A flag is forced
# OFF only when EVERY module that owns it is disabled, so a shared
# feature (Geofences = fleet + dispatch) survives as long as one of its
# departments is on.  This is what makes a disabled module hide its
# features *through the permission system* rather than via a second,
# parallel filter.  The hand-written list this replaced had drifted
# from the catalog on seven features, nine flags (a switch hid the
# page, not the API); closed 2026-09-06 — the registry carries no
# exemptions.
FLAG_MODULES: dict[str, frozenset[str]] = derive_flag_modules()


def module_enabled(disabled_csv: str | None, module: str) -> bool:
    """Whether one module is on for the account."""
    return module not in parse_disabled(disabled_csv)


# ── Account feature switches ─────────────────────────────
# A per-account on/off that is NOT a department: a column on ``accounts``
# an operator sets (no owner UI), read here and nowhere else.  Coaching
# is the one today — ``accounts.coaching_enabled``, default off.  Until
# 2026-09-07 seven places read that column directly (the service, the
# nightly job, the bot, /me, the nav twice, the page), each a path
# around the resolver; now the switch masks the feature's flags exactly
# as a disabled department does, and every reader asks permissions.
# Billing's plan mask (a table of what a tier includes) slots in beside
# this: same shape, same place, fail-closed instead of fail-open.
ACCOUNT_FEATURE_SWITCHES: dict[str, str] = {
    "coaching": "coaching_enabled",
}


def switched_off_flags(acct) -> set[str]:
    """The flags forced off by the account's feature switches."""
    off: set[str] = set()
    for feature_id, column in ACCOUNT_FEATURE_SWITCHES.items():
        if not getattr(acct, column, False):
            off.update(REGISTRY[feature_id].flags)
    return off


def feature_available(acct, feature_id: str) -> bool:
    """Whether a feature is open for the ACCOUNT — the department(s) it
    belongs to on (any of them) and its switch, if it has one, on.  The
    one question a service or a job asks before working for an account
    with no user in hand; a user's access is the permission, which the
    resolver masks with this same answer."""
    if acct is None:
        return False
    entry = REGISTRY[feature_id]
    disabled = parse_disabled(getattr(acct, "disabled_modules", ""))
    if entry.modules and entry.modules <= disabled:
        return False
    column = ACCOUNT_FEATURE_SWITCHES.get(feature_id)
    if column is not None and not getattr(acct, column, False):
        return False
    return True


def masked_off_flags(disabled_csv: str | None) -> set[str]:
    """The permission flags to force off given the account's disabled modules."""
    disabled = parse_disabled(disabled_csv)
    if not disabled:
        return set()
    return {flag for flag, modules in FLAG_MODULES.items() if modules <= disabled}


def account_mask(fs, acct):
    """The account-level mask, whole: department switches and feature
    switches, applied to a role's FeatureSet.  The resolver's one call;
    nothing else masks — there is deliberately no department-only
    variant to reach for.  Duck-typed on the FeatureSet dataclass
    (``dataclasses.replace``) so this module stays import-free of the
    permissions module."""
    if acct is None:
        return fs
    off = masked_off_flags(getattr(acct, "disabled_modules", ""))
    off |= switched_off_flags(acct)
    return _force_off(fs, off)


def _force_off(fs, off: set[str]):
    if not off:
        return fs
    from dataclasses import replace
    return replace(fs, **{flag: False for flag in off})
