"""Score engine — pure: rules + signals → composite score + pillar breakdown.

No I/O, no DB, no clock — fully unit-testable.  Signal collection and
override loading happen in ``service.py``.

structure (April 2026 redesign):
    - Three pillars: Safety / Efficiency / Compliance with caps 50/25/25.
    - Each pillar starts full (= cap).  Penalties drag down within the
      pillar's own budget; bonuses can refund up to the cap; nothing
      leaks across pillars.
    - Total = sum of pillar subtotals → 0..100 with no arbitrary base.
    - Optional smooth curves on a per-rule basis (see ``curves.py``).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Iterable

from .rules.curves import evaluate_curve
from .rules.defs import (
    PILLAR_COMPLIANCE,
    PILLAR_EFFICIENCY,
    PILLAR_SAFETY,
    Rule,
    ScoreEvent,
)


# Sums to 100. Fixed for now; per-tenant tuning is a future iteration.
PILLAR_CAPS: dict[str, int] = {
    PILLAR_SAFETY:     50,
    PILLAR_EFFICIENCY: 25,
    PILLAR_COMPLIANCE: 25,
}
PILLAR_ORDER: tuple[str, ...] = (PILLAR_SAFETY, PILLAR_EFFICIENCY, PILLAR_COMPLIANCE)

# Drivers below this threshold are flagged ``insufficient_data``: kept in
# the list (so admins see them) but excluded from rankings.
MIN_DRIVE_HOURS_FOR_RANKING = 5.0


@dataclass
class PillarSummary:
    name: str                                   # 'safety' | 'efficiency' | 'compliance'
    cap: int                                    # full budget (50/25/25)
    bonus_total: int = 0                        # capped sum of bonus events
    penalty_total: int = 0                      # capped sum of penalty events (≤ 0)
    subtotal: int = 0                           # clamp(0..cap, cap + bonus + penalty)
    has_data: bool = True                       # False ⇒ pillar should render n/a
    bonuses:  list[ScoreEvent] = field(default_factory=list)
    penalties: list[ScoreEvent] = field(default_factory=list)


# subject abstraction. Scorecards are now keyed by
# ``subject_id``/``subject_name``/``subject_type`` so the same engine
# can score either a driver or a vehicle.  ``driver_id``/``driver_name``
# remain as @property aliases for back-compat of the JSON contract
# during the dashboard/bot transition.
VALID_SUBJECT_TYPES: tuple[str, ...] = ("driver", "vehicle")


@dataclass
class Scorecard:
    subject_id: str
    subject_name: str
    total: int                                  # = sum(pillar subtotals)  (0..100)
    pillars: dict[str, PillarSummary]
    subject_type: str = "driver"                # 'driver' | 'vehicle'
    insufficient_data: bool = False
    # Legacy aliases — derived from pillar contents for backward-compat
    # of the JSON contract while the dashboard transitions.
    base: int = 0
    bonus_points: int = 0
    penalty_points: int = 0
    bonuses:   list[ScoreEvent] = field(default_factory=list)
    penalties: list[ScoreEvent] = field(default_factory=list)

    # ── Back-compat aliases (one-release deprecation window) ──
    # Older call sites (bot, AI tools, tests) still ask for
    # ``driver_id`` / ``driver_name``.  These properties keep that path
    # alive without lying about the underlying subject when it's a
    # vehicle — callers that care about the distinction should read
    # ``subject_type`` explicitly.
    @property
    def driver_id(self) -> str:
        return self.subject_id

    @property
    def driver_name(self) -> str:
        return self.subject_name


def _pillar_has_data(pillar: str, signals: dict) -> bool:
    """Heuristic: does this pillar have enough signal substrate to score?

    Used to surface "n/a" in the UI when a tenant hasn't wired up a
    feature (e.g. cameras), instead of silently awarding full marks.
    """
    drive_h = float(signals.get("drive_hours", 0) or 0)
    if pillar == PILLAR_SAFETY:
        # Safety needs either a driving window or a recorded event feed.
        return drive_h > 0 or signals.get("events", {}).get("count", 0) > 0
    if pillar == PILLAR_EFFICIENCY:
        return drive_h > 0
    # Compliance — any of maintenance / cameras / fuel / health visible.
    return any(
        bool(signals.get(k, {})) and (
            (signals[k].get("vehicles_assigned", 0) or 0) > 0
            or (signals[k].get("checks_total", 0) or 0) > 0
            or (signals[k].get("entries_logged", 0) or 0) > 0
            or (signals[k].get("pti_completed", 0) or 0) > 0
            or (signals[k].get("pti_overdue", 0) or 0) > 0
        )
        for k in ("maintenance", "cameras", "fuel", "vehicle_health")
    )


def evaluate_rule(rule: Rule, signals: dict) -> ScoreEvent | None:
    """Run one rule against the merged signals dict.  None if it didn't fire.

    Two modes:
      * Curve mode (``rule.curve_kind`` is not None): smooth ramp via
        ``curves.evaluate_curve`` over an exposure metric.
      * Legacy flat mode: ``rule.points × rule.condition(signals)``.
    """
    if not rule.enabled:
        return None

    # ── Curve mode ──────────────────────────────────────────────
    if rule.curve_kind and rule.curve_value_fn is not None \
            and rule.curve_x_max is not None and rule.curve_y_max is not None:
        try:
            raw = float(rule.curve_value_fn(signals) or 0)
        except Exception:
            return None
        miles = float(signals.get("miles", 0) or 0)
        drive_h = float(signals.get("drive_hours", 0) or 0)
        pts = evaluate_curve(
            rule.curve_kind,
            raw_value=raw,
            miles=miles,
            drive_hours=drive_h,
            x_zero=float(rule.curve_x_zero or 0),
            x_max=float(rule.curve_x_max),
            y_max=float(rule.curve_y_max),
        )
        # An exact zero contribution is "didn't fire" — keeps the
        # breakdown UI uncluttered.
        if abs(pts) < 0.5:
            return None
        return ScoreEvent(
            rule_id=rule.id, label=rule.label,
            category=rule.category, pillar=rule.pillar,
            kind=rule.kind, points=int(round(pts)),
            occurrences=1,  # curves don't have a discrete count
        )

    # ── Legacy flat mode ────────────────────────────────────────
    if rule.condition is None:
        return None
    try:
        n = int(rule.condition(signals) or 0)
    except Exception:
        return None
    if n <= 0:
        return None

    raw = rule.points * n
    points = raw
    if rule.cap is not None:
        if rule.points > 0:
            points = min(raw, rule.cap)
        else:
            points = max(raw, rule.cap)

    return ScoreEvent(
        rule_id=rule.id, label=rule.label,
        category=rule.category, pillar=rule.pillar,
        kind=rule.kind, points=int(points), occurrences=n,
    )


def score(
    subject_id: str,
    subject_name: str,
    signals: dict,
    rules: Iterable[Rule],
    *,
    subject_type: str = "driver",
    pillar_caps: dict[str, int] | None = None,
) -> Scorecard:
    """Compute a Scorecard with per-pillar accumulation.

    Each pillar starts at its cap; bonuses can refund toward the cap;
    penalties drag the subtotal down to 0.  Pillars are independent —
    a Safety penalty can never be paid off by Compliance paperwork.

    *pillar_caps* overrides the module-level ``PILLAR_CAPS`` defaults,
    enabling per-tenant weight tuning (e.g. hazmat carriers can set
    Safety=70, Efficiency=15, Compliance=15).  Must sum to 100 and
    contain all three pillar keys — caller's responsibility to validate.
    """
    caps = pillar_caps if pillar_caps and sum(pillar_caps.values()) == 100 else PILLAR_CAPS
    pillars: dict[str, PillarSummary] = {
        name: PillarSummary(name=name, cap=cap)
        for name, cap in caps.items()
    }
    flat_bonuses:   list[ScoreEvent] = []
    flat_penalties: list[ScoreEvent] = []

    for rule in rules:
        ev = evaluate_rule(rule, signals)
        if ev is None:
            continue
        bucket = pillars.get(ev.pillar)
        if bucket is None:
            # Defensive: rule with unknown pillar — skip.
            continue
        if ev.kind == "bonus":
            bucket.bonuses.append(ev)
            bucket.bonus_total += ev.points
            flat_bonuses.append(ev)
        else:
            bucket.penalties.append(ev)
            bucket.penalty_total += ev.points  # negative
            flat_penalties.append(ev)

    # Per-pillar clamp: subtotal stays in [0, cap]. Bonuses cap-out at
    # the pillar cap; penalties cap-out at 0 (cannot drag negative).
    for pillar in pillars.values():
        raw = pillar.cap + pillar.bonus_total + pillar.penalty_total
        pillar.subtotal = max(0, min(pillar.cap, int(round(raw))))
        pillar.has_data = _pillar_has_data(pillar.name, signals)
        # When a pillar has no supporting data, surface 0 as the
        # subtotal — the API/UI uses ``has_data`` to render n/a so the
        # missing data isn't quietly worth full marks.
        if not pillar.has_data:
            pillar.subtotal = 0
            pillar.bonus_total = 0
            pillar.penalty_total = 0
            pillar.bonuses.clear()
            pillar.penalties.clear()
            # Strip the same events from the legacy flat_* lists too
            # so bonus_total/penalty_total surfaced via the legacy API
            # aliases stay consistent with the per-pillar zero. Without
            # this, a no-data pillar would zero out in pillars[...] but
            # still contribute non-zero values via bonus_total/penalty_total.
            flat_bonuses[:] = [b for b in flat_bonuses if b.pillar != pillar.name]
            flat_penalties[:] = [p for p in flat_penalties if p.pillar != pillar.name]

    total = sum(p.subtotal for p in pillars.values())
    drive_h = float(signals.get("drive_hours", 0) or 0)
    insufficient = drive_h < MIN_DRIVE_HOURS_FOR_RANKING

    return Scorecard(
        subject_id=subject_id,
        subject_name=subject_name,
        subject_type=subject_type,
        total=int(total),
        pillars=pillars,
        insufficient_data=insufficient,
        base=0,
        bonus_points=sum(e.points for e in flat_bonuses),
        penalty_points=sum(e.points for e in flat_penalties),
        bonuses=flat_bonuses,
        penalties=flat_penalties,
    )
