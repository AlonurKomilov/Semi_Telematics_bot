import { useEffect, useMemo, useState } from 'react';
import { useQuery } from '@tanstack/react-query';
import {
  BarChart, Bar, XAxis, YAxis, Tooltip, ResponsiveContainer, Cell,
  LineChart, Line, CartesianGrid, ReferenceLine,
} from 'recharts';
import { apiJSON, apiJSONSlow } from '../../api/client';
import DataTable from '../../components/DataTable';
import type {
  CompositeScorecard,
  CompositeScorecardsResponse,
  ScoreHistoryResponse,
  ScoreEventBreakdown,
  AnyColumn,
} from '../../types';

// ── Color helpers ───────────────────────────────────────────────────

function scoreColor(score: number): string {
  if (score >= 85) return '#22c55e';
  if (score >= 70) return '#84cc16';
  if (score >= 55) return '#eab308';
  if (score >= 40) return '#f97316';
  return '#ef4444';
}

function scoreGrade(score: number): string {
  if (score >= 90) return 'A';
  if (score >= 80) return 'B';
  if (score >= 70) return 'C';
  if (score >= 60) return 'D';
  return 'F';
}

function ScoreBadge({ score }: { score: number }) {
  const c = scoreColor(score);
  return (
    <span
      className="inline-flex items-center gap-1 px-2 py-0.5 rounded-full text-xs font-bold"
      style={{ background: `${c}22`, color: c }}
    >
      <span>{score}</span>
      <span className="opacity-70">·</span>
      <span>{scoreGrade(score)}</span>
    </span>
  );
}

// ── Circular gauge for the drawer ───────────────────────────────────

function ScoreGauge({ score }: { score: number }) {
  const radius = 54;
  const stroke = 10;
  const norm   = radius - stroke / 2;
  const circ   = 2 * Math.PI * norm;
  const offset = circ - (Math.max(0, Math.min(100, score)) / 100) * circ;
  const c = scoreColor(score);
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
        <span className="text-xs text-muted-foreground">grade {scoreGrade(score)}</span>
      </div>
    </div>
  );
}

// ── Category meta ────────────────────────────────────────────────────

const CATEGORY_META: Record<string, { color: string; icon: string }> = {
  safety:     { color: '#ef4444', icon: '🛡' },
  fleet:      { color: '#3b82f6', icon: '🛠' },
  efficiency: { color: '#22c55e', icon: '⚡' },
};

function categoryColor(c: string): string {
  return CATEGORY_META[c]?.color ?? '#6b7280';
}

// ── Score-distribution histogram ─────────────────────────────────────

function ScoreDistribution({ cards }: { cards: CompositeScorecard[] }) {
  const buckets = [
    { label: '0-39',   min: 0,  max: 39,  color: '#ef4444' },
    { label: '40-54',  min: 40, max: 54,  color: '#f97316' },
    { label: '55-69',  min: 55, max: 69,  color: '#eab308' },
    { label: '70-84',  min: 70, max: 84,  color: '#84cc16' },
    { label: '85-100', min: 85, max: 100, color: '#22c55e' },
  ].map((b) => ({
    ...b,
    count: cards.filter((c) => c.score >= b.min && c.score <= b.max).length,
  }));
  return (
    <ResponsiveContainer width="100%" height={150}>
      <BarChart data={buckets} margin={{ top: 16, right: 16, left: 0, bottom: 0 }}>
        <XAxis dataKey="label" tick={{ fill: '#9ca3af', fontSize: 11 }} />
        <YAxis tick={{ fill: '#9ca3af', fontSize: 11 }} allowDecimals={false} />
        <Tooltip
          formatter={(v, _name, props) => {
            const pct = cards.length > 0 ? Math.round((props.payload.count / cards.length) * 100) : 0;
            return [`${v} trucks (${pct}%)`, 'Count'];
          }}
          contentStyle={{ background: '#111827', border: '1px solid #374151', borderRadius: 8, color: '#f9fafb' }} />
        <Bar dataKey="count" radius={[6, 6, 0, 0]} label={{ position: 'top', fontSize: 10, fill: '#9ca3af', formatter: (v: unknown) => Number(v) > 0 ? Number(v) : '' }}>
          {buckets.map((b) => <Cell key={b.label} fill={b.color} />)}
        </Bar>
      </BarChart>
    </ResponsiveContainer>
  );
}

// ── Top vs Bottom comparative chart ──────────────────────────────────

function TopBottomChart({ cards }: { cards: CompositeScorecard[] }) {
  // Memoise so we sort/slice only when `cards` actually changes — not on
  // every parent re-render (audit M5).
  const { top, bottom } = useMemo(() => {
    const sorted = [...cards].sort((a, b) => b.score - a.score);
    const top    = sorted.slice(0, 5).map((c) => ({ name: c.subject_name || c.driver_name, score: c.score }));
    const bottom = sorted.slice(-5).reverse().map((c) => ({ name: c.subject_name || c.driver_name, score: c.score }));
    return { top, bottom };
  }, [cards]);

  const renderGroup = (items: typeof top, label: string, color: string) => (
    <div>
      <p className="text-[10px] font-semibold tracking-wide mb-1" style={{ color }}>{label}</p>
      <ResponsiveContainer width="100%" height={items.length * 28 + 8}>
        <BarChart layout="vertical" data={items} margin={{ top: 0, right: 36, left: 0, bottom: 0 }}>
          <XAxis type="number" domain={[0, 100]} hide />
          <YAxis type="category" dataKey="name" width={56} tick={{ fill: '#9ca3af', fontSize: 12 }} />
          <Tooltip
            formatter={(v) => [`${v}`, 'Score']}
            contentStyle={{ background: '#111827', border: '1px solid #374151', borderRadius: 8, color: '#f9fafb' }} />
          <ReferenceLine x={70} stroke="#6b7280" strokeDasharray="3 3" />
          <Bar dataKey="score" radius={[0, 4, 4, 0]} label={{ position: 'right', fontSize: 11, fill: '#9ca3af' }}>
            {items.map((d, i) => <Cell key={i} fill={scoreColor(d.score)} />)}
          </Bar>
        </BarChart>
      </ResponsiveContainer>
    </div>
  );

  return (
    <div className="flex flex-col gap-3">
      {renderGroup(top,    'TOP 5',    '#22c55e')}
      <div className="border-t border-border/40" />
      {renderGroup(bottom, 'BOTTOM 5', '#ef4444')}
    </div>
  );
}

// ── 30-day history line chart ────────────────────────────────────────
//
// Audit Option C: overlays three faded pillar lines (safety / efficiency
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

function HistoryChart({ driverId }: { driverId: string }) {
  // React Query (Phase E23): caches each driver's 30-day history under a
  // per-driver key so opening the same drawer again is instant.  The four
  // pillar variants are still fanned out in parallel inside one
  // ``queryFn`` so we keep a single cache entry per driver.
  const enc = encodeURIComponent(driverId);
  const { data, isLoading } = useQuery<MergedHistoryPoint[]>({
    queryKey: ['scorecard-history', driverId],
    queryFn: async () => {
      const [total, s, e, c] = await Promise.all([
        apiJSONSlow<ScoreHistoryResponse>(`/safety/scorecards/history?driver_id=${enc}&days=30`),
        apiJSONSlow<ScoreHistoryResponse>(`/safety/scorecards/history?driver_id=${enc}&days=30&pillar=safety`).catch(() => null),
        apiJSONSlow<ScoreHistoryResponse>(`/safety/scorecards/history?driver_id=${enc}&days=30&pillar=efficiency`).catch(() => null),
        apiJSONSlow<ScoreHistoryResponse>(`/safety/scorecards/history?driver_id=${enc}&days=30&pillar=compliance`).catch(() => null),
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
        <CartesianGrid stroke="#374151" strokeDasharray="3 3" />
        <XAxis dataKey="date" tick={{ fill: '#9ca3af', fontSize: 10 }}
               tickFormatter={(d: string) => d.slice(5)} />
        <YAxis domain={[0, 100]} tick={{ fill: '#9ca3af', fontSize: 10 }} />
        <Tooltip
          contentStyle={{ background: '#111827', border: '1px solid #374151', borderRadius: 8, color: '#f9fafb' }} />
        {/* Faded pillar lines underneath the bold total */}
        <Line type="monotone" dataKey="safety"     name="🛡 Safety"
              stroke="#ef4444" strokeWidth={1.25} strokeOpacity={0.55}
              dot={false} connectNulls={false} isAnimationActive={false} />
        <Line type="monotone" dataKey="efficiency" name="⚡ Efficiency"
              stroke="#22c55e" strokeWidth={1.25} strokeOpacity={0.55}
              dot={false} connectNulls={false} isAnimationActive={false} />
        <Line type="monotone" dataKey="compliance" name="🛠 Compliance"
              stroke="#3b82f6" strokeWidth={1.25} strokeOpacity={0.55}
              dot={false} connectNulls={false} isAnimationActive={false} />
        {/* Bold composite total on top */}
        <Line type="monotone" dataKey="total" name="Total"
              stroke="#f9fafb" strokeWidth={2.25} dot={{ r: 2 }}
              isAnimationActive={false} />
        <ReferenceLine y={70} stroke="#6b7280" strokeDasharray="3 3" />
        {showPillarMarker && pillarStartDate && (
          <ReferenceLine x={pillarStartDate} stroke="#a78bfa"
            strokeDasharray="2 4"
            label={{ value: 'pillars', position: 'top', fontSize: 9, fill: '#a78bfa' }}
          />
        )}
      </LineChart>
    </ResponsiveContainer>
  );
}

// ── Bonus / Penalty list row ─────────────────────────────────────────

function BreakdownRow({ event }: { event: ScoreEventBreakdown }) {
  const isBonus = event.kind === 'bonus';
  const color   = isBonus ? '#22c55e' : '#ef4444';
  return (
    <li className="flex items-start gap-2 py-1.5 border-b border-border/50 last:border-0 text-xs">
      <span
        className="shrink-0 mt-0.5 px-1.5 py-0.5 rounded text-[10px] font-semibold"
        style={{ background: `${categoryColor(event.category)}22`, color: categoryColor(event.category) }}
        title={event.category}
      >
        {CATEGORY_META[event.category]?.icon ?? '•'}
      </span>
      <div className="flex-1 min-w-0">
        <div className="flex items-center gap-1.5">
          <span className="truncate">{event.label}</span>
          {event.source && <span className="text-[10px] opacity-60" title="data source">{event.source}</span>}
        </div>
        {event.occurrences > 1 && (
          <span className="text-[10px] text-muted-foreground">×{event.occurrences}</span>
        )}
      </div>
      <span className="font-bold tabular-nums shrink-0" style={{ color }}>
        {isBonus ? '+' : ''}{event.points}
      </span>
    </li>
  );
}

// ── Sparkline (Phase F) ──────────────────────────────────────────────
//
// Tiny inline SVG.  Renders nothing for fewer than two snapshots so a
// single-day series doesn't draw a misleading flat line.

function Sparkline({ values, width = 80, height = 24 }: {
  values: number[]; width?: number; height?: number;
}) {
  if (!values || values.length < 2) {
    return <span className="text-[10px] text-muted-foreground">—</span>;
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
  const trendColor = last >= first ? '#22c55e' : '#ef4444';
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

function makeColumns(anyDriverPaired: boolean): AnyColumn[] {
  return [
  {
    // Phase F: vehicle-first table.  ``driver_name`` carries the truck
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
            <span className="text-[11px] text-muted-foreground">
              {r.paired_driver_name ? '👤' : '🪪'} {inlineDriver}
            </span>
          )}
          {isVehicle && !inlineDriver && anyDriverPaired && (
            <span className="text-[10px] italic text-muted-foreground">no driver paired</span>
          )}
        </div>
      );
    },
  },
  { key: 'company',     label: 'Company', sortable: true },
  {
    key: 'score', label: 'Score', sortable: true,
    render: (v) => <ScoreBadge score={v as number} />,
  },
  {
    // Phase F sparkline column — last ~14 daily totals.  Sortable by
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
              className="text-[10px] font-semibold tabular-nums"
              style={{ color: delta >= 0 ? '#22c55e' : '#ef4444' }}
            >
              {delta >= 0 ? '▲' : '▼'} {Math.abs(delta)}
            </span>
          )}
        </div>
      );
    },
  },
  {
    // Pillar mini-bars (Audit Option C). Only renders content when the
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
              return <span key={k} className="text-[10px] text-muted-foreground italic">n/a</span>;
            }
            const pct = p.cap ? Math.round((p.subtotal / p.cap) * 100) : 0;
            const perfColor = scoreColor(pct);
            return (
              <span key={k}
                className="text-[10px] font-mono tabular-nums px-1.5 py-0.5 rounded"
                style={{ background: `${perfColor}22`, color: perfColor }}
                title={`${k}: ${p.subtotal}/${p.cap} (${pct}%)`}>
                {abbr} {pct}
              </span>
            );
          })}
          {r.insufficient_data && (
            <span className="text-[10px] text-amber-600 dark:text-amber-400" title="insufficient drive time">⚠</span>
          )}
        </div>
      );
    },
  },
  {
    key: 'bonus_total', label: 'Bonuses', sortable: true,
    render: (v) => <span className="text-green-600 dark:text-green-400 font-medium">+{v as number}</span>,
  },
  {
    key: 'penalty_total', label: 'Penalties', sortable: true,
    render: (v) => <span className="text-red-600 dark:text-red-400 font-medium">{v as number}</span>,
  },
  ];
}

// ── Page ─────────────────────────────────────────────────────────────

export default function Scorecards() {
  const [days, setDays]       = useState(7);
  const [error, setError]     = useState('');
  const [detail, setDetail]   = useState<CompositeScorecard | null>(null);
  // Audit Option C — pillar filter chips replace the old category chips.
  // ``all`` shows the full table; otherwise we sort by the chosen pillar's
  // subtotal/cap percentage so the worst (or best) drivers in that pillar
  // bubble to the top.
  type PillarFilter = 'all' | 'safety' | 'efficiency' | 'compliance';
  const [pillarFilter, setPillarFilter] = useState<PillarFilter>('all');

  // Phase F: vehicle-only view.  The legacy driver/truck toggle has been
  // retired \u2014 telematics data is per-vehicle by nature, and drivers
  // (when known) are rendered inline next to their truck name.  The
  // backend default already flipped to ``vehicle``; we still pass the
  // explicit query param so behaviour is unambiguous regardless of any
  // residual per-tenant ``KEY_SCORECARD_DEFAULT_SUBJECT`` override.
  const compositeUrl = `/safety/scorecards/composite?days=${days}&subject=vehicle`;
  const {
    data: composite,
    isLoading,
    error: queryError,
  } = useQuery<CompositeScorecardsResponse>({
    queryKey: ['scorecards-composite', days, 'vehicle'],
    queryFn: () => apiJSONSlow<CompositeScorecardsResponse>(compositeUrl),
    placeholderData: (prev) => prev,
  });

  const cards = composite?.scorecards ?? [];
  const loading = isLoading && !composite;

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

  if (error && cards.length === 0) return <p className="text-destructive">{error}</p>;

  // CSV export (audit L1) — driven from the already-loaded cards array so
  // it reflects the current days filter without an extra API round-trip.
  // Audit Option C: append pillar+exposure columns when the new shape is
  // present (mirrors capabilities/reporting/csv_generators.py).
  function exportCsv() {
    const cardsForExport = pillarFilter === 'all' ? cards : displayCards;
    const hasPillars = cardsForExport.some((c) => c.pillars);
    const headers = [
      'driver_id', 'driver_name', 'company', 'score', 'grade',
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
    const lines = [headers.join(',')];
    for (const c of cardsForExport) {
      const i = c.inputs;
      const row: unknown[] = [
        c.driver_id, c.driver_name, c.company || '', c.score, scoreGrade(c.score),
        c.base, c.bonus_total, c.penalty_total,
        i.miles, i.mpg, i.drive_hours, i.idle_hours,
        i.eco_pct, i.overspeed_min, i.anticipatory_braking_pct,
      ];
      if (hasPillars) {
        const ps = c.pillars?.safety;
        const pe = c.pillars?.efficiency;
        const pc = c.pillars?.compliance;
        row.push(
          ps?.has_data ? ps.subtotal : '',
          pe?.has_data ? pe.subtotal : '',
          pc?.has_data ? pc.subtotal : '',
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
      <div className="flex items-center justify-between mb-6">
        <div>
          <h1 className="text-2xl font-bold">Vehicle Scorecards</h1>
          <p className="text-xs text-muted-foreground mt-1">
            Composite 0-100 score per truck — driver shown when paired · 🅢 Samsara · 🅘 Internal · 🅜 Manual
          </p>
        </div>
        <div className="flex items-center gap-2">
          {loading && cards.length > 0 && (
            <span className="text-[10px] text-muted-foreground animate-pulse">refreshing…</span>
          )}
          <button
            type="button"
            onClick={exportCsv}
            disabled={cards.length === 0}
            className="text-xs px-3 py-2 rounded border border-border bg-muted hover:bg-muted/70 disabled:opacity-40 disabled:cursor-not-allowed"
            title="Download current scorecards as CSV"
          >
            ⬇ CSV
          </button>
          <select
            value={days}
            onChange={(e) => setDays(Number(e.target.value))}
            className="bg-muted border border-border rounded px-3 py-2 text-sm text-foreground/80"
          >
            {[7, 14, 30, 60, 90].map((d) => (
              <option key={d} value={d}>{d} days</option>
            ))}
          </select>
        </div>
      </div>

      {/* KPI strip */}
      {cards.length > 0 && (
        <div className="grid grid-cols-2 md:grid-cols-4 gap-4 mb-6">
          <KpiCard
            label="Active Trucks"
            value={cards.length.toString()}
            sub="any drive activity"
          />
          <KpiCard
            label="Avg Score"
            value={stats.avgScore.toString()}
            sub={scoreGrade(stats.avgScore)}
            color={scoreColor(stats.avgScore)}
          />
          <KpiCard
            label="Top Performers (≥85)"
            value={stats.topPerformers.toString()}
            color="#22c55e"
          />
          <KpiCard
            label="At-Risk (<55)"
            value={stats.atRisk.toString()}
            color={stats.atRisk > 0 ? '#ef4444' : '#6b7280'}
          />
        </div>
      )}

      {loading && cards.length === 0 ? (
        <p className="text-muted-foreground">Loading…</p>
      ) : (
        <>
          {cards.length > 0 && (
            <div className="grid grid-cols-1 lg:grid-cols-2 gap-4 mb-6">
              <div className="bg-card border border-border rounded-xl p-5">
                <p className="text-sm font-medium mb-3">Score Distribution</p>
                <ScoreDistribution cards={cards} />
              </div>
              <div className="bg-card border border-border rounded-xl p-5">
                <p className="text-sm font-medium mb-3">Top vs Bottom 5</p>
                <TopBottomChart cards={cards} />
              </div>
            </div>
          )}
          {/* Audit Option C — pillar filter chips */}
          {cards.length > 0 && cards.some((c) => c.pillars) && (
            <div className="flex items-center gap-2 mb-3 flex-wrap">
              <span className="text-xs text-muted-foreground mr-1">Filter by pillar:</span>
              {([
                { k: 'all',        label: 'All',        color: '#6b7280' },
                { k: 'safety',     label: '🛡 Safety',     color: '#ef4444' },
                { k: 'efficiency', label: '⚡ Efficiency', color: '#22c55e' },
                { k: 'compliance', label: '🛠 Compliance', color: '#3b82f6' },
              ] as { k: PillarFilter; label: string; color: string }[]).map((c) => {
                const active = pillarFilter === c.k;
                return (
                  <button
                    key={c.k}
                    type="button"
                    onClick={() => setPillarFilter(c.k)}
                    className={`text-xs px-3 py-1 rounded-full border transition ${
                      active
                        ? 'border-transparent text-white'
                        : 'border-border text-foreground/70 hover:bg-muted'
                    }`}
                    style={active ? { background: c.color } : undefined}
                  >
                    {c.label}
                  </button>
                );
              })}
              {pillarFilter !== 'all' && (
                <span className="text-[10px] text-muted-foreground ml-2">
                  ⚠ worst-first · {displayCards.length} trucks shown
                </span>
              )}
            </div>
          )}
          <DataTable
            columns={makeColumns(cards.some((c) => !!(c.paired_driver_name || c.assigned_driver_name)))}
            data={displayCards as unknown as Record<string, unknown>[]}
            searchKey="driver_name"
            searchPlaceholder="Search truck #…"
            stickyHeader="65vh"
            onRowClick={(row) => setDetail(row as unknown as CompositeScorecard)}
          />
        </>
      )}

      {detail && (
        <DetailDrawer
          card={detail}
          rank={cards.findIndex((c) => c.driver_id === detail.driver_id) + 1}
          total={cards.length}
          fleetAvg={stats.avgScore}
          onClose={() => setDetail(null)}
        />
      )}
    </div>
  );
}

// ── Sub-components ───────────────────────────────────────────────────

function KpiCard({ label, value, sub, color }: { label: string; value: string; sub?: string; color?: string }) {
  return (
    <div className="bg-card border border-border rounded-lg p-4">
      <p className="text-xs text-muted-foreground">{label}</p>
      <div className="flex items-baseline gap-2">
        <p className="text-2xl font-bold" style={color ? { color } : undefined}>{value}</p>
        {sub && <p className="text-sm font-semibold opacity-70" style={color ? { color } : undefined}>{sub}</p>}
      </div>
    </div>
  );
}

function DetailDrawer({ card, rank, total, fleetAvg, onClose }: {
  card: CompositeScorecard;
  rank: number;
  total: number;
  fleetAvg: number;
  onClose: () => void;
}) {
  // Audit L4: keyboard a11y — close on Esc and announce as a modal dialog.
  useEffect(() => {
    function onKey(e: KeyboardEvent) {
      if (e.key === 'Escape') onClose();
    }
    document.addEventListener('keydown', onKey);
    return () => document.removeEventListener('keydown', onKey);
  }, [onClose]);

  const delta = card.score - fleetAvg;

  return (
    <div className="fixed inset-0 bg-black/60 z-50 flex justify-end" onClick={onClose}>
      <div
        role="dialog"
        aria-modal="true"
        aria-label={`Scorecard detail for ${card.subject_name || card.driver_name}`}
        className="w-[420px] bg-card border-l border-border p-6 overflow-y-auto"
        onClick={(e) => e.stopPropagation()}
      >
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
            aria-label="Close detail panel"
            className="text-muted-foreground hover:text-foreground text-xl"
          >✕</button>
        </div>

        {/* Gauge + rank + fleet delta */}
        <div className="flex items-center gap-5 mb-5">
          <ScoreGauge score={card.score} />
          <div className="flex-1 text-xs space-y-1.5">
            <div className="flex items-center gap-2">
              <span className="text-muted-foreground">Rank</span>
              <span className="font-semibold tabular-nums">#{rank} of {total}</span>
            </div>
            <div className="flex items-center gap-2">
              <span className="text-muted-foreground">Fleet avg</span>
              <span className="font-semibold tabular-nums">{fleetAvg}</span>
              <span
                className="text-[11px] font-bold tabular-nums"
                style={{ color: delta >= 0 ? '#22c55e' : '#ef4444' }}
              >
                {delta >= 0 ? '+' : ''}{delta}
              </span>
            </div>
          </div>
        </div>

        {/* Pillar breakdown — progress bars (Audit Option C) */}
        {card.pillars ? (
          <div className="mb-5 space-y-2">
            {(['safety', 'efficiency', 'compliance'] as const).map((k) => {
              const p = card.pillars![k];
              const pct = p.cap && p.has_data ? Math.round((p.subtotal / p.cap) * 100) : 0;
              const perfColor = p.has_data ? scoreColor(pct) : '#6b7280';
              const LABELS: Record<string, string> = { safety: '🛡 Safety', efficiency: '⚡ Efficiency', compliance: '🛠 Compliance' };
              return (
                <div key={k}>
                  <div className="flex justify-between text-[11px] mb-0.5">
                    <span className="text-muted-foreground">{LABELS[k]}</span>
                    {p.has_data
                      ? <span className="font-semibold tabular-nums" style={{ color: perfColor }}>{p.subtotal}/{p.cap}</span>
                      : <span className="italic text-muted-foreground">n/a</span>}
                  </div>
                  <div className="h-1.5 rounded-full bg-muted overflow-hidden">
                    <div
                      className="h-full rounded-full transition-all"
                      style={{ width: `${pct}%`, background: perfColor }}
                    />
                  </div>
                </div>
              );
            })}
          </div>
        ) : (
          /* Legacy: Base+Bonuses+Penalties math when no pillar data */
          <div className="mb-5 text-xs space-y-1">
            <ScoreMath label="Base"      value={card.base.toString()} />
            <ScoreMath label="Bonuses"   value={`+${card.bonus_total}`} positive />
            <ScoreMath label="Penalties" value={`${card.penalty_total}`} negative />
            <div className="border-t border-border pt-1 mt-1 flex justify-between font-semibold">
              <span>Total</span>
              <span style={{ color: scoreColor(card.score) }}>{card.score}</span>
            </div>
          </div>
        )}

        {card.insufficient_data && (
          <p className="mb-4 text-[11px] italic text-amber-600 dark:text-amber-400">
            ⚠ insufficient drive time this window — excluded from rankings
          </p>
        )}

        {/* History trend */}
        <div className="mb-6">
          <p className="text-[10px] font-semibold text-muted-foreground tracking-wide mb-1">
            30-DAY TREND
          </p>
          <HistoryChart driverId={card.driver_id || card.driver_name} />
        </div>

        {/* Bonus / Penalty breakdown */}
        <div className="grid grid-cols-1 gap-4 mb-6">
          <div>
            <p className="text-[10px] font-semibold text-green-600 dark:text-green-400 tracking-wide mb-1">
              ✓ BONUSES ({card.bonuses.length})
            </p>
            {card.bonuses.length ? (
              <ul>{card.bonuses.map((e) => <BreakdownRow key={e.rule_id} event={e} />)}</ul>
            ) : (
              <p className="text-xs text-muted-foreground italic">no bonuses fired this window</p>
            )}
          </div>
          <div>
            <p className="text-[10px] font-semibold text-red-600 dark:text-red-400 tracking-wide mb-1">
              ✗ PENALTIES ({card.penalties.length})
            </p>
            {card.penalties.length ? (
              <ul>{card.penalties.map((e) => <BreakdownRow key={e.rule_id} event={e} />)}</ul>
            ) : (
              <p className="text-xs text-muted-foreground italic">no penalties fired this window</p>
            )}
          </div>
        </div>

        {/* Raw inputs (Samsara) — preserved per plan */}
        <div className="border-t border-border pt-3">
          <p className="text-[10px] font-semibold text-muted-foreground tracking-wide mb-2">
            INPUTS {card.inputs._source && <span className="opacity-60">{card.inputs._source}</span>}
          </p>
          {card.inputs.miles === 0 && card.inputs.drive_hours > 0 && (
            <p className="text-[11px] text-amber-600 dark:text-amber-400 mb-2">
              ⚠ Odometer data missing — miles and MPG may be inaccurate
            </p>
          )}
          <dl className="grid grid-cols-2 gap-x-4 gap-y-1 text-xs">
            <Stat label="Miles"        value={card.inputs.miles.toLocaleString()} />
            <Stat label="MPG"          value={`${card.inputs.mpg}`} />
            <Stat label="Drive"        value={`${card.inputs.drive_hours}h (${card.inputs.drive_pct}%)`} />
            <Stat label="Idle"         value={`${card.inputs.idle_hours}h (${card.inputs.idle_pct}%)`} />
            <Stat label="Eco %"        value={`${card.inputs.eco_pct}%`} />
            <Stat label="Overspeed"    value={`${card.inputs.overspeed_min} min`} />
            <Stat label="Coast"        value={`${card.inputs.coast_min} min`} />
            <Stat label="Cruise"       value={`${card.inputs.cruise_min} min`} />
            <Stat label="Antic. Brake" value={`${card.inputs.anticipatory_braking_pct}%`} />
          </dl>
        </div>
      </div>
    </div>
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
  const cls = positive ? 'text-green-600 dark:text-green-400'
            : negative ? 'text-red-600 dark:text-red-400'
            : 'text-foreground';
  return (
    <div className="flex justify-between">
      <span className="text-muted-foreground">{label}</span>
      <span className={`font-semibold tabular-nums ${cls}`}>{value}</span>
    </div>
  );
}
