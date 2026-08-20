/**
 * The run BOARD — the settlement read the way a dispatch manager reads
 * their week: one collapsible section per dispatcher, trucks as rows,
 * the period's days as columns, loads sitting on their pickup days.
 *
 * Same data as the sheet, different geometry.  The sheet stays the
 * numeric record (DataGrid: sort/filter/export); the board answers
 * "what did this dispatcher's trucks DO each day" — and on a draft,
 * clicking a day marks it inactive (home time, repair, holiday), which
 * lowers that truck's prorated target exactly like typing the number
 * in the Days & extras dialog.  Finalized runs render read-only.
 *
 * Loads are LIVE while the run is a SNAPSHOT: the loads endpoint
 * reports drift per row, surfaced as a banner — the board must never
 * silently disagree with the sheet.
 */
import { useState } from 'react';
import { useQuery, useQueryClient } from '@tanstack/react-query';
import { useTranslation } from 'react-i18next';
import { CalendarOff, ChevronDown, ChevronRight, TriangleAlert } from 'lucide-react';
import { toast } from 'sonner';
import { Button } from '../../../components/ui/button';
import { Tip } from '../../../components/tooltip';
import { daysCell, daysSummary, loadedDayCount, matchedTip, nextDay, zeroTip } from './explain';
import { DaysTipContent } from './DaysTip';
import { ActionMenu } from '../../../components/ui/context-menu';
import { toneClasses, toneText } from '../../../lib/status';
import {
  getIncentiveRunLoads, patchIncentiveRow,
  type DaySuggestion, type InactiveDate, type RunDetail, type RunLoad,
  type RunRow,
} from '../api';

const REASONS = ['home time', 'repair', 'holiday'];

function usd(v: number): string {
  return `$${v.toLocaleString(undefined, {
    minimumFractionDigits: 2, maximumFractionDigits: 2,
  })}`;
}

/** Inclusive ISO day range — pure calendar strings, no timezone. */
function dayRange(start: string, end: string): string[] {
  const out: string[] = [];
  const d = new Date(`${start.slice(0, 10)}T00:00:00Z`);
  const stop = new Date(`${end.slice(0, 10)}T00:00:00Z`);
  while (d <= stop && out.length < 120) {
    out.push(d.toISOString().slice(0, 10));
    d.setUTCDate(d.getUTCDate() + 1);
  }
  return out;
}

const WEEKDAYS = ['Sun', 'Mon', 'Tue', 'Wed', 'Thu', 'Fri', 'Sat'];
const dayLabel = (iso: string) => {
  const d = new Date(`${iso}T00:00:00Z`);
  return `${WEEKDAYS[d.getUTCDay()]} ${d.getUTCMonth() + 1}/${d.getUTCDate()}`;
};

/** "Woodland, CA 1425734" → "Woodland, CA" (best-effort tidy). */
const place = (s: string) => s.replace(/\s+\d+$/, '').trim();

export default function RunBoard({ run, draft, onChanged, onRecreate }: {
  run: RunDetail;
  draft: boolean;
  onChanged: () => void;
  /** Discard this draft and regenerate the same period from live loads
   *  (the stale banner's remedy) — parent owns the mutation + confirm. */
  onRecreate: () => void;
}) {
  const { t } = useTranslation();
  const [collapsed, setCollapsed] = useState<Record<string, boolean>>({});
  const [busyRow, setBusyRow] = useState<number | null>(null);
  const qc = useQueryClient();
  // Each dispatcher's card scrolls ALONE (owner decision 2026-08-20):
  // a manager reviews one person at a time and their loads differ, so
  // the cards never mirror each other.  Do not reintroduce a shared
  // scroll position.  The state only flips at the 0-boundary, so this
  // never re-renders at scroll rate.
  const [scrolledMap, setScrolledMap] = useState<Record<string, boolean>>({});
  const onBoardScroll = (key: string) => (e: React.UIEvent<HTMLDivElement>) => {
    const on = e.currentTarget.scrollLeft > 0;
    setScrolledMap((m) => (!!m[key] === on ? m : { ...m, [key]: on }));
  };

  const loadsQ = useQuery({
    queryKey: ['kpi-incentive-run-loads', run.id],
    queryFn: () => getIncentiveRunLoads(run.id),
  });

  const days = dayRange(run.period_start, run.period_end);
  const byDispatcher = new Map<string, RunRow[]>();
  for (const row of run.rows) {
    const list = byDispatcher.get(row.dispatcher_name) ?? [];
    list.push(row);
    byDispatcher.set(row.dispatcher_name, list);
  }
  // A truck whose loads split between dispatchers gets a ROW PER
  // DISPATCHER (KPI grades people; each row counts only that person's
  // loads).  Unexplained, the second row reads as a duplicate-data bug
  // — so each such card names its siblings.
  const unitDispatchers = new Map<string, string[]>();
  for (const row of run.rows) {
    if (!row.vehicle_unit) continue;
    const list = unitDispatchers.get(row.vehicle_unit) ?? [];
    list.push(row.dispatcher_name);
    unitDispatchers.set(row.vehicle_unit, list);
  }

  const markDay = async (row: RunRow, day: string, reason: string | null) => {
    const existing = row.inactive_dates ?? [];
    const marks: InactiveDate[] = reason == null
      ? existing.filter((m) => m.date !== day)
      : [...existing.filter((m) => m.date !== day), { date: day, reason }];
    setBusyRow(row.id);
    try {
      const fresh = await patchIncentiveRow(run.id, row.id, { inactive_dates: marks });
      // The board is the ONLY day editor and it PATCHes the WHOLE
      // list, so the cache must carry this response before the row
      // unlocks — a second mark built from the stale prop would
      // silently erase the one just saved.
      qc.setQueryData(['kpi-incentive-run', run.id],
        (old: RunDetail | undefined) => old
          ? { ...old, rows: old.rows.map((x) => x.id === fresh.id ? { ...x, ...fresh } : x) }
          : old);
      onChanged();
    } catch (e) {
      toast.error(e instanceof Error ? e.message : 'Could not save');
    } finally {
      setBusyRow(null);
    }
  };

  const drift = loadsQ.data
    ? loadsQ.data.drift.length + (loadsQ.data.unmatched_loads > 0 ? 1 : 0)
    : 0;
  // The dashed "REPAIR?" chips whisper per-cell; a reviewer scanning a
  // 30-row board needs the COUNT said once, up top, or unconfirmed
  // suggestions scroll past unseen.
  const suggestionCount = loadsQ.data
    ? Object.values(loadsQ.data.suggestions).reduce((a, s) => a + s.length, 0)
    : 0;

  return (
    <div className="space-y-8">
      <div className="space-y-3">
      {draft && (
        /* The board's one non-obvious gesture, said once where the days
           are — a flat cell gives no affordance cue by itself. */
        <p className="text-xs text-muted-foreground">
          {t('kpi_board.hint4',
            'Click a day and pick a reason (home time, repair, holiday) to mark it inactive — the truck’s target lowers with each inactive day. Click an inactive day to make it count again — the target rises back.')}
        </p>
      )}
      {drift > 0 && (
        /* Instruction + the control for it in one block: prose that
           prescribes an action the reader cannot take is a dead end. */
        <div className={`flex flex-wrap items-center gap-2 text-xs ${toneClasses('warn')} px-2 py-1.5 rounded`}>
          <TriangleAlert size={12} className="shrink-0" />
          <span>
            {t('kpi_board.drift_n',
              'Loads changed after this run was generated — {{n}} rows (marked “stale”) still pay from the run’s snapshot.',
              { n: loadsQ.data?.drift.length ?? 0 })}
          </span>
          {draft && (
            <Button size="sm" variant="outline" onClick={onRecreate}>
              {t('kpi_board.recreate', 'Recreate draft')}
            </Button>
          )}
        </div>
      )}
      {/* The board's day states, named once — a reviewer must be
          able to tell WHY Wednesday is amber and Saturday is not
          without decoding chips by trial.  A bounded well: the legend
          is a REGION (the board's only decoder), not loose prose. */}
      <div className="flex flex-wrap items-center gap-x-4 gap-y-1.5 text-xs text-muted-foreground bg-muted/50 rounded-lg px-3 py-2">
        <span className="inline-flex items-center gap-1.5">
          <span className="inline-flex items-center" aria-hidden>
            <span className={`h-6 leading-6 rounded-l rounded-r-none px-1.5 text-xs tabular-nums ${toneClasses('ok')}`}>$950</span>
            <span className="h-6 w-6 rounded-r bg-ok-bg" />
          </span>
          {t('kpi_board.leg_loads4', 'load (rate · place → delivers) — the bar spans its days')}
        </span>
        <span className="inline-flex items-center gap-1.5">
          <span className={`inline-block h-6 leading-6 rounded px-1.5 text-xs font-medium uppercase tracking-wide ${toneClasses('warn')}`}>{t('kpi_board.inactive', 'inactive')}</span>
          {t('kpi_board.leg_inactive2', 'not counted')}
        </span>
        <span className="inline-flex items-center gap-1.5">
          <span className={`inline-block h-6 leading-6 rounded border border-dashed border-warn-bd px-1.5 text-xs uppercase tracking-wide ${toneText('warn')}`}>{t('kpi_board.leg_repair', 'repair?')}</span>
          {t('kpi_board.leg_suggested', 'suggested — click to confirm')}
        </span>
        <span className="inline-flex items-center gap-1.5">
          <span className="inline-flex h-6 w-10 items-center justify-center rounded border border-dashed border-border">
            <CalendarOff size={12} className="text-muted-foreground/60" aria-hidden />
          </span>
          {t('kpi_board.leg_empty2', 'no loads, counting — click to mark inactive')}
        </span>
        <span className="inline-flex items-center gap-1.5">
          <span className="relative inline-block h-1 w-10 rounded bg-muted" aria-hidden>
            <span className="absolute inset-y-0 left-0 rounded bg-ok" style={{ width: '78%' }} />
            <span className="absolute -top-0.5 -bottom-0.5 w-px bg-muted-foreground" style={{ left: '62%' }} />
          </span>
          {t('kpi_board.leg_meter', 'row meter — gross fills toward the next tier; the tick is the target; amber pays 0%')}
        </span>
        {/* "stale" was defined only in the drift banner — which is gone
            once scrolled past, leaving an amber pill with no definition
            anywhere on screen. */}
        <span className="inline-flex items-center gap-1.5">
          <span className={`inline-block h-6 leading-6 rounded px-1.5 text-xs font-medium ${toneClasses('warn')}`}>{t('kpi_board.stale', 'stale')}</span>
          {t('kpi_board.leg_stale', 'loads changed after generation — still pays from the snapshot')}
        </span>
      </div>
      {draft && suggestionCount > 0 && (
        <p className="text-xs text-muted-foreground">
          {t('kpi_board.suggest_count',
            '{{n}} suggested repair days on this board (dashed chips) — click each to confirm as inactive, or ignore to leave the day counted.',
            { n: suggestionCount })}
        </p>
      )}
      </div>

      <div className="space-y-3">
      {[...byDispatcher.entries()].map(([name, rows]) => {
        const gross = rows.reduce((a, r) => a + r.kpi_gross, 0);
        const miles = rows.reduce((a, r) => a + r.miles, 0);
        const baseGross = rows.reduce((a, r) => a + r.base_gross, 0);
        const confirmed = rows.reduce((a, r) => a + r.confirmed_dollars, 0);
        const isCollapsed = !!collapsed[name];
        return (
          <section key={name} className="bg-card border border-border rounded-xl">
            {/* Section header — the dispatcher's summary band. */}
            <button
              type="button"
              onClick={() => setCollapsed((m) => ({ ...m, [name]: !m[name] }))}
              aria-expanded={!isCollapsed}
              className={`sticky top-0 z-30 w-full flex flex-wrap items-center gap-x-4 gap-y-1 px-4 py-2.5 bg-muted border-b border-border text-left hover:bg-border/60 transition ${isCollapsed ? 'rounded-xl' : 'rounded-t-xl'}`}
            >
              {isCollapsed
                ? <ChevronRight size={16} className="text-muted-foreground shrink-0" />
                : <ChevronDown size={16} className="text-muted-foreground shrink-0" />}
              <span className="text-sm font-semibold">{name}</span>
              <span className="text-xs text-muted-foreground tabular-nums">
                {rows.length === 1
                  ? t('kpi_board.truck_one', '1 truck')
                  : t('kpi_board.trucks', '{{n}} trucks', { n: rows.length })}
                {' · '}{Math.round(miles).toLocaleString()} mi
                {' · '}RPM {miles > 0 ? (baseGross / miles).toFixed(2) : '—'}
                {' · '}{usd(gross)}
              </span>
              <span className="ml-auto text-sm font-medium tabular-nums">{usd(confirmed)}</span>
            </button>

            {!isCollapsed && (
              <div className="flex">
                {/* Unit pane — OUTSIDE the scroller: this info never
                    moves, so the scrollbar must not run under it.
                    w-72: the card's content wants ~290px. */}
                <div className={`w-72 shrink-0 border-r border-border ${scrolledMap[name] ? 'shadow-md' : ''}`}>
                  <div className="flex h-8 items-center bg-muted px-3 text-xs font-medium uppercase tracking-wide text-muted-foreground border-b border-border">
                    {t('kpi_board.unit', 'Unit')}
                  </div>
                  {rows.map((row) => (
                    <BoardRow
                      key={row.id}
                      part="unit"
                      row={row}
                      days={days}
                      loads={loadsQ.data ? (loadsQ.data.rows[String(row.id)] ?? []) : undefined}
                      periodStart={run.period_start}
                      periodEnd={run.period_end}
                      suggestions={loadsQ.data?.suggestions[String(row.id)] ?? []}
                      stale={loadsQ.data?.drift.includes(row.id) ?? false}
                      clickable={draft && busyRow !== row.id}
                      alsoUnder={(unitDispatchers.get(row.vehicle_unit) ?? [])
                        .filter((n) => n !== name)}
                      onMark={(day, reason) => markDay(row, day, reason)}
                    />
                  ))}
                </div>
                {/* Days pane — the ONLY thing that scrolls; its
                    scrollbar starts where the calendar starts. */}
                <div
                  onScroll={onBoardScroll(name)}
                  className="min-w-0 flex-1 overflow-x-auto snap-x"
                >
                  {/* minWidth is the scroll floor: below 112px/day the
                      pane scrolls; above it the columns grow equally,
                      so a wide window spends its space on the days. */}
                  <div className="min-w-full" style={{ minWidth: days.length * 112 }}>
                    <div className="flex h-8 border-b border-border bg-muted">
                      {days.map((d) => (
                        <div key={d} className="flex flex-1 snap-start items-center px-2 text-xs text-muted-foreground border-r border-border last:border-r-0">
                          {dayLabel(d)}
                        </div>
                      ))}
                    </div>
                    {rows.map((row) => (
                      <BoardRow
                        key={row.id}
                        part="days"
                        row={row}
                        days={days}
                        loads={loadsQ.data ? (loadsQ.data.rows[String(row.id)] ?? []) : undefined}
                        periodStart={run.period_start}
                        periodEnd={run.period_end}
                        suggestions={loadsQ.data?.suggestions[String(row.id)] ?? []}
                        stale={loadsQ.data?.drift.includes(row.id) ?? false}
                        clickable={draft && busyRow !== row.id}
                        alsoUnder={[]}
                        onMark={(day, reason) => markDay(row, day, reason)}
                      />
                    ))}
                  </div>
                </div>
              </div>
            )}
          </section>
        );
      })}
      </div>
    </div>
  );
}

function BoardRow({ part, row, days, loads, suggestions, stale, clickable, alsoUnder, onMark, periodStart, periodEnd }: {
  /** Which pane this instance renders: the static unit cell or the
   *  scrolling day cells.  Both panes map the same rows at the same
   *  FIXED height (h-36), so they stay aligned without sharing a DOM
   *  ancestor. */
  part: 'unit' | 'days';
  row: RunRow;
  days: string[];
  loads: RunLoad[] | undefined;
  periodStart: string;
  periodEnd: string;
  /** Maintenance-suggested inactive days (human confirms by click). */
  suggestions: DaySuggestion[];
  /** Live loads no longer sum to this row's snapshot. */
  stale: boolean;
  clickable: boolean;
  /** OTHER dispatchers this unit also has a row under in this run —
   *  each row counts only its own dispatcher's loads. */
  alsoUnder: string[];
  onMark: (day: string, reason: string | null) => void;
}) {
  const { t } = useTranslation();
  const marks = new Map((row.inactive_dates ?? []).map((m) => [m.date, m.reason]));
  const suggested = new Map(suggestions.map((sug) => [sug.date, sug]));
  const byDay = new Map<string, RunLoad[]>();
  for (const l of loads ?? []) {
    const d = l.pickup_date;
    byDay.set(d, [...(byDay.get(d) ?? []), l]);
  }
  // Days a load COVERS without starting there — the truck is rolling
  // (picked up earlier, delivering later), not idle.  Each transit day
  // keeps its load + position so the strip reads as a PIECE of that
  // load's bar ("→ $5,800 · Wilmi… — day 2 of 3"), never an anonymous
  // arrow.
  const hiDay = row.window_end.slice(0, 10);
  const prevDay = (iso: string) =>
    new Date(Date.parse(`${iso}T00:00:00Z`) - 86_400_000).toISOString().slice(0, 10);
  const stripLoadAt = (day: string): RunLoad | null => {
    const info = transitInfo.get(day);
    if (!info) return null;
    if ((byDay.get(day) ?? []).length > 0) return null;
    if (marks.get(day) != null) return null;
    if (suggested.has(day)) return null;
    if (!inWindow(day)) return null;
    return info.load;
  };
  /** Consecutive strip days ahead of a pickup — the chip's runway. */
  const stripRun = (l: RunLoad, from: string): number => {
    let c = 0;
    for (let day = nextDay(from); stripLoadAt(day) === l; day = nextDay(day)) c += 1;
    return c;
  };
  // The REAL delivery day — for words (labels, aria, tooltips).
  const deliveryEnd = (l: RunLoad): string => {
    const a = (l.pickup_date || '').slice(0, 10);
    const bRaw = (l.delivery_date || '').slice(0, 10);
    return bRaw && bRaw > a ? bRaw : a;
  };
  // The span's last VISIBLE day — for geometry only.  Never print it:
  // a load rolling past the period would read "delivers <period end>",
  // a clamp masquerading as a date.
  const spanEnd = (l: RunLoad): string => {
    const end = deliveryEnd(l);
    return end > hiDay ? hiDay : end;
  };
  const transitInfo = new Map<string, {
    load: RunLoad; dayNo: number; total: number; last: string;
  }>();
  for (const l of loads ?? []) {
    const a = (l.pickup_date || '').slice(0, 10);
    if (!a) continue;
    const end = spanEnd(l);
    if (end <= a) continue;
    // "day N of TOTAL" counts the REAL trip, not the visible slice.
    const real = deliveryEnd(l);
    let total = 1;
    for (let d = a; d < real; d = nextDay(d)) total += 1;
    let dayNo = 2;
    for (let d = nextDay(a); d <= end; d = nextDay(d), dayNo += 1) {
      if (!transitInfo.has(d)) {
        transitInfo.set(d, { load: l, dayNo, total, last: end });
      }
    }
  }
  const inWindow = (d: string) =>
    d >= row.window_start.slice(0, 10) && d <= row.window_end.slice(0, 10);
  const loadedDays = loadedDayCount(loads, row.window_start, row.window_end);

  // The row meter — a glance-scale answer to "how is this truck doing"
  // that the numbers alone can't give across twenty rows.  Scale: $0 →
  // the next milestone (the next tier's gross when one is coming, else
  // the larger of target and gross).  Fill tone = earning state (ok
  // pays, warn pays 0% — the same tone the "no tier met" chip wears);
  // the tick marks the target.  The text above carries the exact
  // numbers, so the bar is aria-hidden decoration with a hover
  // explanation.
  const meterGross = Number(row.kpi_gross);
  const meterTarget = Number(row.adjusted_target);
  const meterEnd = row.next_tier
    ? meterGross + Number(row.next_tier.gap)
    : Math.max(meterTarget, meterGross);
  const meter = row.weekly_target != null && meterTarget > 0 && meterEnd > 0
    ? {
        fill: Math.min(100, Math.round((meterGross / meterEnd) * 100)),
        tick: meterTarget < meterEnd
          ? Math.round((meterTarget / meterEnd) * 100) : null,
        earning: Number(row.pct) > 0,
      }
    : null;
  const money0 = (v: number) => `$${Math.round(v).toLocaleString()}`;
  const meterTip = !meter ? '' : row.next_tier && meter.tick != null
    ? t('kpi_board.meter_tip',
      'Gross {{g}} — the tick is the {{tgt}} target; the bar ends at the next tier ({{end}} gross).',
      { g: money0(meterGross), tgt: money0(meterTarget), end: money0(meterEnd) })
    : meter.tick != null
      ? t('kpi_board.meter_tip2', 'Gross {{g}} — past the {{tgt}} target (the tick).',
        { g: money0(meterGross), tgt: money0(meterTarget) })
      : t('kpi_board.meter_tip3', 'Gross {{g}} of the {{tgt}} target.',
        { g: money0(meterGross), tgt: money0(meterTarget) });

  // The next-tier line's honesty kit.  The gap is GROSS dollars but the
  // payout is PAY dollars — two currencies in one sentence — and on an
  // RPM tier the gap only closes with revenue at CURRENT miles: a cheap
  // extra load adds miles and moves the tier AWAY.  The surface says
  // both briefly; this tip says them properly.
  const tierDelta = row.next_tier
    ? row.next_tier.dollars_at - row.confirmed_dollars : 0;
  // At $0.00 the gain IS the total — "(+$80.00)" next to "pays $80.00"
  // restates it, so the delta only shows when there is a now to beat.
  const showTierDelta = tierDelta > 0 && Number(row.confirmed_dollars) > 0;
  const tierChain = !row.next_tier ? '' : [
    row.next_tier.min_rpm != null
      ? t('kpi_board.next_gap2', '{{gap}} more (same miles)',
        { gap: money0(row.next_tier.gap) })
      : t('kpi_board.next_gap', '{{gap}} more gross',
        { gap: money0(row.next_tier.gap) }),
    t('kpi_board.next_goal2', '→ {{pct}}%', { pct: row.next_tier.pct }),
    showTierDelta
      ? t('kpi_board.next_pays4', '→ {{at}} (+{{d}})',
        { at: usd(row.next_tier.dollars_at), d: usd(tierDelta) })
      : t('kpi_board.next_pays3', '→ {{at}}',
        { at: usd(row.next_tier.dollars_at) }),
  ].join(' ');
  const tierHint = !row.next_tier ? null : (
    <div className="space-y-0.5 text-left tabular-nums">
      <div>{tierChain}</div>
      <div>{row.next_tier.min_rpm != null
        ? t('kpi_board.tier_rpm',
          'The tier needs RPM ≥ {{rpm}}, so the {{gap}} gap holds at your current miles: more revenue on the SAME miles lifts RPM — an extra cheap load adds miles and can move the tier further away.',
          { rpm: row.next_tier.min_rpm.toFixed(2),
            gap: money0(row.next_tier.gap) })
        : t('kpi_board.tier_any',
          '{{gap}} more gross reaches it at any miles — this tier has no RPM condition.',
          { gap: money0(row.next_tier.gap) })}</div>
    </div>
  );

  if (part === 'unit') {
    return (
      /* Truck identity + its sheet numbers.  Gross and the zero-reason
         live HERE so a $0.00 row explains itself without switching to
         the sheet.  h-36 is the row-height CONTRACT with the days pane
         — overflow-hidden guards it. */
      <div className="h-36 overflow-hidden px-3 py-2 border-b border-border last:border-b-0">
        <div className="flex items-center gap-1.5 text-sm font-medium whitespace-nowrap">
          <span className="shrink-0">{row.vehicle_unit || t('kpi_board.no_unit', 'No unit assigned')}</span>
          <span className="shrink-0 text-xs font-normal text-muted-foreground">{row.company_code}</span>
          {stale && (
            <Tip label={t('kpi_board.stale_tip', 'This row’s loads changed after the run was generated — it still pays from the snapshot.')}>
              <span tabIndex={0} className={`shrink-0 text-xs font-normal ${toneClasses('warn')} px-1.5 rounded`}>
                {t('kpi_board.stale', 'stale')}
              </span>
            </Tip>
          )}
          {alsoUnder.length > 0 && (
            /* The same truck under two dispatchers is BY DESIGN (each
               row counts only its own dispatcher's loads) — but
               unexplained it reads as duplicate data. */
            <Tip label={t('kpi_board.also_under_tip',
              'Unit {{unit}} also has a row under {{names}} in this run — each dispatcher’s row counts only the loads they dispatched on it.',
              { unit: row.vehicle_unit, names: alsoUnder.join(', ') })}>
              <span tabIndex={0}
                className="min-w-0 truncate text-xs font-normal text-muted-foreground underline decoration-dotted decoration-muted-foreground/60 underline-offset-4 cursor-help">
                {t('kpi_board.also_under', 'also under {{name}}', {
                  name: alsoUnder.length === 1
                    ? alsoUnder[0].split(' ')[0]
                    : t('kpi_board.n_others', '{{n}} others', { n: alsoUnder.length }) })}
              </span>
            </Tip>
          )}
        </div>
        {/* One fact family per line — days, then money.  The old single
            run wrapped wherever the column edge fell, splitting facts
            mid-sentence and stranding a trailing "·"; deliberate lines
            break at the same place on every row, so one fact scans
            straight down the column. */}
        <div className="text-xs text-muted-foreground tabular-nums">
          <Tip label={<DaysTipContent row={row} loads={loads}
            draft={clickable} periodStart={periodStart}
            periodEnd={periodEnd} t={t} />}>
            {/* tabIndex: every Tip on this card opens on keyboard focus
                too — the card must not be mouse-only.  The aria-label is
                the breakdown as one sentence (the visual Tip is JSX a
                reader can't see). */}
            <span tabIndex={0}
              aria-label={daysSummary(row, loadedDays, t)}
              className="underline decoration-dotted decoration-muted-foreground/60 underline-offset-4 cursor-help">
              {daysCell(row, loadedDays, t)}
            </span>
          </Tip>
        </div>
        {/* Space groups the card, not ink: 8px opens each group (what
            happened / what could happen), 2px holds a group's lines
            together — proximity separates for free where dividers
            would draw boxes inside an already-bordered row. */}
        <div className="mt-2 text-xs text-muted-foreground tabular-nums">
          <span className="whitespace-nowrap">
            ${Math.round(row.kpi_gross).toLocaleString()}
            {row.weekly_target != null && (
              <span className="text-muted-foreground/70">
                {' '}{t('kpi_board.vs_target', 'vs {{tgt}}',
                  { tgt: `$${Math.round(row.adjusted_target).toLocaleString()}` })}
              </span>
            )}
          </span>
          {row.rpm != null && (
            /* RPM is what most tiers actually test — without it, two
               adjacent rows where MORE gross pays LESS look broken. */
            <>
              {' · '}
              <span className="whitespace-nowrap">
                {t('kpi_board.rpm', 'RPM {{r}}',
                  { r: Number(row.rpm).toFixed(2) })}
              </span>
            </>
          )}
        </div>
        {/* The payout gets its OWN line on every row — a constant shape
            that lands "% → $" at the same y-offset on every card, so a
            reader compares trucks by running one line down the column.
            The WHOLE result is the answer, so the whole line carries
            the weight, not just the dollars. */}
        <div className="mt-0.5 text-sm font-semibold text-foreground tabular-nums">
          <span className="whitespace-nowrap">
            {row.matched_rule ? (
              <Tip label={matchedTip(row.matched_rule, t)}>
                <span tabIndex={0} aria-label={matchedTip(row.matched_rule, t)}
                  className="underline decoration-dotted decoration-muted-foreground/60 underline-offset-4 cursor-help">
                  {Number(row.pct)}%
                </span>
              </Tip>
            ) : (
              <>{Number(row.pct)}%</>
            )}
            {' → '}
            {tierHint ? (
              /* Hover/focus the AMOUNT for the what's-next chain —
                 "$224 more (same miles) → 3.25% → $278.66 (+$49.03)".
                 On the surface there is exactly ONE money line, so the
                 current pay can never be mistaken for the hypothetical
                 (owner decision 2026-08-20). */
              <Tip label={tierHint}>
                <span tabIndex={0}
                  className="underline decoration-dotted decoration-muted-foreground/60 underline-offset-4 cursor-help">
                  {usd(row.confirmed_dollars)}
                </span>
              </Tip>
            ) : (
              usd(row.confirmed_dollars)
            )}
          </span>
        </div>
        {Number(row.pct) === 0 && row.zero_reason && (
          <Tip label={zeroTip(row, t)}>
            <span tabIndex={0} className={`mt-1 inline-block text-xs font-medium ${toneClasses('warn')} px-2 py-0.5 rounded-md`}>
              {row.zero_reason === 'floor' ? t('kpi_runs.zr_floor', 'below floor')
                : row.zero_reason === 'no_active_days' ? t('kpi_runs.zr_days', 'no active days')
                  : row.zero_reason === 'no_target' ? t('kpi_runs.zr_target', 'no target')
                    : t('kpi_runs.zr_tier', 'no tier met')}
            </span>
          </Tip>
        )}

        {meter && (
          <Tip label={meterTip}>
            {/* py-1 -my-1 grows the hover/focus area without moving
                layout — a bare 4px strip is unhoverable.  role="img" +
                the tip text as its name: the meter carries three values
                and must exist in the accessibility tree. */}
            <div tabIndex={0} role="img" aria-label={meterTip}
              className="mt-0.5 py-1 -my-1 cursor-help">
              <div className="relative h-1 w-full rounded bg-muted">
                <div className={`h-full rounded ${meter.earning ? 'bg-ok' : 'bg-warn'}`}
                  style={{ width: `${meter.fill}%` }} />
                {meter.tick != null && (
                  <div className="absolute -top-0.5 -bottom-0.5 w-px bg-muted-foreground"
                    style={{ left: `${meter.tick}%` }} />
                )}
              </div>
            </div>
          </Tip>
        )}

      </div>
    );
  }

  return (
    <div className="flex h-36 border-b border-border last:border-b-0">
      {days.map((d, i) => {
        const dayLoads = byDay.get(d) ?? [];
        const reason = marks.get(d);
        const inside = inWindow(d);
        // The INNER cell fills its wrapper; sizing lives on the wrapper
        // below — the wrapper (or the ActionMenu trigger button) is the
        // real flex item, so putting basis/grow here was dead code and
        // let a wide chip widen ONE row's column off the header grid.
        const cell = (
          <div
            className={`group relative h-full min-h-14 px-1.5 py-1.5 space-y-1 ${
              !inside ? 'bg-muted/60'
                : reason != null ? 'bg-warn-bg' : ''
            } ${clickable && inside ? 'cursor-pointer hover:bg-muted hover:ring-1 hover:ring-inset hover:ring-border' : ''}`}
          >
            {dayLoads.slice(0, 2).map((l, i) => {
              const runDays = i <= 1 ? stripRun(l, d) : 0;
              const cont = runDays > 0;
              // Label runway = this cell + every following STRIP cell.
              // Columns are flex-equal, so the runway is a PERCENTAGE
              // of this cell (100% per covered day) — slightly
              // conservative (each cell's padding is counted once),
              // which only ends the text a few px before the bar.
              const runway = cont
                ? `calc(${(runDays + 1) * 100}% - 12px)` : undefined;
              const loadAria = t('kpi_board.load_aria',
                'Load {{rate}} — {{from}} to {{to}}, {{mi}} mi, delivers {{del}}', {
                  rate: usd(l.total_rate),
                  from: place(l.pickup_location) || '—',
                  to: place(l.delivery_location) || l.load_number,
                  mi: Math.round(l.miles).toLocaleString(),
                  del: dayLabel(deliveryEnd(l)),
                });
              return cont ? (
                /* DAY-ALIGNED fill + floating label.  The fill ends at
                   the cell edge (uniform bg-ok-bg, single alpha layer,
                   day-aligned like the strips and the hover regions);
                   the label floats above the whole bar,
                   pointer-events-none so hovering day 2 hits day 2's
                   transit tip, and names the delivery day on the
                   surface. */
                <div key={i} className="relative">
                  <Tip label={`${place(l.pickup_location)} → ${place(l.delivery_location)} · ${usd(l.total_rate)} · ${Math.round(l.miles).toLocaleString()} mi`}>
                    <div className="block h-6 rounded-l rounded-r-none -mr-1.5 bg-ok-bg"
                      role="img" aria-label={loadAria} />
                  </Tip>
                  <span
                    className="pointer-events-none absolute left-1.5 top-0 z-10 flex h-6 leading-6 text-xs tabular-nums text-ok"
                    style={{ maxWidth: runway }} aria-hidden>
                    {/* The date must SURVIVE truncation — "→ Thu 8/"
                        is misinformation; the city gives way first. */}
                    <span className="min-w-0 truncate">
                      ${Math.round(l.total_rate).toLocaleString()} · {place(l.delivery_location) || l.load_number}
                    </span>
                    <span className="shrink-0 whitespace-pre">
                      {' → '}{dayLabel(deliveryEnd(l))}
                    </span>
                  </span>
                </div>
              ) : (
                <Tip key={i}
                  label={`${place(l.pickup_location)} → ${place(l.delivery_location)} · ${usd(l.total_rate)} · ${Math.round(l.miles).toLocaleString()} mi`}>
                  <div
                    /* h-6 + leading-6 (not flex): truncate's ellipsis
                       needs a block formatting context. */
                    className={`block h-6 leading-6 rounded text-xs tabular-nums px-1.5 truncate ${toneClasses('ok')}`}
                    role="img" aria-label={loadAria}
                  >
                    ${Math.round(l.total_rate).toLocaleString()} · {place(l.delivery_location) || l.load_number}
                  </div>
                </Tip>
              );
            })}
            {dayLoads.length > 2 && (
              <div className="px-1.5 text-xs text-muted-foreground">
                +{dayLoads.length - 2} {t('kpi_board.more', 'more')}
              </div>
            )}
            {reason != null && (
              clickable ? (
                <Tip label={t('kpi_board.unmark_tip3',
                  '{{why}} — click to make {{day}} count again{{stake}}.', {
                    why: reason || t('kpi_board.inactive', 'inactive'),
                    day: dayLabel(d),
                    stake: row.weekly_target != null
                      ? t('kpi_board.stake_up', ', target +{{v}}',
                          { v: `$${Math.round(row.weekly_target / 7).toLocaleString()}` })
                      : '',
                  })}>
                  <div className={`rounded px-1.5 py-0.5 text-xs font-medium ${toneClasses('warn')} uppercase tracking-wide truncate`}>
                    {t('kpi_board.inactive', 'inactive')}{reason ? ` · ${reason}` : ''}
                  </div>
                </Tip>
              ) : (
                <div className={`rounded px-1.5 py-0.5 text-xs font-medium ${toneClasses('warn')} uppercase tracking-wide truncate`}>
                  {t('kpi_board.inactive', 'inactive')}{reason ? ` · ${reason}` : ''}
                </div>
              )
            )}
            {clickable && inside && reason == null && suggested.has(d) && (
              /* Phase 4b stepping stone: a work order on this truck's
                 service day SUGGESTS the mark; the manager confirms via
                 the menu.  Dashed = proposal, filled warn = decided. */
              <Tip label={`${suggested.get(d)!.source} — ${t('kpi_board.suggest_tip', 'click to confirm as inactive')}`}>
                <span className={`block rounded border border-dashed border-warn-bd px-1.5 py-0.5 text-xs uppercase tracking-wide truncate bg-transparent ${toneText('warn')}`}>
                  {suggested.get(d)!.reason}?
                </span>
              </Tip>
            )}
            {clickable && inside && dayLoads.length > 0 && reason == null && (
              /* Loaded cells hide their gesture behind a bare cursor
                 change — on hover a corner glyph names the action
                 without reflowing the cell (overlay, not in-flow). */
              <span className="absolute bottom-1 right-1 hidden group-hover:inline-flex group-focus-within:inline-flex items-center justify-center size-5 rounded border border-dashed border-border bg-card"
                aria-hidden>
                <CalendarOff size={12} className="text-muted-foreground" />
              </span>
            )}
            {inside && dayLoads.length === 0 && reason == null
              && !suggested.has(d) && transitInfo.has(d) && (() => {
              /* A rolling day is a PIECE of its load's bar — same tone
                 as the pickup chip, lighter fill, connected through
                 the cell edges; the delivery day carries the right
                 cap.  An anonymous "in transit" couldn't say WHICH
                 load the truck was rolling for. */
              const info = transitInfo.get(d)!;
              const continues = stripLoadAt(nextDay(d)) === info.load;
              // The strip lives on ITS load's lane (the chip's index
              // on the pickup day); lanes beyond the rendered two get
              // no strip.
              const pickupDay = (info.load.pickup_date || '').slice(0, 10);
              const lane = (byDay.get(pickupDay) ?? []).indexOf(info.load);
              if (lane < 0 || lane > 1) return null;
              // Connected LEFT when yesterday rendered this load's
              // head (its pickup) or its strip; an interrupted span
              // (another chip in between) restarts with a left cap.
              const leftConnected =
                stripLoadAt(prevDay(d)) === info.load
                || (byDay.get(prevDay(d)) ?? [])[0] === info.load;
              return (
                <Tip label={t('kpi_board.transit_tip2',
                  '{{rate}} to {{place}} — in transit, day {{i}} of {{n}}; delivers {{del}}. A working day; it counts.', {
                    rate: `$${Math.round(info.load.total_rate).toLocaleString()}`,
                    place: place(info.load.delivery_location) || info.load.load_number,
                    i: info.dayNo, n: info.total,
                    del: dayLabel(deliveryEnd(info.load)),
                  })}>
                  {/* FLAT — the pickup chip already carries the text
                      once; repeating it per piece breaks the one-bar
                      reading.  h-6 = the chip's exact height, so the
                      bar runs level; AT still hears the story. */}
                  {/* Full-strength token: --ok-bg is ALREADY a 15%
                      tint (color-mix), so an alpha modifier on top
                      painted a ~9% wash — invisible on white.  Same
                      fill as the chip = one unbroken bar. */}
                  <span className={`block h-6 bg-ok-bg ${
                    lane === 1 ? 'mt-7' : ''} ${
                    leftConnected ? '-ml-1.5 rounded-l-none' : 'rounded-l'} ${
                    continues ? 'rounded-r-none -mr-1.5' : 'rounded-r'}`}>
                    <span className="sr-only">
                      {t('kpi_board.transit_sr', 'in transit — {{rate}} to {{place}}', {
                        rate: `$${Math.round(info.load.total_rate).toLocaleString()}`,
                        place: place(info.load.delivery_location) || info.load.load_number,
                      })}
                    </span>
                  </span>
                </Tip>
              );
            })()}
            {clickable && inside && dayLoads.length === 0 && reason == null
              && !suggested.has(d) && !transitInfo.has(d) && (
              /* A persistent dashed WELL with a calendar-off glyph — a
                 "+" promised ADDING something; the gesture REMOVES a
                 day from the target.  Tip names day and action. */
              <Tip label={row.weekly_target != null
                ? t('kpi_board.mark_tip2', 'Mark {{day}} inactive → target −{{v}}',
                    { day: dayLabel(d),
                      v: `$${Math.round(row.weekly_target / 7).toLocaleString()}` })
                : t('kpi_board.mark_tip', 'Mark {{day}} inactive', { day: dayLabel(d) })}>
                <span className="flex h-6 items-center justify-center rounded border border-dashed border-border">
                  <CalendarOff size={12} className="text-muted-foreground/60" aria-hidden />
                </span>
              </Tip>
            )}
          </div>
        );
        // The bar erases the grid line it crosses — only where it
        // VISUALLY crosses: the next day renders load L's strip AND
        // this day renders L's head or L's strip.  A span interrupted
        // by another chip keeps its grid lines.
        const nextStripLoad = stripLoadAt(nextDay(d));
        const seamless = nextStripLoad != null
          && (stripLoadAt(d) === nextStripLoad
            || (dayLoads.length > 0 && dayLoads[0] === nextStripLoad));
        const wrapCls = `flex-1 snap-start min-w-0 ${
          seamless ? '' : 'border-r'} border-border last:border-r-0`;
        if (!clickable || !inside) return <div key={d} className={wrapCls}>{cell}</div>;
        return (
          <ActionMenu
            key={d}
            items={[
              {
                key: 'heading',
                label: `${row.vehicle_unit || t('kpi_board.no_unit', 'No unit assigned')} · ${dayLabel(d)}`,
                disabled: true,
                onSelect: () => {},
              },
              // The consequence AT the moment of choosing — it already
              // lived in the cell's accessible name, invisible to the
              // sighted user with the menu open.
              ...(row.weekly_target != null ? [{
                key: 'consequence',
                label: reason == null
                  ? t('kpi_board.menu_target_down', 'target {{a}} → {{b}}', {
                      a: `$${Math.round(row.adjusted_target).toLocaleString()}`,
                      b: `$${Math.round(Math.max(0, row.adjusted_target - row.weekly_target / 7)).toLocaleString()}`,
                    })
                  : t('kpi_board.menu_target_up', 'count it again: target {{a}} → {{b}}', {
                      a: `$${Math.round(row.adjusted_target).toLocaleString()}`,
                      b: `$${Math.round(row.adjusted_target + row.weekly_target / 7).toLocaleString()}`,
                    }),
                disabled: true,
                onSelect: () => {},
              }] : []),
              ...(reason == null && suggested.has(d) ? [{
                key: 'confirm-suggest',
                label: t('kpi_board.confirm_suggest',
                  'Confirm {{reason}} — {{source}}',
                  { reason: suggested.get(d)!.reason,
                    source: suggested.get(d)!.source }),
                separatorBefore: true,
                onSelect: () => onMark(d, suggested.get(d)!.reason),
              }] : []),
              ...(reason != null ? [{
                key: 'clear',
                label: t('kpi_board.clear', 'Active day (clear mark)'),
                separatorBefore: true,
                onSelect: () => onMark(d, null),
              }] : []),
              ...REASONS.map((r, i) => ({
                key: r,
                label: t(`kpi_board.reason_${r.replace(' ', '_')}`,
                  r.charAt(0).toUpperCase() + r.slice(1)),
                disabled: reason === r,
                separatorBefore: i === 0 && reason == null,
                onSelect: () => onMark(d, r),
              })),
              {
                key: 'cancel',
                label: t('common.cancel', 'Cancel'),
                separatorBefore: true,
                onSelect: () => {},
              },
            ]}
          >
            <button type="button" className={`${wrapCls} text-left`}
              aria-label={(reason != null
                ? t('kpi_board.day_aria_unmark',
                    'Make {{day}} count again for unit {{unit}}{{stake}}',
                    { day: dayLabel(d), unit: row.vehicle_unit,
                      stake: row.weekly_target != null
                        ? ` — ${t('kpi_board.aria_up', 'target +${{v}}',
                            { v: Math.round(row.weekly_target / 7).toLocaleString() })}` : '' })
                : suggested.has(d)
                  /* The suggestion state must reach the screen reader —
                     without this prefix a REPAIR? cell announced exactly
                     like a plain empty day. */
                  ? t('kpi_board.day_aria_suggest',
                      '{{reason}} suggested — confirm {{day}} inactive for unit {{unit}}{{stake}}',
                      { reason: suggested.get(d)!.reason,
                        day: dayLabel(d), unit: row.vehicle_unit,
                        stake: row.weekly_target != null
                          ? ` — ${t('kpi_board.aria_down', 'target −${{v}}',
                              { v: Math.round(row.weekly_target / 7).toLocaleString() })}` : '' })
                  : t('kpi_board.day_aria_mark',
                    'Mark {{day}} inactive for unit {{unit}}{{stake}}',
                    { day: dayLabel(d), unit: row.vehicle_unit,
                      stake: row.weekly_target != null
                        ? ` — ${t('kpi_board.aria_down', 'target −${{v}}',
                            { v: Math.round(row.weekly_target / 7).toLocaleString() })}` : '' }))}>
              {cell}
            </button>
          </ActionMenu>
        );
      })}
    </div>
  );
}
