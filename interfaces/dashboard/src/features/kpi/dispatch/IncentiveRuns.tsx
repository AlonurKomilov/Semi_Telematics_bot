/**
 * Dispatch KPI — the dispatch section's page: the settlement sheet.
 *
 * Owner decision (2026-08-17): the incentives surface IS Dispatch KPI —
 * one page, gated by ``can_kpi`` (the separate can_kpi_incentives flag
 * was folded away; granting KPI grants payout visibility).  The A–D
 * grades page retired with it; its backend endpoint + thresholds
 * config remain for a future return.
 *
 * The run detail grid mirrors the customer's Excel column-for-column
 * (unit, window, days, extras, gross, miles, RPM, target, KPI-%, KPI-$,
 * confirmed, reason) so the output is a document their managers already
 * know how to read.  Per-row editing goes through the grid's own
 * ``rowActions`` (the house pattern), opening small dialogs:
 * "Extras" edits TONU/bonus and recomputes (days are edited per-day on
 * the BOARD — one editor per fact); "Exception" overrides the percent
 * with a mandatory reason, validated server-side against the run's
 * snapshot cap.
 *
 * A finalized run renders read-only: the paid record, never re-priced.
 */
import { useEffect, useState } from 'react';
import { Link } from 'react-router-dom';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { useTranslation } from 'react-i18next';
import {
  ArrowRight, BadgeDollarSign, CalendarRange, Check, Download, History, ListChecks,
  Loader2, Lock, Pencil, Plus, Scale, Table2, Trash2,
} from 'lucide-react';
import { toast } from 'sonner';
import DataGrid from '../../../components/datagrid';
import { ActivityTrailDialog } from '../../../components/activity-trail/ActivityTrailDialog';
import { ARCHIVE_COLUMNS, runSheetColumns } from './runs/columns';
import { usd } from './runs/format';
import { AdjustmentsDrawer } from './runs/AdjustmentsDrawer';
import { EditRowDialog } from './runs/EditRowDialog';
import { ExceptionDialog } from './runs/ExceptionDialog';
import { MonthlyPayoutsPanel } from './runs/MonthlyPayoutsPanel';
import { NewRunDialog } from './runs/NewRunDialog';
import { RunNoteLine } from './runs/RunNoteLine';
import { EmptyState, ErrorState, PageHeader, TableSkeleton } from '../../../components/shell';
import { Button } from '../../../components/ui/button';
import {
  Dialog, DialogContent, DialogFooter, DialogHeader, DialogTitle,
} from '../../../components/ui/dialog';
import { Tip } from '../../../components/tooltip';
import { ScrollRegion } from '../../../components/scrolling';
import { toneClasses, toneText } from '../../../lib/status';
import { usePreference } from '../../../preferences';
import RunBoard from './RunBoard';
import { isHandAdjusted } from './explain';
import { SectionSwitcher } from '../SectionSwitcher';
import { FeatureConfigGear } from '../../_lib/FeatureConfigGear';
import {
  createIncentiveRun, deleteIncentiveRun, downloadIncentiveRunCsv, finalizeIncentiveRun,
  getIncentiveRun, getIncentiveRunLoads, listIncentiveRuns,
  type RunDetail, type RunRow, type RunSummary,
} from '../api';


/** Days a DRAFT has sat past its period end — money payroll cannot see
 *  yet.  0 for finalized or still-running periods. */
function draftOverdueDays(r: RunSummary): number {
  if (r.status !== 'draft') return 0;
  const end = new Date(`${r.period_end.slice(0, 10)}T00:00:00Z`).getTime();
  return Math.max(0, Math.floor((Date.now() - end) / 86_400_000) - 1);
}

// The strip shows the WORKING SET; the archive collapses.  At weekly
// cadence a year mints 52 runs — an unbounded chip row would push the
// settlement grid below the fold and keep growing.
const RECENT_RUNS = 8;

const GRADE_TONE: Record<string, 'ok' | 'info' | 'warn' | 'danger'> = {
  A: 'ok', B: 'info', C: 'warn', D: 'danger',
};

/** The retired grades page's pill, reborn beside each payout: the
 *  dispatcher's A–D for THIS period (analytics; never payout math). */
function GradePill({ value }: { value?: string }) {
  if (!value) return null;
  return (
    <span className={`inline-flex items-center justify-center w-5 h-5 rounded-full border text-xs font-semibold ${toneClasses(GRADE_TONE[value] ?? 'neutral')}`}>
      {value}
    </span>
  );
}

export default function IncentiveRuns() {
  const { t } = useTranslation();
  const qc = useQueryClient();
  const [selected, setSelected] = useState<number | null>(null);
  const [newOpen, setNewOpen] = useState(false);
  const [editRow, setEditRow] = useState<RunRow | null>(null);
  const [exceptRow, setExceptRow] = useState<RunRow | null>(null);
  const [finalizeOpen, setFinalizeOpen] = useState(false);
  const [discardOpen, setDiscardOpen] = useState(false);
  const [recreateOpen, setRecreateOpen] = useState(false);
  const [adjustmentsOpen, setAdjustmentsOpen] = useState(false);
  const [trailOpen, setTrailOpen] = useState(false);
  const [loadsRow, setLoadsRow] = useState<RunRow | null>(null);
  const [showAllRuns, setShowAllRuns] = useState(false);
  // Sheet = the numeric settlement (DataGrid); Board = the same run laid
  // out per dispatcher × day.  A synced preference — a reading style.
  const { value: viewMode, setValue: setViewMode } = usePreference('kpi.incentiveRunView');

  const runsQ = useQuery({
    queryKey: ['kpi-incentive-runs'],
    queryFn: listIncentiveRuns,
  });

  // Newest first — the working period is the one being settled.
  const allRuns = [...(runsQ.data?.runs ?? [])].sort(
    (a, b) => b.period_start.localeCompare(a.period_start) || b.id - a.id,
  );
  // The selected run's chip stays visible even from the collapsed strip.
  const visibleRuns = (() => {
    const head = allRuns.slice(0, RECENT_RUNS);
    const sel = allRuns.find((r) => r.id === selected);
    if (sel && !head.some((r) => r.id === sel.id)) head.push(sel);
    return head;
  })();

  // The detail region never renders BLANK while runs exist: with nothing
  // chosen (first load, after a discard) the newest run selects itself.
  // The same effect is the GHOST GUARD: a selected id missing from the
  // fetched list (discarded here, or by another session) deselects —
  // without it the panel kept rendering a deleted run from cache and
  // refetching its 404 forever.
  const newestId = allRuns[0]?.id;
  const runsLoaded = runsQ.data != null;
  useEffect(() => {
    if (!runsLoaded) return;
    if (selected != null && !allRuns.some((r) => r.id === selected)) {
      setSelected(newestId ?? null);
      return;
    }
    if (selected == null && newestId != null) setSelected(newestId);
    // allRuns is rebuilt per render; runsQ.data is the stable dep.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [runsLoaded, runsQ.data, selected, newestId]);
  const detailQ = useQuery<RunDetail>({
    queryKey: ['kpi-incentive-run', selected],
    queryFn: () => getIncentiveRun(selected as number),
    enabled: selected != null,
    retry: (n, err) =>
      !(err instanceof Error && /not found/i.test(err.message)) && n < 2,
  });

  const refresh = () => {
    qc.invalidateQueries({ queryKey: ['kpi-incentive-runs'] });
    if (selected != null) {
      qc.invalidateQueries({ queryKey: ['kpi-incentive-run', selected] });
    }
  };

  const run = detailQ.data;
  const draft = run?.status === 'draft';
  const zeroCount = run ? run.rows.filter((r) => Number(r.pct) === 0).length : 0;
  const markedDays = run ? run.rows.reduce((a, r) => a + r.inactive_days, 0) : 0;
  const runTotal = run ? run.rows.reduce((a, r) => a + r.confirmed_dollars, 0) : 0;
  const runGross = run ? run.rows.reduce((a, r) => a + r.kpi_gross, 0) : 0;
  const adjustedRows = run ? run.rows.filter(isHandAdjusted) : [];
  // The board's loads query, shared by key — the parent reads drift to
  // annotate Finalize while the run is stale.
  const runLoadsQ = useQuery({
    queryKey: ['kpi-incentive-run-loads', selected],
    queryFn: () => getIncentiveRunLoads(selected as number),
    enabled: selected != null,
    retry: (n, err) =>
      !(err instanceof Error && /not found/i.test(err.message)) && n < 2,
  });
  const staleCount = runLoadsQ.data?.drift.length ?? 0;

  const discard = useMutation({
    mutationFn: () => deleteIncentiveRun(selected as number),
    onSuccess: () => {
      const dead = selected;
      setDiscardOpen(false);
      setSelected(null);
      // Filter the cached list NOW — the auto-select effect runs before
      // the refetch lands, and a stale list would hand it the run that
      // was just deleted.
      qc.setQueryData(['kpi-incentive-runs'],
        (old: { runs: RunSummary[] } | undefined) => old
          ? { ...old, runs: old.runs.filter((r) => r.id !== dead) }
          : old);
      qc.removeQueries({ queryKey: ['kpi-incentive-run', dead] });
      qc.removeQueries({ queryKey: ['kpi-incentive-run-loads', dead] });
      qc.invalidateQueries({ queryKey: ['kpi-incentive-runs'] });
    },
    onError: (e) => toast.error(e instanceof Error ? e.message : 'Failed'),
  });

  const recreate = useMutation({
    mutationFn: async () => {
      const r = run as RunDetail;
      await deleteIncentiveRun(r.id);
      return createIncentiveRun(r.period_start, r.period_end);
    },
    onSuccess: (r) => {
      const dead = selected;
      setRecreateOpen(false);
      setSelected(r.id);
      qc.removeQueries({ queryKey: ['kpi-incentive-run', dead] });
      qc.removeQueries({ queryKey: ['kpi-incentive-run-loads', dead] });
      qc.invalidateQueries({ queryKey: ['kpi-incentive-runs'] });
      qc.invalidateQueries({ queryKey: ['kpi-incentive-run', r.id] });
      qc.invalidateQueries({ queryKey: ['kpi-incentive-run-loads', r.id] });
    },
    onError: (e) => toast.error(e instanceof Error ? e.message : 'Failed'),
  });

  const finalize = useMutation({
    mutationFn: () => finalizeIncentiveRun(selected as number),
    onSuccess: () => { setFinalizeOpen(false); refresh(); },
    onError: (e) => toast.error(e instanceof Error ? e.message : 'Failed'),
  });

  const COLUMNS = runSheetColumns({
    t, runLoads: runLoadsQ.data, draft,
    periodStart: run?.period_start ?? '', periodEnd: run?.period_end ?? '',
    onOpenLoads: setLoadsRow,
  });


  return (
    <div>
      <PageHeader
        icon={BadgeDollarSign}
        title={t('kpi_dispatch.title', 'Dispatch KPI')}
        description={t(
          'kpi_runs.desc2',
          'Dispatcher settlements: each period computed under the rules it was announced with. Finalized runs are the paid record.',
        )}
        actions={(
          <div className="flex items-center gap-2">
            <SectionSwitcher current="dispatch" />
            {/* Demoted while a draft is open — the intended next step is
               FINALIZING the run in front of you, not starting another. */}
            <Button variant={run && draft ? 'outline' : 'default'} size="sm" onClick={() => setNewOpen(true)}>
              <Plus size={16} className="mr-1.5" />
              {t('kpi_runs.new', 'New run')}
            </Button>
            <FeatureConfigGear
              feature={t('kpi_runs.gear_feature', 'Dispatch KPI')}
              to="/kpi/dispatch/configuration"
            />
          </div>
        )}
      />

      {/* ── Runs list ────────────────────────────────────────────── */}
      {runsQ.isLoading && <TableSkeleton />}
      {runsQ.error != null && (
        <ErrorState message={runsQ.error instanceof Error ? runsQ.error.message : 'Failed to load runs'} />
      )}
      {runsQ.data && runsQ.data.runs.length === 0 && (
        <EmptyState
          icon={BadgeDollarSign}
          title={t('kpi_runs.empty_title', 'No runs yet')}
          description={t(
            'kpi_runs.empty_desc',
            'Configure the incentive model and company targets in KPI configuration, then create the first run for a period.',
          )}
          action={(
            <Link to="/kpi/dispatch/configuration"
              className="inline-flex items-center gap-1 py-1 -my-1 text-sm text-primary underline underline-offset-4 hover:no-underline">
              {t('kpi_runs.empty_cta', 'Open KPI configuration')}
              <ArrowRight size={14} />
            </Link>
          )}
        />
      )}
      {allRuns.length > 0 && (
        <div className="mb-4 flex flex-wrap gap-2">
          {visibleRuns.map((r) => (
            <button
              key={r.id}
              type="button"
              onClick={() => setSelected(r.id)}
              aria-pressed={selected === r.id}
              aria-label={t('kpi_runs.select_run', 'Select run {{a}} – {{b}} ({{status}})',
                { a: r.period_start, b: r.period_end, status: r.status })}
              className={`inline-flex items-center gap-2 rounded-md border px-3 py-1.5 text-sm transition ${
                selected === r.id
                  ? 'border-primary bg-primary/5 text-foreground'
                  : 'border-border bg-card text-foreground hover:border-ring'
              }`}
            >
              <span className="tabular-nums">{r.period_start} – {r.period_end}</span>
              <span className={`text-xs font-medium px-2 py-0.5 rounded-md ${
                toneClasses(r.status === 'finalized' ? 'ok'
                  : draftOverdueDays(r) > 1 ? 'warn' : 'info')
              }`}>
                {r.status === 'draft' && draftOverdueDays(r) > 1
                  ? t('kpi_runs.draft_overdue2', 'draft · {{d}}d old', { d: draftOverdueDays(r) })
                  : r.status}
              </span>
            </button>
          ))}
          {allRuns.length > RECENT_RUNS && (
            <button
              type="button"
              onClick={() => setShowAllRuns((v) => !v)}
              aria-expanded={showAllRuns}
              className="inline-flex items-center rounded-md border border-border bg-card px-3 py-1.5 text-sm text-muted-foreground hover:border-ring transition"
            >
              {showAllRuns
                ? t('kpi_runs.hide_archive', 'Hide archive')
                : t('kpi_runs.show_archive', 'All runs ({{n}})', { n: allRuns.length })}
            </button>
          )}
        </div>
      )}

      {/* ── The runs ARCHIVE — a real table once the strip outgrows
          itself (52 weekly runs/year).  Click a row to open that run. */}
      {showAllRuns && allRuns.length > 0 && (
        <div className="mb-4">
          <DataGrid
            tableId="kpi-incentive-runs-archive"
            columns={ARCHIVE_COLUMNS}
            data={allRuns as unknown as Record<string, unknown>[]}
            onRowClick={(r) => setSelected(Number((r as { id: unknown }).id))}
            searchKey={['period_start', 'period_end']}
            searchPlaceholder={t('kpi_runs.archive_search', 'Search period…')}
          />
        </div>
      )}

      {/* ── Run detail: the sheet ───────────────────────────────── */}
      {selected != null && detailQ.isLoading && <TableSkeleton />}
      {run && (
        <div>
          {/* ── THE RUN PANEL — one enclosure for everything that
              describes the selected run (selector strip stays above,
              the row cards/grid below).  Six unenclosed bands failed
              the audit's count test. ── */}
          <section className="bg-card border border-border rounded-xl p-4 space-y-3">
            <div className="flex flex-wrap items-center justify-between gap-3">
              {/* Reversible view switch LEFT, the commit pair RIGHT —
                  the audit measured 9px between reversible and
                  irreversible; now it's the whole row. */}
              <div className="inline-flex items-center gap-0.5 p-0.5 bg-muted/50 border border-border rounded-md"
                role="group" aria-label={t('kpi_runs.view_mode', 'View mode')}>
                <button
                  type="button"
                  onClick={() => setViewMode('sheet')}
                  aria-pressed={viewMode === 'sheet'}
                  className={`inline-flex h-7 items-center gap-1.5 rounded px-2 text-xs ${viewMode === 'sheet'
                    ? 'bg-card text-foreground shadow-sm font-medium'
                    : 'text-muted-foreground hover:text-foreground'} min-h-tap`}
                >
                  <Table2 size={14} />
                  {t('kpi_runs.view_sheet', 'Sheet')}
                </button>
                <button
                  type="button"
                  onClick={() => setViewMode('board')}
                  aria-pressed={viewMode === 'board'}
                  className={`inline-flex h-7 items-center gap-1.5 rounded px-2 text-xs ${viewMode === 'board'
                    ? 'bg-card text-foreground shadow-sm font-medium'
                    : 'text-muted-foreground hover:text-foreground'} min-h-tap`}
                >
                  <CalendarRange size={14} />
                  {t('kpi_runs.view_board', 'Board')}
                </button>
              </div>
              <div className="flex items-center gap-3">
                {/* The figure Finalize commits, AT the button that
                    commits it — it was 12px muted text 640px away. */}
                {draft && (
                  /* Destructive action FIRST and fenced off by a divider
                     — it sat at equal weight directly beside Finalize,
                     where one 32px slip discards instead of commits. */
                  <>
                    <Button variant="ghost"
                      className="text-destructive hover:text-destructive hover:bg-destructive/10"
                      onClick={() => setDiscardOpen(true)}>
                      <Trash2 size={14} className="mr-1.5" />
                      {t('kpi_runs.discard', 'Discard draft')}
                    </Button>
                    <span className="h-5 w-px bg-border" aria-hidden />
                  </>
                )}
                <span className="text-base font-semibold tabular-nums">
                  {usd(runTotal)}
                  {runGross > 0 && (
                    <span className="ml-1.5 text-xs font-normal text-muted-foreground">
                      {t('kpi_runs.total_anchor', '{{pct}}% of {{gross}} gross',
                        { pct: ((runTotal / runGross) * 100).toFixed(1),
                          gross: `$${Math.round(runGross).toLocaleString()}` })}
                    </span>
                  )}
                </span>
                <Button variant="outline" size="sm"
                  onClick={() => downloadIncentiveRunCsv(run.id)
                    .catch((e) => toast.error(e instanceof Error ? e.message : 'Export failed'))}>
                  <Download size={14} className="mr-1.5" />
                  {t('kpi_runs.export', 'Export run')}
                </Button>
                {draft ? (
                  <Button onClick={() => setFinalizeOpen(true)}>
                    <Lock size={14} className="mr-1.5" />
                    {t('kpi_runs.finalize', 'Finalize run')}
                  </Button>
                ) : (
                  <span className={`inline-flex items-center gap-1 text-xs px-2 py-1 rounded ${toneClasses('ok')}`}>
                    <Lock size={12} />
                    {t('kpi_runs.finalized', 'Finalized — the paid record')}
                  </span>
                )}
              </div>
            </div>

            {/* The run's LIFECYCLE, with the finished steps already
                ticked — a flat "draft" label showed no path. */}
            <div className="flex flex-wrap items-center gap-x-1.5 gap-y-1 text-xs">
              {[
                { label: t('kpi_runs.step_generated', 'Generated'), done: true },
                { label: t('kpi_runs.step_review', 'Review & adjust'),
                  done: !draft || adjustedRows.length > 0, current: !!draft },
                { label: t('kpi_runs.step_finalize', 'Finalized'), done: !draft },
                { label: t('kpi_runs.step_paid', 'In {{m}} payout', {
                    m: new Date(`${run.period_end.slice(0, 10)}T00:00:00Z`)
                      .toLocaleDateString(undefined, { month: 'short', year: 'numeric', timeZone: 'UTC' }) }),
                  done: !draft },
              ].map((st, i, arr) => (
                <span key={i} className="inline-flex items-center gap-1.5">
                  <span className={`inline-flex items-center gap-1 ${
                    st.done ? 'text-foreground'
                      : st.current ? 'text-foreground font-medium'
                        : 'text-muted-foreground'}`}>
                    {st.done && <Check size={12} className="text-muted-foreground" />}
                    {st.label}
                  </span>
                  {i < arr.length - 1 && <span className="text-muted-foreground/50">›</span>}
                </span>
              ))}
            </div>

            {/* Run meta — kept in BOTH modes (the sheet used to lose it). */}
            <div className="flex flex-wrap items-center gap-x-3 gap-y-1 text-xs text-muted-foreground">
              <span>{t('kpi_board.n_dispatchers', '{{n}} dispatchers', { n: Object.keys(run.payouts).length })}</span>
              <span>{t('kpi_board.n_trucks', '{{n}} trucks', { n: run.rows.length })}</span>
              {zeroCount > 0 && run && (
                <Tip label={t('kpi_runs.zero_units_tip', 'Units at 0%: {{list}}',
                  { list: run.rows.filter((r) => Number(r.pct) === 0)
                      .map((r) => r.vehicle_unit || t('kpi_runs.unassigned', 'Unassigned'))
                      .join(', ') })}>
                  <span tabIndex={0} className="cursor-help underline decoration-dotted decoration-muted-foreground/60 underline-offset-4">
                    <span className={`font-medium ${toneText('warn')}`}>
                      {t('kpi_board.n_zero2', '{{n}} of {{total}} trucks at 0%',
                        { n: zeroCount, total: run.rows.length })}
                    </span>
                  </span>
                </Tip>
              )}
              {draft && viewMode !== 'board' ? (
                <button type="button" onClick={() => setViewMode('board')}
                  className="underline underline-offset-4 hover:text-foreground transition">
                  {markedDays === 1
                    ? t('kpi_board.one_marked', '1 day marked inactive')
                    : markedDays > 0
                      ? t('kpi_board.n_marked', '{{n}} days marked inactive', { n: markedDays })
                      : t('kpi_board.none_marked', 'no inactive days — mark on the board')}
                </button>
              ) : draft ? (
                <span>
                  {markedDays === 1
                    ? t('kpi_board.one_marked', '1 day marked inactive')
                    : markedDays > 0
                      ? t('kpi_board.n_marked', '{{n}} days marked inactive', { n: markedDays })
                      : t('kpi_board.none_marked2', 'no inactive days — click a day below to mark one')}
                </span>
              ) : (
                <span>{t('kpi_board.n_marked', '{{n}} days marked inactive', { n: markedDays })}</span>
              )}
              <button type="button" onClick={() => setAdjustmentsOpen(true)}
                className={`ml-auto inline-flex h-6 items-center gap-1 rounded-md border border-border bg-card px-2 hover:border-ring transition ${
                  adjustedRows.length > 0 ? 'text-foreground' : 'text-muted-foreground'} min-h-tap`}>
                <ListChecks size={12} />
                {t('kpi_runs.adjustments_n', 'Adjustments ({{n}})', { n: adjustedRows.length })}
              </button>
              {/* The run's who-did-what record — every payout-moving
                  edit (day marks, extras, overrides, finalize) with
                  its author and old → new values. */}
              <button type="button" onClick={() => setTrailOpen(true)}
                className="inline-flex h-6 items-center gap-1 rounded-md border border-border bg-card px-2 text-muted-foreground hover:border-ring transition min-h-tap">
                <History size={12} />
                {t('kpi_runs.activity', 'Activity')}
              </button>
            </div>

            <RunNoteLine run={run} onSaved={refresh} />

            {/* Per-dispatcher payouts — read-only DATA on one flat
                tinted strip, so nothing here borrows a control's pill
                shape. */}
            <div className="flex flex-wrap gap-x-4 gap-y-1 rounded-md bg-muted/40 px-3 py-2 text-sm">
              {Object.entries(run.payouts).sort((a, b) => b[1] - a[1]).map(([name, total]) => (
                <span key={name} className="inline-flex items-center gap-1.5">
                  <span className="text-muted-foreground">{name}</span>
                  {runLoadsQ.data?.dispatcher_grades[name] && (
                    <Tip label={t('kpi_runs.grade_tip',
                      'Grade {{g}} — period analytics against the grading thresholds. Grades never change incentive pay.',
                      { g: runLoadsQ.data.dispatcher_grades[name] })}>
                      <GradePill value={runLoadsQ.data.dispatcher_grades[name]} />
                    </Tip>
                  )}
                  {!runLoadsQ.data && runLoadsQ.isPending && (
                    /* The pill mounts when the loads query lands, a beat
                       after the strip — inserting it reflowed the strip
                       and shifted everything below (the DevTools CLS
                       cluster).  Hold its slot until grades arrive. */
                    <span className="inline-block size-5" aria-hidden />
                  )}
                  <span className="font-medium tabular-nums">{usd(total)}</span>
                </span>
              ))}
            </div>
          </section>

          {/* ≥32px between the run panel and the row list — between-zone
              air must beat the 12px card rhythm inside the list. */}
          <div className="mt-8">
            {viewMode === 'board' ? (
              <RunBoard run={run} draft={!!draft} onChanged={refresh}
                onRecreate={() => setRecreateOpen(true)} />
            ) : (
            <DataGrid
            tableId="kpi-dispatch-settlement"
            columns={COLUMNS}
            // The manager's board view: one collapsible section per
            // dispatcher, group rows carrying summed gross / miles /
            // KPI-$ / confirmed, grand totals in the footer.  A default,
            // not a wall — ungroup via the grid's own chip.
            defaultRowGroup="dispatcher_name"
            defaultAggregation={{
              kpi_gross: 'sum', miles: 'sum', rpm: 'avg',
              kpi_dollars: 'sum', confirmed_dollars: 'sum',
            }}
            // Clicking a row IS the edit gesture on a draft — rowActions
            // alone is right-click-only (the audit's top finding: managers
            // migrating from Excel won't guess a context menu exists).
            onRowClick={draft
              ? (row) => setEditRow(row as unknown as RunRow)
              : undefined}
            data={run.rows as unknown as Record<string, unknown>[]}
            searchKey={['dispatcher_name', 'vehicle_unit', 'company_code']}
            searchPlaceholder={t('kpi_runs.search', 'Search unit, dispatcher…')}
            rowActions={(row) => [
              {
                key: 'loads',
                label: t('kpi_runs.view_loads', 'View loads…'),
                icon: <Table2 size={14} className="text-muted-foreground" />,
                onSelect: () => setLoadsRow(row as unknown as RunRow),
              },
              ...(draft ? [
              {
                key: 'edit',
                label: t('kpi_runs.edit_row2', 'Extras…'),
                icon: <Pencil size={14} className="text-muted-foreground" />,
                onSelect: () => setEditRow(row as unknown as RunRow),
              },
              {
                key: 'exception',
                label: t('kpi_runs.exception', 'Exception…'),
                icon: <Scale size={14} className="text-muted-foreground" />,
                onSelect: () => setExceptRow(row as unknown as RunRow),
              },
              ] : []),
            ]}
          />
            )}
          </div>
        </div>
      )}

      {/* Waits for the summary+board above to reach FINAL height —
          mounted early it sat under a short skeleton and was shoved
          ~a full viewport down when the run detail landed (the 0.16
          CLS event DevTools pinned on this section). Appearing once,
          at its final position, moves nothing. */}
      {!(selected != null && detailQ.isLoading) && (
        <MonthlyPayoutsPanel allRuns={allRuns}
          onSelectRun={(id) => {
            setSelected(id);
            window.scrollTo({ top: 0, behavior: 'smooth' });
          }} />
      )}

      <NewRunDialog
        open={newOpen}
        existing={allRuns}
        onClose={() => setNewOpen(false)}
        onCreated={(r) => { setNewOpen(false); setSelected(r.id); refresh(); }}
      />
      {editRow && selected != null && (
        <EditRowDialog
          runId={selected} row={editRow}
          onClose={() => setEditRow(null)}
          onSaved={() => { setEditRow(null); refresh(); }}
          onGoBoard={() => { setEditRow(null); setViewMode('board'); }}
        />
      )}
      {exceptRow && selected != null && (
        <ExceptionDialog
          runId={selected} row={exceptRow}
          onClose={() => setExceptRow(null)}
          onSaved={() => { setExceptRow(null); refresh(); }}
        />
      )}

      <ActivityTrailDialog
        entityType="kpi_run"
        entityId={run?.id ?? null}
        title={t('kpi_runs.activity_title', 'Run activity — who changed what')}
        open={trailOpen}
        onOpenChange={setTrailOpen}
      />

      <Dialog open={discardOpen} onOpenChange={setDiscardOpen}>
        <DialogContent className="max-w-lg">
          <DialogHeader>
            <DialogTitle>{t('kpi_runs.discard_title', 'Discard this draft?')}</DialogTitle>
          </DialogHeader>
          <p className="text-sm text-muted-foreground">
            {t('kpi_runs.discard_body',
              'This deletes the draft’s {{rows}} rows, including {{adjusted}} with hand-entered adjustments. A new run for the same period can be created at any time; finalized runs can never be discarded.',
              {
                rows: run?.rows.length ?? 0,
                adjusted: run?.rows.filter(isHandAdjusted).length ?? 0,
              })}
          </p>
          <DialogFooter>
            <Button variant="outline" onClick={() => setDiscardOpen(false)}>
              {t('common.cancel', 'Cancel')}
            </Button>
            <Button variant="destructive" onClick={() => discard.mutate()} disabled={discard.isPending}>
              {discard.isPending && <Loader2 size={16} className="animate-spin mr-1.5" />}
              {t('kpi_runs.discard_confirm', 'Discard')}
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>

      {loadsRow && (
        <Dialog open onOpenChange={(o) => { if (!o) setLoadsRow(null); }}>
          <DialogContent className="max-w-xl">
            <DialogHeader>
              <DialogTitle>
                {!runLoadsQ.data
                  ? t('kpi_runs.loads_title_bare', 'Unit {{unit}} — loads', {
                      unit: loadsRow.vehicle_unit || t('kpi_runs.unassigned', 'Unassigned') })
                  : (runLoadsQ.data.rows[String(loadsRow.id)] ?? []).length === 1
                    ? t('kpi_runs.loads_title_one', 'Unit {{unit}} — 1 load', {
                        unit: loadsRow.vehicle_unit || t('kpi_runs.unassigned', 'Unassigned') })
                    : t('kpi_runs.loads_title', 'Unit {{unit}} — {{n}} loads', {
                        unit: loadsRow.vehicle_unit || t('kpi_runs.unassigned', 'Unassigned'),
                        n: (runLoadsQ.data.rows[String(loadsRow.id)] ?? []).length,
                      })}
              </DialogTitle>
            </DialogHeader>
            {runLoadsQ.isError && (
              <p className="text-sm text-danger">
                {t('kpi_runs.loads_err', 'Could not load this row’s loads — close and retry.')}
              </p>
            )}
            {!runLoadsQ.data && !runLoadsQ.isError && (
              <p className="text-sm text-muted-foreground">
                {t('common.loading', 'Loading…')}
              </p>
            )}
            <ScrollRegion className="max-h-96"
              label={t('kpi_runs.loads_region', 'Loads in this row')}>
            <ul className="divide-y divide-border border-t border-border">
              {(runLoadsQ.data?.rows[String(loadsRow.id)] ?? []).map((l, i) => (
                <li key={i} className="py-2 text-sm flex items-center justify-between gap-3">
                  <span className="min-w-0">
                    <span className="block truncate">
                      {l.pickup_location || '—'} → {l.delivery_location || '—'}
                    </span>
                    <span className="text-xs text-muted-foreground tabular-nums">
                      {l.pickup_date} – {l.delivery_date}
                      {l.load_number ? ` · #${l.load_number}` : ''}
                    </span>
                  </span>
                  <span className="shrink-0 text-right">
                    <span className="block font-medium tabular-nums">{usd(l.total_rate)}</span>
                    <span className="text-xs text-muted-foreground tabular-nums">
                      {Math.round(l.miles).toLocaleString()} mi
                    </span>
                  </span>
                </li>
              ))}
            </ul>
            </ScrollRegion>
          </DialogContent>
        </Dialog>
      )}

      {run && (
        <AdjustmentsDrawer
          open={adjustmentsOpen}
          run={run}
          draft={!!draft}
          onClose={() => setAdjustmentsOpen(false)}
          onChanged={refresh}
        />
      )}

      <Dialog open={recreateOpen} onOpenChange={setRecreateOpen}>
        <DialogContent className="max-w-lg">
          <DialogHeader>
            <DialogTitle>{t('kpi_runs.recreate_title', 'Recreate this draft from live loads?')}</DialogTitle>
          </DialogHeader>
          <p className="text-sm text-muted-foreground">
            {t('kpi_runs.recreate_body',
              'The current draft — including {{adjusted}} hand-adjusted rows — is discarded and the period is regenerated from today’s loads under the CURRENT rules. Adjustments do not carry over.',
              {
                adjusted: run?.rows.filter(isHandAdjusted).length ?? 0,
              })}
            {run?.note && (
              <> {t('kpi_runs.recreate_note',
                'The run note (“{{note}}”) does not carry over either.',
                { note: run.note.length > 60 ? `${run.note.slice(0, 60)}…` : run.note })}</>
            )}
          </p>
          <DialogFooter>
            <Button variant="outline" onClick={() => setRecreateOpen(false)}>
              {t('common.cancel', 'Cancel')}
            </Button>
            <Button variant="destructive" onClick={() => recreate.mutate()} disabled={recreate.isPending}>
              {recreate.isPending && <Loader2 size={16} className="animate-spin mr-1.5" />}
              {t('kpi_runs.recreate_confirm', 'Discard & regenerate')}
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>

      <Dialog open={finalizeOpen} onOpenChange={setFinalizeOpen}>
        <DialogContent className="max-w-lg">
          <DialogHeader>
            <DialogTitle>{t('kpi_runs.finalize_title', 'Finalize this run?')}</DialogTitle>
          </DialogHeader>
          <p className="text-sm text-muted-foreground">
            {t('kpi_runs.finalize_body2',
              'Finalizing locks {{rows}} rows across {{dispatchers}} dispatchers — {{total}} — into the {{month}} payout. {{adjusted}} rows carry hand adjustments. Rows can no longer be edited and the run is never re-priced, even if the rules change later.',
              {
                rows: run?.rows.length ?? 0,
                dispatchers: run ? Object.keys(run.payouts).length : 0,
                total: usd(runTotal),
                month: run ? new Date(`${run.period_end.slice(0, 10)}T00:00:00Z`)
                  .toLocaleDateString(undefined, { month: 'long', year: 'numeric', timeZone: 'UTC' }) : '',
                adjusted: run?.rows.filter(isHandAdjusted).length ?? 0,
              })}
          </p>
          {staleCount > 0 && (
            <p className={`text-xs ${toneClasses('warn')} px-2 py-1 rounded inline-block`}>
              {t('kpi_runs.finalize_stale_warn',
                '{{n}} rows are STALE — their loads changed after generation. Consider “Recreate draft” on the board first.',
                { n: staleCount })}
            </p>
          )}
          <DialogFooter>
            <Button variant="outline" onClick={() => setFinalizeOpen(false)}>
              {t('common.cancel', 'Cancel')}
            </Button>
            <Button onClick={() => finalize.mutate()} disabled={finalize.isPending}>
              {finalize.isPending && <Loader2 size={16} className="animate-spin mr-1.5" />}
              {t('kpi_runs.finalize_confirm', 'Finalize')}
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>
    </div>
  );
}



// ── Monthly payouts (weekly calc → monthly payout roll-up) ────────────



// ── New run ───────────────────────────────────────────────────────────


// ── Extras (days are the board's) ─────────────────────────────────────


// ── Exception ─────────────────────────────────────────────────────────

