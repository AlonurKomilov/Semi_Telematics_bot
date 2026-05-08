import { useMemo, useState } from 'react';
import {
  TrendingUp, TrendingDown, Minus, AlertTriangle, Sparkles,
  Lightbulb, Shield, ShieldAlert, ShieldCheck, ChevronRight,
} from 'lucide-react';
import type { CompositeScorecard, ScoreEventBreakdown } from '../types';

// ── Risk band ─────────────────────────────────────────────────
//
// Maps the 0-100 score to LOW/MEDIUM/HIGH using the same thresholds
// as our scoreColor helper for visual consistency.

type RiskBand = 'low' | 'medium' | 'high';

function riskBand(score: number): RiskBand {
  if (score >= 70) return 'low';
  if (score >= 55) return 'medium';
  return 'high';
}

const RISK_META: Record<RiskBand, { label: string; cls: string; icon: typeof Shield }> = {
  low:    { label: 'LOW RISK',    cls: 'bg-green-500/15 text-green-700 dark:text-green-400',   icon: ShieldCheck },
  medium: { label: 'MEDIUM RISK', cls: 'bg-yellow-500/15 text-yellow-700 dark:text-yellow-400', icon: Shield },
  high:   { label: 'HIGH RISK',   cls: 'bg-red-500/15 text-red-700 dark:text-red-400',         icon: ShieldAlert },
};

// ── Trend direction ───────────────────────────────────────────

type TrendDir = 'rising' | 'falling' | 'steady';

function trendDirection(trend: number[] | undefined): { dir: TrendDir; delta: number } {
  if (!trend || trend.length < 4) return { dir: 'steady', delta: 0 };
  const recent = trend.slice(-3);
  const prior = trend.slice(-6, -3);
  if (prior.length === 0) return { dir: 'steady', delta: 0 };
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

interface PeriodCompare {
  window: number;
  last: number;
  prior: number;
  delta: number;
}

function periodCompare(trend: number[] | undefined, days: number): PeriodCompare {
  const t = trend ?? [];
  if (t.length < 2) {
    return { window: Math.max(1, Math.floor(days / 2)), last: 0, prior: 0, delta: 0 };
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
  };
}

// ── Top issue ─────────────────────────────────────────────────

function topIssue(penalties: ScoreEventBreakdown[]): ScoreEventBreakdown | null {
  if (!penalties || penalties.length === 0) return null;
  return [...penalties].sort((a, b) => Math.abs(b.points) - Math.abs(a.points))[0];
}

// ── AI insights ───────────────────────────────────────────────

function buildInsights(
  card: CompositeScorecard,
  band: RiskBand,
  td: { dir: TrendDir; delta: number },
  sev: { severe: number; moderate: number; minor: number },
  top: ScoreEventBreakdown | null,
  comp: PeriodCompare,
): { headline: string; bullets: string[]; coaching: string | null } {
  const bullets: string[] = [];
  let headline: string;

  if (band === 'high') {
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
      : `Driver's risk level is low with a stable trend.`;
  }

  if (sev.severe > 0) {
    bullets.push(`${sev.severe} severe event${sev.severe > 1 ? 's' : ''} recorded — review urgently.`);
  }
  if (top && top.occurrences > 1) {
    bullets.push(`${top.occurrences} ${top.label} event${top.occurrences > 1 ? 's' : ''} accounted for the largest score impact.`);
  } else if (top) {
    bullets.push(`Top issue: ${top.label} (${Math.abs(top.points)} pts).`);
  }
  if (comp.delta > 0) {
    bullets.push(`Last ${comp.window}d total ${comp.last} vs prior ${comp.window}d ${comp.prior} — gap of +${comp.delta} points needs attention.`);
  } else if (comp.delta < 0) {
    bullets.push(`Last ${comp.window}d total ${comp.last} vs prior ${comp.window}d ${comp.prior} — improving by ${Math.abs(comp.delta)} points.`);
  }
  if (card.insufficient_data) {
    bullets.push(`Insufficient drive time this window — score is provisional.`);
  }
  if (bullets.length === 0) {
    bullets.push('No notable patterns this window.');
  }

  let coaching: string | null = null;
  if (top) {
    const t = top.label.toLowerCase();
    if (t.includes('following')) {
      coaching = 'Coach on following distance — keep at least one truck-length per 10 mph.';
    } else if (t.includes('brake') || t.includes('braking')) {
      coaching = 'Discuss anticipatory braking and look-ahead scanning.';
    } else if (t.includes('speed')) {
      coaching = 'Review speed-limit awareness and cruise control use.';
    } else if (t.includes('lane')) {
      coaching = 'Coach on lane discipline and turn-signal use.';
    } else if (t.includes('parking')) {
      coaching = 'Discuss safe-parking selection and pre-trip planning for stops.';
    } else if (t.includes('seatbelt')) {
      coaching = 'Reinforce seatbelt policy — zero tolerance.';
    } else if (t.includes('mobile') || t.includes('phone') || t.includes('distract')) {
      coaching = 'Coach on phone-down policy and the cost of distractions.';
    } else if (t.includes('crash') || t.includes('collision')) {
      coaching = 'Schedule a 1:1 incident review and re-test on collision-avoidance.';
    } else if (t.includes('idle') || t.includes('eco') || t.includes('coast') || t.includes('cruise')) {
      coaching = 'Review fuel-economy habits — idling, coasting, and cruise discipline.';
    } else {
      coaching = `Discuss ${top.label} habits in this week's one-on-one session.`;
    }
  } else if (band !== 'low') {
    coaching = 'Schedule a refresher session and review the weekly scorecard together.';
  }

  return { headline, bullets, coaching };
}

// ── Event categories grouped by pillar ────────────────────────
//
// The competitor flat-lists 13 categories. Ours groups them by the
// same Safety / Compliance / Efficiency pillars our composite score
// already uses, so each pillar in the matrix mirrors the gauge above.

type PillarKey = 'safety' | 'compliance' | 'efficiency';

interface CategoryDef {
  key: string;       // substring matched against event label
  label: string;
  pillar: PillarKey;
}

const KNOWN_CATEGORIES: CategoryDef[] = [
  // Safety ─ harsh / collision / distraction events
  { key: 'crash',         label: 'Crash',                     pillar: 'safety' },
  { key: 'collision',     label: 'Forward Collision Warning', pillar: 'safety' },
  { key: 'following',     label: 'Following Distance',        pillar: 'safety' },
  { key: 'brake',         label: 'Harsh Brake',               pillar: 'safety' },
  { key: 'turn',          label: 'Harsh Turn',                pillar: 'safety' },
  { key: 'lane',          label: 'Lane Departure',            pillar: 'safety' },
  { key: 'speed',         label: 'Speeding',                  pillar: 'safety' },
  { key: 'mobile',        label: 'Mobile Usage',              pillar: 'safety' },
  { key: 'distract',      label: 'Inattentive Driving',       pillar: 'safety' },
  { key: 'drowsy',        label: 'Drowsy',                    pillar: 'safety' },

  // Compliance ─ rules-of-the-road & policy
  { key: 'rolling',       label: 'Rolling Stop',              pillar: 'compliance' },
  { key: 'seatbelt',      label: 'No Seatbelt',               pillar: 'compliance' },
  { key: 'parking',       label: 'Unsafe Parking',            pillar: 'compliance' },

  // Efficiency ─ fuel & driving economy
  { key: 'idle',          label: 'Excessive Idling',          pillar: 'efficiency' },
  { key: 'coast',         label: 'Insufficient Coast',        pillar: 'efficiency' },
  { key: 'cruise',        label: 'Insufficient Cruise',       pillar: 'efficiency' },
  { key: 'anticipat',     label: 'Anticipatory Braking',      pillar: 'efficiency' },
  { key: 'eco',           label: 'Low Eco %',                 pillar: 'efficiency' },
];

const PILLAR_META: Record<PillarKey, { label: string; cls: string; bar: string }> = {
  safety:     { label: 'Safety',     cls: 'text-red-600 dark:text-red-400',     bar: 'bg-red-500' },
  compliance: { label: 'Compliance', cls: 'text-blue-600 dark:text-blue-400',   bar: 'bg-blue-500' },
  efficiency: { label: 'Efficiency', cls: 'text-green-600 dark:text-green-400', bar: 'bg-green-500' },
};

function matchCategory(label: string, key: string): boolean {
  return label.toLowerCase().includes(key);
}

// Score-bar fill colour graded by attainment % (subtotal ÷ cap).
// Mirrors the same green→amber→red ramp the gauge uses so the row
// reads consistently with the score gauge above.
function pillarFillColor(pct: number): string {
  if (pct >= 85) return 'bg-green-500';
  if (pct >= 70) return 'bg-lime-500';
  if (pct >= 55) return 'bg-yellow-500';
  if (pct >= 40) return 'bg-orange-500';
  return 'bg-red-500';
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

  // Build per-pillar groups with counts + max for bar scaling.
  const grouped = useMemo(() => {
    return (['safety', 'compliance', 'efficiency'] as const).map((pillar) => {
      const cats = KNOWN_CATEGORIES.filter((c) => c.pillar === pillar).map((c) => {
        const events = (card.penalties || []).filter((e) => matchCategory(e.label, c.key));
        const count = events.reduce((n, e) => n + (e.occurrences || 1), 0);
        return { ...c, count };
      });
      const total = cats.reduce((n, c) => n + c.count, 0);
      const max = Math.max(1, ...cats.map((c) => c.count));
      return { pillar, cats, total, max };
    });
  }, [card.penalties]);

  const matrixTotal = grouped.reduce((n, g) => n + g.total, 0);

  // Default-expand the pillar with the most events; the others stay
  // collapsed so the drawer reads compact.
  const defaultExpanded = useMemo(() => {
    const top = [...grouped].sort((a, b) => b.total - a.total)[0];
    return top?.total ? top.pillar : 'safety';
  }, [grouped]);
  const [expanded, setExpanded] = useState<Record<PillarKey, boolean>>({
    safety: defaultExpanded === 'safety',
    compliance: defaultExpanded === 'compliance',
    efficiency: defaultExpanded === 'efficiency',
  });

  const RiskIcon = RISK_META[band].icon;
  const TrendIcon = trend.dir === 'rising' ? TrendingUp : trend.dir === 'falling' ? TrendingDown : Minus;
  const trendCls =
    trend.dir === 'rising' ? 'text-amber-600 dark:text-amber-400'
    : trend.dir === 'falling' ? 'text-green-600 dark:text-green-400'
    : 'text-muted-foreground';
  const trendWord =
    trend.dir === 'rising' ? 'Rising'
    : trend.dir === 'falling' ? 'Falling'
    : 'Steady';

  return (
    <div className="space-y-3 mb-5">
      {/* Risk band + trend on one tight row */}
      <div className="flex items-center gap-2 flex-wrap">
        <span
          className={`inline-flex items-center gap-1.5 px-2 py-0.5 rounded-full text-[11px] font-bold tracking-wide ${RISK_META[band].cls}`}
          title={`Score ${card.score}`}
        >
          <RiskIcon size={11} />
          {RISK_META[band].label}
        </span>
        <span className="text-muted-foreground text-[11px]">—</span>
        <span className={`inline-flex items-center gap-1 text-[11px] font-semibold ${trendCls}`}>
          <TrendIcon size={11} />
          {trendWord}{trend.delta !== 0 && ` (${trend.delta > 0 ? '+' : ''}${trend.delta})`}
        </span>
      </div>

      {/* Severity buckets — single tight row */}
      <div className="grid grid-cols-3 gap-1.5">
        <SeverityCell label="Severe" value={sev.severe} tone="critical" />
        <SeverityCell label="Moderate" value={sev.moderate} tone="warning" />
        <SeverityCell label="Minor" value={sev.minor} tone="muted" />
      </div>

      {/* Period comparison — inline with delta arrow */}
      <div className="grid grid-cols-2 gap-1.5">
        <PeriodCell label={`Last ${comp.window}d`} value={comp.last} delta={comp.delta} showDelta />
        <PeriodCell label={`Prior ${comp.window}d`} value={comp.prior} />
      </div>

      {/* Top Issue */}
      {top && (
        <div className="bg-muted/30 border border-border rounded-md px-2.5 py-1.5">
          <div className="flex items-baseline gap-2">
            <p className="text-[10px] uppercase tracking-wide text-muted-foreground font-semibold shrink-0">
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

      {/* AI insights */}
      <div className="bg-gradient-to-br from-primary/5 via-card to-card border border-primary/20 rounded-lg p-2.5">
        <div className="flex items-center gap-1.5 mb-1.5">
          <Sparkles size={13} className="text-primary" />
          <p className="text-xs font-semibold text-foreground">AI Insights</p>
        </div>
        <p className="text-[13px] text-foreground/90 mb-1.5">{insights.headline}</p>
        <ul className="space-y-0.5">
          {insights.bullets.map((b, i) => (
            <li key={i} className="text-xs text-foreground/80 flex items-start gap-1.5">
              <span className="text-primary/60 mt-0.5">·</span>
              <span>{b}</span>
            </li>
          ))}
        </ul>

        {insights.coaching && (
          <div className="mt-2 bg-background/60 border border-primary/30 rounded-md px-2 py-1.5">
            <div className="flex items-center gap-1.5 mb-0.5">
              <Lightbulb size={11} className="text-primary" />
              <p className="text-[10px] uppercase tracking-wide text-primary font-bold">
                Coaching Action
              </p>
            </div>
            <p className="text-xs text-foreground/90 leading-snug">{insights.coaching}</p>
          </div>
        )}
      </div>

      {/* Pillar breakdown — score subtotal AND event matrix in one card.
           Each pillar header shows: name · score X/Y · attainment bar ·
           event count · chevron. Expanded body lists the categories.
           Replaces the old standalone "Pillar breakdown" progress bars
           in the parent drawer so the same data isn't duplicated. */}
      <div className="bg-card border border-border rounded-lg overflow-hidden">
        <div className="flex items-center justify-between px-3 py-2 border-b border-border">
          <p className="text-[10px] uppercase tracking-wide text-muted-foreground font-semibold">
            Pillars · Events by category
          </p>
          <p className="text-[10px] text-muted-foreground tabular-nums">
            {matrixTotal} event{matrixTotal === 1 ? '' : 's'}
          </p>
        </div>
        <div className="divide-y divide-border">
          {grouped.map(({ pillar, cats, total, max }) => {
            const meta = PILLAR_META[pillar];
            const isOpen = expanded[pillar];
            const p = card.pillars?.[pillar];
            const hasScore = !!p?.has_data;
            const subtotal = hasScore ? p!.subtotal : 0;
            const cap = hasScore ? p!.cap : 0;
            const pct = hasScore && cap ? Math.round((subtotal / cap) * 100) : 0;
            const fill = pillarFillColor(pct);

            return (
              <div key={pillar}>
                <button
                  onClick={() =>
                    setExpanded((prev) => ({ ...prev, [pillar]: !prev[pillar] }))
                  }
                  className="w-full px-3 py-2 text-left hover:bg-muted/40 transition"
                  aria-expanded={isOpen}
                >
                  {/* Top row: chevron · pillar name · score X/Y · event count */}
                  <div className="flex items-center justify-between gap-2">
                    <span className="flex items-center gap-1.5 min-w-0">
                      <ChevronRight
                        size={12}
                        className={`text-muted-foreground transition-transform shrink-0 ${isOpen ? 'rotate-90' : ''}`}
                      />
                      <span className={`text-xs font-semibold ${meta.cls}`}>{meta.label}</span>
                      {hasScore ? (
                        <span className="text-[10px] tabular-nums text-muted-foreground">
                          {subtotal}/{cap}
                        </span>
                      ) : (
                        <span className="text-[10px] italic text-muted-foreground">n/a</span>
                      )}
                    </span>
                    <span className="flex items-center gap-1 shrink-0">
                      <span
                        className={`tabular-nums text-xs font-semibold ${total > 0 ? 'text-foreground' : 'text-muted-foreground/60'}`}
                      >
                        {total}
                      </span>
                      <span className="text-[10px] text-muted-foreground">event{total === 1 ? '' : 's'}</span>
                    </span>
                  </div>
                  {/* Score attainment bar — same green→red ramp as the gauge */}
                  {hasScore && (
                    <div className="h-1 bg-muted rounded-full overflow-hidden mt-1.5">
                      <div
                        className={`h-full rounded-full transition-all ${fill}`}
                        style={{ width: `${pct}%` }}
                      />
                    </div>
                  )}
                </button>
                {isOpen && (
                  <ul className="px-3 pb-2 pt-0.5 space-y-1">
                    {cats.map((c) => {
                      const catPct = (c.count / max) * 100;
                      const dim = c.count === 0;
                      return (
                        <li key={c.key} className="flex items-center gap-2 text-[11px]">
                          <span
                            className={`flex-1 truncate ${dim ? 'text-muted-foreground/60' : 'text-foreground'}`}
                          >
                            {c.label}
                          </span>
                          <div className="flex-1 h-1 bg-muted rounded-full overflow-hidden max-w-[60px]">
                            {c.count > 0 && (
                              <div
                                className={`h-full rounded-full ${meta.bar}`}
                                style={{ width: `${catPct}%` }}
                              />
                            )}
                          </div>
                          <span
                            className={`tabular-nums w-5 text-right ${dim ? 'text-muted-foreground/60' : 'text-foreground'}`}
                          >
                            {c.count}
                          </span>
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
        <div className="flex items-start gap-2 text-[11px] text-amber-700 dark:text-amber-400 bg-amber-500/10 border border-amber-500/30 rounded-lg px-2.5 py-1.5">
          <AlertTriangle size={12} className="mt-0.5 shrink-0" />
          <span>Insufficient drive time this window — excluded from rankings.</span>
        </div>
      )}
    </div>
  );
}

// ── Cells ─────────────────────────────────────────────────────

function SeverityCell({
  label, value, tone,
}: {
  label: string;
  value: number;
  tone: 'critical' | 'warning' | 'muted';
}) {
  const cls =
    tone === 'critical' ? 'text-destructive'
    : tone === 'warning' ? 'text-yellow-700 dark:text-yellow-400'
    : 'text-muted-foreground';
  return (
    <div className="bg-card border border-border rounded-md px-2 py-1.5 text-center">
      <p className={`text-lg font-bold tabular-nums leading-none ${cls}`}>{value}</p>
      <p className="text-[10px] uppercase tracking-wide text-muted-foreground mt-0.5">{label}</p>
    </div>
  );
}

function PeriodCell({
  label, value, delta, showDelta,
}: {
  label: string;
  value: number;
  delta?: number;
  showDelta?: boolean;
}) {
  const deltaCls =
    !delta ? 'text-muted-foreground'
    : delta > 0 ? 'text-amber-600 dark:text-amber-400'
    : 'text-green-600 dark:text-green-400';
  return (
    <div className="bg-card border border-border rounded-md px-2 py-1.5 text-center">
      <div className="flex items-baseline justify-center gap-1.5">
        <p className="text-base font-semibold tabular-nums text-foreground leading-none">{value}</p>
        {showDelta && delta !== undefined && delta !== 0 && (
          <span className={`text-[10px] font-bold tabular-nums ${deltaCls}`}>
            {delta > 0 ? '+' : ''}{delta}
          </span>
        )}
      </div>
      <p className="text-[10px] uppercase tracking-wide text-muted-foreground mt-0.5">{label}</p>
    </div>
  );
}
