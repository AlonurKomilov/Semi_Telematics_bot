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
import { useCallback, useMemo, useRef, useState } from 'react';
import { useQuery, useQueryClient } from '@tanstack/react-query';
import { useTranslation } from 'react-i18next';
import { ChevronDown, ChevronRight, ChevronsDownUp, ChevronsUpDown, TriangleAlert } from 'lucide-react';
import { toast } from 'sonner';
import { Button } from '../../../components/ui/button';
import { toneClasses, toneText } from '../../../lib/status';
import { AnchoredMenu } from '../../../components/ui/context-menu';
import { dayRange } from './board/geometry';
import { dayMenuItems } from './board/menu';
import { BoardGlyphDefs, CalendarOffGlyph } from './board/glyphs';
import { NearGate } from './board/NearGate';
import { DayCells } from './board/DayCells';
import { UnitCard } from './board/UnitCard';
import { dayLabel, usd } from './board/shared';
import {
  getIncentiveRunLoads, patchIncentiveRow,
  type DaySuggestion, type InactiveDate, type RunDetail, type RunLoad, type RunRow,
} from '../api';

// Frozen empties: `?? []` allocates a NEW array every render, which
// makes every memoized row look changed.
const EMPTY_NAMES: string[] = [];
const EMPTY_SUGGESTIONS: DaySuggestion[] = [];

export default function RunBoard({ run, draft, onChanged, onRecreate, onOpenLoad }: {
  run: RunDetail;
  draft: boolean;
  onChanged: () => void;
  /** Open one load's details — the page owns the surface that shows
   *  them, the board only says WHICH load the user clicked. */
  onOpenLoad: (row: RunRow, load: RunLoad) => void;
  /** Discard this draft and regenerate the same period from live loads
   *  (the stale banner's remedy) — parent owns the mutation + confirm. */
  onRecreate: () => void;
}) {
  const { t } = useTranslation();
  const [collapsed, setCollapsed] = useState<Record<string, boolean>>({});
  const [busyRow, setBusyRow] = useState<number | null>(null);
  // The board's ONE day menu — cells are plain buttons that anchor it
  // here (board/menu.ts builds the items on open).  A menu component
  // per cell priced large periods out.
  const [menuCell, setMenuCell] = useState<{
    row: RunRow; day: string; anchor: HTMLElement;
  } | null>(null);
  const openDayMenu = useCallback(
    (row: RunRow, day: string, anchor: HTMLElement) =>
      setMenuCell({ row, day, anchor }), []);
  const qc = useQueryClient();
  // Each dispatcher's card scrolls ALONE (owner decision 2026-08-20):
  // a manager reviews one person at a time and their loads differ, so
  // the cards never mirror each other.  Do not reintroduce a shared
  // scroll position.
  //
  // The seam shadow is toggled IMPERATIVELY on the unit pane's node —
  // never through state.  A setState at the 0-boundary re-rendered
  // every section, row and day cell at the exact moment the user
  // started scrolling, which read as a freeze.
  const unitPanesRef = useRef<Map<string, HTMLDivElement>>(new Map());
  const registerUnitPane = useCallback(
    (key: string) => (el: HTMLDivElement | null) => {
      const m = unitPanesRef.current;
      if (el) m.set(key, el); else m.delete(key);
    }, []);
  const onBoardScroll = useCallback(
    (key: string) => (e: React.UIEvent<HTMLDivElement>) => {
      const pane = unitPanesRef.current.get(key);
      if (pane) pane.classList.toggle('shadow-md', e.currentTarget.scrollLeft > 0);
    }, []);

  const loadsQ = useQuery({
    queryKey: ['kpi-incentive-run-loads', run.id],
    queryFn: () => getIncentiveRunLoads(run.id),
  });

  const days = useMemo(
    () => dayRange(run.period_start, run.period_end),
    [run.period_start, run.period_end]);
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
  const alsoUnderByRow = useMemo(() => {
    const byUnit = new Map<string, string[]>();
    for (const row of run.rows) {
      if (!row.vehicle_unit) continue;
      const list = byUnit.get(row.vehicle_unit) ?? [];
      list.push(row.dispatcher_name);
      byUnit.set(row.vehicle_unit, list);
    }
    // One STABLE array per row id — a fresh .filter() per render would
    // defeat memo on every card.
    const out = new Map<number, string[]>();
    for (const row of run.rows) {
      const others = (byUnit.get(row.vehicle_unit) ?? [])
        .filter((n) => n !== row.dispatcher_name);
      out.set(row.id, others.length ? others : EMPTY_NAMES);
    }
    return out;
  }, [run.rows]);

  const markDay = useCallback(async (row: RunRow, day: string, reason: string | null) => {
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
  }, [qc, run.id, onChanged]);

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
      <BoardGlyphDefs />
      <div className="space-y-3">
      {draft && (
        /* The board's one non-obvious gesture, said once where the days
           are — a flat cell gives no affordance cue by itself. */
        <p className="text-xs text-muted-foreground">
          {t('kpi_board.hint4',
            'Click a day and pick a reason (home time, repair, holiday) to mark it inactive — the truck’s target lowers with each inactive day. Click an inactive day to make it count again — the target rises back.')}
        </p>
      )}
      {loadsQ.isPending && draft && (
        /* The drift banner mounts here when the loads query lands —
           on live TMS data drift is the COMMON case, and its late
           insertion pushed the legend and every section down (the
           CLS cluster DevTools showed).  An invisible same-structure
           skeleton holds the exact height; no drift → it collapses
           (the rare case pays the shift, not the common one). */
        <div className="invisible flex flex-wrap items-center gap-2 text-xs px-2 py-1.5 rounded" aria-hidden>
          <span>&nbsp;</span>
          <Button size="sm" variant="outline" tabIndex={-1}>&nbsp;</Button>
        </div>
      )}
      {drift > 0 && (
        /* Instruction + the control for it in one block: prose that
           prescribes an action the reader cannot take is a dead end. */
        <div className={`flex flex-wrap items-center gap-2 text-xs ${toneClasses('warn')} px-2 py-1.5 rounded`}>
          <TriangleAlert className="shrink-0 size-3" />
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
            <CalendarOffGlyph className="text-muted-foreground/60" />
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
      {/* One gesture to shrink a 69-row board to its 12 summary bands
          — collapsed sections keep name, counts and total. */}
      <div className="flex justify-end">
        <Button variant="ghost" size="sm"
          onClick={() => {
            const names = [...byDispatcher.keys()];
            const allCollapsed = names.every((n) => collapsed[n]);
            setCollapsed(Object.fromEntries(names.map((n) => [n, !allCollapsed])));
          }}>
          {[...byDispatcher.keys()].every((n) => collapsed[n]) ? (
            <>
              <ChevronsUpDown className="mr-1.5" />
              {t('kpi_board.expand_all', 'Expand all')}
            </>
          ) : (
            <>
              <ChevronsDownUp className="mr-1.5" />
              {t('kpi_board.collapse_all', 'Collapse all')}
            </>
          )}
        </Button>
      </div>
      {[...byDispatcher.entries()].map(([name, rows], sectionIdx) => {
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
                ? <ChevronRight className="text-muted-foreground shrink-0 size-4" />
                : <ChevronDown className="text-muted-foreground shrink-0 size-4" />}
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

            {/* O-1 (owner-approved): the body mounts only when near
                the viewport — opening the board costs the on-screen
                sections, never the fleet.  The gate holds the exact
                final height (day-header 32 + rows × 144, border-box),
                so page length and layout never move. */}
            {!isCollapsed && (
              <NearGate index={sectionIdx} height={32 + rows.length * 144}>
              <div className="flex">
                {/* Unit pane — OUTSIDE the scroller: this info never
                    moves, so the scrollbar must not run under it.
                    w-72: the card's content wants ~290px. */}
                <div ref={registerUnitPane(name)}
                  className="w-72 shrink-0 border-r border-border">
                  <div className="flex h-8 items-center bg-muted px-3 text-xs font-medium uppercase tracking-wide text-muted-foreground border-b border-border">
                    {t('kpi_board.unit', 'Unit')}
                  </div>
                  {rows.map((row) => (
                    <UnitCard
                      key={row.id}
                      row={row}
                      loads={loadsQ.data ? (loadsQ.data.rows[String(row.id)] ?? []) : undefined}
                      periodStart={run.period_start}
                      periodEnd={run.period_end}
                      stale={loadsQ.data?.drift.includes(row.id) ?? false}
                      clickable={draft && busyRow !== row.id}
                      alsoUnder={alsoUnderByRow.get(row.id) ?? EMPTY_NAMES}
                    />
                  ))}
                </div>
                {/* Days pane — the ONLY thing that scrolls; its
                    scrollbar starts where the calendar starts. */}
                <div
                  onScroll={onBoardScroll(name)}
                  className="min-w-0 flex-1 overflow-x-auto"
                >
                  {/* minWidth is the scroll floor: below 112px/day the
                      pane scrolls; above it the columns grow equally,
                      so a wide window spends its space on the days. */}
                  <div className="min-w-full" style={{ minWidth: days.length * 112 }}>
                    <div className="flex h-8 border-b border-border bg-muted">
                      {days.map((d) => (
                        <div key={d} className="flex flex-1 items-center px-2 text-xs text-muted-foreground border-r border-border last:border-r-0">
                          {dayLabel(d)}
                        </div>
                      ))}
                    </div>
                    {rows.map((row) => (
                      <DayCells
                        key={row.id}
                        row={row}
                        days={days}
                        loads={loadsQ.data ? (loadsQ.data.rows[String(row.id)] ?? []) : undefined}
                        suggestions={loadsQ.data?.suggestions[String(row.id)] ?? EMPTY_SUGGESTIONS}
                        clickable={draft && busyRow !== row.id}
                        onOpenMenu={openDayMenu}
                        onOpenLoad={onOpenLoad}
                      />
                    ))}
                  </div>
                </div>
              </div>
              </NearGate>
            )}
          </section>
        );
      })}
      </div>

      <AnchoredMenu
        open={menuCell != null}
        anchor={menuCell?.anchor ?? null}
        onOpenChange={(o) => { if (!o) setMenuCell(null); }}
        items={menuCell == null ? [] : dayMenuItems({
          row: menuCell.row,
          day: menuCell.day,
          suggestion: (loadsQ.data?.suggestions[String(menuCell.row.id)] ?? [])
            .find((sug) => sug.date === menuCell.day),
          t,
          onMark: markDay,
        })}
      />
    </div>
  );
}
