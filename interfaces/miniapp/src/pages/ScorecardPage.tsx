/**
 * ScorecardPage — driver-friendly safety scorecard.
 *
 * Layout: pillar ring hero with legend + grade colour, week-change banner,
 * 7d/30d/90d range toggle, per-pillar breakdown, score trend sparkline,
 * rank breakdown (fleet), expandable top wins and losses with magnitude bars.
 */

import { useEffect, useState, useCallback, useMemo, useRef } from 'react';
import { Placeholder } from '@telegram-apps/telegram-ui';
import {
  Icon24ArrowUpOutline,
  Icon24ArrowDownOutline,
  Icon24StatisticsOutline,
  Icon24CheckShieldOutline,
  Icon24SpeedometerMiddleOutline,
  Icon24CheckCircleOutline,
  Icon24StarsOutline,
  Icon24CupOutline,
  Icon24WarningTriangleOutline,
  Icon24ChevronDown,
  Icon24ChevronUp,
} from '@vkontakte/icons';
import { apiJSON, apiFetch, ApiError } from '../api/client';
import { PillarRing } from '../components/PillarRing';
import { ScoreSkeleton } from '../components/Skeleton';
import { MyPaystubs } from '../components/MyPaystubs';
import { MyCoaching } from '../components/MyCoaching';
import { usePullToRefresh } from '../hooks/usePullToRefresh';
import { haptics } from '../hooks/useTelegram';

interface PillarBlock {
  name?: string;
  cap: number;
  subtotal: number;
  bonus_total?: number;
  penalty_total?: number;
  has_data: boolean;
  events?: ScoreEvent[];
}
interface ScoreEvent { rule_id?: string; label: string; points: number; count?: number }
interface Pillars { safety: PillarBlock; efficiency: PillarBlock; compliance: PillarBlock }
interface Scorecard {
  subject_id: string;
  subject_name: string;
  total: number;
  pillars: Pillars;
  bonuses: ScoreEvent[];
  penalties: ScoreEvent[];
  insufficient_data: boolean;
}
interface RankSlot { pos: number; total: number }
interface MyScorecardResp {
  scorecard: Scorecard;
  rank_in_pillar: { safety: RankSlot; efficiency: RankSlot; compliance: RankSlot } | null;
  rank_total: RankSlot | null;
  week_delta: {
    total: number | null;
    safety: number | null;
    efficiency: number | null;
    compliance: number | null;
  };
  account_size: number | null;
  days: number;
}

interface ScoreHistoryPoint { date: string; score: number }

type Range = 7 | 30 | 90;

function fmtDelta(d: number | null): string {
  if (d === null) return '—';
  if (d > 0) return `+${d}`;
  return String(d);
}
function deltaColor(d: number | null): string {
  if (d === null || d === 0) return 'var(--tgui--hint_color)';
  if (d > 0) return 'var(--st-green)';
  return 'var(--st-red)';
}

/** Map a 0-100 score to a grade colour token.
 *
 * Cutoffs (85 / 70 / 55) align with the dashboard's ``scoreColor`` so
 * the same driver doesn't render green here and yellow there. The
 * miniapp design system exposes four hues; the dashboard splits the
 * mid range with a fifth (lime/yellow), but the boundaries themselves
 * are shared.
 */
function gradeColor(score: number): string {
  if (score >= 85) return 'var(--st-green)';
  if (score >= 70) return 'var(--st-blue)';
  if (score >= 55) return 'var(--st-orange)';
  return 'var(--st-red)';
}

/** Count-up hook: animates from 0 to target over ~600 ms on mount/change. */
function useCountUp(target: number): number {
  const [display, setDisplay] = useState(0);
  const raf = useRef<number>(0);
  useEffect(() => {
    const start = performance.now();
    const duration = 600;
    function step(now: number) {
      const t = Math.min(1, (now - start) / duration);
      // Ease-out cubic
      const eased = 1 - Math.pow(1 - t, 3);
      setDisplay(Math.round(eased * target));
      if (t < 1) raf.current = requestAnimationFrame(step);
    }
    raf.current = requestAnimationFrame(step);
    return () => cancelAnimationFrame(raf.current);
  }, [target]);
  return display;
}

/** Roles that are not expected to have a personal scorecard. */
const MANAGEMENT_ROLES = new Set(['owner', 'admin', 'fleet', 'safety', 'fleet_manager']);

export function ScorecardPage({ userRole = 'driver' }: { userRole?: string }) {
  const [data, setData]         = useState<MyScorecardResp | null>(null);
  const [loading, setLoading]   = useState(true);
  const [error, setError]       = useState<string | null>(null);
  const [noDataForRange, setNoDataForRange] = useState(false);
  const [range, setRange]       = useState<Range>(7);
  const [history, setHistory]   = useState<ScoreHistoryPoint[]>([]);
  const [winsExpanded, setWinsExpanded]     = useState(false);
  const [lossesExpanded, setLossesExpanded] = useState(false);

  // Per-load token: incremented on every load() call.  Both the main
  // request and the (fire-and-forget) history fetch capture this value
  // and short-circuit if a newer load has started — prevents a slow
  // 7-day history from overwriting the chart after the user has
  // already toggled to 30/90.
  const loadIdRef = useRef(0);

  // Mirror userRole in a ref so it can be read inside `load()` without
  // forcing the callback to re-create on prop change (which would
  // invalidate the load-token and cancel itself on the very next render).
  const userRoleRef = useRef(userRole);
  userRoleRef.current = userRole;

  const load = useCallback(async (days: Range) => {
    const myId = ++loadIdRef.current;
    setError(null);
    setNoDataForRange(false);
    try {
      const resp = await apiJSON<MyScorecardResp>(`/api/safety/scorecards/me?days=${days}`);
      if (myId !== loadIdRef.current) return;   // a newer load() has started
      setData(resp);
      haptics.success();
      // Fetch score history — uses same `days` as the range toggle.
      const sid = resp.scorecard?.subject_id;
      if (sid) {
        apiJSON<{ history: ScoreHistoryPoint[] }>(
          `/api/safety/scorecards/history?subject_id=${encodeURIComponent(sid)}&days=${days}`
        )
          .then(r => {
            if (myId !== loadIdRef.current) return;
            setHistory(r.history ?? []);
          })
          .catch(() => { /* warehouse may be cold */ });
      }
    } catch (e) {
      if (myId !== loadIdRef.current) return;
      const apiErr = e as ApiError;
      const status = apiErr.status;
      if (status === 404) {
        const msg = apiErr.message ?? '';
        // "No driver/truck assignment for caller" — management role with no vehicle.
        // Show a permanent informational error instead of a range-period hint.
        if (msg.toLowerCase().includes('assignment') || msg.toLowerCase().includes('no driver')) {
          setError(
            MANAGEMENT_ROLES.has(userRoleRef.current)
              ? 'Scorecards are available for drivers with an assigned vehicle. Fleet-wide scoring is not available in this view.'
              : 'No vehicle assigned to your account. Contact your admin.'
          );
        } else {
          // No events in this time window — show inline hint, keep any previous data visible.
          setNoDataForRange(true);
        }
      } else {
        setError(e instanceof Error ? e.message : 'Failed to load scorecard');
      }
    }
  }, []);

  useEffect(() => {
    setLoading(true);
    load(range).finally(() => setLoading(false));
  }, [range, load]);

  const ptr = usePullToRefresh<HTMLDivElement>({ onRefresh: () => load(range) });

  const allWins = useMemo(
    () => [...(data?.scorecard.bonuses ?? [])].sort((a, b) => Math.abs(b.points) - Math.abs(a.points)),
    [data],
  );
  const allLosses = useMemo(
    () => [...(data?.scorecard.penalties ?? [])].sort((a, b) => Math.abs(b.points) - Math.abs(a.points)),
    [data],
  );
  const sortedWins   = winsExpanded   ? allWins   : allWins.slice(0, 3);
  const sortedLosses = lossesExpanded ? allLosses : allLosses.slice(0, 3);
  const maxLossMag = useMemo(() => Math.max(1, ...allLosses.map(l => Math.abs(l.points))), [allLosses]);
  const maxWinMag  = useMemo(() => Math.max(1, ...allWins.map(w => Math.abs(w.points))),  [allWins]);

  // ── Count-up hook for the centre score ──────────────────────────
  // Declared before early returns so hook order is stable.
  const animatedTotal = useCountUp(data?.scorecard.total ?? 0);

  if (loading) return <ScoreSkeleton />;

  if (error || !data) {
    return (
      <div className="centered">
        <Placeholder
          header="Scorecard unavailable"
          description={error ?? 'Try again in a moment.'}
          action={<button className="retry-btn" onClick={() => { setLoading(true); load(range).finally(() => setLoading(false)); }}>Retry</button>}
        >
          <Icon24StatisticsOutline width={48} height={48} style={{ opacity: 0.4 }} />
        </Placeholder>
      </div>
    );
  }

  if (noDataForRange && !data) {
    return (
      <div className="centered">
        <Placeholder
          header="No data yet"
          description="No drive data found for this period. Try a shorter range or check back after your first trip."
          action={<button className="retry-btn" onClick={() => { setNoDataForRange(false); setRange(7); }}>Show 7 days</button>}
        >
          <Icon24StatisticsOutline width={48} height={48} style={{ opacity: 0.4 }} />
        </Placeholder>
      </div>
    );
  }


  const { scorecard: sc, rank_in_pillar: rank, rank_total: rt, week_delta: dlt, account_size, days } = data;

  const bannerVariant: 'up' | 'down' | 'flat' =
    dlt.total === null || dlt.total === 0 ? 'flat'
    : dlt.total > 0 ? 'up' : 'down';
  const bannerText =
    dlt.total === null
      ? 'No prior period to compare'
      : dlt.total === 0
      ? 'No change vs. prior period'
      : dlt.total > 0
      ? `Up ${dlt.total} pts vs. prior period`
      : `Down ${Math.abs(dlt.total)} pts vs. prior period`;

  const BannerIcon =
    bannerVariant === 'up'   ? <Icon24ArrowUpOutline width={18} height={18} />
    : bannerVariant === 'down' ? <Icon24ArrowDownOutline width={18} height={18} />
    : null;

  return (
    <div ref={ptr.ref} style={{ overflowY: 'auto', height: '100%' }}>
      <div className={`ptr ${ptr.pulling > 0 || ptr.refreshing ? 'ptr--active' : ''}`}>
        {ptr.refreshing ? '↻ Refreshing…' : ptr.pulling > 0 ? '↓ Release to refresh' : ''}
      </div>

      {/* Ring hero — passes animated total + grade colour (findings #2, #17) */}
      <div className="score-hero">
        <PillarRing
          safety={sc.pillars.safety}
          efficiency={sc.pillars.efficiency}
          compliance={sc.pillars.compliance}
          total={animatedTotal}
          gradeColor={gradeColor(sc.total)}
          size={220}
        />
      </div>

      {/* Per-pillar text breakdown below ring (finding #5) */}
      <div className="score-pillars">
        <div className="score-pillar score-pillar--safety">
          <span className="score-pillar__icon"><Icon24CheckShieldOutline width={14} height={14} /></span>
          <span className="score-pillar__label">Safety</span>
          <span className="score-pillar__val">
            {sc.pillars.safety.has_data ? `${sc.pillars.safety.subtotal}/${sc.pillars.safety.cap}` : 'No data'}
          </span>
          {dlt.safety !== null && (
            <span className="score-pillar__delta" style={{ color: deltaColor(dlt.safety) }}>{fmtDelta(dlt.safety)}</span>
          )}
        </div>
        <div className="score-pillar score-pillar--efficiency">
          <span className="score-pillar__icon"><Icon24SpeedometerMiddleOutline width={14} height={14} /></span>
          <span className="score-pillar__label">Efficiency</span>
          <span className="score-pillar__val">
            {sc.pillars.efficiency.has_data ? `${sc.pillars.efficiency.subtotal}/${sc.pillars.efficiency.cap}` : 'No data'}
          </span>
          {dlt.efficiency !== null && (
            <span className="score-pillar__delta" style={{ color: deltaColor(dlt.efficiency) }}>{fmtDelta(dlt.efficiency)}</span>
          )}
        </div>
        <div className="score-pillar score-pillar--compliance">
          <span className="score-pillar__icon"><Icon24CheckCircleOutline width={14} height={14} /></span>
          <span className="score-pillar__label">Compliance</span>
          <span className="score-pillar__val">
            {sc.pillars.compliance.has_data ? `${sc.pillars.compliance.subtotal}/${sc.pillars.compliance.cap}` : 'No data'}
          </span>
          {dlt.compliance !== null && (
            <span className="score-pillar__delta" style={{ color: deltaColor(dlt.compliance) }}>{fmtDelta(dlt.compliance)}</span>
          )}
        </div>
      </div>

      {/* Range toggle */}
      <div className="score-range" role="tablist" aria-label="Time range">
        {([7, 30, 90] as const).map(d => (
          <button
            key={d}
            role="tab"
            aria-selected={range === d}
            className={`score-range__btn${range === d ? ' score-range__btn--active' : ''}`}
            onClick={() => { haptics.selection(); setRange(d); }}
          >
            {d}d
          </button>
        ))}
      </div>

      {/* Risk Summary self-service download */}
      <RiskSummaryDownload days={range} />

      {/* Pay-for-Performance self-service paystub history */}
      <MyPaystubs />
      <MyCoaching />

      {/* No data for selected range */}
      {noDataForRange && (
        <div className="score-no-data-banner">
          <Icon24WarningTriangleOutline width={20} height={20} style={{ flexShrink: 0, opacity: 0.7 }} />
          No drive data found for the {range}-day window. Try a shorter range.
        </div>
      )}

      {/* Period delta banner (finding #8 — VK icon) */}
      <div className={`score-banner score-banner--${bannerVariant}`}>
        {BannerIcon && <span className="score-banner__icon" aria-hidden>{BannerIcon}</span>}
        <div style={{ flex: 1 }}>
          <div className="score-banner__text">{bannerText}</div>
          <div className="score-banner__sub">
            {account_size != null ? `Last ${days} days · You vs. ${account_size} drivers` : `Last ${days} days`}
          </div>
        </div>
      </div>

      {/* Score trend sparkline — above wins/losses (finding #4) */}
      {history.length >= 5
        ? <ScoreSparkline points={history} rangeDays={range} />
        : history.length > 0 && (
            <div className="score-sparkline-hint">Not enough drive data for a trend yet</div>
          )
      }

      {/* Rank section (fleet only) — coloured pillar icons (finding #10) */}
      {rank && rt && (
        <section className="score-section">
          <div className="score-section__header">Your rank</div>
          <div className="score-rank-row">
            <Icon24StarsOutline width={16} height={16} style={{ color: 'var(--st-orange)' }} />
            <span className="score-rank-row__label">Overall</span>
            <span className="score-rank-row__pos">#{rt.pos} of {rt.total}</span>
          </div>
          <div className="score-rank-row">
            <Icon24CheckShieldOutline width={16} height={16} style={{ color: 'var(--st-green)' }} />
            <span className="score-rank-row__label">Safety</span>
            <span className="score-rank-row__pos">#{rank.safety.pos} of {rank.safety.total}</span>
            <span className="score-rank-row__delta" style={{ color: deltaColor(dlt.safety) }}>{fmtDelta(dlt.safety)}</span>
          </div>
          <div className="score-rank-row">
            <Icon24SpeedometerMiddleOutline width={16} height={16} style={{ color: 'var(--st-blue)' }} />
            <span className="score-rank-row__label">Efficiency</span>
            <span className="score-rank-row__pos">#{rank.efficiency.pos} of {rank.efficiency.total}</span>
            <span className="score-rank-row__delta" style={{ color: deltaColor(dlt.efficiency) }}>{fmtDelta(dlt.efficiency)}</span>
          </div>
          <div className="score-rank-row">
            <Icon24CheckCircleOutline width={16} height={16} style={{ color: 'var(--st-blue)' }} />
            <span className="score-rank-row__label">Compliance</span>
            <span className="score-rank-row__pos">#{rank.compliance.pos} of {rank.compliance.total}</span>
            <span className="score-rank-row__delta" style={{ color: deltaColor(dlt.compliance) }}>{fmtDelta(dlt.compliance)}</span>
          </div>
        </section>
      )}

      {/* Top wins — custom rows, expandable (findings #7, #12, #14) */}
      {allWins.length > 0 && (
        <section className="score-section">
          <div className="score-section__header">
            <Icon24CupOutline width={15} height={15} />
            Top wins
          </div>
          {sortedWins.map((w, i) => {
            const pct = (Math.abs(w.points) / maxWinMag) * 100;
            return (
              <div key={`w-${i}`} className="score-event-row">
                <div className="score-event-row__top">
                  <span className="score-event-row__label">{w.label}</span>
                  <span className="score-event-row__pts score-event-row__pts--win">+{w.points}</span>
                </div>
                <div className="score-bar">
                  <div className="score-bar__fill-bg">
                    <div className="score-bar__fill" style={{ width: `${pct}%`, background: 'var(--st-green)' }} />
                  </div>
                </div>
              </div>
            );
          })}
          {allWins.length > 3 && (
            <button className="score-expand-btn" onClick={() => setWinsExpanded(e => !e)}>
              {winsExpanded
                ? <><Icon24ChevronUp width={14} height={14} /> Show less</>
                : <><Icon24ChevronDown width={14} height={14} /> Show all {allWins.length}</>}
            </button>
          )}
        </section>
      )}

      {/* What hurt your score — custom rows, expandable (findings #7, #12, #14) */}
      {allLosses.length > 0 && (
        <section className="score-section">
          <div className="score-section__header">
            <Icon24WarningTriangleOutline width={15} height={15} />
            What hurt your score
          </div>
          {sortedLosses.map((l, i) => {
            const pct = (Math.abs(l.points) / maxLossMag) * 100;
            return (
              <div key={`l-${i}`} className="score-event-row">
                <div className="score-event-row__top">
                  <span className="score-event-row__label">
                    {l.label}
                    {l.count && l.count > 1 && <span className="score-event-row__count">{l.count}×</span>}
                  </span>
                  <span className="score-event-row__pts score-event-row__pts--loss">{l.points}</span>
                </div>
                <div className="score-bar">
                  <div className="score-bar__fill-bg">
                    <div className="score-bar__fill" style={{ width: `${pct}%`, background: 'var(--st-red)' }} />
                  </div>
                </div>
              </div>
            );
          })}
          {allLosses.length > 3 && (
            <button className="score-expand-btn" onClick={() => setLossesExpanded(e => !e)}>
              {lossesExpanded
                ? <><Icon24ChevronUp width={14} height={14} /> Show less</>
                : <><Icon24ChevronDown width={14} height={14} /> Show all {allLosses.length}</>}
            </button>
          )}
        </section>
      )}

      {/* Insufficient data note */}
      {sc.insufficient_data && (
        <section className="score-section score-section--note">
          Not enough driving in this window for a fully reliable score.
          Numbers will firm up as you log more miles.
        </section>
      )}

      <div style={{ height: 24 }} />
    </div>
  );
}

// ── Score trend sparkline ────────────────────────────────────────────────
// Findings #3/#4/#6/#13/#15: tracks rangeDays, shows VK icon title,
// guards <5 points, uses dynamic y-axis padding (finding #20).

function ScoreSparkline({ points, rangeDays }: { points: ScoreHistoryPoint[]; rangeDays: number }) {
  const pts = points.slice(-rangeDays);
  const scores = pts.map(p => p.score);
  const minS = Math.max(0, Math.min(...scores) - 5);
  const maxS = Math.min(100, Math.max(...scores) + 5);
  const scoreRange = maxS - minS || 1;

  // Dynamic left pad based on label width (finding #20).
  const yLabelWidth = maxS >= 100 ? 32 : 26;
  const chartW = 320;
  const chartH = 60;
  const padL = yLabelWidth;
  const padB = 18;
  const innerW = chartW - padL - 4;
  const innerH = chartH - padB;

  function toX(i: number) { return padL + (i / (pts.length - 1)) * innerW; }
  function toY(s: number)  { return innerH - ((s - minS) / scoreRange) * innerH; }

  const polyline = pts.map((p, i) => `${toX(i)},${toY(p.score)}`).join(' ');
  const area = [
    `${padL},${innerH}`,
    ...pts.map((p, i) => `${toX(i)},${toY(p.score)}`),
    `${toX(pts.length - 1)},${innerH}`,
  ].join(' ');

  const yLabels = [
    { y: toY(minS), label: String(Math.round(minS)) },
    { y: toY(maxS), label: String(Math.round(maxS)) },
  ];
  const xLabels = [0, Math.floor((pts.length - 1) / 2), pts.length - 1]
    .filter((v, i, a) => a.indexOf(v) === i)
    .map(i => ({
      x: toX(i),
      label: new Date(pts[i].date + 'T00:00:00Z').toLocaleDateString(undefined, { month: 'short', day: 'numeric' }),
    }));

  return (
    <div className="score-sparkline">
      <div className="score-sparkline__title">
        <Icon24StatisticsOutline width={16} height={16} />
        Score trend ({rangeDays}d)
      </div>
      <svg viewBox={`0 0 ${chartW} ${chartH}`} className="score-sparkline__svg">
        <polygon points={area} fill="var(--st-blue)" opacity={0.12} />
        <polyline points={polyline} fill="none" stroke="var(--st-blue)" strokeWidth={2}
          strokeLinejoin="round" strokeLinecap="round" />
        <circle cx={toX(pts.length - 1)} cy={toY(pts[pts.length - 1].score)} r={4} fill="var(--st-blue)" />
        {yLabels.map(({ y, label }) => (
          <text key={label} x={padL - 4} y={y + 4} textAnchor="end" fontSize={9}
            fill="var(--tgui--hint_color)">{label}</text>
        ))}
        {xLabels.map(({ x, label }) => (
          <text key={label} x={x} y={chartH} textAnchor="middle" fontSize={9}
            fill="var(--tgui--hint_color)">{label}</text>
        ))}
      </svg>
    </div>
  );
}


// ── Risk Summary self-service download ─────────────────────────────
function RiskSummaryDownload({ days }: { days: Range }) {
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState('');
  async function go(fmt: 'pdf' | 'csv') {
    setErr('');
    setBusy(true);
    haptics.selection();
    try {
      const qp = new URLSearchParams({
        audience: 'payroll',
        fmt,
        days: String(days),
      });
      const res = await apiFetch(`/api/reports/risk-summary/me?${qp}`);
      if (!res.ok) {
        const txt = await res.text().catch(() => '');
        throw new Error(txt || `Request failed (${res.status})`);
      }
      const blob = await res.blob();
      const url = URL.createObjectURL(blob);
      const a = document.createElement('a');
      a.href = url;
      a.download = `risk_summary.${fmt}`;
      a.click();
      URL.revokeObjectURL(url);
    } catch (e) {
      setErr(e instanceof Error ? e.message : 'Download failed');
    } finally {
      setBusy(false);
    }
  }
  return (
    <div style={{
      display: 'flex', gap: 8, alignItems: 'center',
      padding: '10px 16px',
    }}>
      <button
        type="button"
        disabled={busy}
        onClick={() => go('pdf')}
        style={{
          padding: '8px 14px', borderRadius: 999,
          border: '1px solid var(--tgui--button_color, #2481cc)',
          background: 'transparent',
          color: 'var(--tgui--button_color, #2481cc)',
          fontSize: 13, fontWeight: 500,
        }}
      >
        {busy ? 'Generating…' : '📄 My Risk Summary'}
      </button>
      <button
        type="button"
        disabled={busy}
        onClick={() => go('csv')}
        style={{
          padding: '8px 14px', borderRadius: 999,
          border: '1px solid var(--tgui--hint_color, #8d8e90)',
          background: 'transparent',
          color: 'var(--tgui--hint_color, #8d8e90)',
          fontSize: 13,
        }}
      >
        CSV
      </button>
      {err && <span style={{ fontSize: 11, color: 'var(--st-red, #f04747)' }}>{err}</span>}
    </div>
  );
}
