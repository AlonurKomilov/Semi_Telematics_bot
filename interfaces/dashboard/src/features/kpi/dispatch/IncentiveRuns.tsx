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
 * "Days & extras" edits inputs and recomputes; "Exception" overrides the
 * percent with a mandatory reason, validated server-side against the
 * run's snapshot cap.
 *
 * A finalized run renders read-only: the paid record, never re-priced.
 */
import { useEffect, useState } from 'react';
import { Link } from 'react-router-dom';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { useTranslation } from 'react-i18next';
import {
  ArrowRight, BadgeDollarSign, CalendarRange, Check, Download, ListChecks,
  Loader2, Lock, Pencil, Plus, Scale, StickyNote, Table2, Trash2, Undo2,
} from 'lucide-react';
import { toast } from 'sonner';
import DataGrid from '../../../components/datagrid';
import { EmptyState, ErrorState, PageHeader, TableSkeleton } from '../../../components/shell';
import { Button } from '../../../components/ui/button';
import {
  Dialog, DialogContent, DialogFooter, DialogHeader, DialogTitle,
} from '../../../components/ui/dialog';
import {
  Sheet, SheetBody, SheetContent, SheetHeader, SheetTitle,
} from '../../../components/ui/sheet';
import { Input } from '../../../components/ui/input';
import { InfoTip, Tip } from '../../../components/tooltip';
import { ScrollRegion } from '../../../components/scrolling';
import { toneClasses, toneText } from '../../../lib/status';
import { usePreference } from '../../../preferences';
import RunBoard from './RunBoard';
import { daysCell, isHandAdjusted, loadedDayCount, matchedTip } from './explain';
import { DaysTipContent } from './DaysTip';
import { SectionSwitcher } from '../SectionSwitcher';
import { FeatureConfigGear } from '../../_lib/FeatureConfigGear';
import type { AnyColumn } from '../../../types';
import {
  Select, SelectContent, SelectItem, SelectTrigger, SelectValue,
} from '../../../components/ui/select';
import {
  createIncentiveRun, deleteIncentiveRun, downloadIncentiveRunCsv,
  finalizeIncentiveRun, getIncentiveRun, getIncentiveRunLoads,
  getMonthlyPayouts, listIncentiveRuns, patchIncentiveRow,
  previewIncentiveRun, setIncentiveException, setIncentiveRunNote,
  type RunDetail, type RunRow, type RunSummary,
} from '../api';

function usd(v: unknown): string {
  if (v == null) return '—';
  return `$${Number(v).toLocaleString(undefined, {
    minimumFractionDigits: 2, maximumFractionDigits: 2,
  })}`;
}

const iso = (d: Date) => d.toISOString().slice(0, 10);

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

/** In-cell free-text annotation (extras note, inactive reason, override
 *  reason): truncated so a long note can never inflate its column for
 *  every row; the full text lives on hover. */
function Note({ text }: { text: string }) {
  return (
    <Tip label={text}>
      <span className="ml-1 inline-block max-w-40 truncate align-bottom text-xs text-muted-foreground">
        {text}
      </span>
    </Tip>
  );
}

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

// Archive columns — the run LIST as records (period, status, size, money).
const ARCHIVE_COLUMNS: AnyColumn[] = [
  { key: 'period_start', label: 'Period', sortable: true,
    render: (_v, r) => (
      <span className="tabular-nums">{String(r.period_start)} – {String(r.period_end)}</span>
    ) },
  { key: 'status', label: 'Status', sortable: true, filterable: true,
    render: (v) => (
      <span className={`text-xs font-medium px-2 py-0.5 rounded-md ${
        toneClasses(v === 'finalized' ? 'ok' : 'info')}`}>
        {String(v)}
      </span>
    ) },
  { key: 'row_count', label: 'Rows', sortable: true,
    render: (v) => <span className="tabular-nums">{String(v ?? '—')}</span> },
  { key: 'total', label: 'Total', sortable: true, aggregable: true,
    aggFormat: (v) => usd(v),
    render: (v) => <span className="tabular-nums font-medium">{usd(v)}</span> },
  { key: 'created_at', label: 'Created', sortable: true, aggType: 'date',
    render: (v) => (
      <span className="text-xs text-muted-foreground tabular-nums">{String(v || '—').slice(0, 10)}</span>
    ) },
  { key: 'finalized_at', label: 'Finalized', sortable: true,
    render: (v) => (
      <span className="text-xs text-muted-foreground tabular-nums">{String(v || '—').slice(0, 10)}</span>
    ) },
];

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
  const newestId = allRuns[0]?.id;
  useEffect(() => {
    if (selected == null && newestId != null) setSelected(newestId);
  }, [selected, newestId]);
  const detailQ = useQuery<RunDetail>({
    queryKey: ['kpi-incentive-run', selected],
    queryFn: () => getIncentiveRun(selected as number),
    enabled: selected != null,
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
  });
  const staleCount = runLoadsQ.data?.drift.length ?? 0;

  const discard = useMutation({
    mutationFn: () => deleteIncentiveRun(selected as number),
    onSuccess: () => {
      setDiscardOpen(false);
      setSelected(null);
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
      setRecreateOpen(false);
      setSelected(r.id);
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

  const COLUMNS: AnyColumn[] = [
    { key: 'dispatcher_name', label: 'Dispatcher', sortable: true, filterable: true,
      defaultPinned: 'left' },
    { key: 'company_code', label: 'Company', sortable: true, filterable: true },
    { key: 'vehicle_unit', label: 'Unit', sortable: true, defaultPinned: 'left',
      render: (v) => v
        ? <span>{String(v)}</span>
        : <span className="text-muted-foreground">{t('kpi_runs.unassigned', 'Unassigned')}</span> },
    { key: 'window_start', label: 'Window', sortable: true,
      render: (_v, r) => (
        <span className="text-xs text-muted-foreground">
          {String(r.window_start)} – {String(r.window_end)}
        </span>
      ) },
    { key: 'total_days', label: 'Days', sortable: true,
      // Period length never varies within one run — counted days is
      // the only orderable fact in the trio.
      sortKey: (r) => Number((r as unknown as RunRow).total_days)
        - Number((r as unknown as RunRow).inactive_days),
      headerRender: () => (
        <span className="inline-flex items-center gap-1">
          {t('kpi_runs.days_col', 'Days')}
          <span onClick={(e) => e.stopPropagation()}
            onPointerDown={(e) => e.stopPropagation()}
            onKeyDown={(e) => e.stopPropagation()}>
            <InfoTip label={t('kpi_runs.days_info3',
              'Counted days of the period, and how many a load covered (pickup through delivery). Days marked inactive on the board (home time, repair, holiday) drop out, and the target is split across the counted days. A counted day with no load is on the dispatcher.')} />
          </span>
        </span>
      ),
      // period/counted/loaded, with the inactive reason as visible
      // context — these numbers move the target, so they must be
      // readable and self-explaining.
      render: (_v, r) => {
        // The already-computed cost of the inactive days: the bar this
        // truck ISN'T being held to (weekly rate × inactive days ÷ 7).
        const cut = r.weekly_target != null && Number(r.inactive_days) > 0
          ? (Number(r.weekly_target) / 7) * Number(r.inactive_days) : null;
        return (
          // Words label the numbers inline — no notation to decode, so
          // AT reads the cell as-is.  The note leads with the DECISION
          // number (what excusing cost); reasons live in the tooltip.
          <span className="tabular-nums">
            <Tip label={<DaysTipContent row={r}
              loads={runLoadsQ.data ? (runLoadsQ.data.rows[String(r.id)] ?? []) : undefined}
              draft={draft} periodStart={run?.period_start ?? ''}
              periodEnd={run?.period_end ?? ''} t={t} />}>
              <span className="underline decoration-dotted decoration-muted-foreground/60 underline-offset-4 cursor-help">
                {daysCell(r, runLoadsQ.data
                  ? loadedDayCount(runLoadsQ.data.rows[String(r.id)] ?? [],
                      r.window_start, r.window_end) : null, t)}
              </span>
            </Tip>
            {Number(r.inactive_days) > 0 && (
              <Note text={`(${cut != null
                ? `−$${Math.round(cut).toLocaleString()} · ` : ''}${
                t('kpi_runs.n_inactive', '{{n}} inactive', { n: r.inactive_days })})`} />
            )}
          </span>
        );
      } },
    { key: 'kpi_gross', label: 'Gross', sortable: true,
      aggregable: true, aggFormat: (v) => usd(v),
      render: (v, r) => (
        <span className="tabular-nums">
          {usd(v)}
          {Number(r.extras) !== 0 && (
            <Note text={`(incl. ${usd(r.extras)}${r.extras_note ? ` ${r.extras_note}` : ''})`} />
          )}
        </span>
      ) },
    { key: 'miles', label: 'Miles', sortable: true, defaultHidden: true,
      aggregable: true,
      aggFormat: (v) => Math.round(v).toLocaleString(),
      render: (v) => <span className="tabular-nums">{Number(v).toLocaleString()}</span> },
    { key: 'rpm', label: 'RPM', sortable: true,
      // Group/footer RPM is the MILES-WEIGHTED rate (Σ base gross ÷ Σ
      // miles, extras excluded — same base the row RPM uses), never an
      // average of the row rates.
      aggregable: true, aggFns: ['avg'],
      aggRatio: { num: (r) => Number(r.base_gross), den: (r) => Number(r.miles) },
      aggFormat: (v) => v.toFixed(2),
      render: (v) => <span className="tabular-nums">{v == null ? '—' : Number(v).toFixed(2)}</span> },
    { key: 'adjusted_target', label: 'Target', sortable: true,
      // NULL weekly_target = this company has no bar configured — say
      // so, never render an invented number.  A prorated bar names its
      // cause inline: "$2,285.71 · 2 of 7 days" — an unanchored low
      // target reads as an error.
      render: (v, r) => r.weekly_target == null
        ? <span className={`text-xs font-medium ${toneClasses('warn')} px-2 py-0.5 rounded-md`}>{t('kpi_runs.no_target', 'no target')}</span>
        : <span className="tabular-nums">{usd(v)}</span> },
    { key: 'vs_target', label: 'vs Target', sortable: true,
      sortKey: (r) => (r as unknown as RunRow).weekly_target == null
        ? Number.NEGATIVE_INFINITY
        : Number((r as unknown as RunRow).kpi_gross)
          - Number((r as unknown as RunRow).adjusted_target),
      render: (_v, r) => {
        if (r.weekly_target == null) return <span className="text-muted-foreground">—</span>;
        const d = Number(r.kpi_gross) - Number(r.adjusted_target);
        return (
          <span className={`tabular-nums ${d >= 0 ? toneText('ok') : toneText('danger')}`}>
            {d >= 0 ? '+' : '−'}${Math.abs(Math.round(d)).toLocaleString()}
          </span>
        );
      } },
    { key: 'pct', label: 'KPI %', sortable: true,
      // Every zero in a money column carries its reason — the first
      // question a dispatcher asks their manager is "why is this 0?".
      // no_target is annotated on the Target column already.
      render: (v, r) => (
        <span className="tabular-nums">
          {r.matched_rule ? (
            <Tip label={matchedTip(r.matched_rule, t)}>
              <span className="underline decoration-dotted decoration-muted-foreground/60 underline-offset-4 cursor-help">
                {Number(v)}%
              </span>
            </Tip>
          ) : (
            <>{Number(v)}%</>
          )}
          {Number(v) === 0 && r.zero_reason && r.zero_reason !== 'no_target' && (
            <span className={`ml-1 text-xs font-medium ${toneClasses('warn')} px-2 py-0.5 rounded-md`}>
              {r.zero_reason === 'floor' ? t('kpi_runs.zr_floor', 'below floor')
                : r.zero_reason === 'no_active_days' ? t('kpi_runs.zr_days', 'no active days')
                  : t('kpi_runs.zr_tier', 'no tier met')}
            </span>
          )}
          {r.override_pct != null && (
            <span className={`ml-1 text-xs font-medium ${toneClasses('info')} px-2 py-0.5 rounded-md`}>
              → {Number(r.override_pct)}%
            </span>
          )}
        </span>
      ) },
    { key: 'kpi_dollars', label: 'KPI $', sortable: true, defaultHidden: true,
      aggregable: true, aggFormat: (v) => usd(v),
      render: (v) => <span className="tabular-nums">{usd(v)}</span> },
    { key: 'confirmed_dollars', label: 'Confirmed', sortable: true, aggregable: true,
      aggFormat: (v) => usd(v),
      render: (v, r) => (
        <span className="tabular-nums font-medium">
          {usd(v)}
          {r.override_reason ? <Note text={`(${String(r.override_reason)})`} /> : null}
        </span>
      ) },
  ];

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
                  ? t('kpi_runs.draft_overdue', 'draft · {{d}}d', { d: draftOverdueDays(r) })
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
                    : 'text-muted-foreground hover:text-foreground'}`}
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
                    : 'text-muted-foreground hover:text-foreground'}`}
                >
                  <CalendarRange size={14} />
                  {t('kpi_runs.view_board', 'Board')}
                </button>
              </div>
              <div className="flex items-center gap-3">
                {/* The figure Finalize commits, AT the button that
                    commits it — it was 12px muted text 640px away. */}
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
                  <>
                    <Button variant="ghost"
                      className="text-destructive hover:text-destructive hover:bg-destructive/10"
                      onClick={() => setDiscardOpen(true)}>
                      <Trash2 size={14} className="mr-1.5" />
                      {t('kpi_runs.discard', 'Discard draft')}
                    </Button>
                    <Button onClick={() => setFinalizeOpen(true)}>
                      <Lock size={14} className="mr-1.5" />
                      {t('kpi_runs.finalize', 'Finalize run')}
                    </Button>
                  </>
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
                  <span className={`font-medium px-2 py-0.5 rounded-md ${toneClasses('warn')}`}>
                    {t('kpi_board.n_zero', '{{n}} at 0%', { n: zeroCount })}
                  </span>
                </Tip>
              )}
              {draft ? (
                <button type="button" onClick={() => setViewMode('board')}
                  className="underline underline-offset-4 hover:text-foreground transition">
                  {t('kpi_board.n_marked', '{{n}} days marked inactive', { n: markedDays })}
                </button>
              ) : (
                <span>{t('kpi_board.n_marked', '{{n}} days marked inactive', { n: markedDays })}</span>
              )}
              <button type="button" onClick={() => setAdjustmentsOpen(true)}
                className={`inline-flex h-6 items-center gap-1 rounded-md border border-border bg-card px-2 hover:border-ring transition ${
                  adjustedRows.length > 0 ? 'text-foreground' : 'text-muted-foreground'}`}>
                <ListChecks size={12} />
                {t('kpi_runs.adjustments_n', 'Adjustments ({{n}})', { n: adjustedRows.length })}
              </button>
            </div>

            <RunNoteLine run={run} onSaved={refresh} />

            {/* Per-dispatcher payouts — read-only DATA on one flat
                tinted strip, so nothing here borrows a control's pill
                shape. */}
            <div className="flex flex-wrap gap-x-4 gap-y-1 rounded-md bg-muted/40 px-3 py-2 text-sm">
              {Object.entries(run.payouts).map(([name, total]) => (
                <span key={name} className="inline-flex items-center gap-1.5">
                  <span className="text-muted-foreground">{name}</span>
                  {runLoadsQ.data?.dispatcher_grades[name] && (
                    <Tip label={t('kpi_runs.grade_tip',
                      'Grade {{g}} — period analytics against the grading thresholds. Grades never change incentive pay.',
                      { g: runLoadsQ.data.dispatcher_grades[name] })}>
                      <GradePill value={runLoadsQ.data.dispatcher_grades[name]} />
                    </Tip>
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
                label: t('kpi_runs.edit_row', 'Days & extras…'),
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

      <MonthlyPayoutsPanel allRuns={allRuns}
        onSelectRun={(id) => {
          setSelected(id);
          window.scrollTo({ top: 0, behavior: 'smooth' });
        }} />

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
        />
      )}
      {exceptRow && selected != null && (
        <ExceptionDialog
          runId={selected} row={exceptRow}
          onClose={() => setExceptRow(null)}
          onSaved={() => { setExceptRow(null); refresh(); }}
        />
      )}

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
                {t('kpi_runs.loads_title', 'Unit {{unit}} — {{n}} loads', {
                  unit: loadsRow.vehicle_unit || t('kpi_runs.unassigned', 'Unassigned'),
                  n: (runLoadsQ.data?.rows[String(loadsRow.id)] ?? []).length,
                })}
              </DialogTitle>
            </DialogHeader>
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

// ── Run note — one line, the owner's own record ("226 in shop Thu–Fri").
function RunNoteLine({ run, onSaved }: { run: RunDetail; onSaved: () => void }) {
  const { t } = useTranslation();
  const [editing, setEditing] = useState(false);
  const [text, setText] = useState(run.note ?? '');
  const save = async () => {
    try {
      await setIncentiveRunNote(run.id, text);
      setEditing(false);
      onSaved();
    } catch (e) {
      toast.error(e instanceof Error ? e.message : 'Could not save note');
    }
  };
  if (editing) {
    return (
      <div className="flex items-center gap-2">
        <Input value={text} autoFocus className="h-7 max-w-md text-sm"
          placeholder={t('kpi_runs.note_ph', 'Week of 8/3 — 226 in shop Thu–Fri…')}
          onChange={(e) => setText(e.target.value)}
          onKeyDown={(e) => { if (e.key === 'Enter') void save(); if (e.key === 'Escape') setEditing(false); }} />
        <Button size="sm" variant="outline" onClick={save}>{t('common.save', 'Save')}</Button>
      </div>
    );
  }
  return (
    <button type="button" onClick={() => { setText(run.note ?? ''); setEditing(true); }}
      className="inline-flex items-center gap-1.5 text-xs text-muted-foreground hover:text-foreground transition">
      <StickyNote size={12} />
      {run.note
        ? <span className="italic">{run.note}</span>
        : t('kpi_runs.note_add', 'Add a note…')}
    </button>
  );
}

// ── Adjustments drawer — every hand-touched row, WHO and WHEN (the
// attribution migration 198 stores), with one-click revert on drafts.
function AdjustmentsDrawer({ open, run, draft, onClose, onChanged }: {
  open: boolean; run: RunDetail; draft: boolean;
  onClose: () => void; onChanged: () => void;
}) {
  const { t } = useTranslation();
  const [busy, setBusy] = useState<number | null>(null);
  const rows = run.rows.filter(isHandAdjusted);
  const who = (id: number | null) =>
    id == null ? t('kpi_runs.adj_unknown', '—') : (run.user_names[String(id)] ?? `user ${id}`);
  const when = (iso: string) => iso ? iso.slice(0, 16).replace('T', ' ') : '';

  const revert = async (r: RunRow) => {
    setBusy(r.id);
    try {
      if (r.override_pct != null) await setIncentiveException(run.id, r.id, null, '');
      await patchIncentiveRow(run.id, r.id, {
        // Generation marks nothing — reverting means every day counts
        // again and the extras zero out.
        inactive_dates: [], extras: 0, extras_note: '',
      });
      onChanged();
    } catch (e) {
      toast.error(e instanceof Error ? e.message : 'Could not revert');
    } finally {
      setBusy(null);
    }
  };

  return (
    <Sheet open={open} onOpenChange={(o) => { if (!o) onClose(); }}>
      <SheetContent>
        <SheetHeader>
          <SheetTitle>{t('kpi_runs.adj_title', 'Adjustments — {{a}} – {{b}}',
            { a: run.period_start, b: run.period_end })}</SheetTitle>
        </SheetHeader>
        <SheetBody label={t('kpi_runs.adj_title_short', 'Run adjustments')} className="p-4 space-y-3">
          <p className="text-xs text-muted-foreground max-w-prose">
            {t('kpi_runs.adj_desc',
              'Every hand adjustment on this run, with who made it. Reverting returns a row to its computed values.')}
          </p>
          {rows.length === 0 && (
            <p className="text-sm text-muted-foreground">
              {t('kpi_runs.adj_empty', 'No adjustments — every row pays exactly what the engine computed.')}
            </p>
          )}
          <ul className="divide-y divide-border border-t border-border">
            {rows.map((r) => (
              <li key={r.id} className="py-2.5 space-y-1 text-sm">
                <div className="flex items-center justify-between gap-2">
                  <span className="font-medium">{r.vehicle_unit || t('kpi_runs.unassigned', 'Unassigned')}
                    <span className="ml-1.5 text-xs text-muted-foreground">{r.company_code} · {r.dispatcher_name}</span>
                  </span>
                  {draft && (
                    <Button size="sm" variant="ghost" disabled={busy === r.id}
                      onClick={() => revert(r)}>
                      {busy === r.id
                        ? <Loader2 size={14} className="animate-spin" />
                        : <Undo2 size={14} className="mr-1" />}
                      {t('kpi_runs.adj_revert', 'Revert')}
                    </Button>
                  )}
                </div>
                <ul className="text-xs text-muted-foreground space-y-0.5">
                  {r.inactive_days > 0 && (
                    <li>
                      {t('kpi_runs.adj_days', '{{n}} inactive days', { n: r.inactive_days })}
                      {r.inactive_dates.length > 0 && (
                        <> — {r.inactive_dates.map((m) => `${m.date.slice(5)}${m.reason ? ` (${m.reason})` : ''}`).join(', ')}</>
                      )}
                      {r.inactive_reason && r.inactive_dates.length === 0 && <> — {r.inactive_reason}</>}
                    </li>
                  )}
                  {Number(r.extras) !== 0 && (
                    <li>{t('kpi_runs.adj_extras', 'Extras {{v}}', { v: usd(r.extras) })}{r.extras_note ? ` — ${r.extras_note}` : ''}</li>
                  )}
                  {r.override_pct != null && (
                    <li>{t('kpi_runs.adj_override', 'Exception {{p}}% — {{reason}} (by {{who}})',
                      { p: r.override_pct, reason: r.override_reason, who: who(r.confirmed_by) })}</li>
                  )}
                  {(r.adjusted_by != null || r.adjusted_at) && (
                    <li className="text-muted-foreground/70">
                      {t('kpi_runs.adj_by', 'last input edit: {{who}} · {{when}}',
                        { who: who(r.adjusted_by), when: when(r.adjusted_at) })}
                    </li>
                  )}
                </ul>
              </li>
            ))}
          </ul>
        </SheetBody>
      </SheetContent>
    </Sheet>
  );
}

// ── Monthly payouts (weekly calc → monthly payout roll-up) ────────────

/** The last 12 months, newest first, as YYYY-MM + a human label. */
function monthOptions(): { value: string; label: string }[] {
  const out: { value: string; label: string }[] = [];
  const d = new Date();
  for (let i = 0; i < 12; i += 1) {
    const value = `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, '0')}`;
    out.push({
      value,
      label: d.toLocaleDateString(undefined, { month: 'short', year: 'numeric' }),
    });
    d.setMonth(d.getMonth() - 1);
  }
  return out;
}

function MonthlyPayoutsPanel({ allRuns, onSelectRun }: {
  allRuns: RunSummary[];
  onSelectRun: (id: number) => void;
}) {
  const { t } = useTranslation();
  const months = monthOptions();
  const [month, setMonth] = useState(months[0].value);
  // Drafts whose period ends in the viewed month: the concrete money
  // that finalizing would add — the honest version of "no data yet".
  const pendingDrafts = allRuns.filter(
    (r) => r.status === 'draft' && r.period_end.slice(0, 7) === month);
  const q = useQuery({
    queryKey: ['kpi-monthly-payouts', month],
    queryFn: () => getMonthlyPayouts(month),
  });
  const payouts = Object.entries(q.data?.payouts ?? {})
    .sort((a, b) => b[1] - a[1]);

  return (
    <section className="mt-8 bg-card border border-border rounded-xl p-4 space-y-3">
      <div className="flex flex-wrap items-center justify-between gap-3">
        <div>
          <h2 className="text-base font-semibold">
            {t('kpi_runs.monthly_title', 'Monthly payouts')}
          </h2>
          <p className="text-xs text-muted-foreground max-w-prose">
            {t('kpi_runs.monthly_desc',
              'Finalized runs only, summed per dispatcher — a run counts in the month its period ends. For dispatchers paid monthly, this is the number payroll uses.')}
          </p>
        </div>
        <Select value={month} onValueChange={setMonth}
          items={months.map((m) => ({ value: m.value, label: m.label }))}>
          <SelectTrigger className="w-44" aria-label={t('kpi_runs.month', 'Month')}>
            <SelectValue />
          </SelectTrigger>
          <SelectContent>
            {months.map((m) => (
              <SelectItem key={m.value} value={m.value}>{m.label}</SelectItem>
            ))}
          </SelectContent>
        </Select>
      </div>
      {q.isLoading && <TableSkeleton />}
      {!q.isLoading && payouts.length === 0 && (
        <p className="text-sm text-muted-foreground">
          {t('kpi_runs.monthly_empty',
            'No finalized runs end in this month yet — drafts do not count toward a payout total.')}
        </p>
      )}
      {pendingDrafts.length > 0 && (
        <div className="space-y-1 text-xs text-muted-foreground">
          {pendingDrafts.map((d) => (
            <p key={d.id}>
              {t('kpi_runs.monthly_pending2', 'Finalizing the draft ')}
              <button type="button" onClick={() => onSelectRun(d.id)}
                className="tabular-nums underline underline-offset-4 hover:text-foreground transition">
                {d.period_start} – {d.period_end}
              </button>
              {t('kpi_runs.monthly_pending3', ' would add {{total}} to this month.',
                { total: usd(d.total) })}
            </p>
          ))}
        </div>
      )}
      {payouts.length > 0 && q.data && (
        <>
          <ul className="divide-y divide-border border-t border-border">
            {payouts.map(([name, total]) => (
              <li key={name} className="flex items-center justify-between py-2 text-sm">
                <span>{name}</span>
                <span className="font-medium tabular-nums">{usd(total)}</span>
              </li>
            ))}
          </ul>
          <div className="flex items-center justify-between text-sm">
            <span className="text-muted-foreground">
              {t('kpi_runs.monthly_runs', '{{n}} finalized runs', { n: q.data.runs.length })}
              {': '}
              <span className="tabular-nums">
                {q.data.runs.map((r) => `${r.period_start} – ${r.period_end}`).join(', ')}
              </span>
            </span>
            <span className="text-base font-semibold tabular-nums">{usd(q.data.total)}</span>
          </div>
        </>
      )}
    </section>
  );
}

// ── New run ───────────────────────────────────────────────────────────

function NewRunDialog({ open, onClose, onCreated, existing }: {
  open: boolean;
  onClose: () => void;
  onCreated: (r: RunDetail) => void;
  /** Existing runs, newest first — drives the default period and the
   *  overlap warning. */
  existing: RunSummary[];
}) {
  const { t } = useTranslation();
  // Default: the next UNCOVERED 7-day period — the day after the newest
  // run, when that period has already happened; otherwise the last full
  // 7 days ending yesterday.  Defaulting into a period that already has
  // a run invites a duplicate; the warning below catches the rest.
  const yesterday = new Date(); yesterday.setDate(yesterday.getDate() - 1);
  let start = new Date(yesterday); start.setDate(start.getDate() - 6);
  const latest = existing[0];
  if (latest) {
    const next = new Date(`${latest.period_end.slice(0, 10)}T00:00:00`);
    next.setDate(next.getDate() + 1);
    if (iso(next) <= iso(yesterday)) start = next;
  }
  const defEnd = new Date(start); defEnd.setDate(defEnd.getDate() + 6);
  const [from, setFrom] = useState(iso(start));
  const [to, setTo] = useState(
    iso(defEnd) > iso(yesterday) ? iso(yesterday) : iso(defEnd));
  const [busy, setBusy] = useState(false);

  const badOrder = !!from && !!to && to < from;
  const overlap = !badOrder && from && to
    ? existing.find((r) => from <= r.period_end && to >= r.period_start)
    : undefined;
  const previewQ = useQuery({
    queryKey: ['kpi-run-preview', from, to],
    queryFn: () => previewIncentiveRun(from, to),
    enabled: open && !!from && !!to && !badOrder,
  });

  const create = async () => {
    setBusy(true);
    try {
      onCreated(await createIncentiveRun(from, to));
    } catch (e) {
      // 422s carry the setup message ("Incentives are not configured…").
      toast.error(e instanceof Error ? e.message : 'Could not create the run');
    } finally {
      setBusy(false);
    }
  };

  return (
    <Dialog open={open} onOpenChange={(o) => { if (!o) onClose(); }}>
      <DialogContent className="max-w-lg">
        <DialogHeader>
          <DialogTitle>{t('kpi_runs.new_title', 'New incentive run')}</DialogTitle>
        </DialogHeader>
        <p className="text-sm text-muted-foreground">
          {t('kpi_runs.new_body',
            'Rows are generated from the period’s loads and computed under the CURRENT rules, which the run keeps as its snapshot.')}
          {' '}
          <Link to="/kpi/dispatch/configuration" className="text-primary underline underline-offset-4 hover:no-underline">
            {t('kpi_runs.view_rules', 'View current rules')}
          </Link>
        </p>
        <div className="flex items-center gap-3">
          <label className="text-sm space-y-1">
            <span className="block text-muted-foreground">{t('kpi_runs.from', 'From')}</span>
            <Input type="date" value={from} onChange={(e) => setFrom(e.target.value)} />
          </label>
          <label className="text-sm space-y-1">
            <span className="block text-muted-foreground">{t('kpi_runs.to', 'To')}</span>
            <Input type="date" value={to} onChange={(e) => setTo(e.target.value)} />
          </label>
        </div>
        {previewQ.data && (
          /* Scope BEFORE commit: a mis-typed range shows up as a wrong
             truck count here, not as a generated wrong run. */
          <p className="text-xs text-muted-foreground">
            {t('kpi_runs.preview_line',
              'This period holds {{loads}} loads · {{trucks}} trucks · {{dispatchers}} dispatchers · {{gross}} gross.',
              { loads: previewQ.data.loads, trucks: previewQ.data.trucks,
                dispatchers: previewQ.data.dispatchers,
                gross: usd(previewQ.data.gross) })}
          </p>
        )}
        {/* A disabled Create must say WHY; an overlapping period must
            say so BEFORE the duplicate exists. */}
        {badOrder && (
          <p className="text-sm text-danger">
            {t('kpi_runs.bad_order', 'The end date must come after the start date.')}
          </p>
        )}
        {overlap && (
          <p className={`inline-flex items-center text-xs ${toneClasses('warn')} px-2 py-1 rounded`}>
            {t('kpi_runs.overlap_warn',
              'This period overlaps the {{status}} run {{a}} – {{b}} — creating it makes a second run over the same loads.',
              { status: overlap.status, a: overlap.period_start, b: overlap.period_end })}
          </p>
        )}
        <DialogFooter>
          <Button variant="outline" onClick={onClose}>{t('common.cancel', 'Cancel')}</Button>
          <Button onClick={create} disabled={busy || !from || !to || badOrder}>
            {busy && <Loader2 size={16} className="animate-spin mr-1.5" />}
            {t('kpi_runs.create', 'Create run')}
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}

// ── Days & extras ─────────────────────────────────────────────────────

function EditRowDialog({ runId, row, onClose, onSaved }: {
  runId: number; row: RunRow;
  onClose: () => void; onSaved: () => void;
}) {
  const { t } = useTranslation();
  const [inactive, setInactive] = useState(String(row.inactive_days));
  const [reason, setReason] = useState(row.inactive_reason);
  const [extras, setExtras] = useState(String(row.extras));
  const [note, setNote] = useState(row.extras_note);
  const [busy, setBusy] = useState(false);

  const save = async () => {
    setBusy(true);
    try {
      // Only CHANGED fields travel: an echoed inactive_days would hit
      // the server's coarse-number branch and wipe the per-day marks
      // (including the auto edge excuses) on an extras-only edit.
      const patch: Parameters<typeof patchIncentiveRow>[2] = {};
      if (Number(inactive) !== Number(row.inactive_days)) patch.inactive_days = Number(inactive) || 0;
      if (reason !== row.inactive_reason) patch.inactive_reason = reason;
      if (Number(extras) !== Number(row.extras)) patch.extras = Number(extras) || 0;
      if (note !== row.extras_note) patch.extras_note = note;
      if (Object.keys(patch).length > 0) await patchIncentiveRow(runId, row.id, patch);
      onSaved();
    } catch (e) {
      toast.error(e instanceof Error ? e.message : 'Could not save');
    } finally {
      setBusy(false);
    }
  };

  return (
    <Dialog open onOpenChange={(o) => { if (!o) onClose(); }}>
      <DialogContent className="max-w-lg">
        <DialogHeader>
          <DialogTitle>
            {t('kpi_runs.edit_title', 'Unit {{unit}} — days & extras', { unit: row.vehicle_unit })}
          </DialogTitle>
        </DialogHeader>
        <p className="text-sm text-muted-foreground">
          {t('kpi_runs.edit_body',
            'Inactive days (repair, home time) lower the gross target — the dispatcher is not responsible for them. Extras (TONU, bonus) adjust the gross the % applies to. The row recomputes on save.')}
        </p>
        <div className="grid grid-cols-2 gap-3">
          <label className="text-sm space-y-1">
            <span className="block text-muted-foreground">
              {t('kpi_runs.inactive', 'Inactive days (of {{total}})', { total: row.total_days })}
            </span>
            <Input type="number" min={0} max={row.total_days} value={inactive}
              onChange={(e) => setInactive(e.target.value)} />
          </label>
          <label className="text-sm space-y-1">
            <span className="block text-muted-foreground">{t('kpi_runs.reason', 'Reason')}</span>
            <Input value={reason} placeholder={t('kpi_runs.reason_ph', 'repair, home time…')}
              onChange={(e) => setReason(e.target.value)} />
          </label>
          <label className="text-sm space-y-1">
            <span className="block text-muted-foreground">{t('kpi_runs.extras', 'Extras ($)')}</span>
            <Input type="number" step="0.01" value={extras}
              onChange={(e) => setExtras(e.target.value)} />
          </label>
          <label className="text-sm space-y-1">
            <span className="block text-muted-foreground">{t('kpi_runs.extras_note', 'Extras note')}</span>
            <Input value={note} placeholder="TONU, Bonus…"
              onChange={(e) => setNote(e.target.value)} />
          </label>
        </div>
        <DialogFooter>
          <Button variant="outline" onClick={onClose}>{t('common.cancel', 'Cancel')}</Button>
          <Button onClick={save} disabled={busy}>
            {busy && <Loader2 size={16} className="animate-spin mr-1.5" />}
            {t('kpi_runs.save_recompute', 'Save & recompute')}
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}

// ── Exception ─────────────────────────────────────────────────────────

function ExceptionDialog({ runId, row, onClose, onSaved }: {
  runId: number; row: RunRow;
  onClose: () => void; onSaved: () => void;
}) {
  const { t } = useTranslation();
  const [pct, setPct] = useState(row.override_pct != null ? String(row.override_pct) : '');
  const [reason, setReason] = useState(row.override_reason);
  const [busy, setBusy] = useState(false);

  const apply = async (clear: boolean) => {
    setBusy(true);
    try {
      await setIncentiveException(
        runId, row.id,
        clear ? null : Number(pct), clear ? '' : reason,
      );
      onSaved();
    } catch (e) {
      // The server's message carries the cap ("exceeds the policy cap of
      // 2%") — verbatim, no paraphrase.
      toast.error(e instanceof Error ? e.message : 'Could not apply');
    } finally {
      setBusy(false);
    }
  };

  return (
    <Dialog open onOpenChange={(o) => { if (!o) onClose(); }}>
      <DialogContent className="max-w-lg">
        <DialogHeader>
          <DialogTitle>
            {t('kpi_runs.exc_title', 'Unit {{unit}} — exception', { unit: row.vehicle_unit })}
          </DialogTitle>
        </DialogHeader>
        <p className="text-sm text-muted-foreground">
          {t('kpi_runs.exc_body',
            'Replaces the computed {{pct}}% with a chosen percent, for a named reason (home time, truck issues, recovery, new MC). Overrides above the policy cap are refused.',
            { pct: row.pct })}
        </p>
        <div className="grid grid-cols-2 gap-3">
          <label className="text-sm space-y-1">
            <span className="block text-muted-foreground">{t('kpi_runs.exc_pct', 'Override %')}</span>
            <Input type="number" step="0.25" value={pct}
              onChange={(e) => setPct(e.target.value)} />
          </label>
          <label className="text-sm space-y-1">
            <span className="block text-muted-foreground">{t('kpi_runs.exc_reason', 'Reason (required)')}</span>
            <Input value={reason} placeholder={t('kpi_runs.exc_reason_ph', 'new MC, recovery…')}
              onChange={(e) => setReason(e.target.value)} />
          </label>
        </div>
        <DialogFooter>
          {/* mr-auto: a server WRITE must not sit in the Cancel corner
              wearing Cancel's outline shape — far left is the "act on
              existing state" slot. */}
          {row.override_pct != null && (
            <Button variant="outline" className="mr-auto" onClick={() => apply(true)} disabled={busy}>
              {t('kpi_runs.exc_clear', 'Clear override')}
            </Button>
          )}
          <Button variant="outline" onClick={onClose}>{t('common.cancel', 'Cancel')}</Button>
          <Button onClick={() => apply(false)} disabled={busy || pct === '' || !reason.trim()}>
            {busy && <Loader2 size={16} className="animate-spin mr-1.5" />}
            {t('kpi_runs.exc_apply', 'Apply exception')}
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}
