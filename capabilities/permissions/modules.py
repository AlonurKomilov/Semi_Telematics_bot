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

from capabilities.permissions.registry import TOGGLEABLE_MODULES, derive_flag_modules


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
# from the catalog on seven flags; registry.MASK_DRIFT names them.
FLAG_MODULES: dict[str, frozenset[str]] = derive_flag_modules()


def module_enabled(disabled_csv: str | None, module: str) -> bool:
    """Whether one module is on for the account — the check the driver-pay
    service/bot/API gates use (replaces the legacy payroll_enabled flag)."""
    return module not in parse_disabled(disabled_csv)


def masked_off_flags(disabled_csv: str | None) -> set[str]:
    """The permission flags to force off given the account's disabled modules."""
    disabled = parse_disabled(disabled_csv)
    if not disabled:
        return set()
    return {flag for flag, modules in FLAG_MODULES.items() if modules <= disabled}


def mask_disabled_modules(fs, disabled_csv: str | None):
    """Return *fs* with every fully-disabled-module flag forced off.

    Duck-typed on the FeatureSet dataclass (uses ``dataclasses.replace``)
    so this module stays import-free of the permissions module.
    """
    off = masked_off_flags(disabled_csv)
    if not off:
        return fs
    from dataclasses import replace
    return replace(fs, **{flag: False for flag in off})
