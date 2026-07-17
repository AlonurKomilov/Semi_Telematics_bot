/**
 * Capacity — "how much server is left, and when do we upgrade?"
 *
 * Reads the sampler's history (60s minutes → hourly avg+PEAK tier):
 * live gauges with the 70/85% planning bands, a 24h fine series, the
 * 30d peak trend, today's request split by interface surface, the
 * headroom estimate, and the per-account growth table.  Peaks, never
 * averages — capacity planning is about the worst hour, not the
 * typical one.
 *
 * Charts are hand-rolled SVG (single series each, one hue, hover
 * crosshair) — the console deliberately has no chart dependency.
 */
import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { Link } from 'react-router-dom';
import { apiJSON } from '../api/client';
import RangeTabs from '../components/RangeTabs';

// ── wire types ─────────────────────────────────────────────────

interface MetricSample {
  ts?: string; hour?: string;
  cpu_pct?: number | null; peak_cpu_pct?: number | null; avg_cpu_pct?: number | null;
  mem_pct?: number | null; peak_mem_pct?: number | null; avg_mem_pct?: number | null;
  disk_pct?: number | null; disk_used_gb?: number | null;
  disk_busy_pct?: number | null; peak_disk_busy_pct?: number | null;
  requests_min?: number | null; peak_requests_min?: number | null; avg_requests_min?: number | null;
  queue_depth?: number | null; peak_queue_depth?: number | null;
  pg_connections?: number | null; peak_pg_connections?: number | null;
  pg_size_mb?: number | null; redis_mb?: number | null;
  mem_used_mb?: number | null; load1?: number | null; peak_load1?: number | null;
  net_rx_kbps?: number | null; net_tx_kbps?: number | null;
  vehicles_active?: number | null; accounts_active?: number | null;
}

interface Headroom {
  watch_pct: number; act_pct: number;
  window_hours: number; confidence: 'ok' | 'low';
  binding_resource: string | null;
  peak_cpu_pct: number | null; peak_mem_pct: number | null;
  vehicles_now: number | null; vehicles_at_watch: number | null;
  disk_days_to_act: number | null;
}

interface Overview {
  latest: MetricSample | null;
  headroom: Headroom;
  hourly_count: number;
}

interface Breakdown {
  days: number;
  surfaces: Record<string, number>;
  features: Record<string, number>;
}

interface Series { window: string; tier: 'minute' | 'hourly'; points: MetricSample[] }

interface AccountsUsage {
  days: number;
  rows: { day: string; account_id: number; requests: number }[];
  accounts: { account_id: number; name: string; vehicles: number }[];
}

// ── tokens ─────────────────────────────────────────────────────

const SERIES_HUE = '#38bdf8';           // sky-400 — the ONE series hue
const BAND_WATCH = '#f59e0b';           // warn   — 70% planning line
const BAND_ACT = '#ef4444';             // danger — 85% upgrade line

function toneFor(pct: number | null | undefined, watch = 70, act = 85): string {
  if (pct == null) return 'text-slate-500';
  return pct >= act ? 'text-danger' : pct >= watch ? 'text-warn' : 'text-ok';
}

function barTone(pct: number | null | undefined, watch = 70, act = 85): string {
  if (pct == null) return 'bg-slate-700';
  return pct >= act ? 'bg-danger' : pct >= watch ? 'bg-warn' : 'bg-ok';
}

// ── gauges ─────────────────────────────────────────────────────

function Gauge({ label, pct, detail }: { label: string; pct: number | null | undefined; detail: string }) {
  return (
    <div className="bg-slate-900 border border-slate-800 rounded-lg px-4 py-3">
      <div className="flex items-baseline justify-between mb-1.5">
        <span className="text-xs text-slate-400">{label}</span>
        <span className={`text-lg font-semibold tabular-nums ${toneFor(pct)}`}>
          {pct == null ? '—' : `${Math.round(pct)}%`}
        </span>
      </div>
      {/* Track with the 70/85 planning bands as tick marks. */}
      <div className="relative h-2 rounded-full bg-slate-800 overflow-hidden">
        <div
          className={`absolute inset-y-0 left-0 rounded-full ${barTone(pct)}`}
          style={{ width: `${Math.min(Math.max(pct ?? 0, 0), 100)}%` }}
        />
        <div className="absolute inset-y-0 w-px bg-slate-600" style={{ left: '70%' }} />
        <div className="absolute inset-y-0 w-px bg-slate-500" style={{ left: '85%' }} />
      </div>
      <p className="text-[11px] text-slate-500 mt-1.5">{detail}</p>
    </div>
  );
}

function StatTile({ label, value, hint }: { label: string; value: string; hint?: string }) {
  return (
    <div className="bg-slate-900 border border-slate-800 rounded-lg px-4 py-3">
      <p className="text-xs text-slate-400">{label}</p>
      <p className="text-lg font-semibold text-slate-100 tabular-nums mt-0.5">{value}</p>
      {hint && <p className="text-[11px] text-slate-500 mt-0.5">{hint}</p>}
    </div>
  );
}

// ── area chart (single series, hover crosshair) ────────────────

interface ChartPoint { t: string; v: number }

function AreaChart({
  points, unit, height = 120, bands,
}: {
  points: ChartPoint[]; unit: string; height?: number;
  /** Horizontal threshold lines (percent charts pass 70/85). */
  bands?: { at: number; color: string }[];
}) {
  const ref = useRef<HTMLDivElement>(null);
  const [hover, setHover] = useState<{ i: number; xPct: number } | null>(null);
  const W = 600, H = height, PAD = 4;

  const { path, area, max } = useMemo(() => {
    if (!points.length) return { path: '', area: '', max: 1 };
    const vmax = Math.max(...points.map((p) => p.v), 1);
    // Percent charts pin the scale to 100 so the 70/85 bands are honest.
    const m = bands ? Math.max(vmax, 100) : vmax * 1.15;
    const x = (i: number) => PAD + (i / Math.max(points.length - 1, 1)) * (W - PAD * 2);
    const y = (v: number) => H - PAD - (v / m) * (H - PAD * 2);
    const line = points.map((p, i) => `${i ? 'L' : 'M'}${x(i).toFixed(1)},${y(p.v).toFixed(1)}`).join('');
    return {
      path: line,
      area: `${line}L${x(points.length - 1).toFixed(1)},${H - PAD}L${x(0).toFixed(1)},${H - PAD}Z`,
      max: m,
    };
  }, [points, bands, H]);

  const onMove = (e: React.MouseEvent) => {
    const rect = ref.current?.getBoundingClientRect();
    if (!rect || !points.length) return;
    const frac = Math.min(Math.max((e.clientX - rect.left) / rect.width, 0), 1);
    setHover({ i: Math.round(frac * (points.length - 1)), xPct: frac * 100 });
  };

  if (!points.length) {
    return <div className="h-[120px] flex items-center justify-center text-xs text-slate-600">No samples yet</div>;
  }
  const hp = hover ? points[hover.i] : null;
  return (
    <div ref={ref} className="relative" onMouseMove={onMove} onMouseLeave={() => setHover(null)}>
      <svg viewBox={`0 0 ${W} ${H}`} preserveAspectRatio="none" className="w-full block" style={{ height: H }}>
        {bands?.map((b) => (
          <line
            key={b.at}
            x1={0} x2={W}
            y1={H - PAD - (b.at / max) * (H - PAD * 2)}
            y2={H - PAD - (b.at / max) * (H - PAD * 2)}
            stroke={b.color} strokeWidth={1} strokeDasharray="4 4" opacity={0.5}
          />
        ))}
        <path d={area} fill={SERIES_HUE} opacity={0.12} />
        <path d={path} fill="none" stroke={SERIES_HUE} strokeWidth={2} strokeLinejoin="round" />
      </svg>
      {hover && hp && (
        <>
          <div className="absolute inset-y-0 w-px bg-slate-600 pointer-events-none" style={{ left: `${hover.xPct}%` }} />
          <div
            className="absolute -top-1 -translate-y-full bg-slate-800 border border-slate-700 rounded px-2 py-1 text-[11px] text-slate-200 whitespace-nowrap pointer-events-none"
            style={{ left: `${Math.min(hover.xPct, 82)}%` }}
          >
            {hp.t} · <span className="font-semibold tabular-nums">{hp.v}{unit}</span>
          </div>
        </>
      )}
    </div>
  );
}

// ── breakdown panel (shared by interface + feature) ────────────

function BreakdownPanel({
  title, windowLabel, rows, empty,
}: {
  title: string; windowLabel: string; rows: [string, number][]; empty: string;
}) {
  const max = Math.max(...rows.map(([, v]) => v), 1);
  return (
    <div className="bg-slate-900 border border-slate-800 rounded-lg p-4">
      <h2 className="text-xs font-semibold tracking-wider text-slate-400 uppercase mb-3">
        {title} <span className="text-slate-600 normal-case tracking-normal">· {windowLabel}</span>
      </h2>
      {rows.length === 0 && <p className="text-xs text-slate-600">{empty}</p>}
      <div className="space-y-2">
        {rows.map(([name, count]) => (
          <div key={name} className="flex items-center gap-2">
            <span className="w-28 truncate text-xs text-slate-300" title={name}>{name}</span>
            <div className="flex-1 h-2 rounded-full bg-slate-800 overflow-hidden">
              <div className="h-full rounded-full" style={{
                width: `${(count / max) * 100}%`, background: SERIES_HUE, opacity: 0.85,
              }} />
            </div>
            <span className="w-16 text-right text-xs text-slate-400 tabular-nums">
              {count.toLocaleString()}
            </span>
          </div>
        ))}
      </div>
    </div>
  );
}

// ── page ───────────────────────────────────────────────────────

const CHART_METRICS = [
  { key: 'cpu', label: 'CPU', unit: '%', pct: true },
  { key: 'mem', label: 'Memory', unit: '%', pct: true },
  { key: 'req', label: 'Requests / min', unit: '', pct: false },
] as const;
type ChartMetric = (typeof CHART_METRICS)[number]['key'];

function pickValue(p: MetricSample, metric: ChartMetric, tier: 'minute' | 'hourly'): number | null {
  if (metric === 'cpu') return (tier === 'minute' ? p.cpu_pct : p.peak_cpu_pct) ?? null;
  if (metric === 'mem') return (tier === 'minute' ? p.mem_pct : p.peak_mem_pct) ?? null;
  return (tier === 'minute' ? p.requests_min : p.peak_requests_min) ?? null;
}

const WIN_DAYS = { '24h': 1, '7d': 7, '30d': 30 } as const;
const WIN_LABEL = { '24h': 'today', '7d': 'last 7d', '30d': 'last 30d' } as const;

export default function CapacityPage() {
  const [overview, setOverview] = useState<Overview | null>(null);
  const [series, setSeries] = useState<Series | null>(null);
  const [breakdown, setBreakdown] = useState<Breakdown | null>(null);
  const [usage, setUsage] = useState<AccountsUsage | null>(null);
  const [win, setWin] = useState<'24h' | '7d' | '30d'>('24h');
  const [metric, setMetric] = useState<ChartMetric>('cpu');
  const [err, setErr] = useState('');

  const load = useCallback(() => {
    apiJSON<Overview>('/system/capacity/overview').then(setOverview)
      .catch((e: unknown) => setErr(e instanceof Error ? e.message : 'failed'));
    apiJSON<AccountsUsage>('/system/capacity/accounts?days=30').then(setUsage).catch(() => {});
  }, []);

  useEffect(() => {
    load();
    const t = setInterval(load, 60_000);   // gauges follow the sampler's own cadence
    return () => clearInterval(t);
  }, [load]);

  // The window selector drives BOTH the trend chart and the request
  // breakdowns; today's slice stays live via the same 60s cadence.
  useEffect(() => {
    const fetchWin = () => {
      apiJSON<Series>(`/system/capacity/series?window=${win}`).then(setSeries).catch(() => {});
      apiJSON<Breakdown>(`/system/capacity/breakdown?days=${WIN_DAYS[win]}`)
        .then(setBreakdown).catch(() => {});
    };
    fetchWin();
    const t = setInterval(fetchWin, 60_000);
    return () => clearInterval(t);
  }, [win]);

  const latest = overview?.latest;
  const hr = overview?.headroom;

  const chartPoints: ChartPoint[] = useMemo(() => {
    if (!series) return [];
    return series.points
      .map((p) => {
        const v = pickValue(p, metric, series.tier);
        const t = (p.ts ?? p.hour ?? '').slice(5).replace('T', ' ');
        return v == null ? null : { t, v: Math.round(v * 10) / 10 };
      })
      .filter((p): p is ChartPoint => p !== null);
  }, [series, metric]);

  // Per-account totals over the window (requests summed across days).
  const accountRows = useMemo(() => {
    if (!usage) return [];
    const total: Record<number, number> = {};
    for (const r of usage.rows) total[r.account_id] = (total[r.account_id] ?? 0) + r.requests;
    return usage.accounts
      .map((a) => ({ ...a, requests: total[a.account_id] ?? 0 }))
      .sort((a, b) => b.requests - a.requests);
  }, [usage]);

  // Feature list can be long — top 10 named, the tail folds into one row.
  const featureRows = useMemo<[string, number][]>(() => {
    const entries = Object.entries(breakdown?.features ?? {}).sort((a, b) => b[1] - a[1]);
    if (entries.length <= 11) return entries;
    const rest = entries.slice(10).reduce((n, [, v]) => n + v, 0);
    return [...entries.slice(0, 10), [`${entries.length - 10} more`, rest]];
  }, [breakdown]);

  const surfaces = Object.entries(breakdown?.surfaces ?? {}).sort((a, b) => b[1] - a[1]);
  const pctMetric = CHART_METRICS.find((m) => m.key === metric)!;

  return (
    <div>
      <header className="mb-4">
        <h1 className="text-lg font-semibold text-slate-100">Capacity</h1>
        <p className="text-xs text-slate-500 mt-0.5">
          Measured peaks, not guesses — the 70% line means "plan the upgrade",
          85% means "upgrade now".  Samples land every 60s.
        </p>
      </header>

      {err && (
        <div className="mb-3 bg-danger/10 border border-danger/40 text-danger text-sm rounded px-3 py-2">{err}</div>
      )}

      {/* First-run: history exists only once the sampler runs — say so
          instead of showing dead gauges right after a deploy. */}
      {!err && overview && !latest && (
        <div className="mb-3 bg-warn/10 border border-warn/40 text-warn text-sm rounded px-3 py-2">
          No samples yet — the capacity sampler starts with the bot service
          (job <span className="font-mono">capacity_sample</span>, every 60s).
          If services were just deployed, restart them and this page fills in
          within a minute; see <Link to="/scheduler" className="underline">Scheduler</Link>.
        </div>
      )}

      {/* ── live gauges ── */}
      <div className="grid grid-cols-2 md:grid-cols-4 gap-3 mb-3">
        <Gauge label="CPU" pct={latest?.cpu_pct} detail={`load ${latest?.load1 ?? '—'}`} />
        <Gauge label="Memory" pct={latest?.mem_pct}
          detail={latest?.mem_used_mb ? `${(latest.mem_used_mb / 1024).toFixed(1)} GB used` : '—'} />
        <Gauge label="Disk space" pct={latest?.disk_pct}
          detail={latest?.disk_used_gb ? `${latest.disk_used_gb} GB · DB ${latest?.pg_size_mb ?? '—'} MB` : '—'} />
        <Gauge label="Disk I/O" pct={latest?.disk_busy_pct}
          detail={`req/min ${latest?.requests_min ?? '—'} · queue ${latest?.queue_depth ?? '—'} · PG conn ${latest?.pg_connections ?? '—'}`} />
      </div>

      {/* ── headroom ── */}
      <div className="grid grid-cols-2 md:grid-cols-4 gap-3 mb-4">
        <StatTile
          label="Vehicles on this server"
          value={String(hr?.vehicles_now ?? '—')}
          hint={`across ${latest?.accounts_active ?? '—'} accounts`}
        />
        <StatTile
          label="Fits before the 70% line"
          value={hr?.vehicles_at_watch != null ? `≈${hr.vehicles_at_watch} vehicles` : 'collecting…'}
          hint={hr?.binding_resource
            ? `binding resource: ${hr.binding_resource} (peak ${Math.round((hr.binding_resource === 'cpu' ? hr.peak_cpu_pct : hr.peak_mem_pct) ?? 0)}%)`
            : 'needs a few days of peaks'}
        />
        <StatTile
          label="7-day peak CPU / RAM"
          value={hr?.peak_cpu_pct != null ? `${Math.round(hr.peak_cpu_pct)}% / ${Math.round(hr.peak_mem_pct ?? 0)}%` : '—'}
          hint={hr?.confidence === 'low' ? `low confidence — ${hr.window_hours}h of history` : 'from hourly peaks'}
        />
        <StatTile
          label="Disk runway"
          value={hr?.disk_days_to_act != null ? `≈${hr.disk_days_to_act} days` : 'collecting…'}
          hint="days until 85% at current growth"
        />
      </div>

      {/* ── trend chart ── */}
      <div className="bg-slate-900 border border-slate-800 rounded-lg p-4 mb-4">
        <div className="flex items-center justify-between mb-3 flex-wrap gap-2">
          <h2 className="text-xs font-semibold tracking-wider text-slate-400 uppercase">
            {pctMetric.label} — {win === '24h' ? 'last 24h (per minute)' : `${win} (hourly peaks)`}
          </h2>
          <div className="flex items-center gap-3">
            <RangeTabs
              tabs={CHART_METRICS.map((m) => ({ value: m.key, label: m.label }))}
              value={metric}
              onChange={setMetric}
            />
            <RangeTabs tabs={['24h', '7d', '30d'] as const} value={win} onChange={setWin} />
          </div>
        </div>
        <AreaChart
          points={chartPoints}
          unit={pctMetric.unit}
          bands={pctMetric.pct
            ? [{ at: 70, color: BAND_WATCH }, { at: 85, color: BAND_ACT }]
            : undefined}
        />
      </div>

      <div className="grid grid-cols-1 md:grid-cols-2 gap-3 mb-3">
        <BreakdownPanel
          title="Requests · by interface"
          windowLabel={WIN_LABEL[win]}
          rows={surfaces}
          empty="No requests metered in this window."
        />
        <BreakdownPanel
          title="Requests · by feature"
          windowLabel={WIN_LABEL[win]}
          rows={featureRows}
          empty="Feature metering starts with the next service restart."
        />
      </div>

      <div className="grid grid-cols-1 gap-3 mb-4">
        {/* ── per-account usage (30d) ── */}
        <div className="bg-slate-900 border border-slate-800 rounded-lg p-4">
          <h2 className="text-xs font-semibold tracking-wider text-slate-400 uppercase mb-3">
            Account usage · last 30 days
          </h2>
          {accountRows.length === 0 && (
            <p className="text-xs text-slate-600">No per-account metering recorded yet.</p>
          )}
          <table className="w-full text-xs">
            <thead>
              <tr className="text-slate-500 text-left">
                <th className="font-normal pb-1.5">Account</th>
                <th className="font-normal pb-1.5 text-right">Vehicles</th>
                <th className="font-normal pb-1.5 text-right">Requests</th>
              </tr>
            </thead>
            <tbody>
              {accountRows.map((a) => (
                <tr key={a.account_id} className="border-t border-slate-800/60">
                  <td className="py-1.5">
                    <Link to={`/accounts/${a.account_id}`} className="text-slate-200 hover:text-white hover:underline">
                      {a.name}
                    </Link>
                  </td>
                  <td className="py-1.5 text-right text-slate-300 tabular-nums">{a.vehicles}</td>
                  <td className="py-1.5 text-right text-slate-300 tabular-nums">{a.requests.toLocaleString()}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </div>

      <p className="text-xs text-slate-600">
        Load model note: 4truck PULLS telemetry from Samsara on schedulers — vehicle growth
        shows up as ingest/CPU load, not inbound request volume.  The headroom estimate uses
        the binding resource's 7-day peak, linear in active vehicles; it firms up as history
        accumulates.  This host also runs services outside 4truck (k3s, docker) — host peaks
        include them.
      </p>
    </div>
  );
}
