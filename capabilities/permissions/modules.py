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

The frontend mirrors this list in interfaces/dashboard/src/config/
featureCatalog.ts (TOGGLEABLE_MODULES); keep the two in sync.
"""
from __future__ import annotations

# Order is display order on the Permissions page's department bands.
# "payroll" is a single-feature module: it was historically gated by the
# one-off accounts.payroll_enabled flag; folding it here gives it the same
# on/off mechanism as every other department (one switch, one home).
TOGGLEABLE_MODULES: tuple[str, ...] = (
    "fleet", "dispatch", "safety", "hr", "accounting", "payroll",
)


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


# ── Module → permission mask ──────────────────────────────────────
# Which department module(s) "own" each permission flag.  A flag is
# forced OFF only when EVERY module that owns it is disabled — so a
# shared feature (e.g. Geofences = fleet+dispatch) survives as long as
# one of its departments is on.  This is what makes a disabled module
# hide its features *through the permission system* rather than via a
# second, parallel filter.  Mirrors the `modules` arrays in the frontend
# feature catalog (interfaces/dashboard/src/config/featureCatalog.ts).
#
# NB: flags NOT listed here are never masked — that means the universal
# (core) flags (location/vehicle/alerts/reports/AI) and the account-admin
# flags (manage_*).  A couple of features (Parking → can_alerts, Cameras
# → can_faults) are gated by a *system* flag, so they can't be masked
# here; the generated sidebar applies the catalog's module filter for
# those at the nav layer instead.
FLAG_MODULES: dict[str, frozenset[str]] = {
    # Fleet
    "can_maintenance_all": frozenset({"fleet"}),
    "can_maintenance_vehicle": frozenset({"fleet"}),
    "can_work_orders_all": frozenset({"fleet"}),
    "can_work_orders_vehicle": frozenset({"fleet"}),
    "can_inspections_all": frozenset({"fleet"}),
    "can_inspections_vehicle": frozenset({"fleet"}),
    # Dispatch
    "can_route_all": frozenset({"dispatch"}),
    "can_route_vehicle": frozenset({"dispatch"}),
    # Geofences — fleet + dispatch
    "can_geofence_all": frozenset({"fleet", "dispatch"}),
    "can_geofence_vehicle": frozenset({"fleet", "dispatch"}),
    # Cameras — safety + fleet
    "can_cameras": frozenset({"safety", "fleet"}),
    # Parking — dispatch + fleet + safety
    "can_parking_all": frozenset({"dispatch", "fleet", "safety"}),
    "can_parking_vehicle": frozenset({"dispatch", "fleet", "safety"}),
    # Safety + HR
    "can_events_all": frozenset({"safety", "hr"}),
    "can_events_vehicle": frozenset({"safety", "hr"}),
    "can_scorecard_all": frozenset({"safety", "hr"}),
    "can_scorecard_vehicle": frozenset({"safety", "hr"}),
    "can_manage_scorecard_rules": frozenset({"safety", "hr"}),  # Scorecards' config — masks with its feature

    "can_coaching_admin": frozenset({"hr", "safety"}),
    "can_coaching_view_own": frozenset({"hr", "safety"}),
    # Drivers (documents) — hr + fleet + safety
    "can_manage_driver_docs": frozenset({"hr", "fleet", "safety"}),
    "can_driver_docs_own": frozenset({"hr", "fleet", "safety"}),
    # Recruiting (driver-application intake) — hr.  Masking these with the
    # HR module means disabling HR turns recruiting OFF at the API too, not
    # just hidden in the nav — so "module off" is a real switch.
    "can_manage_applications": frozenset({"hr"}),
    "can_convert_to_driver": frozenset({"hr"}),
    # Accounting (costs + payroll)
    "can_fuel_cost": frozenset({"accounting", "dispatch"}),
    "can_cost_per_mile": frozenset({"accounting", "fleet"}),
    # Payroll — its own module (see TOGGLEABLE_MODULES note).
    "can_payroll_admin": frozenset({"payroll"}),
    "can_payroll_view_own": frozenset({"payroll"}),
}


def module_enabled(disabled_csv: str | None, module: str) -> bool:
    """Whether one module is on for the account — the check the payroll
    service/bot/API gates use (replaces the legacy payroll_enabled flag)."""
    return module not in parse_disabled(disabled_csv)


def masked_off_flags(disabled_csv: str | None) -> set[str]:
    """The permission flags to force off given the account's disabled modules."""
    disabled = parse_disabled(disabled_csv)
    if not disabled:
        return set()
    return {flag for flag, mods in FLAG_MODULES.items() if mods <= disabled}


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
