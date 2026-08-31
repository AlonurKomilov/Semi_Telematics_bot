import { useEffect, useMemo, useState } from 'react';
import { useTranslation } from 'react-i18next';
import { useNavigate } from 'react-router-dom';
import { useQuery } from '@tanstack/react-query';
import { AlertTriangle, Download, Trophy, X } from 'lucide-react';
import {
  BarChart, Bar, XAxis, YAxis, Tooltip, ResponsiveContainer, Cell,
  LineChart, Line, CartesianGrid, ReferenceLine,
} from 'recharts';
import { apiJSON, apiJSONSlow } from '../../api/client';
import { toneClasses, toneText, chartColor, type Tone } from '../../lib/status';
import DataGrid from '../../components/datagrid';
import { Sheet, SheetContent, SheetBody } from '../../components/ui/sheet';
import DriverInsights from '@/features/scorecards/DriverInsights';
import {
  PageHeader,
  EmptyState,
  ErrorState,
  TableSkeleton,
  DateRangePresets,
  useLoadingStage,
} from '../../components/shell';
import { useShellConfig } from '../../hooks/useShellConfig';
import { useViewPermissions } from '../../hooks/useViewPermissions';
import { useTimezone } from '../../hooks/useTimezone';
import { formatDate } from '../../utils/datetime';
import { blocksForPersona } from '../../features/scorecards/personaConfig';
import type {
  CompositeScorecard,
  CompositeScorecardsResponse,
  ScoreHistoryResponse,
  ScoreEventBreakdown,
  ScoreExplanationResponse,
  AnyColumn,
} from '../../types';
import { FeatureConfigGear } from '../_lib/FeatureConfigGear';
import { Card } from '@/components/ui/card';
import { Tip } from '../../components/tooltip';
import { Badge } from '@/components/ui/badge';

import { CHART_FONT_2XS, CHART_FONT_MD, CHART_FONT_SM, CHART_FONT_XS } from "@/lib/chartText";
import { useRadiusPx } from '@/lib/radius';
import { RoundedBar } from '@/components/charts/RoundedBar';
// ── Color helpers ───────────────────────────────────────────────────

// Single source of truth for score → bucket.  Thresholds are
// unchanged from the legacy A-F mapping (85/70/55/40) — only the
// label changed to metallic tiers (Platinum/Gold/Silver/Bronze/Red).
// Keeping the thresholds means no driver moves bucket on rollout.
// ``tierKey`` is an i18n key suffix; UI components render via
// ``t('tier.' + tierKey)``.
const SCORE_BUCKETS = [
  { min: 85, tierKey: 'platinum' },
  { min: 70, tierKey: 'gold'     },
  { min: 55, tierKey: 'silver'   },
  { min: 40, tierKey: 'bronze'   },
  { min: 0,  tierKey: 'red'      },
] as const;

function scoreBucket(score: number) {
  return SCORE_BUCKETS.find((b) => score >= b.min) ?? SCORE_BUCKETS[SCORE_BUCKETS.length - 1];
}

function scoreTierKey(score: number): string {
  return scoreBucket(score).tierKey;
}

// Score is a health signal, so its colour funnels through the semantic
// tones: ok ≥ 70 (good / low-risk), warn ≥ 40 (attention), danger below
// (poor).  Boundaries sit on the existing bucket thresholds so no card
// changes tone band on rollout.
function scoreTone(score: number): Tone {
  if (score >= 70) return 'ok';
  if (score >= 40) return 'warn';
  return 'danger';
}

// CSS-var form of the score tone — for inline ``style`` on charts /
// gauges / KPI figures that can't take Tailwind classes.
function scoreVar(score: number): string {
  return `var(--${scoreTone(score)})`;
}

function ScoreBadge({ score, tierLabel }: { score: number; tierLabel: string }) {
  return (
    <Badge tone={scoreTone(score)} className="font-bold">
      <span>{score}</span>
      <span className="opacity-70">·</span>
      <span>{tierLabel}</span>
    </Badge>
  );
}

// ── Circular gauge for the drawer ───────────────────────────────────

function ScoreGauge({ score, tierLabel }: { score: number; tierLabel: string }) {
  const radius = 54;
  const stroke = 10;
  const norm   = radius - stroke / 2;
  const circ   = 2 * Math.PI * norm;
  const offset = circ - (Math.max(0, Math.min(100, score)) / 100) * circ;
  const c = scoreVar(score);
  return (
    <div className="relative w-32 h-32 shrink-0">
      <svg width="128" height="128" viewBox="0 0 128 128">
        <circle cx="64" cy="64" r={norm}
          stroke="currentColor" className="text-muted/40"
          strokeWidth={stroke} fill="transparent" />
        <circle cx="64" cy="64" r={norm}
          stroke={c} strokeWidth={stroke} strokeLinecap="round"
          strokeDasharray={circ} strokeDashoffset={offset}
          fill="transparent" transform="rotate(-90 64 64)"
          style={{ transition: 'stroke-dashoffset .6s ease' }} />
      </svg>
      <div className="absolute inset-0 flex flex-col items-center justify-center">
        <span className="text-3xl font-bold tabular-nums" style={{ color: c }}>{score}</span>
        <span className="text-xs text-muted-foreground">{tierLabel}</span>
      </div>
    </div>
  );
}

// ── Score-distribution histogram ─────────────────────────────────────

function ScoreDistribution({ cards }: { cards: CompositeScorecard[] }) {
  const radiusPx = useRadiusPx();
  // Band fill follows the score tone (ok ≥ 70, warn ≥ 40, danger below)
  // so the histogram reads with the same good/attention/bad language as
  // the gauge and badges.
  const buckets = [
    { label: '0-39',   min: 0,  max: 39,  color: scoreVar(0)  },
    { label: '40-54',  min: 40, max: 54,  color: scoreVar(40) },
    { label: '55-69',  min: 55, max: 69,  color: scoreVar(55) },
    { label: '70-84',  min: 70, max: 84,  color: scoreVar(70) },
    { label: '85-100', min: 85, max: 100, color: scoreVar(85) },
  ].map((b) => ({
    ...b,
    count: cards.filter((c) => c.score >= b.min && c.score <= b.max).length,
  }));
  return (
    <ResponsiveContainer width="100%" height={150}>
      <BarChart data={buckets} margin={{ top: 16, right: 16, left: 0, bottom: 0 }}>
        <XAxis dataKey="label" tick={{ fill: 'var(--muted-foreground)', fontSize: CHART_FONT_SM }} />
        <YAxis tick={{ fill: 'var(--muted-foreground)', fontSize: CHART_FONT_SM }} allowDecimals={false} />
        <Tooltip
          formatter={(v, _name, props) => {
            const pct = cards.length > 0 ? Math.round((props.payload.count / cards.length) * 100) : 0;
            return [`${v} trucks (${pct}%)`, 'Count'];
          }}
          contentStyle={{ background: 'var(--card)', border: '1px solid var(--border)', borderRadius: 'var(--radius)', color: 'var(--foreground)' }} />
        <Bar dataKey="count" shape={<RoundedBar corners="top" radiusPx={radiusPx} />} label={{ position: 'top', fontSize: CHART_FONT_XS, fill: 'var(--muted-foreground)', formatter: (v: unknown) => Number(v) > 0 ? Number(v) : '' }}>
          {buckets.map((b) => <Cell key={b.label} fill={b.color} />)}
        </Bar>
      </BarChart>
    </ResponsiveContainer>
  );
}

// ── Top vs Bottom comparative chart ──────────────────────────────────

function TopBottomChart({ cards }: { cards: CompositeScorecard[] }) {
  const radiusPx = useRadiusPx();
  // Memoise so we sort/slice only when `cards` actually changes — not on
 // every parent re-render.
  const { top, bottom } = useMemo(() => {
    const sorted = [...cards].sort((a, b) => b.score - a.score);
    const top    = sorted.slice(0, 5).map((c) => ({ name: c.subject_name || c.driver_name, score: c.score }));
    const bottom = sorted.slice(-5).reverse().map((c) => ({ name: c.subject_name || c.driver_name, score: c.score }));
    return { top, bottom };
  }, [cards]);

  const renderGroup = (items: typeof top, label: string, color: string) => (
    <div>
      <p className="text-2xs font-semibold tracking-wide mb-1" style={{ color }}>{label}</p>
      <ResponsiveContainer width="100%" height={items.length * 28 + 8}>
        <BarChart layout="vertical" data={items} margin={{ top: 0, right: 36, left: 0, bottom: 0 }}>
          <XAxis type="number" domain={[0, 100]} hide />
          <YAxis type="category" dataKey="name" width={56} tick={{ fill: 'var(--muted-foreground)', fontSize: CHART_FONT_MD }} />
          <Tooltip
            formatter={(v) => [`${v}`, 'Score']}
            contentStyle={{ background: 'var(--card)', border: '1px solid var(--border)', borderRadius: 'var(--radius)', color: 'var(--foreground)' }} />
          <ReferenceLine x={70} stroke="var(--muted-foreground)" strokeDasharray="3 3" />
          <Bar dataKey="score" shape={<RoundedBar corners="right" radiusPx={radiusPx} />} label={{ position: 'right', fontSize: CHART_FONT_SM, fill: 'var(--muted-foreground)' }}>
            {items.map((d, i) => <Cell key={i} fill={scoreVar(d.score)} />)}
          </Bar>
        </BarChart>
      </ResponsiveContainer>
    </div>
  );

  return (
    <div className="flex flex-col gap-3">
      {renderGroup(top,    'TOP 5',    'var(--ok)')}
      <div className="border-t border-border" />
      {renderGroup(bottom, 'BOTTOM 5', 'var(--danger)')}
    </div>
  );
}

// ── 30-day history line chart ────────────────────────────────────────
//
// overlays three faded pillar lines (safety / efficiency
// / compliance) under the bold composite line.  Pre-Option-C snapshots
// don't carry pillar data — the chart degrades gracefully to the bold
// total line + a dashed marker showing where pillar tracking began.

interface MergedHistoryPoint {
  date: string;
  total: number;
  safety?:     number | null;
  efficiency?: number | null;
  compliance?: number | null;
}

function HistoryChart({ driverId, days }: { driverId: string; days: number }) {
 // React Query: caches each driver's history under a
  // per-driver+days key so opening the same drawer again is instant.
  // The four pillar variants fan out in parallel inside one ``queryFn``.
  const enc = encodeURIComponent(driverId);
  const histDays = Math.max(7, Math.min(days, 180));
  const { data, isLoading } = useQuery<MergedHistoryPoint[]>({
    queryKey: ['scorecard-history', driverId, histDays],
    queryFn: async () => {
      const [total, s, e, c] = await Promise.all([
        apiJSONSlow<ScoreHistoryResponse>(`/safety/scorecards/history?driver_id=${enc}&days=${histDays}`),
        apiJSONSlow<ScoreHistoryResponse>(`/safety/scorecards/history?driver_id=${enc}&days=${histDays}&pillar=safety`).catch(() => null),
        apiJSONSlow<ScoreHistoryResponse>(`/safety/scorecards/history?driver_id=${enc}&days=${histDays}&pillar=efficiency`).catch(() => null),
        apiJSONSlow<ScoreHistoryResponse>(`/safety/scorecards/history?driver_id=${enc}&days=${histDays}&pillar=compliance`).catch(() => null),
      ]);
      const safetyByDate     = new Map((s?.history ?? []).map((p) => [p.date, p.score]));
      const efficiencyByDate = new Map((e?.history ?? []).map((p) => [p.date, p.score]));
      const complianceByDate = new Map((c?.history ?? []).map((p) => [p.date, p.score]));
      return (total.history || []).map((p) => ({
        date: p.date,
        total: p.score,
        safety:     safetyByDate.get(p.date) ?? null,
        efficiency: efficiencyByDate.get(p.date) ?? null,
        compliance: complianceByDate.get(p.date) ?? null,
      }));
    },
    staleTime: 5 * 60_000,  // history changes once a day; cache aggressively.
  });

  const points = data ?? [];
  // Earliest date with any pillar value — drives the dashed marker.
  const pillarStartDate = points.find(
    (p) => p.safety != null || p.efficiency != null || p.compliance != null,
  )?.date ?? null;

  if (isLoading) return <p className="text-xs text-muted-foreground">loading history…</p>;
  if (points.length < 2) {
    return (
      <p className="text-xs text-muted-foreground italic">
        No history yet — snapshots accrue nightly.
      </p>
    );
  }
  // The dashed "pillar data starts here" marker only renders when pillar
  // data starts mid-series — i.e., the user has snapshots from before the
  // Option-C rollout.  All-new tenants skip the marker entirely.
  const showPillarMarker = pillarStartDate != null && pillarStartDate !== points[0].date;
  return (
    <ResponsiveContainer width="100%" height={160}>
      <LineChart data={points} margin={{ top: 6, right: 8, left: -10, bottom: 0 }}>
        <CartesianGrid stroke="var(--border)" strokeDasharray="3 3" />
        <XAxis dataKey="date" tick={{ fill: 'var(--muted-foreground)', fontSize: CHART_FONT_XS }}
               tickFormatter={(d: string) => d.slice(5)} />
        <YAxis domain={[0, 100]} tick={{ fill: 'var(--muted-foreground)', fontSize: CHART_FONT_XS }} />
        <Tooltip
          contentStyle={{ background: 'var(--card)', border: '1px solid var(--border)', borderRadius: 'var(--radius)', color: 'var(--foreground)' }} />
        {/* Faded pillar lines underneath the bold total — categorical
            series, so they ride the chart-token palette. */}
        <Line type="monotone" dataKey="safety"     name="🛡 Safety"
              stroke={chartColor(1)} strokeWidth={1.25} strokeOpacity={0.55}
              dot={false} connectNulls={false} isAnimationActive={false} />
        <Line type="monotone" dataKey="efficiency" name="⚡ Efficiency"
              stroke={chartColor(2)} strokeWidth={1.25} strokeOpacity={0.55}
              dot={false} connectNulls={false} isAnimationActive={false} />
        <Line type="monotone" dataKey="compliance" name="🛠 Compliance"
              stroke={chartColor(3)} strokeWidth={1.25} strokeOpacity={0.55}
              dot={false} connectNulls={false} isAnimationActive={false} />
        {/* Bold composite total on top */}
        <Line type="monotone" dataKey="total" name="Total"
              stroke="var(--foreground)" strokeWidth={2.25} dot={{ r: 2 }}
              isAnimationActive={false} />
        <ReferenceLine y={70} stroke="var(--muted-foreground)" strokeDasharray="3 3" />
        {showPillarMarker && pillarStartDate && (
          <ReferenceLine x={pillarStartDate} stroke={chartColor(4)}
            strokeDasharray="2 4"
            label={{ value: 'pillars', position: 'top', fontSize: CHART_FONT_2XS, fill: chartColor(4) }}
          />
        )}
      </LineChart>
    </ResponsiveContainer>
  );
}

// ── Score explanation: "Why did my score change?" ────────────
//
// Shows added/cleared/earned/lost events between the latest snapshot
// and the one ~``days`` ago.  Backed by /api/safety/scorecards/{id}/
// explanation — no client-side diffing.  Empty state when fewer
// than 2 snapshots exist (driver too new or tenant just onboarded).

function ScoreExplanationCard({
  subjectId, subjectType, days,
}: {
  subjectId: string;
  subjectType: 'driver' | 'vehicle';
  days: number;
}) {
  const { t } = useTranslation();
  const enc = encodeURIComponent(subjectId);
  const { data, isLoading } = useQuery<ScoreExplanationResponse>({
    queryKey: ['scorecard-explanation', subjectId, subjectType, days],
    queryFn: () => apiJSON<ScoreExplanationResponse>(
      `/safety/scorecards/${enc}/explanation?subject_type=${subjectType}&days=${days}`,
    ),
    staleTime: 5 * 60_000,
  });

  if (isLoading) {
    return <p className="text-xs text-muted-foreground italic">…</p>;
  }
  if (!data || !data.available) {
    return (
      <p className="text-xs text-muted-foreground italic">
        {t('scorecards.explanation_no_data')}
      </p>
    );
  }

  const sections: { titleKey: string; events: ScoreEventBreakdown[]; tone: 'good' | 'bad' }[] = [
    { titleKey: 'scorecards.explanation_penalties_added',   events: data.penalties_added   || [], tone: 'bad' },
    { titleKey: 'scorecards.explanation_penalties_cleared', events: data.penalties_cleared || [], tone: 'good' },
    { titleKey: 'scorecards.explanation_bonuses_earned',    events: data.bonuses_earned    || [], tone: 'good' },
    { titleKey: 'scorecards.explanation_bonuses_lost',      events: data.bonuses_lost      || [], tone: 'bad' },
  ];
  const anyContent = sections.some((s) => s.events.length > 0);
  const delta = data.score_delta ?? 0;
  const deltaColor =
    delta > 0 ? 'var(--ok)' : delta < 0 ? 'var(--danger)' : 'var(--muted-foreground)';

  return (
    <div className="text-xs">
      <div className="flex items-baseline justify-between mb-2">
        <p className="text-muted-foreground">
          {t('scorecards.explanation_subtitle', { from_date: data.from_date })}
        </p>
        <span
          className="tabular-nums font-semibold"
          style={{ color: deltaColor }}
          title={`${data.from_score} → ${data.to_score}`}
        >
          {delta >= 0 ? '+' : ''}{delta} pts
        </span>
      </div>
      {!anyContent ? (
        <p className="text-muted-foreground italic">
          {t('scorecards.explanation_no_change')}
        </p>
      ) : (
        <div className="space-y-2">
          {sections.map((s, idx) => s.events.length > 0 && (
            <div key={idx}>
              <p
                className="text-2xs font-semibold tracking-wide mb-1"
                style={{ color: s.tone === 'good' ? 'var(--ok)' : 'var(--danger)' }}
              >
                {s.tone === 'good' ? '✓' : '✗'} {t(s.titleKey)}
              </p>
              <ul className="space-y-0.5">
                {s.events.map((e) => (
                  <li key={e.rule_id} className="flex items-baseline justify-between gap-2">
                    <span className="truncate">
                      {e.label}
                      {e.occurrences > 1 && (
                        <span className="text-2xs text-muted-foreground ml-1">
                          {t('scorecards.explanation_event_occurred', { count: e.occurrences })}
                        </span>
                      )}
                    </span>
                    <span
                      className="font-medium tabular-nums shrink-0"
                      style={{ color: s.tone === 'good' ? 'var(--ok)' : 'var(--danger)' }}
                    >
                      {e.points > 0 ? '+' : ''}{e.points}
                    </span>
                  </li>
                ))}
              </ul>
            </div>
          ))}
        </div>
      )}
    </div>
  );
}

// ── Sparkline ──────────────────────────────────────────────
//
// Tiny inline SVG.  Renders nothing for fewer than two snapshots so a
// single-day series doesn't draw a misleading flat line.

function Sparkline({ values, width = 80, height = 24 }: {
  values: number[]; width?: number; height?: number;
}) {
  if (!values || values.length < 2) {
    return <span className="text-2xs text-muted-foreground">—</span>;
  }
  const min = Math.min(...values);
  const max = Math.max(...values);
  const span = Math.max(1, max - min);
  const stepX = width / (values.length - 1);
  const points = values
    .map((v, i) => `${(i * stepX).toFixed(1)},${(height - ((v - min) / span) * height).toFixed(1)}`)
    .join(' ');
  const last = values[values.length - 1];
  const first = values[0];
  const trendColor = last >= first ? 'var(--ok)' : 'var(--danger)';
  return (
    <svg
      width={width}
      height={height}
      viewBox={`0 0 ${width} ${height}`}
      className="overflow-visible"
      aria-label={`Trend: ${values.join(', ')}`}
    >
      <polyline
        points={points}
        fill="none"
        stroke={trendColor}
        strokeWidth={1.5}
        strokeLinecap="round"
        strokeLinejoin="round"
      />
      <circle
        cx={width}
        cy={height - ((last - min) / span) * height}
        r={2}
        fill={trendColor}
      />
    </svg>
  );
}

// ── Table columns ────────────────────────────────────────────────────

function makeColumns(
  anyDriverPaired: boolean,
  t: (key: string, opts?: Record<string, unknown>) => string,
): AnyColumn[] {
  return [
  {
 // vehicle-first table. ``driver_name`` carries the truck
    // name when ``subject_type`` is vehicle (legacy alias from the
    // backend).  We render the truck name on top with the paired or
    // manually-assigned driver name as a small subline so admins can
    // see which truck-and-driver combination produced the score
    // without losing the truck-as-primary-subject framing.
    key: 'driver_name', label: 'Truck', sortable: true,
    render: (_v, row) => {
      const r = row as unknown as CompositeScorecard;
      const isVehicle = r.subject_type === 'vehicle';
      const inlineDriver = r.paired_driver_name || r.assigned_driver_name;
      return (
        <div className="flex flex-col leading-tight">
          <span className="font-medium">{r.subject_name || r.driver_name}</span>
          {isVehicle && inlineDriver && (
            <span className="text-2xs text-muted-foreground">
              {r.paired_driver_name ? '👤' : '🪪'} {inlineDriver}
            </span>
          )}
          {isVehicle && !inlineDriver && anyDriverPaired && (
            <span className="text-2xs italic text-muted-foreground">no driver paired</span>
          )}
        </div>
      );
    },
  },
  { key: 'company',     label: 'Company', sortable: true, filterable: true },
  {
    key: 'score', label: 'Score', sortable: true,
    filterable: true, filterMode: 'range',
    filterRange: { min: 0, max: 100, step: 1 },
    // Score is a 0–100 INDEX, not additive — offer avg (the fleet-average
    // KPI) + min/max (best/worst), never sum.  Footer shows a rounded
    // number, not the tier badge.
    aggregable: true, aggFns: ['avg', 'min', 'max'],
    aggFormat: (value) => <span className="tabular-nums font-bold">{Math.round(value)}</span>,
    // Insufficient-data drivers ride the same gauge as everyone else;
    // surface the provisional state inline so a "100 Platinum" cell
 // with thin telemetry doesn't read as authoritative.
    render: (v, row) => {
      const r = row as unknown as CompositeScorecard;
      const score = v as number;
      return (
        <div className="flex items-center gap-1.5">
          <ScoreBadge score={score} tierLabel={t(`tier.${scoreTierKey(score)}`)} />
          {r.probationary && (
            <span
              className={`text-2xs ${toneText('warn')}`}
              title={t('scorecards.probationary_banner_desc')}
            >
              ⏳
            </span>
          )}
          {r.insufficient_data && !r.probationary && (
            <span
              className={`text-2xs ${toneText('warn')}`}
              title={t('scorecards.insufficient_drive_time')}
            >
              ⚠
            </span>
          )}
        </div>
      );
    },
  },
  {
 // sparkline column — last ~14 daily totals. Sortable by
    // *last* value so the worst-trending trucks naturally cluster
    // when the user toggles ascending sort.
    key: 'score_trend', label: '14-day', sortable: false,
    render: (_v, row) => {
      const r = row as unknown as CompositeScorecard;
      const values = r.score_trend ?? [];
      const delta = values.length >= 2 ? values[values.length - 1] - values[0] : null;
      return (
        <div className="flex items-center gap-1.5">
          <Sparkline values={values} />
          {delta !== null && (
            <span
              className={`text-2xs font-semibold tabular-nums ${toneText(delta >= 0 ? 'ok' : 'danger')}`}
            >
              {delta >= 0 ? '▲' : '▼'} {Math.abs(delta)}
            </span>
          )}
        </div>
      );
    },
  },
  {
 // Pillar mini-bars. Only renders content when the
    // backend supplies the new ``pillars`` block; otherwise blank — the
    // legacy Bonuses/Penalties columns still tell the story.
    key: 'pillars', label: 'S · E · C', sortable: false,
    render: (_v, row) => {
      const r = row as unknown as CompositeScorecard;
      if (!r.pillars) return <span className="text-muted-foreground text-xs">—</span>;
      const cells: { k: 'safety' | 'efficiency' | 'compliance'; abbr: string }[] = [
        { k: 'safety',     abbr: 'S' },
        { k: 'efficiency', abbr: 'E' },
        { k: 'compliance', abbr: 'C' },
      ];
      return (
        <div className="flex items-center gap-1.5">
          {cells.map(({ k, abbr }) => {
            const p = r.pillars![k];
            if (!p.has_data) {
              // Match the layout of a data cell (same padding +
              // monospace) so columns don't jump when one pillar has
              // data and another doesn't. Dashed border signals
              // "missing" without competing with the red bucket.
              return (
                <span
                  key={k}
                  data-state="unavailable"
                  className="text-2xs font-mono tabular-nums px-1.5 py-0.5 rounded border border-dashed border-muted-foreground/40 text-muted-foreground italic"
                  title={`${k}: ${t('scorecards.no_data_window')}`}
                >
                  {abbr} {t('common.na')}
                </span>
              );
            }
            const pct = p.cap ? Math.round((p.subtotal / p.cap) * 100) : 0;
            return (
              <span key={k}
                className={`text-2xs font-mono tabular-nums px-1.5 py-0.5 rounded-md border ${toneClasses(scoreTone(pct))}`}
                title={`${k}: ${p.subtotal}/${p.cap} (${pct}%)`}>
                {abbr} {pct}
              </span>
            );
          })}
          {r.insufficient_data && (
            <Tip label={t('scorecards.insufficient_drive_time')}>
              <AlertTriangle
                className={`size-3 shrink-0 ${toneText('warn')}`}
                aria-label={t('scorecards.insufficient_drive_time')}
              />
            </Tip>
          )}
        </div>
      );
    },
  },
  {
    key: 'bonus_total', label: 'Bonuses', sortable: true,
    // Bonus POINTS — additive.  sum (fleet total) / avg per driver.
    aggregable: true, aggFns: ['sum', 'avg', 'max'],
    aggFormat: (value) => <span className={`font-medium ${toneText('ok')}`}>+{Math.round(value)}</span>,
    render: (v) => <span className={`font-medium ${toneText('ok')}`}>+{v as number}</span>,
  },
  {
    key: 'penalty_total', label: 'Penalties', sortable: true,
    // Penalty POINTS (already ≤ 0) — sum / avg; min = worst driver.
    aggregable: true, aggFns: ['sum', 'avg', 'min'],
    aggFormat: (value) => <span className={`font-medium ${toneText('danger')}`}>{Math.round(value)}</span>,
    render: (v) => <span className={`font-medium ${toneText('danger')}`}>{v as number}</span>,
  },
  ];
}

// ── Page ─────────────────────────────────────────────────────────────

export default function Scorecards() {
  const { t } = useTranslation();
  const tz = useTimezone();
  // Persona composition: resolve the active persona's block set once at
  // the page wrapper (features/scorecards/personaConfig.ts);
  // block components never read persona state themselves.
  const { persona } = useShellConfig();
  const blocks = blocksForPersona(persona);
  // Scorecard Rules is this feature's CONFIG component (docs/FEATURES.md) —
  // reached via a gear icon in the header (→ /scorecard-rules), not a peer
  // tab: config is a deliberate action, distinct from the live scoreboard.
  const navigate = useNavigate();
  const { has } = useViewPermissions();
  const canRules = has('can_manage_config_all');
  // Default 30 days — matches Events, RiskSummary and Reports so the
  // dashboard's "default window" is consistent across the SAFETY group.
  const [days, setDays]       = useState(30);
  const [error, setError]     = useState('');
  const [detail, setDetail]   = useState<CompositeScorecard | null>(null);
 // pillar filter chips replace the old category chips.
  // ``all`` shows the full table; otherwise we sort by the chosen pillar's
  // subtotal/cap percentage so the worst (or best) drivers in that pillar
  // bubble to the top.
  type PillarFilter = 'all' | 'safety' | 'efficiency' | 'compliance';
  const [pillarFilter, setPillarFilter] = useState<PillarFilter>('all');

 // vehicle-only view. The legacy driver/truck toggle has been
  // retired \u2014 telematics data is per-vehicle by nature, and drivers
  // (when known) are rendered inline next to their truck name.  The
  // backend default already flipped to ``vehicle``; we still pass the
  // explicit query param so behaviour is unambiguous regardless of any
  // residual per-tenant ``KEY_SCORECARD_DEFAULT_SUBJECT`` override.
  const compositeUrl = `/safety/scorecards/composite?days=${days}&subject=vehicle`;
  const {
    data: composite,
    isLoading,
    isFetching,
    error: queryError,
  } = useQuery<CompositeScorecardsResponse>({
    queryKey: ['scorecards-composite', days, 'vehicle'],
    queryFn: () => apiJSONSlow<CompositeScorecardsResponse>(compositeUrl),
    placeholderData: (prev) => prev,
  });

  const cards = composite?.scorecards ?? [];
  const loading = isLoading && !composite;
  const generatedAt = composite?.generated_at;
  // "Updated 4 min ago" — re-render once a minute so the label stays
  // honest while the page is left open.
  const [, _setNow] = useState(0);
  useEffect(() => {
    if (!generatedAt) return;
    const id = window.setInterval(() => _setNow((n) => n + 1), 60_000);
    return () => window.clearInterval(id);
  }, [generatedAt]);
  const relativeUpdated = useMemo(() => {
    if (!generatedAt) return '';
    const ts = new Date(generatedAt).getTime();
    if (Number.isNaN(ts)) return '';
    const sec = Math.max(0, Math.floor((Date.now() - ts) / 1000));
    if (sec < 60) return t('common.updated_just_now');
    if (sec < 3600) return t('common.updated_min_ago', { n: Math.floor(sec / 60) });
    if (sec < 86400) return t('common.updated_hr_ago', { n: Math.floor(sec / 3600) });
    return t('common.updated_d_ago', { n: Math.floor(sec / 86400) });
  }, [generatedAt, t]);
  // Composite scorecards are heavy — they evaluate every truck and
  // can take 20-30s on a cold cache + warehouse fallback.  Surface
  // progressive feedback so the page never reads as frozen.
  const stage = useLoadingStage(loading);

  useEffect(() => {
    if (queryError) {
      setError(queryError instanceof Error ? queryError.message : 'Failed to load');
    } else {
      setError('');
    }
  }, [queryError]);

  const stats = useMemo(() => {
    if (cards.length === 0) {
      return { avgScore: 0, atRisk: 0, topPerformers: 0 };
    }
    return {
      avgScore:      Math.round(cards.reduce((s, c) => s + c.score, 0) / cards.length),
      atRisk:        cards.filter((c) => c.score < 55).length,
      topPerformers: cards.filter((c) => c.score >= 85).length,
    };
  }, [cards]);

  // Filter + sort by selected pillar.  When ``all`` is selected we keep the
  // server's default order (by score desc).  When a specific pillar is
  // chosen we drop drivers whose pillar lacks data and sort ascending so
  // the worst-performers in that pillar surface first — admins almost
  // always want to coach the bottom, not celebrate the top.
  const displayCards = useMemo(() => {
    if (pillarFilter === 'all') return cards;
    const withData = cards.filter((c) => c.pillars?.[pillarFilter]?.has_data);
    return [...withData].sort((a, b) => {
      const ap = a.pillars?.[pillarFilter];
      const bp = b.pillars?.[pillarFilter];
      const ar = ap && ap.cap ? ap.subtotal / ap.cap : 0;
      const br = bp && bp.cap ? bp.subtotal / bp.cap : 0;
      return ar - br;
    });
  }, [cards, pillarFilter]);

  // The config gear — a deliberate "configure scoring" action in the header,
  // shown only to roles that may edit the rules.  Routes to the standalone
  // /scorecard-rules page (this feature's config surface), instead of a peer
  // tab sitting next to the live scoreboard.
  // The shared gear, in page mode. This was hand-rolled: its own button,
  // its own size-7 (off the size-8 the others use), its own permission
  // check, and a native title= the tooltip rule bans. Scorecards' config
  // is a full CRUD page rather than a panel, so the gear navigates —
  // which is exactly what `to` exists for. FeatureConfigGear self-gates
  // on can_manage_config_all, so `canRules` is no longer consulted here.
  const rulesGear = (
    <FeatureConfigGear
      feature={t('scorecards.page_title')}
      to="/scorecard-rules"
    />
  );

  if (error && cards.length === 0) {
    return (
      <div>
        <PageHeader
          icon={Trophy}
          title={t('scorecards.page_title')}
          description={t('scorecards.page_description')}
          actions={rulesGear}
        />
        <ErrorState
          title={t('errors.load_failed')}
          message={error}
          onRetry={() => window.location.reload()}
        />
      </div>
    );
  }

 // CSV export — driven from the already-loaded cards array so
  // it reflects the current days filter without an extra API round-trip.
 // append pillar+exposure columns when the new shape is
  // present (mirrors capabilities/reporting/csv_generators.py).
  function exportCsv() {
    const cardsForExport = pillarFilter === 'all' ? cards : displayCards;
    const hasPillars = cardsForExport.some((c) => c.pillars);
    const headers = [
      'subject_id', 'subject_name', 'driver_id', 'driver_name',
      'company', 'score', 'tier',
      'base', 'bonus_total', 'penalty_total',
      'miles', 'mpg', 'drive_hours', 'idle_hours',
      'eco_pct', 'overspeed_min', 'anticipatory_braking_pct',
    ];
    if (hasPillars) {
      headers.push(
        'safety_50', 'efficiency_25', 'compliance_25', 'total_100',
        'insufficient_data', 'exposure_miles', 'exposure_drive_hours',
      );
    }
    const esc = (v: unknown): string => {
      if (v == null) return '';
      const s = String(v);
      return /[",\n]/.test(s) ? `"${s.replace(/"/g, '""')}"` : s;
    };
    // Spell "no data" explicitly in CSV so spreadsheets don't misread
    // empty cells as zeros — important for pillar subtotals where 0
    // means "max penalties applied" and missing data means "n/a".
    const NA = 'n/a';
    const lines = [headers.join(',')];
    for (const c of cardsForExport) {
      const i = c.inputs;
      const row: unknown[] = [
        c.subject_id ?? c.driver_id,
        c.subject_name ?? c.driver_name,
        c.driver_id, c.driver_name, c.company || '', c.score, t(`tier.${scoreTierKey(c.score)}`),
        c.base, c.bonus_total, c.penalty_total,
        i.miles, i.mpg, i.drive_hours, i.idle_hours,
        i.eco_pct, i.overspeed_min, i.anticipatory_braking_pct,
      ];
      if (hasPillars) {
        const ps = c.pillars?.safety;
        const pe = c.pillars?.efficiency;
        const pc = c.pillars?.compliance;
        row.push(
          ps?.has_data ? ps.subtotal : NA,
          pe?.has_data ? pe.subtotal : NA,
          pc?.has_data ? pc.subtotal : NA,
          c.total ?? c.score,
          c.insufficient_data ? '1' : '0',
          c.exposure?.miles ?? '',
          c.exposure?.drive_hours ?? '',
        );
      }
      lines.push(row.map(esc).join(','));
    }
    const blob = new Blob([lines.join('\n')], { type: 'text/csv;charset=utf-8' });
    const url  = URL.createObjectURL(blob);
    const a    = document.createElement('a');
    const stamp = new Date().toISOString().slice(0, 10);
    a.href = url;
    a.download = `scorecards_${days}d_${stamp}.csv`;
    document.body.appendChild(a);
    a.click();
    document.body.removeChild(a);
    URL.revokeObjectURL(url);
  }

  return (
    <div>
      <PageHeader
        icon={Trophy}
        title={t('scorecards.page_title')}
        description={t('scorecards.page_description')}
        actions={
          <div className="flex items-center gap-2">
            {relativeUpdated && (
              <span
                className="text-2xs text-muted-foreground"
                title={generatedAt ? formatDate(generatedAt, { timeZone: tz }) : undefined}
              >
                {t('common.updated_prefix')} {relativeUpdated}
              </span>
            )}
            {loading && cards.length > 0 && (
              <span className="text-2xs text-muted-foreground animate-pulse">{t('common.refreshing')}</span>
            )}
            <button
              type="button"
              onClick={exportCsv}
              disabled={cards.length === 0}
              className="inline-flex items-center gap-1.5 text-xs px-3 py-1.5 rounded-md border border-border bg-background hover:bg-muted disabled:opacity-40 disabled:cursor-not-allowed transition min-h-tap"
              title={t('scorecards.csv_download_title')}
            >
              <Download className="size-3" />
              {t('scorecards.csv_label')}
            </button>
            <DateRangePresets value={days} onChange={setDays} isFetching={isFetching} />
            {rulesGear}
          </div>
        }
      />

      {/* KPI strip */}
      {cards.length > 0 && blocks.includes('kpi') && (
        <div className="grid grid-cols-2 md:grid-cols-4 gap-4 mb-6">
          <KpiCard
            label={t('scorecards.kpi_active_trucks')}
            value={cards.length.toString()}
            sub={t('scorecards.kpi_active_trucks_sub')}
          />
          <KpiCard
            label={t('scorecards.kpi_avg_score')}
            value={stats.avgScore.toString()}
            sub={t(`tier.${scoreTierKey(stats.avgScore)}`)}
            color={scoreVar(stats.avgScore)}
          />
          <KpiCard
            label={t('scorecards.kpi_top_performers')}
            value={stats.topPerformers.toString()}
            color="var(--ok)"
          />
          <KpiCard
            label={t('scorecards.kpi_at_risk')}
            value={stats.atRisk.toString()}
            color={stats.atRisk > 0 ? 'var(--danger)' : 'var(--muted-foreground)'}
          />
        </div>
      )}

      {stage === 'timeout' && cards.length === 0 ? (
        <ErrorState
          title={t('common.loading_takes_long')}
          message={t('scorecards.loading_too_long_message')}
        />
      ) : loading && cards.length === 0 ? (
        <TableSkeleton
          rows={8}
          cols={6}
          message={
            stage === 'slow'
              ? t('scorecards.loading_slow')
              : t('scorecards.loading_default')
          }
        />
      ) : cards.length === 0 ? (
        <EmptyState
          icon={Trophy}
          title="No scorecards in this window"
          description="Try widening the date range — composite scores need at least a few hours of drive activity."
        />
      ) : (
        <>
          {cards.length > 0 && (blocks.includes('distribution') || blocks.includes('top_bottom')) && (
            <div className="grid grid-cols-1 lg:grid-cols-2 gap-4 mb-6">
              {blocks.includes('distribution') && (
                <Card>
                  <p className="text-sm font-medium mb-3">{t('scorecards.score_distribution')}</p>
                  <ScoreDistribution cards={cards} />
                </Card>
              )}
              {blocks.includes('top_bottom') && (
                <Card>
                  <p className="text-sm font-medium mb-3">{t('scorecards.top_vs_bottom')}</p>
                  <TopBottomChart cards={cards} />
                </Card>
              )}
            </div>
          )}
          {/* Pillar filter chips */}
          {cards.length > 0 && cards.some((c) => c.pillars) && (
            <div className="flex items-center gap-2 mb-3 flex-wrap">
              <span className="text-xs text-muted-foreground mr-1">{t('scorecards.filter_by_pillar')}</span>
              {/* Pillar identity is categorical (not a value status), so the
                  active-chip hue rides the chart-token palette — the same
                  hues the pillar lines use in the history chart. */}
              {([
                { k: 'all',        labelKey: 'scorecards.filter_all',        color: 'var(--muted-foreground)' },
                { k: 'safety',     labelKey: 'scorecards.pillar_safety',     color: chartColor(1) },
                { k: 'efficiency', labelKey: 'scorecards.pillar_efficiency', color: chartColor(2) },
                { k: 'compliance', labelKey: 'scorecards.pillar_compliance', color: chartColor(3) },
              ] as { k: PillarFilter; labelKey: string; color: string }[]).map((c) => {
                const active = pillarFilter === c.k;
                const label = t(c.labelKey);
                return (
                  <button
                    key={c.k}
                    type="button"
                    onClick={() => setPillarFilter(c.k)}
                    aria-pressed={active}
                    aria-label={t('scorecards.filter_aria', { pillar: label.replace(/^\W+\s*/, '') })}
                    // A pillar FILTER — you press it. Shape is the grammar here
                  // (features/alerts/_shared/components.tsx): a capsule
                  // states a fact, a bordered rounded-rect invites a
                  // click. This file had it inverted against its own score
                  // badge four hundred lines up, which is a rectangle.
                  className={`text-xs px-3 py-1 rounded-md border transition ${
                      active
                        ? 'border-transparent text-background'
                        : 'border-border text-foreground/70 hover:bg-muted'
                    } min-h-tap`}
                    style={active ? { background: c.color } : undefined}
                  >
                    {label}
                  </button>
                );
              })}
              {pillarFilter !== 'all' && (
                <span className="text-2xs text-muted-foreground ml-2">
                  {t('scorecards.worst_first_hint', { count: displayCards.length })}
                </span>
              )}
            </div>
          )}
          <DataGrid
            columns={makeColumns(
              cards.some((c) => !!(c.paired_driver_name || c.assigned_driver_name)),
              t,
            )}
            data={displayCards as unknown as Record<string, unknown>[]}
            searchKey="driver_name"
            searchPlaceholder={t('scorecards.search_truck_placeholder')}
            stickyHeader="65vh"
            tableId="scorecards"
            onRowClick={(row) => setDetail(row as unknown as CompositeScorecard)}
          />
        </>
      )}

      {detail && (
        <DetailDrawer
          card={detail}
          // Rank against the currently displayed cohort so a
          // pillar-filtered view doesn't surface an account-wide rank
          // that confuses what the user is looking at.
          rank={displayCards.findIndex((c) => c.driver_id === detail.driver_id) + 1}
          total={displayCards.length}
          aggregateAvg={stats.avgScore}
          days={days}
          onClose={() => setDetail(null)}
        />
      )}
    </div>
  );
}

// ── Sub-components ───────────────────────────────────────────────────

function KpiCard({ label, value, sub, color }: { label: string; value: string; sub?: string; color?: string }) {
  return (
    <Card>
      <p className="text-xs text-muted-foreground">{label}</p>
      <div className="flex items-baseline gap-2">
        <p className="text-2xl font-bold" style={color ? { color } : undefined}>{value}</p>
        {sub && <p className="text-sm font-semibold opacity-70" style={color ? { color } : undefined}>{sub}</p>}
      </div>
    </Card>
  );
}

function DetailDrawer({ card, rank, total, aggregateAvg, days, onClose }: {
  card: CompositeScorecard;
  rank: number;
  total: number;
  aggregateAvg: number;
  /** Page-level date range (in days) — propagates to the trend chart
      and the period-comparison cells inside DriverInsights so the
      drawer always reflects the same window the user picked at the top. */
  days: number;
  onClose: () => void;
}) {
  const { t } = useTranslation();
 // keyboard a11y — close on Esc and announce as a modal dialog.
  useEffect(() => {
    function onKey(e: KeyboardEvent) {
      if (e.key === 'Escape') onClose();
    }
    document.addEventListener('keydown', onKey);
    return () => document.removeEventListener('keydown', onKey);
  }, [onClose]);

  const delta = card.score - aggregateAvg;

  return (
    // This one already declared ``role="dialog"`` and ``aria-modal`` by
    // hand — honest, and still not a modal: the announcement was true
    // while the BEHAVIOUR (focus trap, Escape, background scroll lock)
    // was absent, which is the worse failure of the two.  <Sheet> makes
    // the announcement earned; <SheetBody> makes the body a real
    // scroll region.
    <Sheet open onOpenChange={(o) => { if (!o) onClose(); }}>
      <SheetContent
        side="right"
        className="p-0"
        size="lg"
        aria-label={`Scorecard detail for ${card.subject_name || card.driver_name}`}
        // Its own ✕ sits in the header below; the primitive's would land
        // on top of it.  Fifth sheet in this codebase to need this — the
        // default is ``true`` and every converted modal brings its own.
        showCloseButton={false}
      >
        <SheetBody label="Scorecard detail" className="p-6">
        <div className="flex items-center justify-between mb-4">
          <div>
            <h2 className="text-lg font-semibold">{card.subject_name || card.driver_name}</h2>
            {card.subject_type === 'vehicle' && (card.paired_driver_name || card.assigned_driver_name) && (
              <p className="text-xs text-foreground/70">
                {card.paired_driver_name ? '👤 Driver: ' : '🪪 Assigned: '}
                {card.paired_driver_name || card.assigned_driver_name}
              </p>
            )}
            {card.company && <p className="text-xs text-muted-foreground">{card.company}</p>}
          </div>
          <button
            onClick={onClose}
            aria-label={t('common.close')}
            className="text-muted-foreground hover:text-foreground p-1 min-h-tap"
          >
            <X className="size-4" />
          </button>
        </div>

        {/* Risk Summary deep-link */}
        <RiskSummaryLink card={card} />

        {/* Risk band, trend, severity buckets, period compare, top issue,
             AI insights, coaching action, full categories matrix —
             closes the gap with the competitor's per-driver detail page.
             ``days`` flows through so the period compare adapts to the
             window the user selected upstream. */}
        <DriverInsights card={card} days={days} />

        {/* Gauge + rank + fleet delta */}
        <div className="flex items-center gap-5 mb-5">
          <ScoreGauge score={card.score} tierLabel={t(`tier.${scoreTierKey(card.score)}`)} />
          <div className="flex-1 text-xs space-y-1.5">
            <div className="flex items-center gap-2">
              <span className="text-muted-foreground">Rank</span>
              <span className="font-semibold tabular-nums">#{rank} of {total}</span>
            </div>
            <div className="flex items-center gap-2">
              <span className="text-muted-foreground">Fleet avg</span>
              <span className="font-semibold tabular-nums">{aggregateAvg}</span>
              <span
                className="text-2xs font-bold tabular-nums"
                style={{ color: delta >= 0 ? 'var(--ok)' : 'var(--danger)' }}
                title={`${delta >= 0 ? '+' : ''}${delta} vs fleet average`}
              >
                {delta >= 0 ? '+' : ''}{delta} vs fleet
              </span>
            </div>
          </div>
        </div>

        {/* Score math — visible reconciliation so users can trace
             the gauge number back to its inputs.  Pillar-shape cards
             show Safety/Compliance/Efficiency subtotals; legacy cards
             fall back to Base/Bonuses/Penalties.  Either way the user
             can match the gauge to the math. */}
        {card.pillars ? (
          <div className="mb-5 text-xs space-y-1">
            {(['safety', 'compliance', 'efficiency'] as const).map((pk) => {
              const p = card.pillars?.[pk];
              if (!p?.has_data) return null;
              const label = pk[0].toUpperCase() + pk.slice(1);
              return (
                <ScoreMath
                  key={pk}
                  label={`${label} subtotal`}
                  value={`${p.subtotal}/${p.cap}`}
                />
              );
            })}
            <div className="border-t border-border pt-1 mt-1 flex justify-between font-semibold">
              <span>Total</span>
              <span style={{ color: scoreVar(card.score) }}>{card.score}/100</span>
            </div>
          </div>
        ) : (
          <div className="mb-5 text-xs space-y-1">
            <ScoreMath label={t('detail_drawer.score_math_base')}      value={card.base.toString()} />
            <ScoreMath label={t('detail_drawer.score_math_bonuses')}   value={`+${card.bonus_total}`} positive />
            <ScoreMath label={t('detail_drawer.score_math_penalties')} value={`${card.penalty_total}`} negative />
            <div className="border-t border-border pt-1 mt-1 flex justify-between font-semibold">
              <span>Total</span>
              <span style={{ color: scoreVar(card.score) }}>{card.score}</span>
            </div>
          </div>
        )}

        {card.probationary && (
          <div className={`mb-4 flex items-start gap-2 text-2xs border rounded-lg px-2.5 py-2 ${toneClasses('warn')}`}>
            <span className="text-base leading-none">⏳</span>
            <div>
              <p className="font-semibold">{t('scorecards.probationary_banner_title')}</p>
              <p className="opacity-90">{t('scorecards.probationary_banner_desc')}</p>
            </div>
          </div>
        )}

        {card.insufficient_data && (
          <p className={`mb-4 text-2xs italic ${toneText('warn')}`}>
            {t('scorecards.insufficient_window')}
          </p>
        )}

        {/* History trend */}
        <div className="mb-6">
          <p className="text-2xs font-semibold text-muted-foreground tracking-wide mb-1">
            {`${Math.max(7, Math.min(days, 180))}-DAY TREND`}
          </p>
          <HistoryChart driverId={card.driver_id || card.driver_name} days={days} />
        </div>

        {/* Score explanation — "Why did my score change?" diff of the
             latest snapshot vs ~``days`` ago.  Backed by the new
             /scorecards/{subject_id}/explanation endpoint; renders an
             empty state for drivers with <2 snapshots. */}
        <div className="mb-6">
          <p className="text-2xs font-semibold text-muted-foreground tracking-wide mb-1">
            {t('scorecards.explanation_title').toUpperCase()}
          </p>
          <ScoreExplanationCard
            subjectId={card.subject_id || card.driver_id || card.driver_name}
            subjectType={(card.subject_type === 'vehicle' ? 'vehicle' : 'driver') as 'driver' | 'vehicle'}
            days={Math.max(2, Math.min(days, 30))}
          />
        </div>

        {/* Bonus / Penalty lists were here — merged into the
             "Pillars · Your Events" section inside DriverInsights so
             each event renders exactly once with its pillar context. */}

        {/* Raw inputs (Samsara) — preserved per plan.
             When odometer data is missing (miles=0 with non-zero drive
             hours), Samsara's Driver Stats response is partial and the
             behavior fields (eco/overspeed/coast/cruise/anticipatory
             braking) are typically nulled-into-zero by the backend.
             Rendering them as authoritative "0%" reads as if the
             driver actually had perfect behavior, so we collapse them
             all to "—" once the warning fires. */}
        <div className="border-t border-border pt-3">
          <p className="text-2xs font-semibold text-muted-foreground tracking-wide mb-2">
            INPUTS {card.inputs._source && <span className="opacity-60">{card.inputs._source}</span>}
          </p>
          {(() => {
            const odometerMissing = card.inputs.miles === 0 && card.inputs.drive_hours > 0;
            const fmt = (n: number, suffix: string) => odometerMissing ? '—' : `${n}${suffix}`;
            return (
              <>
                {odometerMissing && (
                  <p className={`text-2xs mb-2 ${toneText('warn')}`}>
                    ⚠ Odometer / Driver-Stats data missing — miles, MPG and behavior metrics are not reliable for this window
                  </p>
                )}
                <dl className="grid grid-cols-2 gap-x-4 gap-y-1 text-xs">
                  <Stat label={t('detail_drawer.input_miles')}        value={odometerMissing ? '—' : card.inputs.miles.toLocaleString()} />
                  <Stat label={t('detail_drawer.input_mpg')}          value={odometerMissing ? '—' : `${card.inputs.mpg}`} />
                  <Stat label={t('detail_drawer.input_drive')}        value={`${card.inputs.drive_hours}h (${card.inputs.drive_pct}%)`} />
                  <Stat label={t('detail_drawer.input_idle')}         value={`${card.inputs.idle_hours}h (${card.inputs.idle_pct}%)`} />
                  <Stat label={t('detail_drawer.input_eco')}          value={fmt(card.inputs.eco_pct, '%')} />
                  <Stat label={t('detail_drawer.input_overspeed')}    value={fmt(card.inputs.overspeed_min, ' min')} />
                  <Stat label={t('detail_drawer.input_coast')}        value={fmt(card.inputs.coast_min, ' min')} />
                  <Stat label={t('detail_drawer.input_cruise')}       value={fmt(card.inputs.cruise_min, ' min')} />
                  <Stat label={t('detail_drawer.input_antic_braking')} value={fmt(card.inputs.anticipatory_braking_pct, '%')} />
                </dl>
              </>
            );
          })()}
        </div>
        </SheetBody>
      </SheetContent>
    </Sheet>
  );
}

function Stat({ label, value }: { label: string; value: string }) {
  return (
    <>
      <dt className="text-muted-foreground">{label}</dt>
      <dd className="font-medium text-right tabular-nums">{value}</dd>
    </>
  );
}

function ScoreMath({ label, value, positive, negative }: {
  label: string; value: string; positive?: boolean; negative?: boolean;
}) {
  const cls = positive ? toneText('ok')
            : negative ? toneText('danger')
            : 'text-foreground';
  return (
    <div className="flex justify-between">
      <span className="text-muted-foreground">{label}</span>
      <span className={`font-semibold tabular-nums ${cls}`}>{value}</span>
    </div>
  );
}


// ── Stakeholder Risk Summary deep-link ──────────────────────────────
function RiskSummaryLink({ card }: { card: CompositeScorecard }) {
  const subjectType = card.subject_type === 'vehicle' ? 'vehicle' : 'driver';
  const subjectId =
    subjectType === 'vehicle'
      ? (card.subject_name || '')
      : (card.driver_id || '');
  if (!subjectId) return null;
  const params = new URLSearchParams({
    subject_type: subjectType,
    subject_id: subjectId,
    audience: 'owner',
    days: '30',
  });
  if (card.company) params.set('company', card.company);
  return (
    <div className="mb-4">
      <a
        href={`/reports/risk-summary?${params.toString()}`}
        className="inline-flex items-center gap-1.5 text-xs font-medium px-3 py-1.5 rounded
                   border border-primary text-foreground hover:bg-primary/10 transition"
        title="Generate Stakeholder Risk Summary report"
      >
        📄 Generate Risk Summary
      </a>
    </div>
  );
}
