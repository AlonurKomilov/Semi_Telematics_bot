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
import { useQuery } from '@tanstack/react-query';
import { useTranslation } from 'react-i18next';
import { ChevronDown, ChevronRight, Plus, TriangleAlert } from 'lucide-react';
import { toast } from 'sonner';
import { Tip } from '../../../components/tooltip';
import { ActionMenu } from '../../../components/ui/context-menu';
import { toneClasses } from '../../../lib/status';
import {
  getIncentiveRunLoads, patchIncentiveRow,
  type InactiveDate, type RunDetail, type RunLoad, type RunRow,
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

export default function RunBoard({ run, draft, onChanged }: {
  run: RunDetail;
  draft: boolean;
  onChanged: () => void;
}) {
  const { t } = useTranslation();
  const [collapsed, setCollapsed] = useState<Record<string, boolean>>({});
  const [busyRow, setBusyRow] = useState<number | null>(null);
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
      await patchIncentiveRow(run.id, row.id, { inactive_dates: marks });
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

  return (
    <div className="space-y-3">
      {draft && (
        /* The board's one non-obvious gesture, said once where the days
           are — a flat cell gives no affordance cue by itself. */
        <p className="text-xs text-muted-foreground">
          {t('kpi_board.hint',
            'Click a day to mark it inactive (home time, repair, holiday) — the truck’s target lowers with each marked day.')}
        </p>
      )}
      {drift > 0 && (
        <p className={`inline-flex items-center gap-1.5 text-xs ${toneClasses('warn')} px-2 py-1 rounded`}>
          <TriangleAlert size={12} />
          {t('kpi_board.drift',
            'Loads changed since this run was generated — the affected rows still pay from the run’s snapshot. Recreate the draft to re-read them.')}
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
              <span className="text-xs text-muted-foreground">
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
                      <div key={d} className={`min-w-28 flex-1 shrink-0 snap-start px-2 py-1.5 text-xs text-muted-foreground border-r border-border last:border-r-0 ${i % 2 === 1 ? 'bg-muted/30' : ''}`}>
                        {dayLabel(d)}
                      </div>
                    ))}
                  </div>

                  {rows.map((row) => (
                    <BoardRow
                      key={row.id}
                      row={row}
                      days={days}
                      loads={loadsQ.data?.rows[String(row.id)] ?? []}
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

function BoardRow({ row, days, loads, scrolled, clickable, onMark }: {
  row: RunRow;
  days: string[];
  loads: RunLoad[];
  /** Any card is horizontally scrolled — draw the frozen-column seam. */
  scrolled: boolean;
  clickable: boolean;
  onMark: (day: string, reason: string | null) => void;
}) {
  const { t } = useTranslation();
  const marks = new Map((row.inactive_dates ?? []).map((m) => [m.date, m.reason]));
  const byDay = new Map<string, RunLoad[]>();
  for (const l of loads) {
    const d = l.pickup_date;
    byDay.set(d, [...(byDay.get(d) ?? []), l]);
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
          {row.total_days - row.inactive_days}/{row.total_days}
          {' '}{t('kpi_board.days', 'days')}
          {' · '}${Math.round(row.kpi_gross).toLocaleString()}
          {' · '}{Number(row.pct)}% → {usd(row.confirmed_dollars)}
        </div>
        {Number(row.pct) === 0 && row.zero_reason && (
          <span className={`mt-1 inline-block text-xs ${toneClasses('warn')} px-1.5 py-0.5 rounded`}>
            {row.zero_reason === 'floor' ? t('kpi_runs.zr_floor', 'below floor')
              : row.zero_reason === 'no_active_days' ? t('kpi_runs.zr_days', 'no active days')
                : row.zero_reason === 'no_target' ? t('kpi_runs.zr_target', 'no target')
                  : t('kpi_runs.zr_tier', 'no tier met')}
          </span>
        )}
      </div>

      {days.map((d, i) => {
        const dayLoads = byDay.get(d) ?? [];
        const reason = marks.get(d);
        const inside = inWindow(d);
        const cell = (
          <div
            className={`min-w-28 flex-1 shrink-0 snap-start min-h-14 px-1.5 py-1.5 border-r border-border last:border-r-0 space-y-1 ${
              !inside ? 'bg-muted/40'
                : reason != null ? 'bg-warn-bg'
                  : i % 2 === 1 ? 'bg-muted/30' : ''
            } ${clickable && inside ? 'cursor-pointer hover:bg-muted/50' : ''}`}
          >
            {dayLoads.slice(0, 2).map((l, i) => (
              <Tip key={i}
                label={`${place(l.pickup_location)} → ${place(l.delivery_location)} · ${usd(l.total_rate)} · ${Math.round(l.miles).toLocaleString()} mi`}>
                <div className={`rounded px-1.5 py-0.5 text-xs ${toneClasses('ok')} truncate`}>
                  {place(l.delivery_location) || l.load_number} · ${Math.round(l.total_rate).toLocaleString()}
                </div>
              </Tip>
            ))}
            {dayLoads.length > 2 && (
              <div className="px-1.5 text-xs text-muted-foreground">
                +{dayLoads.length - 2} {t('kpi_board.more', 'more')}
              </div>
            )}
            {reason != null && (
              <div className={`rounded px-1.5 py-0.5 text-xs ${toneClasses('warn')} uppercase tracking-wide truncate`}>
                {reason || t('kpi_board.inactive', 'inactive')}
              </div>
            )}
            {clickable && inside && dayLoads.length === 0 && reason == null && (
              /* A persistent dashed WELL, not a 40%-opacity glyph — the
                 hint names day-marking as the surface's main
                 interaction, so its target carries real drawn area. */
              <span className="flex h-6 items-center justify-center rounded border border-dashed border-border">
                <Plus size={12} className="text-muted-foreground/60" aria-hidden />
              </span>
            )}
          </div>
        );
        if (!clickable || !inside) return <div key={d}>{cell}</div>;
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
            <button type="button" className="text-left"
              aria-label={t('kpi_board.day_aria', 'Mark {{day}} for unit {{unit}}',
                { day: d, unit: row.vehicle_unit })}>
              {cell}
            </button>
          </ActionMenu>
        );
      })}
    </div>
  );
}
