import { useMemo } from 'react';
import { AlertTriangle, Sparkles, Lightbulb } from 'lucide-react';
import { toneClasses, toneText, type Tone } from '../lib/status';
import type { CompositeScorecard, ScoreEventBreakdown } from '../types';

// ── Risk band ─────────────────────────────────────────────────
//
// Maps the 0-100 score to LOW/MEDIUM/HIGH using the same thresholds
// as our scoreColor helper.  The chip that used to surface this
// alongside a "rising/falling" trend pill was removed (it duplicated
// what the gauge + AI Insights already show), but the band still
// drives the AI Insights headline copy so the function stays.

type RiskBand = 'low' | 'medium' | 'high';

function riskBand(score: number): RiskBand {
  if (score >= 70) return 'low';
  if (score >= 55) return 'medium';
  return 'high';
}

// ── Trend direction ───────────────────────────────────────────
//
// 'unknown' covers brand-new drivers and any account whose nightly
// snapshots haven't accrued the 4-point minimum yet.  Surfacing
// 'steady' there was misleading — a driver with no history isn't
// actually stable, we just can't tell.

type TrendDir = 'rising' | 'falling' | 'steady' | 'unknown';

function trendDirection(trend: number[] | undefined): { dir: TrendDir; delta: number } {
  if (!trend || trend.length < 4) return { dir: 'unknown', delta: 0 };
  const recent = trend.slice(-3);
  const prior = trend.slice(-6, -3);
  if (prior.length === 0) return { dir: 'unknown', delta: 0 };
  const avg = (xs: number[]) => xs.reduce((a, b) => a + b, 0) / xs.length;
  const delta = Math.round(avg(recent) - avg(prior));
  if (delta >= 3) return { dir: 'rising', delta };
  if (delta <= -3) return { dir: 'falling', delta };
  return { dir: 'steady', delta };
}

// ── Severity bucketing ────────────────────────────────────────
//
// Heuristic from |points| × occurrences until the backend ships an
// explicit severity field on each event.

function bucketSeverity(events: ScoreEventBreakdown[]): { severe: number; moderate: number; minor: number } {
  let severe = 0, moderate = 0, minor = 0;
  for (const e of events) {
    const mag = Math.abs(e.points);
    const count = e.occurrences || 1;
    if (mag >= 8) severe += count;
    else if (mag >= 3) moderate += count;
    else if (mag >= 1) minor += count;
  }
  return { severe, moderate, minor };
}

// ── Period comparison (last half vs prior half) ───────────────
//
// ``hasData`` is false when the trend array is too thin to split into
// two halves — we render "—" in that state instead of showing zeros
// that look like real "zero events" data.

interface PeriodCompare {
  window: number;
  last: number;
  prior: number;
  delta: number;
  hasData: boolean;
}

function periodCompare(trend: number[] | undefined, days: number): PeriodCompare {
  const t = trend ?? [];
  if (t.length < 2) {
    return { window: Math.max(1, Math.floor(days / 2)), last: 0, prior: 0, delta: 0, hasData: false };
  }
  const half = Math.max(1, Math.min(Math.floor(days / 2), Math.floor(t.length / 2)));
  const last = t.slice(-half);
  const prior = t.slice(-half * 2, -half);
  const sum = (xs: number[]) => xs.reduce((a, b) => a + b, 0);
  return {
    window: half,
    last: Math.round(sum(last)),
    prior: Math.round(sum(prior)),
    delta: Math.round(sum(last)) - Math.round(sum(prior)),
    hasData: true,
  };
}

// ── Top issue ─────────────────────────────────────────────────

function topIssue(penalties: ScoreEventBreakdown[]): ScoreEventBreakdown | null {
  if (!penalties || penalties.length === 0) return null;
  return [...penalties].sort((a, b) => Math.abs(b.points) - Math.abs(a.points))[0];
}

// ── AI insights ───────────────────────────────────────────────

// Rule-id-keyed coaching map — replaces the previous label-substring
// heuristic which generated wrong copy when the top issue was a
// hardware problem (e.g. obstructed camera) or label punctuation
// changed.  Anything not in this map and not in NON_BEHAVIOR_RULE_IDS
// falls back to a generic message.  Hardware issues route to a
// fleet-side action instead of a driver one-on-one.
const COACHING_BY_RULE: Record<string, string> = {
  'safety.crash':              'Schedule a 1:1 incident review and re-test on collision-avoidance.',
  'safety.near_collision':     'Coach on following distance and look-ahead scanning — keep at least one truck-length per 10 mph.',
  'safety.harsh_brake':        'Discuss anticipatory braking and look-ahead scanning.',
  'safety.harsh_turn':         'Review cornering technique and approach speed.',
  'safety.overspeed_rate':     'Review speed-limit awareness and cruise-control use.',
  'safety.distracted_driving': 'Coach on phone-down policy and the cost of distractions.',
  'safety.mobile_usage':       'Reinforce phone-down policy — zero tolerance while in motion.',
  'safety.forward_collision':  'Coach on following distance and forward awareness.',
  'safety.rolling_stop':       'Reinforce full-stop policy at signs and lights.',
  'eff.green_band_low':        'Review fuel-economy habits — idling, coasting, and cruise discipline.',
  'eff.idle_pct_high':         'Review idle-reduction policy and PTO usage.',
};

const HARDWARE_ACTION_BY_RULE: Record<string, string> = {
  'compliance.camera_obstructed':   'Fleet action: inspect and re-align the dashcam mount; clean the lens.',
  'compliance.active_dtc':          'Fleet action: pull the truck for diagnostics — driver did not cause this.',
  'compliance.health_critical':     'Fleet action: schedule immediate service for the affected truck.',
  'compliance.health_minor':        'Fleet action: top up DEF / address the minor alert on the assigned truck.',
  'compliance.maintenance_overdue': 'Ops action: schedule the overdue maintenance task.',
  'compliance.pti_overdue':         'Ops action: enforce pre-trip inspection completion on this driver\'s shifts.',
  'efficiency.fuel_anomaly':        'Fleet/finance action: review the flagged fuel entries for theft or fuelling error.',
};

function buildInsights(
  card: CompositeScorecard,
  band: RiskBand,
  td: { dir: TrendDir; delta: number },
  sev: { severe: number; moderate: number; minor: number },
  top: ScoreEventBreakdown | null,
  comp: PeriodCompare,
): { headline: string | null; bullets: string[]; coaching: string | null } {
  const bullets: string[] = [];

  // ── Headline ─────────────────────────────────────────────────
  // For the all-clear case (LOW + steady + no severe events) the
  // headline just restates the chips above it.  Suppress to reduce
 // visual duplication.
  let headline: string | null;
  if (band === 'low' && td.dir === 'steady' && sev.severe === 0) {
    headline = null;
  } else if (band === 'high') {
    headline = `Driver's risk level is high${td.dir === 'rising' ? ' and trending worse' : ''}.`;
  } else if (band === 'medium') {
    headline = td.dir === 'rising'
      ? `Driver's risk level is moderate and rising.`
      : `Driver's risk level is moderate but trending ${td.dir === 'falling' ? 'better' : 'flat'}.`;
  } else {
    headline = td.dir === 'rising'
      ? `Driver's risk level is low, but recent trend shows an increase in events.`
      : td.dir === 'falling'
      ? `Driver's risk level is low and improving.`
      : null;  // unknown trend on a low-risk driver: nothing meaningful to say
  }

  // ── Bullets ──────────────────────────────────────────────────
  if (sev.severe > 0) {
    bullets.push(`${sev.severe} severe event${sev.severe > 1 ? 's' : ''} recorded — review urgently.`);
  }
 // drop the duplicate "Top issue: ..." bullet. The
  // standalone TOP ISSUE card above already surfaces the same fact.
  // Keep the bullet only when occurrence count adds context the
  // standalone card lacks (single-row card only shows ``1×``).
  if (top && top.occurrences > 1) {
    bullets.push(`${top.occurrences} ${top.label} event${top.occurrences > 1 ? 's' : ''} accounted for the largest score impact.`);
  }
  if (comp.hasData && comp.delta > 0) {
    bullets.push(`Last ${comp.window}d total ${comp.last} vs prior ${comp.window}d ${comp.prior} — gap of +${comp.delta} points needs attention.`);
  } else if (comp.hasData && comp.delta < 0) {
    bullets.push(`Last ${comp.window}d total ${comp.last} vs prior ${comp.window}d ${comp.prior} — improving by ${Math.abs(comp.delta)} points.`);
  }
  if (card.insufficient_data) {
    bullets.push(`Insufficient drive time this window — score is provisional.`);
  }

  // ── Coaching ─────────────────────────────────────────────────
  let coaching: string | null = null;
  if (top) {
    if (top.rule_id && HARDWARE_ACTION_BY_RULE[top.rule_id]) {
      coaching = HARDWARE_ACTION_BY_RULE[top.rule_id];
    } else if (top.rule_id && COACHING_BY_RULE[top.rule_id]) {
      coaching = COACHING_BY_RULE[top.rule_id];
    } else if (top.rule_id && NON_BEHAVIOR_RULE_IDS.has(top.rule_id)) {
      coaching = 'Fleet/ops action — not a driver-behavior issue, route to the appropriate team.';
    } else {
      // Genuine driving habit we don't have specific copy for —
      // generic but at least pointing at "behavior" not "habit"
      // since the latter overpromises insight into the driver's
      // intent.
      coaching = `Review ${top.label.toLowerCase()} events in this week's one-on-one session.`;
    }
  } else if (band !== 'low') {
    coaching = 'Schedule a refresher session and review the weekly scorecard together.';
  }

  return { headline, bullets, coaching };
}

// ── Pillar grouping ───────────────────────────────────────────
//
// Events (bonuses + penalties) are grouped by the backend ``pillar``
// field on each event.  The previous design pre-rendered every
// possible category as a zero-count row — on a clean driver that
// meant ~18 empty rows of visual noise.  The merged view shows only
// events that actually fired, plus a per-pillar subtotal so users
// still see which pillar earned / lost the points.

type PillarKey = 'safety' | 'compliance' | 'efficiency';

// Pillar identity colours — a fixed per-pillar category palette (Safety /
// Compliance / Efficiency), NOT a value-driven status signal, so they stay
// on the categorical palette rather than the ok/warn/danger tones.
const PILLAR_META: Record<PillarKey, { label: string; cls: string }> = {
  safety:     { label: 'Safety',     cls: 'text-red-600 dark:text-red-400' },
  compliance: { label: 'Compliance', cls: 'text-blue-600 dark:text-blue-400' },
  efficiency: { label: 'Efficiency', cls: 'text-green-600 dark:text-green-400' },
};

// Hardware / vehicle-state rules that aren't a driver-behavior habit
// — we want them in the matrix and the penalty list (they affect
// score) but the auto-generated Coaching Action shouldn't tell a
// manager to "discuss [hardware] habits."  See A6 in the audit.
const NON_BEHAVIOR_RULE_IDS = new Set<string>([
  'compliance.camera_obstructed',
  'compliance.active_dtc',
  'compliance.health_critical',
  'compliance.health_minor',
  'compliance.maintenance_overdue',
  'compliance.pti_overdue',
  'efficiency.fuel_anomaly',
]);

// Score-bar fill tone graded by attainment % (subtotal ÷ cap).
// Mirrors the same good→attention→bad ramp the gauge uses so the row
// reads consistently with the score gauge above: ok ≥ 70, warn ≥ 40,
// danger below.
function scoreTone(pct: number): Tone {
  if (pct >= 70) return 'ok';
  if (pct >= 40) return 'warn';
  return 'danger';
}

// ── Component ─────────────────────────────────────────────────

export interface DriverInsightsProps {
  card: CompositeScorecard;
  /** Page-level window so the period-compare cells use the same range
      the user picked at the top of the scorecards page. */
  days: number;
}

export default function DriverInsights({ card, days }: DriverInsightsProps) {
  const band = riskBand(card.score);
  const trend = trendDirection(card.score_trend);
  const sev = bucketSeverity(card.penalties || []);
  const comp = periodCompare(card.score_trend, days);
  const top = topIssue(card.penalties || []);
  const insights = useMemo(
    () => buildInsights(card, band, trend, sev, top, comp),
    [card, band, trend, sev, top, comp],
  );

  // Group bonuses + penalties together by backend ``pillar``.  Empty
  // pillars render a "no events — full credit" line rather than
  // 18 zero-count rows.  Events with no pillar field fall back to
  // 'compliance' (covers legacy snapshots predating the field).
  const pillarBlocks = useMemo(() => {
    const all: ScoreEventBreakdown[] = [
      ...(card.penalties || []),
      ...(card.bonuses || []),
    ];
    return (['safety', 'compliance', 'efficiency'] as const).map((pk) => {
      const events = all.filter((e) => {
        const p = (e.pillar as PillarKey | undefined) || 'compliance';
        return p === pk;
      });
      // Penalties first (worst impact first), then bonuses descending
      // by points — keeps "what hurt me" above "what helped me."
      events.sort((a, b) => {
        const aPen = a.kind === 'penalty' ? 0 : 1;
        const bPen = b.kind === 'penalty' ? 0 : 1;
        if (aPen !== bPen) return aPen - bPen;
        return Math.abs(b.points) - Math.abs(a.points);
      });
      return { pillar: pk, events };
    });
  }, [card.bonuses, card.penalties]);

  const totalEvents = pillarBlocks.reduce((n, p) => n + p.events.length, 0);

  // Risk band, trend direction, severity buckets and period
  // comparison are computed (and used by buildInsights for AI bullets)
  // but no longer rendered as standalone chips/cells above the
  // Top-Issue / AI-Insights / Pillar-matrix stack — feedback was that
  // the chip strip duplicated info that is already in AI Insights and
  // the gauge below, taking screen real estate without adding signal.

  return (
    <div className="space-y-3 mb-5">
      {/* Top Issue */}
      {top && (
        <div className="bg-muted/30 border border-border rounded-md px-2.5 py-1.5">
          <div className="flex items-baseline gap-2">
            <p className="text-3xs uppercase tracking-wide text-muted-foreground font-semibold shrink-0">
              Top Issue
            </p>
            <p className="text-sm font-medium text-foreground truncate">
              {top.label}
              <span className="text-xs text-muted-foreground ml-1.5">
                ({top.occurrences}× · {Math.abs(top.points)} pts)
              </span>
            </p>
          </div>
        </div>
      )}

      {/* AI insights — suppressed entirely when there's nothing
           meaningful to add over the chips/cards above (low-risk
           steady driver with no severe events, no period delta, and
           no top issue).  See . */}
      {(insights.headline || insights.bullets.length > 0 || insights.coaching) && (
        <div className="bg-gradient-to-br from-primary/5 via-card to-card border border-primary/20 rounded-lg p-2.5">
          <div className="flex items-center gap-1.5 mb-1.5">
            <Sparkles size={14} className="text-primary" />
            <p className="text-xs font-semibold text-foreground">AI Insights</p>
          </div>
          {insights.headline && (
            <p className="text-sm text-foreground/90 mb-1.5">{insights.headline}</p>
          )}
          {insights.bullets.length > 0 && (
            <ul className="space-y-0.5">
              {insights.bullets.map((b, i) => (
                <li key={i} className="text-xs text-foreground/80 flex items-start gap-1.5">
                  <span className="text-primary/60 mt-0.5">·</span>
                  <span>{b}</span>
                </li>
              ))}
            </ul>
          )}

          {insights.coaching && (
            <div className="mt-2 bg-background/60 border border-primary/30 rounded-md px-2 py-1.5">
              <div className="flex items-center gap-1.5 mb-0.5">
                <Lightbulb size={12} className="text-primary" />
                <p className="text-3xs uppercase tracking-wide text-primary font-bold">
                  {top && top.rule_id && HARDWARE_ACTION_BY_RULE[top.rule_id]
                    ? 'Fleet / Ops Action'
                    : 'Coaching Action'}
                </p>
              </div>
              <p className="text-xs text-foreground/90 leading-snug">{insights.coaching}</p>
            </div>
          )}
        </div>
      )}

      {/* Pillars · Your Events — merged pillar header + signed-points
           event list under each pillar.  Replaces the old penalty-only
           matrix-with-zero-rows AND the standalone BONUSES/PENALTIES
           section in the drawer below.  One source of truth per
           event: no more "Camera obstructed shown twice on the same
           screen" duplication.  Hardware/ops events get an inline
           "↳ Fleet action: …" note so the manager knows it's not a
           coaching matter. */}
      <div className="bg-card border border-border rounded-lg overflow-hidden">
        <div className="flex items-center justify-between px-3 py-2 border-b border-border">
          <p className="text-3xs uppercase tracking-wide text-muted-foreground font-semibold">
            Pillars · Your Events
          </p>
          <p className="text-3xs text-muted-foreground tabular-nums">
            {totalEvents} event{totalEvents === 1 ? '' : 's'}
          </p>
        </div>
        <div className="divide-y divide-border">
          {pillarBlocks.map(({ pillar, events }) => {
            const meta = PILLAR_META[pillar];
            const p = card.pillars?.[pillar];
            const hasScore = !!p?.has_data;
            const subtotal = hasScore ? p!.subtotal : 0;
            const cap = hasScore ? p!.cap : 0;
            const pct = hasScore && cap ? Math.round((subtotal / cap) * 100) : 0;
            // Solid bg token for the fill bar: bg-ok / bg-warn / bg-danger.
            const fill = `bg-${scoreTone(pct)}`;
            return (
              <div key={pillar} className="px-3 py-2">
                <div className="flex items-center justify-between gap-2">
                  <span className="flex items-center gap-1.5 min-w-0">
                    <span className={`text-xs font-semibold ${meta.cls}`}>{meta.label}</span>
                    {hasScore ? (
                      <span
                        className="text-3xs tabular-nums text-muted-foreground"
                        title={`${subtotal} of ${cap} points earned in this pillar`}
                      >
                        {subtotal}/{cap} pts
                      </span>
                    ) : (
                      <span className="text-3xs italic text-muted-foreground">n/a</span>
                    )}
                  </span>
                  {events.length > 0 && (
                    <span className="text-3xs text-muted-foreground tabular-nums">
                      {events.length} event{events.length === 1 ? '' : 's'}
                    </span>
                  )}
                </div>
                {hasScore && (
                  <div className="h-1 bg-muted rounded-full overflow-hidden mt-1.5 mb-1.5">
                    <div
                      className={`h-full rounded-full transition-all ${fill}`}
                      style={{ width: `${pct}%` }}
                    />
                  </div>
                )}
                {events.length === 0 ? (
                  <p className="text-2xs italic text-muted-foreground">
                    No events this window — full credit.
                  </p>
                ) : (
                  <ul className="space-y-1 mt-1">
                    {events.map((e) => {
                      const isBonus = e.kind === 'bonus';
                      const pointsColor = toneText(isBonus ? 'ok' : 'danger');
                      const hardwareAction = e.rule_id ? HARDWARE_ACTION_BY_RULE[e.rule_id] : undefined;
                      return (
                        <li key={`${e.rule_id || e.label}-${e.kind}`} className="text-2xs">
                          <div className="flex items-baseline gap-2">
                            <span className={`shrink-0 ${pointsColor} text-3xs`} aria-hidden>
                              {isBonus ? '✓' : '✗'}
                            </span>
                            <span className="flex-1 truncate text-foreground">
                              {e.label}
                              {e.occurrences > 1 && (
                                <span className="text-muted-foreground ml-1">({e.occurrences}×)</span>
                              )}
                            </span>
                            <span
                              className={`tabular-nums font-semibold shrink-0 ${pointsColor}`}
                            >
                              {e.points > 0 ? '+' : ''}{e.points}
                            </span>
                          </div>
                          {hardwareAction && (
                            <p className="text-3xs text-muted-foreground italic mt-0.5 pl-4 leading-snug">
                              ↳ {hardwareAction}
                            </p>
                          )}
                        </li>
                      );
                    })}
                  </ul>
                )}
              </div>
            );
          })}
        </div>
      </div>

      {card.insufficient_data && (
        <div className={`flex items-start gap-2 text-2xs border rounded-lg px-2.5 py-1.5 ${toneClasses('warn')}`}>
          <AlertTriangle size={12} className="mt-0.5 shrink-0" />
          <span>Insufficient drive time this window — excluded from rankings.</span>
        </div>
      )}
    </div>
  );
}

