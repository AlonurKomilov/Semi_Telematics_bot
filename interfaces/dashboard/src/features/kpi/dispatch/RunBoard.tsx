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
import { useRef, useState } from 'react';
import { useQuery, useQueryClient } from '@tanstack/react-query';
import { useTranslation } from 'react-i18next';
import { CalendarOff, ChevronDown, ChevronRight, TriangleAlert } from 'lucide-react';
import { toast } from 'sonner';
import { Button } from '../../../components/ui/button';
import { Tip } from '../../../components/tooltip';
import { daysCell, loadedDayCount, matchedTip, nextDay } from './explain';
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

/** The zero-reason, with the numbers that CAUSED it — a verdict without
 *  its threshold is unarguable and unexplainable. */
function zeroTip(row: RunRow, t: (k: string, d: string, o?: Record<string, unknown>) => string): string {
  const g = `$${Math.round(row.kpi_gross).toLocaleString()}`;
  const tgt = row.adjusted_target
    ? `$${Math.round(row.adjusted_target).toLocaleString()}` : '';
  if (row.zero_reason === 'no_target') {
    return t('kpi_board.zt_no_target', 'This company has no weekly target configured — set one in KPI configuration.');
  }
  if (row.zero_reason === 'floor') {
    return t('kpi_board.zt_floor', '{{g}} gross at RPM {{rpm}} is under BOTH removal floors.', { g, rpm: row.rpm ?? '—' });
  }
  if (row.zero_reason === 'no_active_days') {
    return t('kpi_board.zt_days', 'Every day of the window is marked inactive.');
  }
  return t('kpi_board.zt_tier', '{{g}} gross vs {{tgt}} target at RPM {{rpm}} matches no tier.', { g, tgt, rpm: row.rpm ?? '—' });
}

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
  // Eleven cards, ONE scroll position: every card's day scroller mirrors
  // the one being dragged, so reading Sunday costs one scroll, not
  // eleven.  Imperative (no state) — setting scrollLeft on siblings
  // cannot re-render at scroll rate.
  const scrollersRef = useRef<Map<string, HTMLDivElement>>(new Map());
  const [scrolled, setScrolled] = useState(false);
  const registerScroller = (key: string) => (el: HTMLDivElement | null) => {
    const m = scrollersRef.current;
    if (el) m.set(key, el); else m.delete(key);
  };
  const onBoardScroll = (e: React.UIEvent<HTMLDivElement>) => {
    const src = e.currentTarget;
    setScrolled(src.scrollLeft > 0);
    for (const el of scrollersRef.current.values()) {
      if (el !== src && el.scrollLeft !== src.scrollLeft) {
        el.scrollLeft = src.scrollLeft;
      }
    }
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
    <div className="space-y-3">
      {draft && (
        /* The board's one non-obvious gesture, said once where the days
           are — a flat cell gives no affordance cue by itself. */
        <p className="text-xs text-muted-foreground">
          {t('kpi_board.hint3',
            'Click a day to mark it inactive (home time, repair, holiday) — the truck’s target lowers with each inactive day. Click an inactive day to make it count again — the target rises back.')}
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
      {/* The board's four day states, named once — a reviewer must be
          able to tell WHY Wednesday is amber and Saturday is not
          without decoding chips by trial. */}
      <div className="flex flex-wrap items-center gap-x-4 gap-y-1 text-xs text-muted-foreground">
        <span className="inline-flex items-center gap-1.5">
          <span className={`inline-block rounded px-1.5 text-xs tabular-nums ${toneClasses('ok')}`}>$950</span>
          {t('kpi_board.leg_loads3', 'load (rate · delivery place)')}
        </span>
        <span className="inline-flex items-center gap-1.5">
          <span className={`inline-block rounded px-1.5 text-xs font-medium uppercase tracking-wide ${toneClasses('warn')}`}>{t('kpi_board.inactive', 'inactive')}</span>
          {t('kpi_board.leg_inactive2', 'not counted')}
        </span>
        <span className="inline-flex items-center gap-1.5">
          <span className={`inline-block rounded border border-dashed border-warn-bd px-1.5 text-xs uppercase tracking-wide ${toneText('warn')}`}>{t('kpi_board.leg_repair', 'repair?')}</span>
          {t('kpi_board.leg_suggested', 'suggested — click to confirm')}
        </span>
        <span className="inline-flex items-center gap-1.5">
          <span className="inline-flex size-5 items-center justify-center rounded border border-dashed border-border">
            <CalendarOff size={12} className="text-muted-foreground/60" aria-hidden />
          </span>
          {t('kpi_board.leg_empty2', 'no loads, counting — click to mark inactive')}
        </span>
      </div>
      {draft && suggestionCount > 0 && (
        <p className="text-xs text-muted-foreground">
          {t('kpi_board.suggest_count',
            '{{n}} suggested repair days on this board (dashed chips) — click each to confirm as inactive, or ignore to leave the day counted.',
            { n: suggestionCount })}
        </p>
      )}

      {[...byDispatcher.entries()].map(([name, rows]) => {
        const gross = rows.reduce((a, r) => a + r.kpi_gross, 0);
        const miles = rows.reduce((a, r) => a + r.miles, 0);
        const baseGross = rows.reduce((a, r) => a + r.base_gross, 0);
        const confirmed = rows.reduce((a, r) => a + r.confirmed_dollars, 0);
        const isCollapsed = !!collapsed[name];
        return (
          <section key={name} className="bg-card border border-border rounded-xl overflow-hidden">
            {/* Section header — the dispatcher's summary band. */}
            <button
              type="button"
              onClick={() => setCollapsed((m) => ({ ...m, [name]: !m[name] }))}
              aria-expanded={!isCollapsed}
              className="w-full flex flex-wrap items-center gap-x-4 gap-y-1 px-4 py-2.5 bg-muted/40 border-b border-border text-left hover:bg-muted/70 transition"
            >
              {isCollapsed
                ? <ChevronRight size={16} className="text-muted-foreground shrink-0" />
                : <ChevronDown size={16} className="text-muted-foreground shrink-0" />}
              <span className="text-sm font-semibold">{name}</span>
              <span className="text-xs text-muted-foreground tabular-nums">
                {t('kpi_board.trucks', '{{n}} trucks', { n: rows.length })}
                {' · '}{Math.round(miles).toLocaleString()} mi
                {' · '}RPM {miles > 0 ? (baseGross / miles).toFixed(2) : '—'}
                {' · '}{usd(gross)}
              </span>
              <span className="ml-auto text-sm font-medium tabular-nums">{usd(confirmed)}</span>
            </button>

            {!isCollapsed && (
              <div
                ref={registerScroller(name)}
                onScroll={onBoardScroll}
                className="overflow-x-auto snap-x scroll-pl-56"
              >
                <div className="w-max min-w-full">
                  {/* Day header row. */}
                  <div className="flex border-b border-border bg-muted/20">
                    <div className={`sticky left-0 z-10 w-56 shrink-0 bg-card px-3 py-1.5 text-xs font-medium uppercase tracking-wide text-muted-foreground border-r border-border ${scrolled ? 'shadow-md' : ''}`}>
                      {t('kpi_board.unit', 'Unit')}
                    </div>
                    {days.map((d, i) => (
                      <div key={d} className={`w-28 flex-none snap-start px-2 py-1.5 text-xs text-muted-foreground border-r border-border last:border-r-0 ${i % 2 === 1 ? 'bg-muted/30' : ''}`}>
                        {dayLabel(d)}
                      </div>
                    ))}
                  </div>

                  {rows.map((row) => (
                    <BoardRow
                      key={row.id}
                      row={row}
                      days={days}
                      loads={loadsQ.data ? (loadsQ.data.rows[String(row.id)] ?? []) : undefined}
                      periodStart={run.period_start}
                      periodEnd={run.period_end}
                      suggestions={loadsQ.data?.suggestions[String(row.id)] ?? []}
                      stale={loadsQ.data?.drift.includes(row.id) ?? false}
                      scrolled={scrolled}
                      clickable={draft && busyRow !== row.id}
                      onMark={(day, reason) => markDay(row, day, reason)}
                    />
                  ))}
                </div>
              </div>
            )}
          </section>
        );
      })}
    </div>
  );
}

function BoardRow({ row, days, loads, suggestions, stale, scrolled, clickable, onMark, periodStart, periodEnd }: {
  row: RunRow;
  days: string[];
  loads: RunLoad[] | undefined;
  periodStart: string;
  periodEnd: string;
  /** Maintenance-suggested inactive days (human confirms by click). */
  suggestions: DaySuggestion[];
  /** Live loads no longer sum to this row's snapshot. */
  stale: boolean;
  /** Any card is horizontally scrolled — draw the frozen-column seam. */
  scrolled: boolean;
  clickable: boolean;
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
  const daysDiff = (a: string, z: string) =>
    Math.round((Date.parse(`${z}T00:00:00Z`) - Date.parse(`${a}T00:00:00Z`)) / 86_400_000);
  const spanEnd = (l: RunLoad): string => {
    const a = (l.pickup_date || '').slice(0, 10);
    const bRaw = (l.delivery_date || '').slice(0, 10);
    const end = bRaw && bRaw > a ? bRaw : a;
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
    let total = 1;
    for (let d = a; d < end; d = nextDay(d)) total += 1;
    let dayNo = 2;
    for (let d = nextDay(a); d <= end; d = nextDay(d), dayNo += 1) {
      if (!transitInfo.has(d)) {
        transitInfo.set(d, { load: l, dayNo, total, last: end });
      }
    }
  }
  const inWindow = (d: string) =>
    d >= row.window_start.slice(0, 10) && d <= row.window_end.slice(0, 10);

  return (
    <div className="flex border-b border-border last:border-b-0">
      {/* Truck identity + its sheet numbers, pinned while days scroll.
          Gross and the zero-reason live HERE so a $0.00 row explains
          itself without switching to the sheet. */}
      <div className={`sticky left-0 z-10 w-56 shrink-0 bg-card px-3 py-2 border-r border-border ${scrolled ? 'shadow-md' : ''}`}>
        <div className="text-sm font-medium">
          {row.vehicle_unit || t('kpi_board.unassigned', 'Unassigned unit')}
          <span className="ml-1.5 text-xs text-muted-foreground">{row.company_code}</span>
        </div>
        <div className="text-xs text-muted-foreground tabular-nums">
          <Tip label={<DaysTipContent row={row} loads={loads}
            draft={clickable} periodStart={periodStart}
            periodEnd={periodEnd} t={t} />}>
            <span className="underline decoration-dotted decoration-muted-foreground/60 underline-offset-4 cursor-help">
              {daysCell(row, loadedDayCount(loads, row.window_start, row.window_end), t)}
            </span>
          </Tip>
          {' · '}
          <span className="whitespace-nowrap">
            ${Math.round(row.kpi_gross).toLocaleString()}
            {row.weekly_target != null && (
              <span className="text-muted-foreground/70">
                {' '}{t('kpi_board.vs_target', 'vs {{tgt}}',
                  { tgt: `$${Math.round(row.adjusted_target).toLocaleString()}` })}
              </span>
            )}
          </span>
          {' · '}
          {row.matched_rule ? (
            <Tip label={matchedTip(row.matched_rule, t)}>
              <span className="underline decoration-dotted decoration-muted-foreground/60 underline-offset-4 cursor-help">
                {Number(row.pct)}%
              </span>
            </Tip>
          ) : (
            <>{Number(row.pct)}%</>
          )}
          {' → '}{usd(row.confirmed_dollars)}
        </div>
        {Number(row.pct) === 0 && row.zero_reason && (
          <Tip label={zeroTip(row, t)}>
            <span className={`mt-1 inline-block text-xs font-medium ${toneClasses('warn')} px-2 py-0.5 rounded-md`}>
              {row.zero_reason === 'floor' ? t('kpi_runs.zr_floor', 'below floor')
                : row.zero_reason === 'no_active_days' ? t('kpi_runs.zr_days', 'no active days')
                  : row.zero_reason === 'no_target' ? t('kpi_runs.zr_target', 'no target')
                    : t('kpi_runs.zr_tier', 'no tier met')}
            </span>
          </Tip>
        )}
        {row.next_tier && (
          /* Endowed progress: the row is already most of the way to the
             next tier — a shortfall stated in dollars converts a dead
             number into a target. */
          <div className="mt-1 space-y-0.5">
            <div className="text-xs text-muted-foreground tabular-nums">
              {t('kpi_board.next_tier',
                '{{gap}} short of the {{pct}}% tier ({{at}})',
                { gap: `$${Math.round(row.next_tier.gap).toLocaleString()}`,
                  pct: row.next_tier.pct,
                  at: `$${row.next_tier.dollars_at.toFixed(2)}` })}
            </div>
            <div className="h-1 w-40 max-w-full rounded bg-muted overflow-hidden">
              <div className="h-full rounded bg-primary/60"
                style={{ width: `${Math.min(100, Math.round(
                  (row.kpi_gross / (row.kpi_gross + row.next_tier.gap)) * 100))}%` }} />
            </div>
          </div>
        )}
        {stale && (
          <Tip label={t('kpi_board.stale_tip', 'This row’s loads changed after the run was generated — it still pays from the snapshot.')}>
            <span className={`mt-1 ml-1 inline-block text-xs font-medium ${toneClasses('warn')} px-2 py-0.5 rounded-md`}>
              {t('kpi_board.stale', 'stale')}
            </span>
          </Tip>
        )}
      </div>

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
              !inside ? 'bg-muted/40'
                : reason != null ? 'bg-warn-bg'
                  : i % 2 === 1 ? 'bg-muted/30' : ''
            } ${clickable && inside ? 'cursor-pointer hover:bg-muted/50' : ''}`}
          >
            {dayLoads.slice(0, 2).map((l, i) => {
              const cont = spanEnd(l) > d;
              // Text runway = this cell + every following covered cell
              // (w-28 = 112px each), minus the 6px insets both ends.
              const runway = cont
                ? (daysDiff(d, spanEnd(l)) + 1) * 112 - 12 : undefined;
              return (
                <Tip key={i}
                  label={`${place(l.pickup_location)} → ${place(l.delivery_location)} · ${usd(l.total_rate)} · ${Math.round(l.miles).toLocaleString()} mi`}>
                  <div
                    /* h-6 + leading-6 (not flex): truncate's ellipsis
                       needs a block formatting context — text inside a
                       flex container clips without the "…". */
                    className={`block h-6 leading-6 rounded text-xs tabular-nums ${toneClasses('ok')} ${
                      cont
                        ? 'rounded-r-none relative z-10 w-max max-w-none overflow-hidden whitespace-nowrap pl-1.5 pr-2'
                        : 'px-1.5 truncate'}`}
                    style={cont ? { maxWidth: runway } : undefined}
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
              <span className="absolute bottom-1 right-1 hidden group-hover:inline-flex items-center justify-center size-5 rounded border border-dashed border-border bg-card"
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
              const continues = d < info.last;
              return (
                <Tip label={t('kpi_board.transit_tip2',
                  '{{rate}} to {{place}} — in transit, day {{i}} of {{n}}; delivers {{del}}. A working day; it counts.', {
                    rate: `$${Math.round(info.load.total_rate).toLocaleString()}`,
                    place: place(info.load.delivery_location) || info.load.load_number,
                    i: info.dayNo, n: info.total, del: dayLabel(info.last),
                  })}>
                  {/* FLAT — the pickup chip already carries the text
                      once; repeating it per piece breaks the one-bar
                      reading.  h-6 = the chip's exact height, so the
                      bar runs level; AT still hears the story. */}
                  {/* Full-strength token: --ok-bg is ALREADY a 15%
                      tint (color-mix), so an alpha modifier on top
                      painted a ~9% wash — invisible on white.  Same
                      fill as the chip = one unbroken bar. */}
                  <span className={`block h-6 bg-ok-bg -ml-1.5 ${
                    continues ? 'rounded-none -mr-1.5' : 'rounded-r rounded-l-none'}`}>
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
        // The bar erases the grid line it crosses: no right border on
        // a cell whose NEXT day is covered by a rolling load (next day
        // being a transit day implies the span includes this one).
        const seamless = transitInfo.has(nextDay(d));
        const wrapCls = `w-28 flex-none snap-start ${
          seamless ? '' : 'border-r'} border-border last:border-r-0`;
        if (!clickable || !inside) return <div key={d} className={wrapCls}>{cell}</div>;
        return (
          <ActionMenu
            key={d}
            items={[
              {
                key: 'heading',
                label: `${row.vehicle_unit || t('kpi_board.unassigned', 'Unassigned unit')} · ${dayLabel(d)}`,
                disabled: true,
                onSelect: () => {},
              },
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
