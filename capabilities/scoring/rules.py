"""Built-in default scoring rules.

Each rule is a pure function over the merged ``signals`` dict produced by
``capabilities.scoring.signals.collect_all``.  The ``condition`` callable
returns the number of times the rule fires for the window; the engine
multiplies that by ``points`` (clamped by ``cap``) — *legacy flat path*.

added a parallel **curve path**: when ``curve_kind`` is
non-null the rule evaluates as a smooth linear ramp over an exposure
metric (miles / drive-hours / pct), letting Safety penalties scale with
how much a driver actually drove instead of crossing brittle thresholds.

Per-account overrides live in the ``score_rules`` table and merge on top
of the defaults at evaluation time — same pattern used by
``role_permissions``.  Admins override scalar fields (``points``, ``cap``,
``enabled``, the three curve anchors); the curve *kind* and the signal
function stay code-defined.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Callable, Optional

from .curves import LINEAR_PCT, LINEAR_PER_HOUR, LINEAR_PER_KMI


# ── Pillar identifiers (used by the engine accumulator) ───────────

PILLAR_SAFETY     = "safety"
PILLAR_EFFICIENCY = "efficiency"
PILLAR_COMPLIANCE = "compliance"


# ── Score event (engine output, one per fired rule) ────────────────

@dataclass(frozen=True)
class ScoreEvent:
    rule_id: str
    label: str
    category: str            # 'safety' | 'fleet' | 'efficiency' (legacy UI grouping)
    pillar: str              # 'safety' | 'efficiency' | 'compliance'
    kind: str                # 'bonus' | 'penalty'
    points: int              # signed: + for bonus, − for penalty
    occurrences: int         # how many times the rule fired (1 for curves)


# ── Rule definition ────────────────────────────────────────────────

@dataclass(frozen=True)
class Rule:
    id: str
    label: str
    category: str            # legacy UI grouping ('safety'/'fleet'/'efficiency')
    pillar: str              # NEW: scoring pillar ('safety'/'efficiency'/'compliance')
    kind: str                # 'bonus' | 'penalty'
    points: int              # signed default per-occurrence weight (legacy path)
    condition: Optional[Callable[[dict], int]] = None
    cap: Optional[int] = None  # max signed total contribution per window (legacy path)
    enabled: bool = True
    # ── Curve mode (additive) — null fields fall back to the legacy
    # flat ``points × occurrences`` evaluator.
    curve_kind: Optional[str] = None
    curve_value_fn: Optional[Callable[[dict], float]] = field(default=None, hash=False, compare=False)
    curve_x_zero: Optional[float] = None
    curve_x_max:  Optional[float] = None
    curve_y_max:  Optional[int]   = None


# ── Built-in defaults ──────────────────────────────────────────────
#
# ``pillar`` controls which 0-cap budget the rule contributes to.
# ``category`` is preserved for the existing rule-editor UI grouping
# (Safety/Fleet/Efficiency tabs).

DEFAULT_RULES: tuple[Rule, ...] = (
    # ── SAFETY pillar (cap 50) ───────────────────────────────────
    Rule(
        id="safety.zero_events_week",
        label="Zero safety events (≥ 5 drive-hours)",
        category="safety", pillar=PILLAR_SAFETY, kind="bonus", points=10,
        # Reward only when the driver actually worked the window — stops
        # couch-drivers earning the bonus by being absent.
        condition=lambda s: 1 if (
            s.get("events", {}).get("count", 0) == 0
            and float(s.get("drive_hours", 0) or 0) >= 5.0
        ) else 0,
    ),
    Rule(
        id="safety.crash",
        # Crashes are too severe for a smooth ramp — keep flat.
        label="Crash event",
        category="safety", pillar=PILLAR_SAFETY, kind="penalty", points=-30, cap=-60,
        condition=lambda s: int(s.get("events", {}).get("by_type", {}).get("crash", 0)),
    ),
    Rule(
        id="safety.harsh_brake",
        label="Harsh braking (per 1000 mi)",
        category="safety", pillar=PILLAR_SAFETY, kind="penalty",
        # Curve: 0/kmi → 0 pts, 10/kmi → −20 pts (clamped beyond).
        # Legacy ``points``/``cap`` retained as fallback when admin clears
        # the curve fields.
        points=-3, cap=-30,
        curve_kind=LINEAR_PER_KMI,
        curve_value_fn=lambda s: float(s.get("events", {}).get("by_type", {}).get("braking", 0)),
        curve_x_zero=0.0, curve_x_max=10.0, curve_y_max=-20,
    ),
    Rule(
        id="safety.harsh_turn",
        label="Harsh turning (per 1000 mi)",
        category="safety", pillar=PILLAR_SAFETY, kind="penalty",
        points=-2, cap=-20,
        curve_kind=LINEAR_PER_KMI,
        curve_value_fn=lambda s: float(s.get("events", {}).get("by_type", {}).get("harshTurn", 0)),
        curve_x_zero=0.0, curve_x_max=10.0, curve_y_max=-15,
    ),
    Rule(
        id="safety.overspeed_rate",
        label="Speeding rate (min per drive-hour)",
        category="safety", pillar=PILLAR_SAFETY, kind="penalty",
        points=-5, cap=-25,
        # rate = overspeed_min / drive_h.  Anchor: 0/h → 0, 10 min/h → −25.
        curve_kind=LINEAR_PER_HOUR,
        curve_value_fn=lambda s: float(s.get("overspeed_min", 0) or 0),
        curve_x_zero=0.0, curve_x_max=10.0, curve_y_max=-25,
    ),
    Rule(
        id="safety.distracted_driving",
        label="Distracted driving (per 1000 mi)",
        category="safety", pillar=PILLAR_SAFETY, kind="penalty",
        points=-4, cap=-20,
        # Smooth ramp: 0/kmi → 0 pts, 5 events/kmi → −20 pts.
        # Samsara label: ``distractedDriving``
        curve_kind=LINEAR_PER_KMI,
        curve_value_fn=lambda s: float(
            s.get("events", {}).get("by_type", {}).get("distractedDriving", 0)
        ),
        curve_x_zero=0.0, curve_x_max=5.0, curve_y_max=-20,
    ),
    Rule(
        id="safety.mobile_usage",
        label="Mobile / phone usage while driving",
        category="safety", pillar=PILLAR_SAFETY, kind="penalty",
        # Flat: each confirmed mobile-use event is −8 (hard to argue away),
        # capped at −20 so a bad week doesn't bury the score entirely.
        points=-8, cap=-20,
        condition=lambda s: int(s.get("events", {}).get("by_type", {}).get("mobileUsage", 0)),
    ),
    Rule(
        id="safety.forward_collision",
        label="Forward collision warning (per 1000 mi)",
        category="safety", pillar=PILLAR_SAFETY, kind="penalty",
        points=-3, cap=-15,
        # Samsara label: ``forwardCollisionWarning``
        curve_kind=LINEAR_PER_KMI,
        curve_value_fn=lambda s: float(
            s.get("events", {}).get("by_type", {}).get("forwardCollisionWarning", 0)
        ),
        curve_x_zero=0.0, curve_x_max=5.0, curve_y_max=-15,
    ),
    Rule(
        id="safety.rolling_stop",
        label="Rolling stop (failed to come to full stop)",
        category="safety", pillar=PILLAR_SAFETY, kind="penalty",
        points=-5, cap=-15,
        # Samsara label: ``rollingStop``
        condition=lambda s: int(s.get("events", {}).get("by_type", {}).get("rollingStop", 0)),
    ),
    Rule(
        id="safety.near_collision",
        label="Near collision / tailgating event",
        category="safety", pillar=PILLAR_SAFETY, kind="penalty",
        # Severe: −15 each, but less lethal than a crash so capped at −30.
        # Samsara label: ``nearCollision`` or ``tailgating``
        points=-15, cap=-30,
        condition=lambda s: int(
            s.get("events", {}).get("by_type", {}).get("nearCollision", 0)
            + s.get("events", {}).get("by_type", {}).get("tailgating", 0)
        ),
    ),

    # ── EFFICIENCY pillar (cap 25) ───────────────────────────────
    Rule(
        id="eff.green_band_high",
        label="Strong eco / green-band driving",
        category="efficiency", pillar=PILLAR_EFFICIENCY, kind="bonus", points=5,
        condition=lambda s: 1 if s.get("green_pct", 0) >= 80 else 0,
    ),
    Rule(
        id="eff.green_band_low",
        label="Low eco / green-band driving",
        category="efficiency", pillar=PILLAR_EFFICIENCY, kind="penalty", points=-5,
        condition=lambda s: 1 if 0 < s.get("green_pct", 0) < 50 else 0,
    ),
    Rule(
        id="eff.antic_brake_high",
        label="Strong anticipatory braking",
        category="efficiency", pillar=PILLAR_EFFICIENCY, kind="bonus", points=3,
        condition=lambda s: 1 if s.get("antic_pct", 0) >= 80 else 0,
    ),
    Rule(
        id="eff.idle_pct_high",
        label="Excessive idling (> 25 % of wheel time)",
        category="efficiency", pillar=PILLAR_EFFICIENCY, kind="penalty",
        points=-4, cap=-12,
        # New rule (audit recommendation): idle_pct already a 0-100 %
        # signal — flat threshold for now, curve next iteration.
        condition=lambda s: 1 if (
            float(s.get("idle_pct", 0) or 0) > 25.0
            and float(s.get("drive_hours", 0) or 0) >= 1.0
        ) else 0,
    ),
    # ``efficiency.fuel_anomaly`` was historically tagged "fleet" — it's an
    # economic signal (low MPG / theft) so it lives in the Efficiency
    # pillar.
    Rule(
        id="efficiency.fuel_anomaly",
        label="Anomalous fuel entry (low MPG / theft pattern)",
        category="efficiency", pillar=PILLAR_EFFICIENCY, kind="penalty", points=-5, cap=-15,
        condition=lambda s: int(s.get("fuel", {}).get("anomalous_entries", 0)),
    ),

    # ── COMPLIANCE pillar (cap 25) ───────────────────────────────
    Rule(
        id="compliance.pti_on_time",
        # Audit: doing the legally-required job isn't a bonus.  Reduced
        # from +3/+15 cap to +1/+5 cap for one release (deprecation
        # path); next release retires the rule.
        label="Pre-trip inspection on time",
        category="compliance", pillar=PILLAR_COMPLIANCE, kind="bonus", points=1, cap=5,
        condition=lambda s: int(s.get("maintenance", {}).get("pti_completed", 0)),
    ),
    Rule(
        id="compliance.pti_overdue",
        label="Overdue pre-trip inspection",
        category="compliance", pillar=PILLAR_COMPLIANCE, kind="penalty", points=-5, cap=-15,
        condition=lambda s: int(s.get("maintenance", {}).get("pti_overdue", 0)),
    ),
    Rule(
        id="compliance.maintenance_overdue",
        label="Overdue maintenance task",
        category="compliance", pillar=PILLAR_COMPLIANCE, kind="penalty", points=-3, cap=-10,
        condition=lambda s: int(s.get("maintenance", {}).get("other_overdue", 0)),
    ),
    Rule(
        id="compliance.camera_clean",
        label="Cameras passing all checks",
        category="compliance", pillar=PILLAR_COMPLIANCE, kind="bonus", points=2, cap=5,
        condition=lambda s: 1 if (
            s.get("cameras", {}).get("checks_total", 0) > 0
            and s.get("cameras", {}).get("checks_failing", 0) == 0
        ) else 0,
    ),
    Rule(
        id="compliance.camera_obstructed",
        label="Camera obstructed / misaligned",
        category="compliance", pillar=PILLAR_COMPLIANCE, kind="penalty", points=-3, cap=-10,
        condition=lambda s: int(s.get("cameras", {}).get("vehicles_with_failing_camera", 0)),
    ),
    Rule(
        id="compliance.fuel_logged",
        label="Fuel entries logged for all trucks",
        category="compliance", pillar=PILLAR_COMPLIANCE, kind="bonus", points=2, cap=5,
        condition=lambda s: 1 if (
            s.get("fuel", {}).get("vehicles_assigned", 0) > 0
            and s.get("fuel", {}).get("vehicles_with_entries", 0)
                >= s.get("fuel", {}).get("vehicles_assigned", 0)
        ) else 0,
    ),
    Rule(
        # the label "All assigned trucks fault-free" read
        # as a driver-behavior win, but the driver doesn't control DTCs.
        # Re-labelled to make it explicit this is a vehicle-health
        # signal that happens to live in the driver-compliance pillar
        # because compliance is where we surface fleet hygiene.
        # Cap stays at +8 (small reward) so it doesn't dominate score.
        id="compliance.vehicle_health_clean",
        label="Paired truck fault-free (vehicle hygiene)",
        category="compliance", pillar=PILLAR_COMPLIANCE, kind="bonus", points=4, cap=8,
        condition=lambda s: 1 if (
            s.get("vehicle_health", {}).get("vehicles_assigned", 0) > 0
            and s.get("vehicle_health", {}).get("vehicles_clean", 0)
                == s.get("vehicle_health", {}).get("vehicles_assigned", 0)
        ) else 0,
    ),
    Rule(
        id="compliance.active_dtc",
        label="Truck rolling with active fault code",
        category="compliance", pillar=PILLAR_COMPLIANCE, kind="penalty", points=-4, cap=-12,
        condition=lambda s: int(s.get("vehicle_health", {}).get("vehicles_with_faults", 0)),
    ),
    Rule(
        id="compliance.health_critical",
        label="Critical health alert (low oil / overheating / low battery)",
        category="compliance", pillar=PILLAR_COMPLIANCE, kind="penalty", points=-6, cap=-15,
        condition=lambda s: int(s.get("vehicle_health", {}).get("critical_alerts", 0)),
    ),
    Rule(
        id="compliance.health_minor",
        label="Minor health alert (low DEF / seatbelt)",
        category="compliance", pillar=PILLAR_COMPLIANCE, kind="penalty", points=-2, cap=-8,
        condition=lambda s: int(s.get("vehicle_health", {}).get("minor_alerts", 0)),
    ),
)


def get_default_rules() -> tuple[Rule, ...]:
    return DEFAULT_RULES


def apply_overrides(rules: tuple[Rule, ...], overrides: dict[str, dict]) -> list[Rule]:
    """Merge per-account overrides onto built-in defaults.

    ``overrides`` is ``{rule_id: {points, cap, enabled, curve_x_zero,
    curve_x_max, curve_y_max}}`` typically loaded from the ``score_rules``
    table.  Curve *kind* and value-fn are NOT overridable (code-defined)
    so admins can re-tune slope but not point a rule at a different
    signal.  Unknown rule_ids are ignored (forward-compatible).
    """
    out: list[Rule] = []
    for r in rules:
        ov = overrides.get(r.id) or {}
        out.append(Rule(
            id=r.id,
            label=r.label,
            category=r.category,
            pillar=r.pillar,
            kind=r.kind,
            points=int(ov["points"]) if "points" in ov else r.points,
            condition=r.condition,
            cap=(int(ov["cap"]) if ov.get("cap") is not None else r.cap)
                if "cap" in ov else r.cap,
            enabled=bool(ov["enabled"]) if "enabled" in ov else r.enabled,
            curve_kind=r.curve_kind,  # not overridable
            curve_value_fn=r.curve_value_fn,
            curve_x_zero=(float(ov["curve_x_zero"])
                          if ov.get("curve_x_zero") is not None else r.curve_x_zero),
            curve_x_max=(float(ov["curve_x_max"])
                         if ov.get("curve_x_max") is not None else r.curve_x_max),
            curve_y_max=(int(ov["curve_y_max"])
                         if ov.get("curve_y_max") is not None else r.curve_y_max),
        ))
    return out


__all__ = (
    "DEFAULT_RULES", "PILLAR_SAFETY", "PILLAR_EFFICIENCY", "PILLAR_COMPLIANCE",
    "Rule", "ScoreEvent", "apply_overrides", "get_default_rules",
    "LINEAR_PCT", "LINEAR_PER_HOUR", "LINEAR_PER_KMI",
)
