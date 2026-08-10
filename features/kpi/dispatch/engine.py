"""Dispatcher incentive engine — pure, money-safe.

Computes what a dispatcher is PAID: a % of the gross their trucks earned,
looked up from a customer-configured tier table.  This is compensation,
not the A–D analytics next door in ``grades.py`` — the surfaces exposing
its output carry their own permission.

Everything here is a pure function over plain dicts (JSON-friendly, so
config rows can come straight from storage).  No IO, no clock, no DB —
the golden test reproduces a real settlement sheet to the cent, including
its $1,332.03 period total, and that is the acceptance bar for any change
in this file.

VERIFIED SEMANTICS — each was reverse-engineered from the customer's
real settlement sheet and checked numerically, not assumed:

  * RPM = load gross WITHOUT extras ÷ miles, rounded half-up to 2dp,
    and tier comparison uses the ROUNDED value.  Both halves matter:
    (22,680 − 350 bonus) ÷ 10,222 = 2.1845 → the sheet says 2.18, so
    extras are excluded from RPM; and 15,200 ÷ 7,601 = 1.9997 → the
    sheet says 2.00 AND paid the ≥2.0 tier, so comparisons see 2.00.

  * The %-base ("KPI gross") = load gross + extras.  TONU, bonuses and
    other dispatch additions count toward the money the % applies to
    (owner decision), even though they are excluded from RPM.

  * Adjusted target = weekly_target ÷ 7 × active_days.  A truck in the
    shop or a driver on home time is not the dispatcher's fault, so the
    bar drops with the days the truck could work: $8,000/wk over 19
    active days is a $21,714.29 target, verified against the sheet.

  * Money rounds HALF-UP.  22,675 × 1.5% = 340.125 → $340.13 on the
    sheet.  Python's built-in round() is banker's rounding and answers
    340.12 — a one-cent disagreement with every settlement the industry
    writes, multiplied by every row.  Hence Decimal throughout.

  * Weekly-equivalent gross = kpi_gross ÷ active_days × 7.  Every gross
    threshold in a tier table is a WEEKLY number (that is how the
    customers write them), so a part-period truck is scaled up before
    comparison rather than judged on a partial total.

Three tier models, all customer-configured (never hardcoded):

  ladder   rows of (requires_target?, min_weekly_gross?, min_rpm?) → pct;
           the highest pct among satisfied rows wins.
  hybrid   rows of (gross_min..gross_max, rpm_min..rpm_max) → pct; a row
           matches only when BOTH ranges hold.
  fixed    two independent ladders (axis 'rpm' | 'gross'); the per-axis
           best percentages are combined by ``combine_rule``:
           'lower' | 'higher' | 'add'.
"""

from __future__ import annotations

from decimal import Decimal, ROUND_HALF_UP
from typing import Any, Optional

MODELS = ("ladder", "fixed", "hybrid")
COMBINE_RULES = ("lower", "higher", "add")


def money(x: Any) -> float:
    """Round to cents, half-up — settlement rounding, not banker's."""
    return float(Decimal(str(x)).quantize(Decimal("0.01"), ROUND_HALF_UP))


def compute_rpm(base_gross: float, miles: float) -> Optional[float]:
    """Rate per mile from LOAD gross (extras excluded), 2dp half-up.

    Returns the rounded value because that is what tier comparison uses
    — see the module docstring's 1.9997→2.00 case.
    """
    if not miles or miles <= 0:
        return None
    return money(Decimal(str(base_gross)) / Decimal(str(miles)))


def adjusted_target(weekly_target: float, active_days: int) -> float:
    """The period's gross bar: weekly target prorated by active days."""
    if active_days <= 0:
        return 0.0
    return money(
        Decimal(str(weekly_target)) / Decimal(7) * Decimal(active_days))


def weekly_equivalent(kpi_gross: float, active_days: int) -> float:
    """Period gross scaled to a 7-day week, for tier thresholds."""
    if active_days <= 0:
        return 0.0
    return money(
        Decimal(str(kpi_gross)) / Decimal(active_days) * Decimal(7))


def _floored(config: dict, weekly_eq: float, rpm: Optional[float]) -> bool:
    """The removal rule: "trucks under $X gross AND Y RPM".  BOTH must be
    below — a low-gross truck with strong RPM is not removed."""
    fg = config.get("floor_weekly_gross")
    fr = config.get("floor_rpm")
    if fg is None or fr is None:
        return False
    return weekly_eq < float(fg) and (rpm is None or rpm < float(fr))


def resolve_pct(
    config: dict, tiers: list[dict], *,
    weekly_eq: float, rpm: Optional[float], target_met: bool,
) -> float:
    """The tier lookup.  Returns a percent number (1.5 means 1.5%)."""
    model = config.get("model", "ladder")
    if model not in MODELS:
        raise ValueError(f"unknown incentive model {model!r}")
    if _floored(config, weekly_eq, rpm):
        return 0.0
    r = rpm if rpm is not None else 0.0

    if model == "ladder":
        best = 0.0
        for t in tiers:
            if t.get("requires_target") and not target_met:
                continue
            mg = t.get("min_weekly_gross")
            if mg is not None and weekly_eq < float(mg):
                continue
            mr = t.get("min_rpm")
            if mr is not None and r < float(mr):
                continue
            best = max(best, float(t["pct"]))
        return best

    if model == "hybrid":
        best = 0.0
        for t in tiers:
            if not (float(t["gross_min"]) <= weekly_eq <= float(t["gross_max"])):
                continue
            if not (float(t["rpm_min"]) <= r <= float(t["rpm_max"])):
                continue
            best = max(best, float(t["pct"]))
        return best

    # fixed: best of each axis, combined by rule.
    rule = config.get("combine_rule", "lower")
    if rule not in COMBINE_RULES:
        raise ValueError(f"unknown combine_rule {rule!r}")
    best_rpm = max(
        (float(t["pct"]) for t in tiers
         if t.get("axis") == "rpm" and r >= float(t["min"])),
        default=0.0,
    )
    best_gross = max(
        (float(t["pct"]) for t in tiers
         if t.get("axis") == "gross" and weekly_eq >= float(t["min"])),
        default=0.0,
    )
    if rule == "lower":
        return min(best_rpm, best_gross)
    if rule == "higher":
        return max(best_rpm, best_gross)
    return best_rpm + best_gross


def compute_truck_row(
    config: dict, tiers: list[dict], *,
    base_gross: float, extras: float, miles: float,
    weekly_target: float, active_days: int,
) -> dict:
    """One truck's period row — the settlement sheet's row, computed.

    ``base_gross`` is the loads' revenue; ``extras`` the signed sum of
    TONU / bonus / dispatch additions.  RPM sees only the base; the %
    applies to their sum.  Everything money is rounded half-up.
    """
    kpi_gross = money(Decimal(str(base_gross)) + Decimal(str(extras)))
    rpm = compute_rpm(base_gross, miles)
    target = adjusted_target(weekly_target, active_days)
    weekly_eq = weekly_equivalent(kpi_gross, active_days)
    target_met = active_days > 0 and kpi_gross >= target
    pct = (
        0.0 if active_days <= 0
        else resolve_pct(
            config, tiers,
            weekly_eq=weekly_eq, rpm=rpm, target_met=target_met,
        )
    )
    # A zero in a money column carries its reason (UX audit rule): "why
    # is this 0%?" is the first question a dispatcher asks their manager,
    # and the engine is the only thing that knows the answer.
    zero_reason = ""
    if pct == 0.0:
        if active_days <= 0:
            zero_reason = "no_active_days"
        elif _floored(config, weekly_eq, rpm):
            zero_reason = "floor"
        else:
            zero_reason = "no_tier"
    return {
        "kpi_gross": kpi_gross,
        "rpm": rpm,
        "adjusted_target": target,
        "weekly_equivalent": weekly_eq,
        "target_met": target_met,
        "pct": pct,
        "kpi_dollars": money(Decimal(str(kpi_gross)) * Decimal(str(pct)) / 100),
        "zero_reason": zero_reason,
    }


def validate_exception(override_pct: float, config: dict) -> None:
    """The policy wall: "no exceptions above the cap".

    An exception replaces the computed % with a human-chosen one, for
    named reasons (home time, truck issues, recovery, new MC).  The June
    2026 policy allows them only up to 2% — above that, only the actual
    numbers count.  Raises rather than clamps: a silently-reduced
    override is a payout nobody chose.
    """
    cap = config.get("exception_cap_pct")
    if cap is not None and float(override_pct) > float(cap):
        raise ValueError(
            f"exception {override_pct}% exceeds the policy cap of {cap}% — "
            "overrides above the cap are not permitted; the computed "
            "numbers stand"
        )


def confirmed_dollars(
    row: dict, config: dict, override_pct: Optional[float] = None,
) -> float:
    """The row's payable amount: computed, or a validated override."""
    if override_pct is None:
        return float(row["kpi_dollars"])
    validate_exception(override_pct, config)
    return money(
        Decimal(str(row["kpi_gross"])) * Decimal(str(override_pct)) / 100)


def payout_total(confirmed: list[float]) -> float:
    """A dispatcher's period payout — the sum of confirmed row amounts."""
    return money(sum(Decimal(str(c)) for c in confirmed))


# ── Config validation (called by the endpoint BEFORE any write) ──────
#
# The engine owns what a legal configuration is, because the engine is
# what will consume it.  Storage stores faithfully; the endpoint refuses
# loudly.  Everything raises ValueError with a message an admin can act
# on — these surface as 422s, not stack traces.

CADENCES = ("weekly", "monthly", "custom")

_TIER_KEYS = {
    "ladder": {"requires_target", "min_weekly_gross", "min_rpm", "pct"},
    "hybrid": {"gross_min", "gross_max", "rpm_min", "rpm_max", "pct"},
    "fixed": {"axis", "min", "pct"},
}


def _num(v: Any, what: str) -> float:
    try:
        f = float(v)
    except (TypeError, ValueError):
        raise ValueError(f"{what} must be a number, got {v!r}")
    if f < 0:
        raise ValueError(f"{what} cannot be negative")
    return f


def validate_config(config: dict) -> None:
    """The config row's invariants, independent of tiers."""
    model = config.get("model", "ladder")
    if model not in MODELS:
        raise ValueError(
            f"model must be one of {', '.join(MODELS)}, got {model!r}")
    rule = config.get("combine_rule", "lower")
    if rule not in COMBINE_RULES:
        raise ValueError(
            f"combine_rule must be one of {', '.join(COMBINE_RULES)}, "
            f"got {rule!r}")
    cadence = config.get("calc_cadence", "weekly")
    if cadence not in CADENCES:
        raise ValueError(
            f"calc_cadence must be one of {', '.join(CADENCES)}, "
            f"got {cadence!r}")
    if cadence == "custom":
        days = config.get("calc_custom_days")
        if not isinstance(days, int) or not (1 <= days <= 92):
            raise ValueError(
                "custom cadence needs calc_custom_days between 1 and 92")
    for key in ("exception_cap_pct", "floor_weekly_gross", "floor_rpm"):
        if config.get(key) is not None:
            _num(config[key], key)
    cap = config.get("exception_cap_pct")
    if cap is not None and float(cap) > 100:
        raise ValueError("exception_cap_pct is a percent — over 100 is a typo")


def validate_tiers(model: str, tiers: list[Any]) -> None:
    """Every tier row must be consumable by ``resolve_pct`` for this
    model.  Checked at WRITE time so a bad row is refused with a message
    naming it, instead of silently paying 0% at run time."""
    if not isinstance(tiers, list):
        raise ValueError("tiers must be a list of rows")
    allowed = _TIER_KEYS.get(model)
    if allowed is None:
        raise ValueError(f"unknown model {model!r}")
    for i, t in enumerate(tiers, start=1):
        if not isinstance(t, dict):
            raise ValueError(f"tier {i} must be an object")
        unknown = set(t) - allowed
        if unknown:
            raise ValueError(
                f"tier {i} has keys {sorted(unknown)} that the "
                f"{model} model does not use")
        pct = _num(t.get("pct"), f"tier {i} pct")
        if pct > 100:
            raise ValueError(f"tier {i} pct is a percent — {pct} is a typo")
        if model == "ladder":
            for k in ("min_weekly_gross", "min_rpm"):
                if t.get(k) is not None:
                    _num(t[k], f"tier {i} {k}")
        elif model == "hybrid":
            for k in ("gross_min", "gross_max", "rpm_min", "rpm_max"):
                _num(t.get(k), f"tier {i} {k}")
            if float(t["gross_min"]) > float(t["gross_max"]):
                raise ValueError(f"tier {i}: gross_min exceeds gross_max")
            if float(t["rpm_min"]) > float(t["rpm_max"]):
                raise ValueError(f"tier {i}: rpm_min exceeds rpm_max")
        else:  # fixed
            if t.get("axis") not in ("rpm", "gross"):
                raise ValueError(
                    f"tier {i} axis must be 'rpm' or 'gross'")
            _num(t.get("min"), f"tier {i} min")
